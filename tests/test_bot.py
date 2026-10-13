import asyncio
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
from bot import AccountCommands, AccountView, DropBot, panel
from store import Store


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "accounts.sqlite3"
        self.store = Store(self.path)
        self.account = self.store.add("Drop 1")

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def test_only_one_concurrent_claim_succeeds(self):
        def claim(user):
            other = Store(self.path)
            try:
                return other.take(self.account, user)
            finally:
                other.close()
        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(sum(pool.map(claim, [10, 20, 30, 40])), 1)

    def test_release_requires_owner_or_manager(self):
        self.assertTrue(self.store.take(self.account, 10))
        self.assertFalse(self.store.release(self.account, 20))
        self.assertEqual(self.store.get(self.account)["owner"], 10)
        self.assertTrue(self.store.release(self.account, 20, manager=True))
        self.assertIsNone(self.store.get(self.account)["since"])
        self.assertTrue(self.store.take(self.account, 30))
        self.assertTrue(self.store.release(self.account, 30))

    def test_persists_on_restart_and_accounts_are_independent(self):
        second = self.store.add("Drop 2")
        self.store.take(self.account, 10)
        self.store.message(self.account, 123456)
        self.store.close()
        self.store = Store(self.path)
        self.assertEqual(self.store.by_message(123456)["owner"], 10)
        self.assertIsNone(self.store.get(second)["owner"])
        self.assertIn("<@10>", panel(self.store.get(self.account)).description)

    def test_duplicate_name_and_deleted_account(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.add("drop 1")
        self.store.remove(self.account)
        self.assertFalse(self.store.take(self.account, 10))


class InteractionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.directory.name) / "db.sqlite3")
        self.account = self.store.add("Drop 1")
        self.store.message(self.account, 123)
        self.bot = DropBot(1, 2, 0, self.store)

    async def asyncTearDown(self):
        await self.bot.close()
        self.directory.cleanup()

    def interaction(self, user=10, manager=False):
        return SimpleNamespace(
            message=SimpleNamespace(id=123), user=SimpleNamespace(id=user),
            permissions=SimpleNamespace(manage_guild=manager),
            response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )

    async def test_persistent_views_and_command_registration(self):
        self.assertTrue(AccountView(self.bot).is_persistent())
        self.assertTrue(AccountView(self.bot, occupied=True).take.disabled)
        self.assertTrue(AccountView(self.bot, occupied=False).release.disabled)
        group = AccountCommands(self.bot)
        self.assertEqual({c.name for c in group.commands}, {"ajouter", "retirer", "renommer", "actualiser"})

    async def test_wrong_channel_rejected_without_mutation(self):
        self.bot.permitted = lambda interaction: False
        interaction = self.interaction()
        await self.bot.change(interaction, True)
        interaction.response.send_message.assert_awaited_once()
        self.assertIsNone(self.store.get(self.account)["owner"])

    async def test_callbacks_serialize_and_cannot_release_other_member(self):
        self.bot.permitted = lambda interaction: True
        self.bot.try_publish = AsyncMock(return_value=True)
        await asyncio.gather(self.bot.change(self.interaction(10), True), self.bot.change(self.interaction(20), True))
        self.assertEqual(self.store.get(self.account)["owner"], 10)
        await self.bot.change(self.interaction(20), False)
        self.assertEqual(self.store.get(self.account)["owner"], 10)
        await self.bot.change(self.interaction(20, manager=True), False)
        self.assertIsNone(self.store.get(self.account)["owner"])

    async def test_publish_failure_preserves_reservation_and_queues_retry(self):
        self.bot.permitted = lambda interaction: True
        self.bot.publish = AsyncMock(side_effect=RuntimeError("salon inaccessible"))
        with self.assertLogs("drop", level="ERROR"):
            await self.bot.change(self.interaction(), True)
        self.assertEqual(self.store.get(self.account)["owner"], 10)
        self.assertIn(self.account, self.bot.dirty)

    async def test_missing_panel_is_recreated(self):
        response = SimpleNamespace(status=404, reason="Not Found")
        partial = SimpleNamespace(edit=AsyncMock(side_effect=discord.NotFound(response, "missing")))
        channel = SimpleNamespace(get_partial_message=lambda _: partial, send=AsyncMock(return_value=SimpleNamespace(id=456)))
        self.bot.channel = AsyncMock(return_value=channel)
        await self.bot.publish(self.account)
        self.assertEqual(self.store.get(self.account)["message_id"], 456)
        self.assertNotIn(self.account, self.bot.dirty)


if __name__ == "__main__":
    unittest.main()
