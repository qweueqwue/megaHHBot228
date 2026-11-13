import sqlite3
import threading
import time

class DB:
    def __init__(self, path="hogwarts.sqlite3"):
        self.con = sqlite3.connect(path, check_same_thread=False)
        self.con.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        self.init()

    def run(self, sql, args=(), fetch=False, many=False):
        with self.lock:
            cur = self.con.cursor()
            if many: cur.executemany(sql, args)
            else: cur.execute(sql, args)
            self.con.commit()
            return cur.fetchall() if fetch else cur.lastrowid

    def init(self):
        self.run("""CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY, username TEXT, name TEXT, house TEXT,
            points INTEGER DEFAULT 0, created INTEGER)""")
        self.run("""CREATE TABLE IF NOT EXISTS points_log(
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, house TEXT,
            amount INTEGER, reason TEXT, created INTEGER)""")
        self.run("""CREATE TABLE IF NOT EXISTS duels(
            id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, a INTEGER, b INTEGER,
            hp_a INTEGER DEFAULT 100, hp_b INTEGER DEFAULT 100, turn INTEGER,
            status TEXT DEFAULT 'pending', shield INTEGER DEFAULT 0, dodge INTEGER DEFAULT 0)""")
        self.run("""CREATE TABLE IF NOT EXISTS loans(
            book_id INTEGER, user_id INTEGER, due INTEGER, PRIMARY KEY(book_id))""")
        self.run("""CREATE TABLE IF NOT EXISTS mail(
            id INTEGER PRIMARY KEY AUTOINCREMENT, sender INTEGER, receiver INTEGER,
            body TEXT, owl TEXT, anonymous INTEGER, deliver_at INTEGER, delivered INTEGER DEFAULT 0)""")
        self.run("""CREATE TABLE IF NOT EXISTS chats(chat_id INTEGER PRIMARY KEY)""")

    def user(self, uid, username=None, name=None):
        old = self.run("SELECT * FROM users WHERE id=?", (uid,), True)
        if old:
            self.run("UPDATE users SET username=?, name=? WHERE id=?", (username or old[0]['username'], name or old[0]['name'], uid))
        else:
            self.run("INSERT INTO users VALUES(?,?,?,?,?,?)", (uid, username, name, None, 0, int(time.time())))
        return self.run("SELECT * FROM users WHERE id=?", (uid,), True)[0]

    def find_username(self, username):
        rows = self.run("SELECT * FROM users WHERE lower(username)=lower(?)", (username.lstrip('@'),), True)
        return rows[0] if rows else None

    def house_total(self, house):
        r = self.run("SELECT COALESCE(SUM(amount),0) n FROM points_log WHERE house=?", (house,), True)
        return r[0]['n']
