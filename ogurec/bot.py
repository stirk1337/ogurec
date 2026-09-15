import discord
from discord.ext import commands
from loguru import logger

from ogurec.config.settings import Settings


class OgurecBot(commands.Bot):
    def __init__(self, settings: Settings):
        self.settings = settings

        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        intents.presences = True

        super().__init__(
            command_prefix=settings.prefix,
            intents=intents,
        )

    async def setup_hook(self):
        try:
            synced = await self.tree.sync()
            names = ", ".join(f"/{cmd.name}" for cmd in synced) or "пусто"
            logger.info(f"Синхронизировано {len(synced)} команд: {names}")
        except Exception:
            logger.exception("Не смог синхронизировать slash-команды")

    async def on_ready(self):
        logger.info(f"We have logged in as {self.user}")
