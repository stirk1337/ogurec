import inspect
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from discord import app_commands
from discord.ext import tasks

from ogurec.cogs.conversation_cog import ConversationCog
from ogurec.memory import UserMemory


class MemoryCommandTests(unittest.TestCase):
    def test_rebuild_memory_slash_is_not_the_nightly_loop(self):
        self.assertIsInstance(ConversationCog.rebuild_memory, app_commands.Command)
        self.assertEqual(ConversationCog.rebuild_memory.name, "rebuild_memory")
        self.assertIsInstance(ConversationCog.rebuild_memory_loop, tasks.Loop)

    def test_slash_rebuild_uses_all_accumulated_messages(self):
        source = inspect.getsource(ConversationCog.rebuild_memory.callback)
        self.assertIn("since_hours=None", source)


class RebuildAllTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.memory = UserMemory(path=str(Path(self.temp.name) / "memory.db"), gpt_client=object())
        await self.memory.init()
        now = int(time.time())
        for user_id, name, created_at in (
            (1, "today", now),
            (2, "hours_ago", now - 2 * 3600),
            (3, "two_days_ago", now - 48 * 3600),
        ):
            await self.memory.conn.execute(
                "INSERT INTO messages(user_id, name, channel_id, content, created_at) VALUES(?, ?, ?, ?, ?)",
                (user_id, name, 1, "привет", created_at),
            )
        await self.memory.conn.commit()
        self.seen: list[int] = []

        async def fake_rebuild(user_id, name):
            self.seen.append(user_id)

        self.memory.rebuild = fake_rebuild

    async def asyncTearDown(self):
        await self.memory.conn.close()
        self.temp.cleanup()

    async def test_nightly_rebuild_only_people_who_wrote_today(self):
        with patch("ogurec.memory.asyncio.sleep", new_callable=AsyncMock):
            await self.memory.rebuild_all()
        self.assertEqual(set(self.seen), {1, 2})

    async def test_manual_rebuild_includes_everyone_with_messages(self):
        with patch("ogurec.memory.asyncio.sleep", new_callable=AsyncMock):
            await self.memory.rebuild_all(since_hours=None)
        self.assertEqual(set(self.seen), {1, 2, 3})
