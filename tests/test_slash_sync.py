import unittest
from unittest.mock import AsyncMock, MagicMock

from ogurec.slash_sync import ACTIVITY_ENTRY_POINT, sync_slash_commands, with_activity_entry_point


class EntryPointPayloadTests(unittest.TestCase):
    def test_keeps_activity_entry_point_from_remote(self):
        payload = [{"name": "loldle", "type": 1, "description": "today"}]
        remote = [
            {"id": "1", "name": "loldle", "type": 1, "description": "today"},
            {"id": "99", "name": "launch", "type": ACTIVITY_ENTRY_POINT, "description": "", "handler": 2},
        ]

        merged = with_activity_entry_point(payload, remote)

        entry = next(command for command in merged if command["type"] == ACTIVITY_ENTRY_POINT)
        self.assertEqual(entry["id"], "99")
        self.assertEqual(entry["name"], "launch")
        self.assertEqual(entry["handler"], 2)

    def test_does_not_duplicate_entry_point_already_in_payload(self):
        payload = [{"id": "99", "name": "launch", "type": ACTIVITY_ENTRY_POINT, "description": ""}]
        remote = [{"id": "99", "name": "launch", "type": ACTIVITY_ENTRY_POINT, "description": ""}]

        merged = with_activity_entry_point(payload, remote)

        self.assertEqual(sum(command["type"] == ACTIVITY_ENTRY_POINT for command in merged), 1)

    def test_unchanged_when_discord_has_no_entry_point(self):
        payload = [{"name": "hello", "type": 1, "description": "hi"}]
        self.assertEqual(with_activity_entry_point(payload, [{"name": "hello", "type": 1}]), payload)


class SyncSlashCommandsTests(unittest.IsolatedAsyncioTestCase):
    async def test_upsert_payload_includes_entry_point(self):
        local = MagicMock()
        local.to_dict.return_value = {"name": "loldle", "type": 1, "description": "today"}
        bot = MagicMock()
        bot.application_id = 123
        bot.tree._get_all_commands.return_value = [local]
        bot.tree._state = MagicMock()
        bot.http.get_global_commands = AsyncMock(
            return_value=[{"id": "99", "name": "launch", "type": 4, "description": "", "handler": 2}]
        )
        bot.http.bulk_upsert_global_commands = AsyncMock(
            return_value=[{"id": "1", "name": "loldle", "type": 1, "description": "today", "application_id": "123", "version": "1"}]
        )

        await sync_slash_commands(bot)

        payload = bot.http.bulk_upsert_global_commands.await_args.kwargs["payload"]
        self.assertTrue(any(command.get("type") == 4 for command in payload))
        self.assertTrue(any(command.get("name") == "loldle" for command in payload))
