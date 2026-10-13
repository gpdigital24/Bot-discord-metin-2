import sqlite3
import time
from pathlib import Path


class Store:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE COLLATE NOCASE,
            owner INTEGER, since INTEGER, message_id INTEGER UNIQUE
        )""")
        self.db.commit()

    def all(self):
        return self.db.execute("SELECT * FROM accounts ORDER BY id").fetchall()

    def get(self, account_id):
        return self.db.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()

    def by_message(self, message_id):
        return self.db.execute("SELECT * FROM accounts WHERE message_id=?", (message_id,)).fetchone()

    def add(self, name):
        with self.db:
            return self.db.execute("INSERT INTO accounts(name) VALUES (?)", (name,)).lastrowid

    def message(self, account_id, message_id):
        with self.db:
            self.db.execute("UPDATE accounts SET message_id=? WHERE id=?", (message_id, account_id))

    def take(self, account_id, user_id):
        with self.db:
            return self.db.execute(
                "UPDATE accounts SET owner=?, since=? WHERE id=? AND owner IS NULL",
                (user_id, int(time.time()), account_id),
            ).rowcount == 1

    def release(self, account_id, user_id, manager=False):
        with self.db:
            return self.db.execute(
                """UPDATE accounts SET owner=NULL, since=NULL
                   WHERE id=? AND owner IS NOT NULL AND (owner=? OR ?=1)""",
                (account_id, user_id, int(manager)),
            ).rowcount == 1

    def rename(self, account_id, name):
        with self.db:
            return self.db.execute("UPDATE accounts SET name=? WHERE id=?", (name, account_id)).rowcount

    def remove(self, account_id):
        with self.db:
            self.db.execute("DELETE FROM accounts WHERE id=?", (account_id,))

    def close(self):
        self.db.close()
