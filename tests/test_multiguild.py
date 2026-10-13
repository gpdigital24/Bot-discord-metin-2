import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiohttp.test_utils import TestClient, TestServer
from bot import AccountCommands, ConfigurationView, DropBot
from dashboard import Dashboard
from store import Store
from test_dashboard import fixture


class MigrationTests(unittest.TestCase):
    def test_legacy_data_and_backup_survive_migration_and_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'accounts.sqlite3'
            db = sqlite3.connect(path)
            db.executescript('''
                CREATE TABLE accounts(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL UNIQUE COLLATE NOCASE,owner INTEGER,since INTEGER,message_id INTEGER UNIQUE);
                CREATE TABLE settings(id INTEGER PRIMARY KEY CHECK(id=1),guild_id INTEGER,channel_id INTEGER,role_id INTEGER);
                INSERT INTO settings VALUES(1,111,222,333);
                INSERT INTO accounts VALUES(8,'Drop',444,123456,555);
            ''')
            db.close()
            store = Store(path)
            self.assertEqual(store.settings(111)['channel_id'],222)
            self.assertEqual(tuple(store.get(8)),(8,111,'Drop',444,123456,555))
            self.assertEqual(store.all(999),[])
            second = store.add('Drop',999)
            self.assertGreater(second,8)
            self.assertEqual(store.by_message(555,999),None)
            store.close()
            store = Store(path)
            self.assertEqual(len(store.all(111)),1)
            self.assertEqual(len(store.all(999)),1)
            store.close()
            backup = sqlite3.connect(str(path)+'.before-multiguild.bak')
            self.assertEqual(backup.execute('SELECT owner FROM accounts WHERE id=8').fetchone()[0],444)
            backup.close()

    def test_unknown_legacy_owner_stops_without_losing_accounts(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'db.sqlite3'
            db=sqlite3.connect(path)
            db.executescript("CREATE TABLE accounts(id INTEGER PRIMARY KEY,name TEXT,owner INTEGER,since INTEGER,message_id INTEGER); INSERT INTO accounts VALUES(1,'Drop',NULL,NULL,NULL);")
            db.close()
            with self.assertRaises(RuntimeError): Store(path)
            store=Store(path,legacy_guild_id=111)
            self.assertEqual(store.get(1)['guild_id'],111)
            store.close()


class IsolationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.bot,self.guild,self.member=fixture(Path(self.tmp.name)/'db.sqlite3')
        self.foreign=self.bot.store.add('Compte privé guilde B',99)
        self.bot.store.take(self.foreign,88,99)
        self.bot.store.configure(99,999,777)
        self.local=self.bot.store.add('Compte A',11)
        self.dashboard=Dashboard(self.bot,'http://localhost:8080')
        self.client=TestClient(TestServer(self.dashboard.app))
        await self.client.start_server()
        ticket=self.dashboard.issue(11,10).split('ticket=')[1]
        await self.client.post('/api/login',json={'ticket':ticket},headers={'Origin':self.dashboard.origin})
        self.state=await (await self.client.get('/api/state')).json()
        self.headers={'Origin':self.dashboard.origin,'X-CSRF-Token':self.state['csrf'],'X-Guild-ID':'11'}

    async def asyncTearDown(self):
        await self.client.close()
        self.bot.store.close()
        self.tmp.cleanup()

    async def test_foreign_accounts_never_listed_or_mutated(self):
        self.assertEqual([a['name'] for a in self.state['accounts']],['Compte A'])
        for action in ('rename','release','remove','refresh'):
            response=await self.client.post('/api/accounts',json={'action':action,'id':self.foreign,'name':'Intrusion','guild_id':99},headers=self.headers)
            self.assertEqual(response.status,404,action)
        self.assertEqual(self.bot.store.get(self.foreign)['owner'],88)
        self.assertEqual(self.bot.store.get(self.foreign)['name'],'Compte privé guilde B')
        self.bot.try_publish.assert_not_awaited()

    async def test_configure_does_not_modify_other_server(self):
        response=await self.client.post('/api/settings',json={'channel_id':'22','role_id':'0','guild_id':99},headers=self.headers)
        self.assertEqual(response.status,200)
        self.assertEqual(self.bot.store.settings(99)['channel_id'],999)
        self.assertEqual(self.bot.store.settings(99)['role_id'],777)

    async def test_stale_tab_cannot_switch_guild_silently(self):
        self.headers['X-Guild-ID']='99'
        response=await self.client.post('/api/accounts',json={'action':'add','name':'Wrong server'},headers=self.headers)
        self.assertEqual(response.status,403)
        self.assertEqual(len(self.bot.store.all(11)),1)

    async def test_unconfigured_second_server_can_log_in(self):
        other=SimpleNamespace(**vars(self.guild));other.id=100;other.name='Serveur test'
        self.bot.get_guild=lambda i: other if i==100 else self.guild
        ticket=self.dashboard.issue(100,10).split('ticket=')[1]
        response=await self.client.post('/api/login',json={'ticket':ticket},headers={'Origin':self.dashboard.origin})
        self.assertEqual(response.status,200)
        state=await (await self.client.get('/api/state')).json()
        self.assertEqual(state['guild_id'],'100')
        self.assertEqual(state['channel_id'],'0')
        self.assertEqual(state['accounts'],[])

    async def test_store_scopes_all_mutations(self):
        self.assertFalse(self.bot.store.take(self.foreign,10,11))
        self.assertFalse(self.bot.store.release(self.foreign,10,11,manager=True))
        self.assertFalse(self.bot.store.rename(self.foreign,'No',11))
        self.assertFalse(self.bot.store.remove(self.foreign,11))

    async def test_slash_commands_and_buttons_reject_foreign_account(self):
        real=DropBot(0,0,0,self.bot.store)
        real.try_publish=AsyncMock()
        real.permitted=lambda interaction: True
        interaction=SimpleNamespace(guild_id=11,user=SimpleNamespace(id=10),message=SimpleNamespace(id=555),
            permissions=SimpleNamespace(manage_guild=True),response=SimpleNamespace(defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()))
        self.bot.store.message(self.foreign,555)
        commands=AccountCommands(real)
        await commands.renommer.callback(commands,interaction,self.foreign,'Intrusion')
        await commands.retirer.callback(commands,interaction,self.foreign,True)
        await real.change(interaction,False)
        real.try_publish.assert_not_awaited()
        self.assertEqual(self.bot.store.get(self.foreign)['owner'],88)
        view=ConfigurationView(real,10,11)
        interaction.guild_id=99
        interaction.response.send_message=AsyncMock()
        self.assertFalse(await view.interaction_check(interaction))
        view.stop()
        # Test owns the shared store and closes it in tearDown.
        await real.dashboard.close()
