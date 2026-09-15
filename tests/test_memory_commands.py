import inspect
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from discord import app_commands
from discord.ext import tasks

from ogurec.cogs.conversation_cog import ConversationCog
from ogurec.memory import UserMemory


def fake_message(discord_id, user_id, name, content, *, bot=False, hours_ago=0):
    return SimpleNamespace(
        id=discord_id,
        author=SimpleNamespace(id=user_id, name=name, bot=bot),
        content=content,
        channel=SimpleNamespace(id=1),
        created_at=datetime.now(timezone.utc) - timedelta(hours=hours_ago),
    )


class MemoryCommandTests(unittest.TestCase):
    def test_rebuild_memory_slash_is_not_the_nightly_loop(self):
        self.assertIsInstance(ConversationCog.rebuild_memory, app_commands.Command)
        self.assertEqual(ConversationCog.rebuild_memory.name, "rebuild_memory")
        self.assertIsInstance(ConversationCog.rebuild_memory_loop, tasks.Loop)

    def test_slash_rebuild_pulls_discord_history_for_last_day(self):
        source = inspect.getsource(ConversationCog.rebuild_memory.callback)
        self.assertIn("_pull_recent_messages", source)
        self.assertIn("since_hours=24", source)


class RebuildAllTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.memory = UserMemory(path=str(Path(self.temp.name) / "memory.db"), gpt_client=object())
        await self.memory.init()
        self.seen: list[int] = []

        async def fake_rebuild(user_id, name):
            self.seen.append(user_id)

        self.memory.rebuild = fake_rebuild

    async def asyncTearDown(self):
        await self.memory.conn.close()
        self.temp.cleanup()

    async def test_nightly_rebuild_only_people_who_wrote_today(self):
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
        with patch("ogurec.memory.asyncio.sleep", new_callable=AsyncMock):
            await self.memory.rebuild_all()
        self.assertEqual(set(self.seen), {1, 2})

    async def test_ingest_history_rebuilds_everyone_who_wrote_not_just_live_index(self):
        messages = [
            fake_message(10, 1, "a", "привет"),
            fake_message(11, 2, "b", "как дела"),
            fake_message(12, 3, "c", "го в доту"),
            fake_message(13, 4, "d", "я позже"),
            fake_message(14, 5, "e", "ок"),
            fake_message(15, 9, "bot", "beep", bot=True),
            fake_message(16, 8, "empty", "   "),
        ]
        added = await self.memory.ingest_messages(messages)
        self.assertEqual(added, 5)
        self.assertEqual(await self.memory.ingest_messages(messages), 0)
        with patch("ogurec.memory.asyncio.sleep", new_callable=AsyncMock):
            await self.memory.rebuild_all(since_hours=24)
        self.assertEqual(set(self.seen), {1, 2, 3, 4, 5})
