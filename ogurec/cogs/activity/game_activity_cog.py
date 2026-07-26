from datetime import datetime as dt

import discord
from discord.ext import commands, tasks
from loguru import logger

from ogurec.bot import OgurecBot
from ogurec.cogs.activity.game_activity_storage_cog import ActivityStorage
from ogurec.config.settings import Settings
from ogurec.utils import TIME_ZONE


class GameActivity(commands.Cog):
    def __init__(self, bot: OgurecBot, activity_storage: ActivityStorage, settings: Settings):
        self.bot = bot
        self.users_id = settings.users_discord_id
        self.active_sessions = {}
        self.activity_storage = activity_storage
        self.cleanup_task.start()

    def get_game(self, member: discord.Member) -> str | None:
        for activity in member.activities:
            if activity.type == discord.ActivityType.playing:
                return activity.name

    @commands.Cog.listener()
    async def on_presence_update(
        self,
        before: discord.Member,
        after: discord.Member,
    ):
        if after.id not in self.users_id:
            return

        old_game = self.get_game(before)
        new_game = self.get_game(after)

        if old_game == new_game:
            return

        # Пользователь вышел из игры
        if old_game is not None and new_game is None:
            session = self.active_sessions.pop(after.id, None)

            if session:
                duration = dt.now(TIME_ZONE) - session["started"]

                logger.info(f"{after} закончил играть в {session['game']} ({duration})")
                await self.activity_storage.end_game(user_id=after.id, ended_at=int(dt.now(TIME_ZONE).timestamp()))

        if after.id in self.active_sessions:
            return

        # Пользователь зашел в игру
        if new_game is not None:
            self.active_sessions[after.id] = {
                "game": new_game,
                "started": dt.now(TIME_ZONE),
            }

            logger.info(f"{after} начал играть в {new_game}")

            await self.activity_storage.start_game(
                user_id=after.id,
                game=new_game,
                started_at=int(dt.now(TIME_ZONE).timestamp()),
            )

    @tasks.loop(minutes=1)
    async def cleanup_task(self):
        now = dt.now(TIME_ZONE)

        if now.hour == 6 and now.minute == 5:
            try:
                await self.activity_storage.cleanup()
                logger.info("Activity cleanup completed")
            except Exception:
                logger.exception("Failed to cleanup activity storage")