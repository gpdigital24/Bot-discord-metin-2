import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
from aiohttp.test_utils import TestClient, TestServer
from dashboard import Dashboard
from store import Store


def fixture(path):
    member = SimpleNamespace(id=10, display_name='Gwend', guild_permissions=SimpleNamespace(manage_guild=True))
    channel = MagicMock(spec=discord.TextChannel)
    channel.id, channel.name, channel.mention = 22, 'comptes-drop', '<#22>'
    channel.permissions_for.return_value = discord.Permissions.all()
    channel.get_partial_message.return_value.delete = AsyncMock()
    guild = SimpleNamespace(id=11, name='La guilde', text_channels=[channel], roles=[], me=object(),
                            fetch_member=AsyncMock(return_value=member), get_member=lambda _: member,
                            get_channel=lambda i: channel if i == 22 else None, get_role=lambda _: None)
    bot = SimpleNamespace(store=Store(path), guild_id=11, channel_id=22, role_id=0,
                          lock=asyncio.Lock(), dirty=set(), is_ready=lambda: True,
                          get_guild=lambda i: guild if i == 11 else None,
                          try_publish=AsyncMock(return_value=True), channel=AsyncMock(return_value=channel),
                          notify_available=AsyncMock())
    bot.store.configure(11, 22, 0)
    return bot, guild, member


class DashboardTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.bot, self.guild, self.member = fixture(Path(self.tmp.name) / 'db.sqlite3')
        self.dashboard = Dashboard(self.bot, 'http://localhost:8080')
        self.client = TestClient(TestServer(self.dashboard.app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.bot.store.close()
        self.tmp.cleanup()

    async def login(self):
        ticket = self.dashboard.issue(11, 10).split('ticket=')[1]
        response = await self.client.post('/api/login', json={'ticket': ticket}, headers={'Origin':self.dashboard.origin})
        self.assertEqual(response.status, 200)
        self.assertTrue(response.cookies['drop_session']['httponly'])
        state = await (await self.client.get('/api/state')).json()
        return {'Origin':self.dashboard.origin, 'X-CSRF-Token':state['csrf']}, ticket

    async def test_anonymous_and_replayed_ticket_rejected(self):
        self.assertEqual((await self.client.get('/api/state')).status, 401)
        headers, ticket = await self.login()
        self.assertEqual((await self.client.post('/api/login', json={'ticket':ticket}, headers=headers)).status, 401)

    async def test_cross_origin_and_missing_csrf_rejected(self):
        headers, _ = await self.login()
        self.assertEqual((await self.client.post('/api/accounts',json={'action':'add','name':'Bad'},headers={'Origin':self.dashboard.origin})).status, 403)
        headers['Origin'] = 'https://evil.example'
        self.assertEqual((await self.client.post('/api/accounts',json={'action':'add','name':'Bad'},headers=headers)).status, 403)
        self.assertEqual(len(self.bot.store.all()), 0)

    async def test_permissions_rechecked_after_login(self):
        await self.login()
        self.member.guild_permissions.manage_guild = False
        self.assertEqual((await self.client.get('/api/state')).status, 403)

    async def test_expired_ticket_rejected(self):
        ticket = self.dashboard.issue(11, 10).split('ticket=')[1]
        self.dashboard.tickets[ticket]['expires'] = time.time() - 1
        self.assertEqual((await self.client.post('/api/login',json={'ticket':ticket},headers={'Origin':self.dashboard.origin})).status, 401)

    async def test_add_rename_release_remove_sync_discord(self):
        headers, _ = await self.login()
        async def action(data):
            return await self.client.post('/api/accounts',json=data,headers=headers)
        self.assertEqual((await action({'action':'add','name':'Drop 1'})).status, 200)
        account = self.bot.store.all()[0]
        self.assertEqual((await action({'action':'rename','id':account['id'],'name':'Forêt'})).status, 200)
        self.bot.store.take(account['id'],10,11)
        self.assertEqual((await action({'action':'remove','id':account['id']})).status, 409)
        self.assertEqual((await action({'action':'release','id':account['id']})).status, 200)
        self.bot.notify_available.assert_awaited_once()
        self.assertEqual((await action({'action':'remove','id':account['id']})).status, 200)
        self.assertEqual(len(self.bot.store.all()), 0)

    async def test_settings_persist_and_logout_invalidates_session(self):
        headers, _ = await self.login()
        response = await self.client.post('/api/settings',json={'channel_id':'22','role_id':'0'},headers=headers)
        self.assertEqual(response.status, 200)
        self.assertEqual(self.bot.store.settings(11)['guild_id'],11)
        self.assertEqual((await self.client.post('/api/logout',json={},headers=headers)).status, 200)
        self.assertEqual((await self.client.get('/api/state')).status, 401)

    async def test_no_private_data_on_landing_page(self):
        response = await self.client.get('/')
        self.assertEqual(response.status,200)
        self.assertIn("frame-ancestors 'none'",response.headers['Content-Security-Policy'])
        self.assertNotIn('Gwend',await response.text())

    async def test_multiple_roles_persist_validate_and_notify(self):
        from bot import DropBot
        roles = []
        for rid in (31, 32):
            role = MagicMock(spec=discord.Role)
            role.id, role.name, role.mention, role.mentionable = rid, str(rid), f'<@&{rid}>', True
            role.is_default.return_value = False
            roles.append(role)
        self.guild.roles = roles
        self.guild.get_role = lambda rid: next((r for r in roles if r.id == rid), None)
        channel = self.guild.text_channels[0]
        channel.guild = self.guild
        channel.send = AsyncMock()
        headers, _ = await self.login()
        response = await self.client.post('/api/settings', json={'channel_id':'22', 'role_ids':['31','32','31']}, headers=headers)
        self.assertEqual(response.status, 200)
        state = await (await self.client.get('/api/state')).json()
        self.assertEqual(state['role_ids'], ['31','32'])
        self.bot.store.close()
        self.bot.store = Store(Path(self.tmp.name) / 'db.sqlite3')
        self.assertEqual(self.bot.store.settings(11)['role_ids'], [31,32])
        await DropBot.notify_status(self.bot, {'guild_id':11,'name':'Drop'})
        args = channel.send.call_args
        self.assertIn('<@&31> <@&32>', args.args[0])
        self.assertEqual(args.kwargs['allowed_mentions'].roles, roles)
        self.assertFalse(args.kwargs['allowed_mentions'].everyone)
        await DropBot.notify_status(self.bot, {'guild_id':11,'name':'Drop'}, taken_by=10)
        args = channel.send.call_args
        self.assertIn('<@&31> <@&32>', args.args[0])
        self.assertIn('est pris par <@10>', args.args[0])
        self.assertEqual(args.kwargs['allowed_mentions'].roles, roles)
        self.assertFalse(args.kwargs['allowed_mentions'].users)

        for bad in (['31','999'], ['31'] * 26, '31'):
            response = await self.client.post('/api/settings', json={'channel_id':'22','role_ids':bad}, headers=headers)
            self.assertEqual(response.status, 400)
            self.assertEqual(self.bot.store.settings(11)['role_ids'], [31,32])
        response = await self.client.post('/api/settings', json={'channel_id':'22','role_ids':[]}, headers=headers)
        self.assertEqual(response.status, 200)
        self.bot.store.close()
        self.bot.store = Store(Path(self.tmp.name) / 'db.sqlite3')
        self.assertEqual(self.bot.store.settings(11)['role_ids'], [])

    async def test_old_single_role_settings_are_migrated(self):
        self.bot.store.db.execute('INSERT OR REPLACE INTO guild_settings VALUES (11,22,31)')
        self.bot.store.db.commit()
        self.bot.store.close()
        self.bot.store = Store(Path(self.tmp.name) / 'db.sqlite3')
        self.assertEqual(self.bot.store.settings(11)['role_ids'], [31])
