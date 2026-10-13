import sqlite3
import time
from pathlib import Path


class Store:
    def __init__(self, path, legacy_guild_id=0):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        columns = {row['name'] for row in self.db.execute('PRAGMA table_info(accounts)')}
        if columns and 'guild_id' not in columns:
            backup = sqlite3.connect(str(path) + '.before-multiguild.bak')
            self.db.backup(backup)
            backup.close()
            has_settings = self.db.execute("SELECT 1 FROM sqlite_master WHERE name='settings'").fetchone()
            saved = self.db.execute('SELECT * FROM settings WHERE id=1').fetchone() if has_settings else None
            old_guild = (saved['guild_id'] if saved else 0) or legacy_guild_id
            if not old_guild and self.db.execute('SELECT 1 FROM accounts LIMIT 1').fetchone():
                self.db.close()
                raise RuntimeError('Migration impossible : serveur des anciens comptes inconnu. Renseigne DISCORD_GUILD_ID.')
            self.db.execute('BEGIN IMMEDIATE')
            try:
                self.db.execute('ALTER TABLE accounts RENAME TO accounts_legacy')
                self._schema()
                self.db.execute('''INSERT INTO accounts(id,guild_id,name,owner,since,message_id)
                    SELECT id,?,name,owner,since,message_id FROM accounts_legacy''', (old_guild,))
                if saved and old_guild:
                    self.db.execute('INSERT INTO guild_settings VALUES (?,?,?)', (old_guild,saved['channel_id'],saved['role_id']))
                self.db.execute('DROP TABLE accounts_legacy')
                if has_settings:
                    self.db.execute('DROP TABLE settings')
                self.db.commit()
            except Exception:
                self.db.rollback()
                self.db.close()
                raise
        else:
            self._schema()
            self.db.commit()

        with self.db:
            self.db.execute('CREATE TABLE IF NOT EXISTS notification_roles (guild_id INTEGER NOT NULL, role_id INTEGER NOT NULL, PRIMARY KEY(guild_id, role_id))')
            self.db.execute('INSERT OR IGNORE INTO notification_roles SELECT guild_id,role_id FROM guild_settings WHERE role_id != 0')

    def _schema(self):
        self.db.execute('''CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL,
            name TEXT NOT NULL COLLATE NOCASE, owner INTEGER, since INTEGER,
            message_id INTEGER UNIQUE, UNIQUE(guild_id,name))''')
        self.db.execute('''CREATE TABLE IF NOT EXISTS guild_settings (
            guild_id INTEGER PRIMARY KEY, channel_id INTEGER NOT NULL, role_id INTEGER NOT NULL)''')

    def settings(self, guild_id):
        row = self.db.execute('SELECT * FROM guild_settings WHERE guild_id=?', (guild_id,)).fetchone()
        result = dict(row) if row else dict(guild_id=guild_id, channel_id=0, role_id=0)
        result['role_ids'] = [r[0] for r in self.db.execute('SELECT role_id FROM notification_roles WHERE guild_id=? ORDER BY role_id', (guild_id,))]
        return result

    def configured_guilds(self):
        return [r[0] for r in self.db.execute('SELECT guild_id FROM guild_settings')]

    def configure(self, guild_id, channel_id, role_id):
        roles = sorted(set(int(r) for r in (role_id if isinstance(role_id, list) else [role_id]) if int(r)))
        if len(roles) > 25 or any(r < 1 for r in roles):
            raise ValueError('Choisis au maximum 25 rôles.')
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO guild_settings VALUES (?,?,?)', (guild_id, channel_id, roles[0] if roles else 0))
            self.db.execute('DELETE FROM notification_roles WHERE guild_id=?', (guild_id,))
            self.db.executemany('INSERT INTO notification_roles VALUES (?,?)', [(guild_id, r) for r in roles])

    def all(self, guild_id=None):
        if guild_id is None:
            return self.db.execute('SELECT * FROM accounts ORDER BY id').fetchall()
        return self.db.execute('SELECT * FROM accounts WHERE guild_id=? ORDER BY id', (guild_id,)).fetchall()

    def get(self, account_id, guild_id=None):
        if guild_id is None:
            return self.db.execute('SELECT * FROM accounts WHERE id=?', (account_id,)).fetchone()
        return self.db.execute('SELECT * FROM accounts WHERE id=? AND guild_id=?', (account_id,guild_id)).fetchone()

    def by_message(self, message_id, guild_id):
        return self.db.execute('SELECT * FROM accounts WHERE message_id=? AND guild_id=?', (message_id,guild_id)).fetchone()

    def add(self, name, guild_id):
        with self.db:
            return self.db.execute('INSERT INTO accounts(name,guild_id) VALUES (?,?)', (name,guild_id)).lastrowid

    def message(self, account_id, message_id):
        with self.db:
            self.db.execute('UPDATE accounts SET message_id=? WHERE id=?', (message_id,account_id))

    def take(self, account_id, user_id, guild_id):
        with self.db:
            return self.db.execute('UPDATE accounts SET owner=?,since=? WHERE id=? AND guild_id=? AND owner IS NULL',
                (user_id,int(time.time()),account_id,guild_id)).rowcount == 1

    def release(self, account_id, user_id, guild_id, manager=False):
        with self.db:
            return self.db.execute('''UPDATE accounts SET owner=NULL,since=NULL
                WHERE id=? AND guild_id=? AND owner IS NOT NULL AND (owner=? OR ?=1)''',
                (account_id,guild_id,user_id,int(manager))).rowcount == 1

    def rename(self, account_id, name, guild_id):
        with self.db:
            return self.db.execute('UPDATE accounts SET name=? WHERE id=? AND guild_id=?', (name,account_id,guild_id)).rowcount

    def remove(self, account_id, guild_id):
        with self.db:
            return self.db.execute('DELETE FROM accounts WHERE id=? AND guild_id=?', (account_id,guild_id)).rowcount

    def close(self):
        self.db.close()
