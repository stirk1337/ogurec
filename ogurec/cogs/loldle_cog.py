"""Ког LoLdle: слэш-команда, колбэки активити и обновление табло канала.

Механика сообщений — в ogurec/loldle/board.py, состояние дня — в ogurec/loldle/store.py.
"""

import asyncio
import json

import discord
from discord import app_commands
from discord.ext import commands, tasks
from loguru import logger

from ogurec.bot import OgurecBot
from ogurec.loldle.board import Boards
from ogurec.loldle.captions import index_by_id, names_for, playing_line
from ogurec.loldle.day import format_day, loldle_day
from ogurec.loldle.diag import http_detail, play_ctx, play_custom_id_of
from ogurec.loldle.ids import coerce_channel_id, message_id, parse_instance_channel, resolve_channel_id
from ogurec.loldle.rules import player_has_progress
from ogurec.loldle.server import ActivityServer
from ogurec.loldle.session import Sessions
from ogurec.loldle.store import LoldleStore
from ogurec.loldle.view import LoldleView, PlayButton


class Loldle(commands.Cog):
    def __init__(self, bot: OgurecBot, activity_server: ActivityServer):
        self.bot = bot
        self.activity_server = activity_server
        self.store = LoldleStore()
        self.sessions = Sessions(activity_server.rooms)
        self.boards = Boards(bot, self.store, activity_server.session)
        self._last: dict[int, str] = {}
        self._locks: dict[int, asyncio.Lock] = {}
        self._board_tasks: dict[int, asyncio.Task] = {}
        activity_server.on_progress = self.on_progress
        activity_server.on_reset = self.on_reset
        activity_server.on_idle = self.on_idle

    async def cog_load(self):
        self.bot.add_dynamic_items(PlayButton)
        self.bot.add_view(LoldleView())
        self.reset_loop.start()
        logger.info("loldle cog loaded")

    async def cog_unload(self):
        self.reset_loop.cancel()
        for task in self._board_tasks.values():
            task.cancel()
        self.bot.remove_dynamic_items(PlayButton)
        logger.info("loldle cog unloaded")

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction) -> None:
        if interaction.type is not discord.InteractionType.component:
            return
        custom_id = play_custom_id_of(interaction) or ""
        if not custom_id.startswith("loldle:play"):
            return
        logger.info("loldle click {}", play_ctx(interaction))

    def _lock(self, channel_id: int) -> asyncio.Lock:
        return self._locks.setdefault(channel_id, asyncio.Lock())

    @app_commands.command(name="loldle", description="Показать сегодняшние результаты LoLdle")
    async def loldle(self, interaction: discord.Interaction):
        channel = self._channel(interaction)
        logger.info("loldle slash {}", play_ctx(interaction))
        if channel is None:
            await interaction.response.send_message("Команду нужно вызывать в текстовом канале.", ephemeral=True)
            return
        await interaction.response.defer()
        try:
            await self.show_today(channel, interaction)
        except Exception as exc:
            logger.exception("loldle slash failed {} {}", play_ctx(interaction), http_detail(exc))
            try:
                await interaction.edit_original_response(content="Не получилось показать LoLdle.")
            except discord.HTTPException as send_exc:
                logger.exception("loldle slash error edit failed {}", http_detail(send_exc))
                try:
                    await interaction.followup.send("Не получилось показать LoLdle.", ephemeral=True)
                except discord.HTTPException as follow_exc:
                    logger.exception("loldle slash error followup failed {}", http_detail(follow_exc))

    def bind_user_channel(self, interaction: discord.Interaction) -> discord.abc.Messageable | None:
        channel = self._channel(interaction)
        if channel is None:
            return None
        self.sessions.remember_user(str(interaction.user.id), channel.id, loldle_day())
        self.store.remember_user_channel(str(interaction.user.id), channel.id)
        return channel

    async def touch_session(self, interaction: discord.Interaction):
        channel = self.bind_user_channel(interaction)
        if channel is None:
            logger.warning("loldle play without channel {}", play_ctx(interaction))
            return
        self.store.add_starter(channel.id, interaction.user.id)
        if not self.sessions.live(channel.id):
            logger.info("loldle session start channel={} user={}", channel.id, interaction.user.id)
        self.sessions.open_unless_live(channel.id)
        self.sessions.add_starter(interaction.user.id, channel.id)
        logger.info("loldle play queued publish channel={} user={}", channel.id, interaction.user.id)
        self._schedule_publish(channel)

    async def show_today(self, channel: discord.abc.Messageable, interaction: discord.Interaction) -> None:
        channel_id = channel.id
        today = loldle_day()
        players, starters = self._people_for_board(channel_id)
        content, file = await self.boards.card(
            players,
            starters,
            streak=self._streak(channel_id),
            title=f"LoLdle · {format_day(today)}",
            headline=f"LoLdle · {format_day(today)}",
            recap=True,
            ping=False,
            status=True,
        )
        previous = await self.boards.today(channel)
        logger.info("loldle slash turn thinking into board channel={}", channel_id)
        message = await interaction.edit_original_response(
            content=content,
            attachments=[file],
            view=LoldleView(today),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        self._remember_board(channel_id, message)
        if previous is not None and previous.id != message.id:
            try:
                await previous.delete()
                logger.info("loldle slash dropped old board channel={} message={}", channel_id, previous.id)
            except discord.HTTPException as exc:
                logger.warning("loldle slash delete old board failed message={} {}", previous.id, http_detail(exc))
        logger.info("loldle slash posted channel={} message={}", channel_id, message.id)

    async def on_progress(self, instance_id: str, player: dict) -> dict:
        user_id = str(player.get("id") or "")
        if not user_id:
            return player
        today = loldle_day()
        channel_id = await self._channel_of(player, instance_id, user_id, "progress")
        if channel_id is None:
            return player
        stored = self.store.upsert_player(channel_id, player)
        if stored is None:
            stored = self.store.player(channel_id, user_id)
        if stored is None and player_has_progress(player):
            logger.warning(
                "loldle progress not saved channel={} user={} payload_day={} server_day={}",
                channel_id,
                user_id,
                player.get("day"),
                today,
            )
        try:
            self.store.add_starter(channel_id, int(user_id))
        except ValueError:
            pass
        self.sessions.open_unless_live(channel_id)
        if stored is not None:
            self.sessions.set_player(channel_id, user_id, stored)
        self.sessions.add_starter(user_id, channel_id)
        channel = await self.boards.messageable(channel_id)
        if channel is not None:
            self._schedule_publish(channel)
        return stored or {**player, "day": today, "progress": {}}

    async def on_reset(self, instance_id: str, payload: dict) -> None:
        user_id = str(payload.get("id") or "")
        if not user_id:
            return
        channel_id = await self._channel_of(payload, instance_id, user_id, "reset")
        if channel_id is None:
            return
        jobs = self.store.remove_player(channel_id, user_id)
        self.sessions.drop_player(channel_id, user_id)
        self.sessions.drop_starter(user_id, channel_id)
        self._last.pop(channel_id, None)
        channel = await self.boards.messageable(channel_id)
        if channel is not None:
            await self._rewrite_after_reset(channel, jobs)

    async def on_idle(self, instance_id: str):
        self.sessions.forget_instance(instance_id)

    async def _channel_of(self, payload: dict, instance_id: str, user_id: str, kind: str) -> int | None:
        channel_id = resolve_channel_id(
            payload,
            instance_id,
            self.sessions.instances,
            self.sessions.user_channels,
            stored_user_channel=self.store.user_channel(user_id),
        )
        if channel_id is None:
            channel_id = await self._lookup_instance_channel(instance_id)
        if channel_id is None:
            logger.warning("LoLdle {} without channel user={} instance={}", kind, user_id, instance_id)
            return None
        self.sessions.remember_instance(instance_id, channel_id)
        self.sessions.remember_user(user_id, channel_id, loldle_day())
        self.store.remember_user_channel(user_id, channel_id)
        return channel_id

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
        try:
            await self.boards.restore_play_buttons()
        except Exception:
            logger.exception("Failed to restore LoLdle play buttons")

    async def _post_recap(self, job: dict) -> None:
        channel_id = int(job["channel_id"])
        channel = await self.boards.messageable(channel_id)
        if channel is None:
            return
        async with self._lock(channel_id):
            await self.boards.freeze(channel, self.store.freeze_day(channel_id, job["day"]))
            if job.get("played"):
                recap, recap_file = await self.boards.card(
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
                    self.boards.send(
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
                logger.info(
                    "loldle recap posted channel={} day={} message={}",
                    channel_id,
                    job["day"],
                    recap_message.id,
                )
            self.store.mark_recapped(channel_id, job["day"])
            self.sessions.close(channel_id)
            self.boards.cache.pop(channel_id, None)
            self._last.pop(channel_id, None)

    async def _publish(self, channel: discord.abc.Messageable) -> None:
        channel_id = channel.id
        today = loldle_day()
        async with self._lock(channel_id):
            players, starters = self._people_for_board(channel_id)
            streak = self._streak(channel_id)
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
            message = await self.boards.today(channel)
            if message is not None and self._last.get(channel_id) == fingerprint:
                logger.info("loldle publish skip unchanged channel={} message={}", channel_id, message.id)
                return
            content, file = await self.boards.card(
                players,
                starters,
                streak=streak,
                title=f"LoLdle · {format_day(today)}",
                headline=playing_line(live_names) if self.sessions.live(channel_id) else None,
            )
            try:
                mentions = discord.AllowedMentions.none()
                if message is None:
                    logger.info("loldle publish send channel={}", channel_id)
                    message = await asyncio.wait_for(
                        self.boards.send(
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
                    logger.info("loldle publish edit channel={} message={}", channel_id, message.id)
                    message = await asyncio.wait_for(
                        self.boards.edit(
                            message,
                            content=content,
                            file=file,
                            day=today,
                            play=True,
                            mentions=mentions,
                        ),
                        timeout=12,
                    )
            except (discord.HTTPException, TimeoutError) as exc:
                logger.exception("loldle publish failed channel={} {}", channel_id, http_detail(exc))
                return
            self._remember_board(channel_id, message)
            self._last[channel_id] = fingerprint
            logger.info("loldle publish done channel={} message={}", channel_id, message.id)

    async def _rewrite_after_reset(self, channel: discord.abc.Messageable, jobs: list[dict]) -> None:
        today = loldle_day()
        streak = self._streak(channel.id)
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
        targets = [int(item) for item in (job.get("board_id"), recap_id) if item]
        if not targets:
            return
        day = str(job.get("day") or "")
        title = f"Итоги · {format_day(day)}" if day else "Итоги LoLdle"
        async with self._lock(channel.id):
            for mid in targets:
                content, file = await self.boards.card(
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
                        self.boards.edit(
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

    def _streak(self, channel_id: int) -> int:
        return int(self.store.channel(channel_id).get("streak") or 0)

    def _remember_board(self, channel_id: int, message: discord.Message) -> None:
        self.boards.cache[channel_id] = message
        self.store.set_board_id(channel_id, message.id)

    def _people_for_board(self, channel_id: int) -> tuple[list[dict], set[int]]:
        people = {str(player.get("id")): player for player in self.store.today_players(channel_id) if player.get("id")}
        starters = self.store.today_starters(channel_id)
        for user_id in starters:
            people.setdefault(str(user_id), self._stub_player(user_id))
        return list(people.values()), starters

    def _stub_player(self, user_id: int) -> dict:
        return {
            "id": str(user_id),
            "name": self.boards.display_name(user_id) or "Игрок",
            "avatar": self._avatar_key(user_id),
            "day": loldle_day(),
            "progress": {},
        }

    def _avatar_key(self, user_id: int) -> str | None:
        user = self.bot.get_user(user_id)
        if user is None or user.avatar is None:
            return None
        return user.avatar.key

    def _live_names(self, channel_id: int, players: list[dict]) -> list[str]:
        by_id = index_by_id(players)
        lookup = self.boards.display_name
        if not self.sessions.live(channel_id):
            return names_for(self.store.today_starters(channel_id), by_id, lookup=lookup)
        starters = self.sessions.starters(channel_id)
        rest = [user_id for user_id in self.sessions.player_ids(channel_id) if user_id not in starters]
        return names_for(list(starters) + rest, by_id, lookup=lookup)
