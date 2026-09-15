from datetime import datetime as dt

from discord.ext import commands
from loguru import logger

from ogurec.utils import TIME_ZONE


class Utils(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="sync")
    async def sync(self, ctx: commands.Context):
        try:
            synced = await self.bot.tree.sync()
            names = ", ".join(f"/{cmd.name}" for cmd in synced) or "пусто"
            logger.info(f"Синхронизировано {len(synced)} команд: {names}")
            await ctx.send(f"Синхронизировано {len(synced)} команд.")
        except Exception:
            logger.exception("Не смог синхронизировать slash-команды")
            raise

    @commands.command(name="time")
    async def time(self, ctx: commands.Context):
        now = dt.now(tz=TIME_ZONE)
        week = now.isocalendar()[1]
        await ctx.send(now.strftime("%H:%M UTC %a") + f" week {week}")
