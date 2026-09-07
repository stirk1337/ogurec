import asyncio
import json
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks
from loguru import logger

from ogurec.activity.loldle_store import (
    LoldleStore,
    format_day,
    loldle_day,
    merge_player,
    previous_day,
    resolve_channel_id,
    ru_days,
    session_is_live,
)
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
RECENT_WINDOW = 5
SESSION_GRACE = 120


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
        self.session_players: dict[int, dict[str, dict]] = {}
        self.session_starters: dict[int, set[int]] = {}
        self.session_open: dict[int, bool] = {}
        self.session_seen: dict[int, float] = {}
        self.user_channels: dict[str, int] = {}
        self._last: dict[int, str] = {}
        self._locks: dict[int, asyncio.Lock] = {}
        self._board_tasks: dict[int, asyncio.Task] = {}
        self.store = LoldleStore()
        activity_server.on_progress = self.on_progress
        activity_server.on_idle = self.on_idle

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
        for task in self._board_tasks.values():
            task.cancel()

    def _lock(self, channel_id: int) -> asyncio.Lock:
        return self._locks.setdefault(channel_id, asyncio.Lock())

    @app_commands.command(name="loldle", description="Показать сегодняшние результаты LoLdle")
    async def loldle(self, interaction: discord.Interaction):
        channel = self._channel(interaction)
        if channel is None:
            await interaction.response.send_message("Команду нужно вызывать в текстовом канале.", ephemeral=True)
            return
        await interaction.response.defer()
        await self.show_today(channel, interaction)

    async def touch_session(self, interaction: discord.Interaction):
        channel = self._channel(interaction)
        if channel is None:
            return
        if self._finished_today(channel.id, interaction.user.id):
            return
        if not self._session_live(channel.id):
            self._begin_session(channel.id)
        self._touch(channel.id)
        self.user_channels[str(interaction.user.id)] = channel.id
        self.session_starters.setdefault(channel.id, set()).add(interaction.user.id)
        self.starters.setdefault(channel.id, set()).add(interaction.user.id)
        await self._publish(channel, clicked=interaction.message if interaction.message else None)

    async def show_today(self, channel: discord.abc.Messageable, interaction: discord.Interaction) -> None:
        channel_id = channel.id
        try:
            players = self._today_list(channel_id)
            starters = {int(player["id"]) for player in players if str(player.get("id") or "").isdigit()}
            streak = int(self.store.channel(channel_id).get("streak") or 0)
            content, embed, file = await self._card(
                players,
                starters,
                streak=streak,
                title=f"LoLdle · {format_day(loldle_day())}",
                headline=f"LoLdle · {format_day(loldle_day())}",
                recap=True,
                ping=False,
                status=True,
            )
            mentions = discord.AllowedMentions.none()
            recent = await self._recent_messages(channel)
            existing = next((message for message in recent if self._is_board(message)), None)
            if existing is not None:
                await existing.edit(
                    content=content,
                    embed=embed,
                    attachments=[file],
                    view=LoldleView(),
                    allowed_mentions=mentions,
                )
                self.boards[channel_id] = existing
                await interaction.delete_original_response()
                return
            message = await interaction.edit_original_response(
                content=content,
                embed=embed,
                attachments=[file],
                view=LoldleView(),
                allowed_mentions=mentions,
            )
            self.boards[channel_id] = message
        except discord.HTTPException:
            logger.exception("Failed to post LoLdle status")
            await interaction.edit_original_response(content="Не получилось показать LoLdle.")

    async def on_progress(self, instance_id: str, player: dict) -> dict:
        user_id = str(player.get("id") or "")
        if not user_id:
            return player
        channel_id = resolve_channel_id(player, instance_id, self.instances, self.user_channels)
        if channel_id is None:
            return player
        self.instances[instance_id] = channel_id
        self.user_channels[user_id] = channel_id
        roster = self.rosters.setdefault(channel_id, {})
        session = self.session_players.setdefault(channel_id, {})
        already = user_id in session
        player = merge_player(roster.get(user_id), player)
        player = merge_player(session.get(user_id), player)
        roster[user_id] = player
        try:
            self.starters.setdefault(channel_id, set()).add(int(user_id))
        except ValueError:
            pass
        self.store.remember(channel_id, list(roster.values()), self.starters.get(channel_id, set()))
        if self._done(player) >= 5 and not already:
            return player
        if not self._session_live(channel_id):
            self._begin_session(channel_id)
            session = self.session_players.setdefault(channel_id, {})
        self._touch(channel_id)
        session[user_id] = player
        try:
            self.session_starters.setdefault(channel_id, set()).add(int(user_id))
        except ValueError:
            pass
        channel = self.bot.get_channel(channel_id)
        if isinstance(channel, discord.abc.Messageable):
            self._schedule_publish(channel)
        return player

    async def on_idle(self, _instance_id: str):
        return

    def _schedule_publish(self, channel: discord.abc.Messageable) -> None:
        channel_id = channel.id
        pending = self._board_tasks.get(channel_id)
        if pending is not None and not pending.done():
            pending.cancel()

        async def flush():
            try:
                await asyncio.sleep(1.2)
                await self._publish(channel)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Failed to update LoLdle session card")

        self._board_tasks[channel_id] = asyncio.create_task(flush())

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
            self.session_players[channel_id] = {}
            self.session_starters[channel_id] = set()
            self.session_open[channel_id] = False
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
                    ping=False,
                )
                await channel.send(
                    content=recap,
                    embed=recap_embed,
                    file=recap_file,
                    allowed_mentions=discord.AllowedMentions.none(),
                )

    async def _publish(self, channel: discord.abc.Messageable, clicked: discord.Message | None = None):
        channel_id = channel.id
        async with self._lock(channel_id):
            players = self._session_list(channel_id)
            starters = self.session_starters.get(channel_id, set())
            self.store.remember(
                channel_id,
                list(self.rosters.get(channel_id, {}).values()),
                self.starters.get(channel_id, set()),
            )
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
                mentions = discord.AllowedMentions.none()
                if message is None:
                    message = await asyncio.wait_for(
                        channel.send(
                            content=content,
                            embed=embed,
                            file=file,
                            view=LoldleView(),
                            allowed_mentions=mentions,
                        ),
                        timeout=12,
                    )
                else:
                    await asyncio.wait_for(
                        message.edit(
                            content=content,
                            embed=embed,
                            attachments=[file],
                            view=LoldleView(),
                            allowed_mentions=mentions,
                        ),
                        timeout=12,
                    )
            except (discord.HTTPException, TimeoutError, asyncio.TimeoutError):
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

    def _touch(self, channel_id: int) -> None:
        self.session_open[channel_id] = True
        self.session_seen[channel_id] = time.monotonic()

    def _begin_session(self, channel_id: int) -> None:
        self.session_players[channel_id] = {}
        self.session_starters[channel_id] = set()
        self._touch(channel_id)
        self.boards.pop(channel_id, None)
        self._last.pop(channel_id, None)

    def _has_sockets(self, channel_id: int) -> bool:
        return any(
            self.activity_server.rooms.get(instance_id)
            for instance_id, mapped in self.instances.items()
            if mapped == channel_id
        )

    def _session_live(self, channel_id: int) -> bool:
        return session_is_live(
            has_sockets=self._has_sockets(channel_id),
            opened=self.session_open.get(channel_id, False),
            seen_at=self.session_seen.get(channel_id, 0),
            now=time.monotonic(),
            grace=SESSION_GRACE,
        )

    def _today_list(self, channel_id: int) -> list[dict]:
        people = dict(self.rosters.get(channel_id) or {})
        if self._session_live(channel_id):
            for player in self._session_list(channel_id):
                user_id = str(player.get("id") or "")
                if not user_id:
                    continue
                people[user_id] = merge_player(people.get(user_id), player)
        return list(people.values())

    def _session_list(self, channel_id: int) -> list[dict]:
        people = dict(self.session_players.get(channel_id) or {})
        for user_id in self.session_starters.get(channel_id, set()):
            key = str(user_id)
            if key in people:
                continue
            user = self.bot.get_user(user_id)
            avatar = None
            if user is not None and user.avatar is not None:
                avatar = user.avatar.key
            people[key] = {
                "id": key,
                "name": (user.global_name or user.display_name or user.name) if user else "Игрок",
                "avatar": avatar,
                "progress": {},
            }
        return list(people.values())

    async def _recent_board(
        self,
        channel: discord.abc.Messageable,
        clicked: discord.Message | None = None,
    ) -> discord.Message | None:
        if not self._session_live(channel.id):
            return None
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
        ping: bool | None = None,
        status: bool = False,
    ) -> tuple[str, discord.Embed, discord.File]:
        ranked = sorted(players, key=lambda item: (-self._done(item), item.get("name") or ""))
        avatars = await fetch_avatars(self.activity_server.session, ranked)
        image = render_scoreboard(ranked, avatars, title=title, streak=streak)
        file = discord.File(image, filename="loldle.png")
        embed = discord.Embed(color=0xC8AA6E)
        embed.set_image(url="attachment://loldle.png")
        return self._caption(
            ranked,
            starters,
            streak,
            headline,
            recap=recap,
            ping=ping,
            status=status,
        ), embed, file

    def _caption(
        self,
        players: list[dict],
        starters: set[int],
        streak: int,
        headline: str | None = None,
        recap: bool = False,
        ping: bool | None = None,
        status: bool = False,
    ) -> str:
        names: list[str] = []
        seen: set[int] = set()
        by_id: dict[int, dict] = {}
        for player in players:
            try:
                user_id = int(player.get("id"))
            except (TypeError, ValueError):
                continue
            by_id[user_id] = player
        mention = recap if ping is None else ping
        for user_id in starters:
            names.append(self._player_name(by_id.get(user_id), user_id, ping=mention))
            seen.add(user_id)
        for player in players:
            try:
                user_id = int(player.get("id"))
            except (TypeError, ValueError):
                continue
            if user_id in seen:
                continue
            names.append(self._player_name(player, user_id, ping=mention))
            seen.add(user_id)
        if status:
            lines = [headline or f"LoLdle · {format_day(loldle_day())}"]
            if not players:
                lines.append("Сегодня ещё никто не играл — нажми Играть")
            if streak > 0:
                lines.append(f"🔥 Стрик сервера: {ru_days(streak)}")
            best = max((self._done(player) for player in players), default=0)
            for player in players:
                done = self._done(player)
                try:
                    user_id = int(player.get("id"))
                except (TypeError, ValueError):
                    user_id = None
                label = self._player_name(player, user_id, ping=False)
                crown = "👑 " if done == best and done else ""
                lines.append(f"{crown}**{done}/5** — {label}")
            return "\n".join(lines)
        lines = [headline or self._playing_line(names)]
        if not recap:
            return lines[0]
        if names:
            lines.append(" ".join(names))
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
            try:
                user_id = int(player.get("id"))
            except (TypeError, ValueError):
                user_id = None
            label = self._player_name(player, user_id, ping=mention)
            crown = "👑 " if done == best and done else ""
            lines.append(f"{crown}**{done}/5** — {label}")
        return "\n".join(lines)

    def _player_name(self, player: dict | None, user_id: int | None, *, ping: bool) -> str:
        if ping and user_id is not None:
            return f"<@{user_id}>"
        if player and player.get("name"):
            return str(player["name"])
        if user_id is not None:
            user = self.bot.get_user(user_id)
            if user is not None:
                return user.global_name or user.display_name or user.name
        return "Игрок"

    def _playing_line(self, names: list[str]) -> str:
        if not names:
            return "Кто-то играет в LoLdle"
        if len(names) == 1:
            return f"{names[0]} играет в LoLdle"
        if len(names) == 2:
            return f"{names[0]} и {names[1]} играют в LoLdle"
        return f"{', '.join(names[:-1])} и {names[-1]} играют в LoLdle"

    def _finished_today(self, channel_id: int, user_id: int) -> bool:
        player = self.rosters.get(channel_id, {}).get(str(user_id))
        return bool(player) and self._done(player) >= 5

    def _done(self, player: dict) -> int:
        progress = player.get("progress") or {}
        return sum(1 for mode, _ in MODES if progress.get(mode, {}).get("done"))
