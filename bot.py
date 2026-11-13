import asyncio
import os
import random
import time
from html import escape

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message
from dotenv import load_dotenv

from content import SPELLS, EVENTS, BOOKS, HOUSES
from storage import DB

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("Не найден BOT_TOKEN в .env")
ADMINS = {int(x) for x in os.getenv("ADMIN_IDS", "").split() if x.isdigit()}
EVENT_MIN = int(os.getenv("EVENT_INTERVAL_MINUTES", "30"))
MAIL_MIN = int(os.getenv("MAIL_DELAY_MINUTES", "5"))

db = DB()
bot = Bot(TOKEN)
dp = Dispatcher()

HELP = '''<b>Команды Хогвартса</b>
/start — регистрация
/house Гриффиндор — выбрать факультет
/spell [учебные|боевые|запрещённые] — заклинание
/duel @игрок — вызов на дуэль
/accept — принять дуэль
/cast Название — применить заклинание
/defend — Protego на ход
/dodge — попытка уклониться
/event — случайное событие
/library — каталог книг
/borrow ID — взять книгу
/return ID — вернуть книгу
/points — таблица факультетов
/award @игрок 10 причина — выдать баллы, админ
/mail @игрок текст — письмо с задержкой
/mail anonymous @игрок текст — анонимное письмо
/inbox — доставленные письма
/help — эта справка'''


def remember(message: Message):
    u = message.from_user
    db.user(u.id, u.username, u.full_name)
    if message.chat.type in ("group", "supergroup"):
        db.run("INSERT OR IGNORE INTO chats(chat_id) VALUES(?)", (message.chat.id,))


def arg_text(message):
    return message.text.partition(" ")[2].strip()


def find_spell(name):
    n = name.casefold().strip()
    return next((s for s in SPELLS if s["name"].casefold() == n), None)


def target_username(token):
    if not token.startswith("@"):
        return None
    return db.find_username(token)


def is_admin(uid):
    return uid in ADMINS


@dp.message(Command("start"))
async def start(message: Message):
    remember(message)
    await message.answer("🪄 Добро пожаловать в Хогвартс!\n\n" + HELP)


@dp.message(Command("help"))
async def help_cmd(message: Message):
    remember(message)
    await message.answer(HELP)


@dp.message(Command("house"))
async def house(message: Message):
    remember(message)
    choice = arg_text(message).casefold()
    aliases = {"гриффиндор": "Гриффиндор", "слизерин": "Слизерин", "когтевран": "Когтевран", "пуффендуй": "Пуффендуй"}
    if choice not in aliases:
        await message.answer("Укажите: Гриффиндор, Слизерин, Когтевран или Пуффендуй.")
        return
    db.run("UPDATE users SET house=? WHERE id=?", (aliases[choice], message.from_user.id))
    await message.answer(f"🎩 Вы распределены на факультет <b>{aliases[choice]}</b>!", parse_mode="HTML")


@dp.message(Command("spell"))
async def spell(message: Message):
    remember(message)
    category = arg_text(message).casefold()
    allowed = {"учебные", "боевые", "запрещённые"}
    pool = [s for s in SPELLS if category in allowed and s["category"] == category] if category in allowed else SPELLS
    s = random.choice(pool)
    await message.answer(f"✨ <b>{s['name']}</b>\nКатегория: {s['category']}\n{s['effect']}", parse_mode="HTML")


@dp.message(Command("duel"))
async def duel(message: Message):
    remember(message)
    target = target_username(arg_text(message).split()[0] if arg_text(message) else "")
    if not target:
        await message.answer("Укажите зарегистрированного игрока: /duel @username")
        return
    if target["id"] == message.from_user.id:
        await message.answer("Нельзя вызвать на дуэль самого себя.")
        return
    active = db.run("SELECT * FROM duels WHERE status IN ('pending','active') AND (a=? OR b=?)", (message.from_user.id, message.from_user.id), True)
    if active:
        await message.answer("У вас уже есть незавершённая дуэль.")
        return
    d = db.run("INSERT INTO duels(chat_id,a,b,turn,status) VALUES(?,?,?,?,?)", (message.chat.id, message.from_user.id, target["id"], message.from_user.id, "pending"))
    await message.answer(f"⚔️ Игрок @{escape(target['username'] or target['name'])}, вас вызывают на дуэль!\nПринять: /accept\nID дуэли: {d}", parse_mode="HTML")


@dp.message(Command("accept"))
async def accept(message: Message):
    remember(message)
    rows = db.run("SELECT * FROM duels WHERE b=? AND status='pending' ORDER BY id DESC LIMIT 1", (message.from_user.id,), True)
    if not rows:
        await message.answer("Для вас нет ожидающих вызовов.")
        return
    d = rows[0]
    db.run("UPDATE duels SET status='active' WHERE id=?", (d["id"],))
    await message.answer("⚔️ Дуэль началась! У каждого 100 HP. Ход вызывающего. Используйте /cast Stupefy, /defend или /dodge.")


def duel_for(uid):
    rows = db.run("SELECT * FROM duels WHERE status='active' AND (a=? OR b=?)", (uid, uid), True)
    return rows[0] if rows else None


async def duel_action(message: Message, action, spell=None):
    remember(message)
    d = duel_for(message.from_user.id)
    if not d:
        await message.answer("У вас нет активной дуэли.")
        return
    if d["turn"] != message.from_user.id:
        await message.answer("Сейчас ход противника.")
        return
    if action == "cast":
        if not spell:
            await message.answer("Укажите заклинание: /cast Stupefy")
            return
        if spell["kind"] == "forbidden" and not is_admin(message.from_user.id):
            await message.answer("🚫 Запрещённые заклинания в дуэлях доступны только администраторам.")
            return
        if spell["kind"] == "utility":
            await message.answer(f"✨ {spell['name']}: {spell['effect']} В дуэли это не наносит урон.")
            return
        opponent = d["b"] if d["a"] == message.from_user.id else d["a"]
        damage = spell["damage"]
        if d["shield"]:
            damage //= 2
        if d["dodge"] and random.random() < .5:
            damage = 0
        hp_col = "hp_b" if d["a"] == message.from_user.id else "hp_a"
        new_hp = max(0, d[hp_col] - damage)
        if new_hp == 0:
            db.run(f"UPDATE duels SET {hp_col}=?, status='finished' WHERE id=?", (new_hp, d["id"]))
            await message.answer(f"💥 {spell['name']} наносит {damage} урона. Дуэль окончена — победа игрока @{message.from_user.username or message.from_user.full_name}!")
            return
        db.run(f"UPDATE duels SET {hp_col}=?, turn=?, shield=0, dodge=0 WHERE id=?", (new_hp, opponent, d["id"]))
        await message.answer(f"⚡ {spell['name']} наносит {damage} урона. У соперника осталось {new_hp} HP. Ход переходит противнику.")
    elif action == "defend":
        db.run("UPDATE duels SET shield=1, turn=?, shield=1, dodge=0 WHERE id=?", (d["b"] if d["a"] == message.from_user.id else d["a"], d["id"]))
        await message.answer("🛡️ Protego активирован: следующий урон уменьшен наполовину.")
    else:
        db.run("UPDATE duels SET dodge=1, turn=?, shield=0 WHERE id=?", (d["b"] if d["a"] == message.from_user.id else d["a"], d["id"]))
        await message.answer("💨 Вы приготовились уклониться: шанс избежать следующей атаки — 50%.")


@dp.message(Command("cast"))
async def cast(message: Message):
    await duel_action(message, "cast", find_spell(arg_text(message)))


@dp.message(Command("defend"))
async def defend(message: Message):
    await duel_action(message, "defend")


@dp.message(Command("dodge"))
async def dodge(message: Message):
    await duel_action(message, "dodge")


@dp.message(Command("event"))
async def event(message: Message):
    remember(message)
    await message.answer("📜 <b>Событие для роли:</b>\n" + random.choice(EVENTS), parse_mode="HTML")


@dp.message(Command("library"))
async def library(message: Message):
    remember(message)
    text = "📚 <b>Библиотека Хогвартса</b>\n"
    for i, title, section, desc in BOOKS:
        text += f"\n<b>{i}. {title}</b> — {section}\n{desc}"
    await message.answer(text, parse_mode="HTML")


@dp.message(Command("borrow"))
async def borrow(message: Message):
    remember(message)
    try: bid = int(arg_text(message))
    except ValueError:
        await message.answer("Пример: /borrow 1"); return
    book = next((b for b in BOOKS if b[0] == bid), None)
    if not book: await message.answer("Такой книги нет."); return
    u = db.user(message.from_user.id)
    if book[2] == "Секретный" and u["points"] < 50:
        await message.answer("🔒 Секретный раздел откроется после 50 личных баллов."); return
    if db.run("SELECT * FROM loans WHERE book_id=?", (bid,), True):
        await message.answer("Эта книга уже выдана."); return
    db.run("INSERT INTO loans VALUES(?,?,?)", (bid, message.from_user.id, int(time.time()) + 7*86400))
    await message.answer(f"📖 Вы взяли «{book[1]}» на 7 дней.")


@dp.message(Command("return"))
async def return_book(message: Message):
    remember(message)
    try: bid = int(arg_text(message))
    except ValueError: await message.answer("Пример: /return 1"); return
    db.run("DELETE FROM loans WHERE book_id=? AND user_id=?", (bid, message.from_user.id))
    await message.answer("📚 Книга возвращена в библиотеку.")


@dp.message(Command("points"))
async def points(message: Message):
    remember(message)
    totals = [(h, db.house_total(h)) for h in HOUSES]
    totals.sort(key=lambda x: x[1], reverse=True)
    await message.answer("🏆 <b>Кубок факультетов</b>\n" + "\n".join(f"{i}. {h}: {p}" for i, (h,p) in enumerate(totals, 1)), parse_mode="HTML")


@dp.message(Command("award"))
async def award(message: Message):
    remember(message)
    if not is_admin(message.from_user.id): await message.answer("Только для администраторов."); return
    parts = arg_text(message).split(maxsplit=2)
    if len(parts) < 3 or not parts[0].startswith("@"):
        await message.answer("Пример: /award @username 10 за смелость"); return
    target = target_username(parts[0])
    if not target or not target["house"]: await message.answer("Игрок не найден или не выбрал факультет."); return
    try: amount = int(parts[1])
    except ValueError: await message.answer("Количество баллов должно быть числом."); return
    db.run("UPDATE users SET points=points+? WHERE id=?", (amount, target["id"]))
    db.run("INSERT INTO points_log(user_id,house,amount,reason,created) VALUES(?,?,?,?,?)", (target["id"], target["house"], amount, parts[2], int(time.time())))
    await message.answer(f"✨ Начислено {amount} баллов факультету {target['house']}.")


@dp.message(Command("mail"))
async def mail(message: Message):
    remember(message)
    parts = arg_text(message).split(maxsplit=2)
    anonymous = 0
    if parts and parts[0].casefold() == "anonymous": anonymous = 1; parts.pop(0)
    if len(parts) < 2 or not parts[0].startswith("@"):
        await message.answer("Пример: /mail @username текст\nАнонимно: /mail anonymous @username текст"); return
    target = target_username(parts[0])
    if not target: await message.answer("Адресат должен сначала написать боту /start."); return
    db.run("INSERT INTO mail(sender,receiver,body,owl,anonymous,deliver_at) VALUES(?,?,?,?,?,?)", (message.from_user.id, target["id"], parts[1], random.choice(["🦉 Хедвига", "🦉 Букля", "🦉 Серая сова"]), anonymous, int(time.time()) + MAIL_MIN*60))
    await message.answer(f"🦉 Письмо отправлено. Доставка займёт около {MAIL_MIN} минут.")


@dp.message(Command("inbox"))
async def inbox(message: Message):
    remember(message)
    rows = db.run("SELECT * FROM mail WHERE receiver=? AND delivered=1 ORDER BY id DESC LIMIT 10", (message.from_user.id,), True)
    if not rows: await message.answer("📭 Доставленных писем нет."); return
    text = "📬 <b>Ваша магическая почта</b>\n"
    for r in rows:
        sender = "Анонимный волшебник" if r["anonymous"] else (db.run("SELECT name FROM users WHERE id=?", (r["sender"],), True)[0]["name"] or "Волшебник")
        text += f"\n{r['owl']} — <b>{escape(sender)}</b>:\n{escape(r['body'])}\n"
    await message.answer(text, parse_mode="HTML")


async def background():
    last_event = time.time()
    while True:
        now = int(time.time())
        rows = db.run("SELECT * FROM mail WHERE delivered=0 AND deliver_at<=?", (now,), True)
        for r in rows:
            try:
                sender = "Анонимный волшебник" if r["anonymous"] else "Ваш корреспондент"
                await bot.send_message(r["receiver"], f"🦉 Вам доставлено письмо!\nОтправитель: {sender}\n\n{r['body']}")
            except Exception:
                pass
            db.run("UPDATE mail SET delivered=1 WHERE id=?", (r["id"],))
        if now - last_event >= EVENT_MIN * 60:
            chats = db.run("SELECT chat_id FROM chats", fetch=True)
            for c in chats:
                try: await bot.send_message(c["chat_id"], "📜 <b>Событие для роли:</b>\n" + random.choice(EVENTS), parse_mode="HTML")
                except Exception: pass
            last_event = now
        await asyncio.sleep(30)


async def main():
    asyncio.create_task(background())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
