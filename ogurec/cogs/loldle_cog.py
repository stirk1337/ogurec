import asyncio
import json

import discord
from discord import app_commands
from discord.ext import commands, tasks
from loguru import logger

from ogurec.activity.loldle_store import LoldleStore, format_day, loldle_day, previous_day, ru_days
from ogurec.activity.scoreboard import fetch_avatars, render_scoreboard
from ogurec.activity.server import ActivityServer
from ogurec.bot import OgurecBot

MODES = (
    ("classic", "Классика"),
    ("quote", "Цитата"),
    ("ability", "Умение"),
    ("emoji", "Эмодзи"),
    ("splash", "Сплеш"),
)
PLAY_ID = "loldle:play"
RECENT_WINDOW = 10


class LoldleView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Играть", style=discord.ButtonStyle.success, custom_id=PLAY_ID)
    async def play(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.launch_activity()
        cog = interaction.client.get_cog("Loldle")
        if isinstance(cog, Loldle):
            await cog.touch_session(interaction)


class Loldle(commands.Cog):
    def __init__(self, bot: OgurecBot, activity_server: ActivityServer):
        self.bot = bot
        self.activity_server = activity_server
        self.boards: dict[int, discord.Message] = {}
        self.instances: dict[str, int] = {}
        self.rosters: dict[int, dict[str, dict]] = {}
        self.starters: dict[int, set[int]] = {}
        self._last: dict[int, str] = {}
        self._locks: dict[int, asyncio.Lock] = {}
        self.store = LoldleStore()
        activity_server.on_progress = self.on_progress

    async def cog_load(self):
        self.bot.add_view(LoldleView())
        today = loldle_day()
        for channel_id, state in self.store.channels():
            if state.get("roster_day") != today:
                continue
            self.rosters[channel_id] = {
                str(player.get("id")): player for player in state.get("players") or [] if player.get("id")
            }
            self.starters[channel_id] = set(state.get("starters") or [])
        self.reset_loop.start()

    async def cog_unload(self):
        self.reset_loop.cancel()

    def _lock(self, channel_id: int) -> asyncio.Lock:
        return self._locks.setdefault(channel_id, asyncio.Lock())

    @app_commands.command(name="loldle", description="Запустить LoLdle на 5 режимов")
    async def loldle(self, interaction: discord.Interaction):
        if interaction.channel is None or not isinstance(interaction.channel, discord.abc.Messageable):
            await interaction.response.send_message("Команду нужно вызывать в текстовом канале.", ephemeral=True)
            return
        await interaction.response.launch_activity()
        await self.touch_session(interaction)

    async def touch_session(self, interaction: discord.Interaction):
        channel = self._channel(interaction)
        if channel is None:
            return
        self.starters.setdefault(channel.id, set()).add(interaction.user.id)
        await self._publish(channel, clicked=interaction.message if interaction.message else None)

    async def on_progress(self, instance_id: str, players: list[dict]):
        channel_id = next((int(player["channelId"]) for player in players if player.get("channelId")), None)
        if channel_id is None:
            channel_id = self.instances.get(instance_id)
        if channel_id is None:
            return
        self.instances[instance_id] = channel_id
        roster = self.rosters.setdefault(channel_id, {})
        for player in players:
            user_id = str(player.get("id") or "")
            if user_id:
                roster[user_id] = player
                try:
                    self.starters.setdefault(channel_id, set()).add(int(user_id))
                except ValueError:
                    pass
        channel = self.bot.get_channel(channel_id)
        if not isinstance(channel, discord.abc.Messageable):
            return
        await self._publish(channel)

    @tasks.loop(seconds=30)
    async def reset_loop(self):
        today = loldle_day()
        for channel_id, state in self.store.channels():
            if state.get("last_reset_day") == today:
                continue
            try:
                await self._roll_day(channel_id)
            except Exception:
                logger.exception("Failed to reset LoLdle day")

    @reset_loop.before_loop
    async def before_reset_loop(self):
        await self.bot.wait_until_ready()

    async def _roll_day(self, channel_id: int):
        channel = self.bot.get_channel(channel_id)
        if not isinstance(channel, discord.abc.Messageable):
            return
        async with self._lock(channel_id):
            yesterday = previous_day(loldle_day())
            state, recap_players, recap_starters = self.store.close_day(channel_id)
            self.rosters[channel_id] = {}
            self.starters[channel_id] = set()
            self.boards.pop(channel_id, None)
            self._last.pop(channel_id, None)
            streak = int(state.get("streak") or 0)
            played = bool(recap_players or recap_starters)
            if played:
                recap, recap_embed, recap_file = await self._card(
                    recap_players,
                    recap_starters,
                    streak=streak,
                    title=f"Итоги · {format_day(yesterday)}",
                    headline=f"Итоги дня · {format_day(yesterday)}",
                    recap=True,
                )
                await channel.send(
                    content=recap,
                    embed=recap_embed,
                    file=recap_file,
                    allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=True),
                )
            fresh, fresh_embed, fresh_file = await self._card(
                [],
                set(),
                streak=streak,
                title=f"LoLdle · {format_day(loldle_day())}",
                headline="Новый день LoLdle",
            )
            message = await channel.send(
                content=fresh,
                embed=fresh_embed,
                file=fresh_file,
                view=LoldleView(),
                allowed_mentions=discord.AllowedMentions.none(),
            )
            self.boards[channel_id] = message

    async def _publish(self, channel: discord.abc.Messageable, clicked: discord.Message | None = None):
        channel_id = channel.id
        async with self._lock(channel_id):
            players = list(self.rosters.get(channel_id, {}).values())
            starters = self.starters.get(channel_id, set())
            self.store.remember(channel_id, players, starters)
            if any(self._done(player) for player in players):
                self.store.mark_played(channel_id)
            streak = int(self.store.channel(channel_id).get("streak") or 0)
            fingerprint = json.dumps(
                {
                    "streak": streak,
                    "starters": sorted(starters),
                    "players": [(player.get("id"), player.get("progress")) for player in players],
                },
                sort_keys=True,
                default=str,
            )
            message = await self._recent_board(channel, clicked)
            if message is not None and self._last.get(channel_id) == fingerprint:
                return
            content, embed, file = await self._card(
                players,
                starters,
                streak=streak,
                title=f"LoLdle · {format_day(loldle_day())}",
            )
            try:
                if message is None:
                    message = await channel.send(content=content, embed=embed, file=file, view=LoldleView())
                else:
                    await message.edit(content=content, embed=embed, attachments=[file], view=LoldleView())
            except discord.HTTPException:
                logger.exception("Failed to post LoLdle session")
                return
            self.boards[channel_id] = message
            self._last[channel_id] = fingerprint

    def _channel(self, interaction: discord.Interaction) -> discord.abc.Messageable | None:
        channel = interaction.channel
        if isinstance(channel, discord.abc.Messageable):
            return channel
        if interaction.channel_id is None:
            return None
        found = self.bot.get_channel(interaction.channel_id)
        return found if isinstance(found, discord.abc.Messageable) else None

    async def _recent_board(
        self,
        channel: discord.abc.Messageable,
        clicked: discord.Message | None = None,
    ) -> discord.Message | None:
        recent = await self._recent_messages(channel)
        recent_ids = {message.id for message in recent}
        if clicked is not None and self._is_board(clicked) and clicked.id in recent_ids:
            return clicked
        cached = self.boards.get(channel.id)
        if cached is not None and cached.id in recent_ids:
            return cached
        for message in recent:
            if self._is_board(message):
                return message
        return None

    async def _recent_messages(self, channel: discord.abc.Messageable) -> list[discord.Message]:
        history = getattr(channel, "history", None)
        if history is None:
            return []
        try:
            return [message async for message in history(limit=RECENT_WINDOW)]
        except discord.HTTPException:
            logger.exception("Failed to read recent LoLdle messages")
            return []

    def _is_board(self, message: discord.Message) -> bool:
        if self.bot.user is None or message.author.id != self.bot.user.id:
            return False
        for row in message.components:
            for child in getattr(row, "children", []):
                if getattr(child, "custom_id", None) == PLAY_ID:
                    return True
        return False

    async def _card(
        self,
        players: list[dict],
        starters: set[int],
        *,
        streak: int,
        title: str,
        headline: str | None = None,
        recap: bool = False,
    ) -> tuple[str, discord.Embed, discord.File]:
        ranked = sorted(players, key=lambda item: (-self._done(item), item.get("name") or ""))
        avatars = await fetch_avatars(self.activity_server.session, ranked)
        image = render_scoreboard(ranked, avatars, title=title, streak=streak)
        file = discord.File(image, filename="loldle.png")
        embed = discord.Embed(color=0xC8AA6E)
        embed.set_image(url="attachment://loldle.png")
        return self._caption(ranked, starters, streak, headline, recap), embed, file

    def _caption(
        self,
        players: list[dict],
        starters: set[int],
        streak: int,
        headline: str | None = None,
        recap: bool = False,
    ) -> str:
        mentions: list[str] = []
        seen: set[int] = set()
        for user_id in starters:
            mentions.append(f"<@{user_id}>")
            seen.add(user_id)
        for player in players:
            raw = player.get("id")
            try:
                user_id = int(raw)
            except (TypeError, ValueError):
                continue
            if user_id in seen:
                continue
            mentions.append(f"<@{user_id}>")
            seen.add(user_id)
        lines = [headline or self._playing_line(mentions)]
        if recap and mentions:
            lines.append(" ".join(mentions))
        if recap and streak <= 0:
            lines.append("Стрик сброшен — за день никто не угадал ни один режим")
        elif streak > 0:
            lines.append(f"🔥 Стрик сервера: {ru_days(streak)}")
        else:
            lines.append("🔥 Стрик сервера: 0")
        if not players:
            return "\n".join(lines)
        best = max((self._done(player) for player in players), default=0)
        for player in players:
            done = self._done(player)
            mention = f"<@{player['id']}>" if player.get("id") else player.get("name") or "Игрок"
            crown = "👑 " if done == best and done else ""
            lines.append(f"{crown}**{done}/5** — {mention}")
        return "\n".join(lines)

    def _playing_line(self, mentions: list[str]) -> str:
        if not mentions:
            return "Кто-то играет в LoLdle"
        if len(mentions) == 1:
            return f"{mentions[0]} играет в LoLdle"
        if len(mentions) == 2:
            return f"{mentions[0]} и {mentions[1]} играют в LoLdle"
        return f"{', '.join(mentions[:-1])} и {mentions[-1]} играют в LoLdle"

    def _done(self, player: dict) -> int:
        progress = player.get("progress") or {}
        return sum(1 for mode, _ in MODES if progress.get(mode, {}).get("done"))
