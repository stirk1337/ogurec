import unittest

from discord import app_commands
from discord.ext import tasks

from ogurec.cogs.conversation_cog import ConversationCog


class MemoryCommandTests(unittest.TestCase):
    def test_rebuild_memory_slash_is_not_the_nightly_loop(self):
        self.assertIsInstance(ConversationCog.rebuild_memory, app_commands.Command)
        self.assertEqual(ConversationCog.rebuild_memory.name, "rebuild_memory")
        self.assertIsInstance(ConversationCog.rebuild_memory_loop, tasks.Loop)
