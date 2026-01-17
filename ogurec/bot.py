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

    async def on_ready(self):
        logger.info(f"We have logged in as {self.user}")
