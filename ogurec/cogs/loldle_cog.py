import asyncio
import json
import re
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks
from loguru import logger

from ogurec.activity.loldle_store import (
    PLAY_ID,
    LoldleStore,
    coerce_channel_id,
    format_day,
    iter_custom_ids,
    loldle_day,
    message_id,
    parse_instance_channel,
    play_custom_id,
    play_id_day,
    player_wins,
    resolve_channel_id,
    ru_days,
    session_is_live,
)
from ogurec.activity.scoreboard import fetch_avatars, render_scoreboard
from ogurec.activity.server import ActivityServer
from ogurec.bot import OgurecBot

SESSION_GRACE = 120


async def handle_play(interaction: discord.Interaction) -> None:
    cog = interaction.client.get_cog("Loldle")
    if not isinstance(cog, Loldle):
        if not interaction.response.is_done():
            await interaction.response.launch_activity()
        return
    cog.bind_user_channel(interaction)
    if not interaction.response.is_done():
        await interaction.response.launch_activity()
    await cog.touch_session(interaction)


class PlayButton(discord.ui.DynamicItem[discord.ui.Button], template=r"loldle:play:(?P<day>\d{4}-\d{2}-\d{2})"):
    def __init__(self, day: str) -> None:
        super().__init__(
            discord.ui.Button(
                label="Играть",
                style=discord.ButtonStyle.success,
                custom_id=play_custom_id(day),
            )
        )
        self.day = day

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Button,
        match: re.Match[str],
        /,
    ):
        del interaction, item
        return cls(match["day"])

    async def callback(self, interaction: discord.Interaction) -> None:
        await handle_play(interaction)


class LoldleView(discord.ui.View):
    def __init__(self, day: str | None = None):
        super().__init__(timeout=None)
        self.day = day or loldle_day()
        self.add_item(PlayButton(self.day))


class LegacyLoldleView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Играть", style=discord.ButtonStyle.success, custom_id=PLAY_ID)
    async def play(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await handle_play(interaction)


class Loldle(commands.Cog):
    def __init__(self, bot: OgurecBot, activity_server: ActivityServer):
        self.bot = bot
        self.activity_server = activity_server
        self.boards: dict[int, discord.Message] = {}
        self.instances: dict[str, int] = {}
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
        activity_server.on_reset = self.on_reset
        activity_server.on_idle = self.on_idle

    async def cog_load(self):
        self.bot.add_dynamic_items(PlayButton)
        self.bot.add_view(LegacyLoldleView())
        self.reset_loop.start()

    async def cog_unload(self):
        self.reset_loop.cancel()
        for task in self._board_tasks.values():
            task.cancel()
        self.bot.remove_dynamic_items(PlayButton)

    def _lock(self, channel_id: int) -> asyncio.Lock:
        return self._locks.setdefault(channel_id, asyncio.Lock())

    @app_commands.command(name="loldle", description="Показать сегодняшние результаты LoLdle")
    async def loldle(self, interaction: discord.Interaction):
        channel = self._channel(interaction)
        if channel is None:
            await interaction.response.send_message("Команду нужно вызывать в текстовом канале.", ephemeral=True)
            return
        await interaction.response.defer()
        try:
            await self.show_today(channel, interaction)
        except Exception:
            logger.exception("Failed to post LoLdle status")
            try:
                await interaction.followup.send("Не получилось показать LoLdle.", ephemeral=True)
            except discord.HTTPException:
                logger.exception("Failed to report LoLdle error")

    def bind_user_channel(self, interaction: discord.Interaction) -> discord.abc.Messageable | None:
        channel = self._channel(interaction)
        if channel is None:
            return None
        self.user_channels[str(interaction.user.id)] = channel.id
        self.store.remember_user_channel(str(interaction.user.id), channel.id)
        return channel

    async def touch_session(self, interaction: discord.Interaction):
        channel = self.bind_user_channel(interaction)
        if channel is None:
            return
        self.store.add_starter(channel.id, interaction.user.id)
        if not self._session_live(channel.id):
            self._begin_session(channel.id)
        self._touch(channel.id)
        self.session_starters.setdefault(channel.id, set()).add(interaction.user.id)
        await self._publish(channel)

    async def show_today(self, channel: discord.abc.Messageable, interaction: discord.Interaction) -> None:
        channel_id = channel.id
        today = loldle_day()
        players, starters = self._people_for_board(channel_id)
        streak = int(self.store.channel(channel_id).get("streak") or 0)
        content, file = await self._card(
            players,
            starters,
            streak=streak,
            title=f"LoLdle · {format_day(today)}",
            headline=f"LoLdle · {format_day(today)}",
            recap=True,
            ping=False,
            status=True,
        )
        mentions = discord.AllowedMentions.none()
        existing = await self._today_board(channel)
        if existing is not None:
            try:
                message = await self._edit_board(
                    existing, content=content, file=file, day=today, play=True, mentions=mentions
                )
                self.boards[channel_id] = message
                self.store.set_board_id(channel_id, message.id)
                await interaction.delete_original_response()
                return
            except (discord.HTTPException, TypeError, ValueError):
                logger.exception("Failed to edit today's LoLdle board, posting a new one")
                file.reset()
        message = await interaction.followup.send(
            content=content,
            file=file,
            view=LoldleView(today),
            allowed_mentions=mentions,
            wait=True,
        )
        try:
            await interaction.delete_original_response()
        except discord.HTTPException:
            pass
        self.boards[channel_id] = message
        self.store.set_board_id(channel_id, message.id)

    async def on_progress(self, instance_id: str, player: dict) -> dict:
        user_id = str(player.get("id") or "")
        if not user_id:
            return player
        today = loldle_day()
        channel_id = resolve_channel_id(
            player,
            instance_id,
            self.instances,
            self.user_channels,
            stored_user_channel=self.store.user_channel(user_id),
        )
        if channel_id is None:
            channel_id = await self._lookup_instance_channel(instance_id)
        if channel_id is None:
            logger.warning("LoLdle progress without channel user={} instance={}", user_id, instance_id)
            return player
        self.instances[instance_id] = channel_id
        self.user_channels[user_id] = channel_id
        self.store.remember_user_channel(user_id, channel_id)
        stored = self.store.upsert_player(channel_id, player)
        if stored is None:
            stored = self.store.player(channel_id, user_id)
        try:
            self.store.add_starter(channel_id, int(user_id))
        except ValueError:
            pass
        if not self._session_live(channel_id):
            self._begin_session(channel_id)
        self._touch(channel_id)
        if stored is not None:
            self.session_players.setdefault(channel_id, {})[user_id] = stored
        try:
            self.session_starters.setdefault(channel_id, set()).add(int(user_id))
        except ValueError:
            pass
        channel = await self._messageable(channel_id)
        if channel is not None:
            self._schedule_publish(channel)
        return stored or {**player, "day": today, "progress": {}}

    async def on_reset(self, instance_id: str, payload: dict) -> None:
        user_id = str(payload.get("id") or "")
        if not user_id:
            return
        channel_id = resolve_channel_id(
            payload,
            instance_id,
            self.instances,
            self.user_channels,
            stored_user_channel=self.store.user_channel(user_id),
        )
        if channel_id is None:
            channel_id = await self._lookup_instance_channel(instance_id)
        if channel_id is None:
            logger.warning("LoLdle reset without channel user={} instance={}", user_id, instance_id)
            return
        jobs = self.store.remove_player(channel_id, user_id)
        self.session_players.get(channel_id, {}).pop(user_id, None)
        try:
            self.session_starters.get(channel_id, set()).discard(int(user_id))
        except ValueError:
            pass
        self._last.pop(channel_id, None)
        channel = await self._messageable(channel_id)
        if channel is not None:
            await self._rewrite_after_reset(channel, jobs)

    async def on_idle(self, instance_id: str):
        self.instances.pop(instance_id, None)

    async def _lookup_instance_channel(self, instance_id: str) -> int | None:
        parsed = parse_instance_channel(instance_id)
        if parsed:
            return parsed
        app_id = self.bot.application_id or self.bot.settings.discord_client_id
        token = self.bot.settings.discord_bot_token
        if not app_id or not instance_id or not token:
            return None
        url = f"https://discord.com/api/v10/applications/{app_id}/activity-instances/{instance_id}"
        data = None
        try:
            for attempt in range(2):
                async with self.activity_server.session.get(url, headers={"Authorization": f"Bot {token}"}) as response:
                    if response.status == 200:
                        data = await response.json(content_type=None)
                        break
                    if response.status == 404 and attempt == 0:
                        await asyncio.sleep(0.4)
                        continue
                    logger.warning(
                        "LoLdle activity instance lookup HTTP {} instance={}",
                        response.status,
                        instance_id,
                    )
                    return None
        except Exception:
            logger.exception("Failed to resolve LoLdle activity instance")
            return None
        location = data.get("location") if isinstance(data, dict) else None
        location = location or {}
        return coerce_channel_id(location.get("channel_id")) or parse_instance_channel(location.get("id"))

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
        for job in self.store.due_recaps():
            try:
                await self._post_recap(job)
            except Exception:
                logger.exception("Failed to reset LoLdle day")

    @reset_loop.before_loop
    async def before_reset_loop(self):
        await self.bot.wait_until_ready()

    async def _post_recap(self, job: dict) -> None:
        channel_id = int(job["channel_id"])
        channel = await self._messageable(channel_id)
        if channel is None:
            return
        async with self._lock(channel_id):
            await self._freeze_board(channel, self.store.freeze_day(channel_id, job["day"]))
            if job.get("played"):
                recap, recap_file = await self._card(
                    list(job.get("players") or []),
                    set(job.get("starters") or set()),
                    streak=int(job.get("streak") or 0),
                    title=f"Итоги · {format_day(job['day'])}",
                    headline=f"Итоги дня · {format_day(job['day'])}",
                    recap=True,
                    ping=False,
                    remaining=False,
                )
                recap_message = await asyncio.wait_for(
                    self._send_board(
                        channel,
                        content=recap,
                        file=recap_file,
                        day=loldle_day(),
                        play=True,
                        mentions=discord.AllowedMentions.none(),
                    ),
                    timeout=12,
                )
                self.store.set_recap_id(channel_id, job["day"], recap_message.id)
            self.store.mark_recapped(channel_id, job["day"])
            self.session_players[channel_id] = {}
            self.session_starters[channel_id] = set()
            self.session_open[channel_id] = False
            self.boards.pop(channel_id, None)
            self._last.pop(channel_id, None)

    async def _publish(self, channel: discord.abc.Messageable) -> None:
        channel_id = channel.id
        today = loldle_day()
        async with self._lock(channel_id):
            players, starters = self._people_for_board(channel_id)
            streak = int(self.store.channel(channel_id).get("streak") or 0)
            live_names = self._live_names(channel_id, players)
            fingerprint = json.dumps(
                {
                    "streak": streak,
                    "starters": sorted(starters),
                    "players": [(player.get("id"), player.get("progress")) for player in players],
                    "live": live_names,
                },
                sort_keys=True,
                default=str,
            )
            message = await self._today_board(channel)
            if message is not None and self._last.get(channel_id) == fingerprint:
                return
            content, file = await self._card(
                players,
                starters,
                streak=streak,
                title=f"LoLdle · {format_day(today)}",
                headline=self._playing_line(live_names) if self._session_live(channel_id) else None,
            )
            try:
                mentions = discord.AllowedMentions.none()
                if message is None:
                    message = await asyncio.wait_for(
                        self._send_board(
                            channel,
                            content=content,
                            file=file,
                            day=today,
                            play=True,
                            mentions=mentions,
                        ),
                        timeout=12,
                    )
                else:
                    message = await asyncio.wait_for(
                        self._edit_board(
                            message,
                            content=content,
                            file=file,
                            day=today,
                            play=True,
                            mentions=mentions,
                        ),
                        timeout=12,
                    )
            except (discord.HTTPException, TimeoutError):
                logger.exception("Failed to post LoLdle session")
                return
            self.boards[channel_id] = message
            self.store.set_board_id(channel_id, message.id)
            self._last[channel_id] = fingerprint

    async def _rewrite_after_reset(self, channel: discord.abc.Messageable, jobs: list[dict]) -> None:
        today = loldle_day()
        streak = int(self.store.channel(channel.id).get("streak") or 0)
        await self._publish(channel)
        for job in sorted(jobs, key=lambda item: item.get("day") or ""):
            if job.get("day") == today:
                continue
            await self._rewrite_day_card(channel, job, streak)

    async def _rewrite_day_card(self, channel: discord.abc.Messageable, job: dict, streak: int) -> None:
        fetch = getattr(channel, "fetch_message", None)
        if fetch is None:
            return
        recap_id = message_id(job.get("recap_id"))
        ids = [job.get("board_id"), recap_id]
        targets = [int(item) for item in ids if item]
        if not targets:
            return
        day = str(job.get("day") or "")
        title = f"Итоги · {format_day(day)}" if day else "Итоги LoLdle"
        async with self._lock(channel.id):
            for mid in targets:
                content, file = await self._card(
                    list(job.get("players") or []),
                    set(job.get("starters") or set()),
                    streak=streak,
                    title=title,
                    headline=f"Итоги дня · {format_day(day)}" if day else None,
                    recap=True,
                    ping=False,
                    remaining=False,
                )
                play = recap_id is not None and mid == recap_id
                try:
                    message = await fetch(mid)
                    await asyncio.wait_for(
                        self._edit_board(
                            message,
                            content=content,
                            file=file,
                            day=loldle_day() if play else None,
                            play=play,
                            mentions=discord.AllowedMentions.none(),
                        ),
                        timeout=12,
                    )
                except discord.NotFound:
                    continue
                except (discord.HTTPException, TimeoutError):
                    logger.exception("Failed to rewrite LoLdle card after reset")

    def _channel(self, interaction: discord.Interaction) -> discord.abc.Messageable | None:
        channel = interaction.channel
        if isinstance(channel, discord.abc.Messageable):
            return channel
        if interaction.channel_id is None:
            return None
        found = self.bot.get_channel(interaction.channel_id)
        return found if isinstance(found, discord.abc.Messageable) else None

    async def _messageable(self, channel_id: int) -> discord.abc.Messageable | None:
        channel = self.bot.get_channel(channel_id)
        if isinstance(channel, discord.abc.Messageable):
            return channel
        try:
            fetched = await self.bot.fetch_channel(channel_id)
        except discord.HTTPException:
            return None
        return fetched if isinstance(fetched, discord.abc.Messageable) else None

    def _touch(self, channel_id: int) -> None:
        self.session_open[channel_id] = True
        self.session_seen[channel_id] = time.monotonic()

    def _begin_session(self, channel_id: int) -> None:
        self.session_players[channel_id] = {}
        self.session_starters[channel_id] = set()
        self._touch(channel_id)

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

    def _people_for_board(self, channel_id: int) -> tuple[list[dict], set[int]]:
        people = {str(player.get("id")): player for player in self.store.today_players(channel_id) if player.get("id")}
        starters = self.store.today_starters(channel_id)
        for user_id in starters:
            key = str(user_id)
            if key in people:
                continue
            people[key] = self._stub_player(user_id)
        return list(people.values()), starters

    def _stub_player(self, user_id: int) -> dict:
        user = self.bot.get_user(user_id)
        avatar = None
        if user is not None and user.avatar is not None:
            avatar = user.avatar.key
        return {
            "id": str(user_id),
            "name": (user.global_name or user.display_name or user.name) if user else "Игрок",
            "avatar": avatar,
            "day": loldle_day(),
            "progress": {},
        }

    def _live_names(self, channel_id: int, players: list[dict]) -> list[str]:
        by_id: dict[int, dict] = {}
        for player in players:
            try:
                by_id[int(player.get("id"))] = player
            except (TypeError, ValueError):
                continue
        names: list[str] = []
        seen: set[int] = set()
        if self._session_live(channel_id):
            for user_id in self.session_starters.get(channel_id, set()):
                names.append(self._player_name(by_id.get(user_id), user_id, ping=False))
                seen.add(user_id)
            for user_id in self.session_players.get(channel_id, {}):
                try:
                    numeric = int(user_id)
                except (TypeError, ValueError):
                    continue
                if numeric in seen:
                    continue
                names.append(self._player_name(by_id.get(numeric), numeric, ping=False))
                seen.add(numeric)
            return names
        for user_id in self.store.today_starters(channel_id):
            names.append(self._player_name(by_id.get(user_id), user_id, ping=False))
        return names

    async def _today_board(self, channel: discord.abc.Messageable) -> discord.Message | None:
        today = loldle_day()
        board_id = self.store.board_id(channel.id, today)
        fetch = getattr(channel, "fetch_message", None)
        if board_id and fetch is not None:
            try:
                message = await fetch(board_id)
                if self._is_components_v2(message):
                    await self._drop_board(channel, message)
                elif self._is_today_board(message, today):
                    return message
            except discord.NotFound:
                self.store.set_board_id(channel.id, None)
            except discord.HTTPException:
                logger.exception("Failed to fetch today's LoLdle board")
        cached = self.boards.get(channel.id)
        if cached is not None and self._is_components_v2(cached):
            self.boards.pop(channel.id, None)
            cached = None
        if cached is not None and self._is_today_board(cached, today):
            return cached
        return None

    def _is_components_v2(self, message: discord.Message) -> bool:
        return bool(getattr(message.flags, "components_v2", False))

    async def _drop_board(self, channel: discord.abc.Messageable, message: discord.Message) -> None:
        logger.warning("Dropping unusable LoLdle board {}", message.id)
        try:
            await message.delete()
        except discord.HTTPException:
            pass
        self.store.set_board_id(channel.id, None)
        self.boards.pop(channel.id, None)

    def _is_today_board(self, message: discord.Message, today: str) -> bool:
        if self._is_components_v2(message):
            return False
        if self.bot.user is None or message.author.id != self.bot.user.id:
            return False
        want = play_custom_id(today)
        for custom_id in iter_custom_ids(message.components):
            if custom_id == want:
                return True
            if play_id_day(custom_id) not in (None, today) and custom_id != PLAY_ID:
                return False
        return False

    async def _freeze_board(self, channel: discord.abc.Messageable, board_id: int | None) -> None:
        if not board_id:
            return
        fetch = getattr(channel, "fetch_message", None)
        if fetch is None:
            return
        try:
            message = await fetch(int(board_id))
            await message.edit(view=None)
        except (discord.HTTPException, TypeError, ValueError):
            logger.exception("Failed to freeze yesterday LoLdle board")

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
        remaining: bool = True,
    ) -> tuple[str, discord.File]:
        ranked = sorted(players, key=lambda item: (-player_wins(item), item.get("name") or ""))
        avatars = await fetch_avatars(self.activity_server.session, ranked)
        image = render_scoreboard(ranked, avatars, title=title, streak=streak, remaining=remaining)
        file = discord.File(image, filename="loldle.png")
        return self._caption(
            ranked,
            starters,
            streak,
            headline,
            recap=recap,
            ping=ping,
            status=status,
        ), file

    async def _send_board(
        self,
        channel: discord.abc.Messageable,
        *,
        content: str,
        file: discord.File,
        day: str | None,
        play: bool,
        mentions: discord.AllowedMentions,
    ) -> discord.Message:
        return await channel.send(
            content=content,
            file=file,
            view=LoldleView(day) if play else None,
            allowed_mentions=mentions,
        )

    async def _edit_board(
        self,
        message: discord.Message,
        *,
        content: str,
        file: discord.File,
        day: str | None,
        play: bool,
        mentions: discord.AllowedMentions,
    ) -> discord.Message:
        if self._is_components_v2(message):
            file.reset()
            return await self._replace_board(message, content=content, file=file, day=day, play=play, mentions=mentions)
        try:
            return await message.edit(
                content=content,
                embed=None,
                attachments=[file],
                view=LoldleView(day) if play else None,
                allowed_mentions=mentions,
            )
        except discord.HTTPException:
            file.reset()
            return await self._replace_board(message, content=content, file=file, day=day, play=play, mentions=mentions)

    async def _replace_board(
        self,
        message: discord.Message,
        *,
        content: str,
        file: discord.File,
        day: str | None,
        play: bool,
        mentions: discord.AllowedMentions,
    ) -> discord.Message:
        channel = message.channel
        try:
            await message.delete()
        except discord.HTTPException:
            pass
        if not isinstance(channel, discord.abc.Messageable):
            raise TypeError("LoLdle board has no messageable channel")
        return await self._send_board(
            channel,
            content=content,
            file=file,
            day=day,
            play=play,
            mentions=mentions,
        )

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
            best = max((player_wins(player) for player in players), default=0)
            for player in players:
                done = player_wins(player)
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
        best = max((player_wins(player) for player in players), default=0)
        for player in players:
            done = player_wins(player)
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
