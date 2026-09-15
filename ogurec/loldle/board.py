"""Сообщение-табло в канале: отправить, отредактировать, найти сегодняшнее, вернуть кнопку.

Вся возня с капризами Discord живёт здесь: components v2 нельзя редактировать в обычное
сообщение, упавший edit спасает send-then-delete, а не delete-then-send.
"""

import discord
from loguru import logger

from ogurec.loldle.captions import caption
from ogurec.loldle.day import loldle_day
from ogurec.loldle.diag import http_detail
from ogurec.loldle.ids import PLAY_ID, iter_custom_ids, message_id, play_custom_id, play_id_day
from ogurec.loldle.rules import player_wins
from ogurec.loldle.scoreboard import fetch_avatars, render_scoreboard
from ogurec.loldle.store import LoldleStore
from ogurec.loldle.view import LoldleView


class Boards:
    """Кэш сообщений-табло по каналу плюс отправка и правка этих сообщений."""

    def __init__(self, bot, store: LoldleStore, http):
        self.bot = bot
        self.store = store
        self.http = http
        self.cache: dict[int, discord.Message] = {}

    def display_name(self, user_id: int) -> str | None:
        user = self.bot.get_user(user_id)
        if user is None:
            return None
        return user.global_name or user.display_name or user.name

    async def messageable(self, channel_id: int) -> discord.abc.Messageable | None:
        channel = self.bot.get_channel(channel_id)
        if isinstance(channel, discord.abc.Messageable):
            return channel
        try:
            fetched = await self.bot.fetch_channel(channel_id)
        except discord.HTTPException:
            return None
        return fetched if isinstance(fetched, discord.abc.Messageable) else None

    async def card(
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
        avatars = await fetch_avatars(self.http, ranked)
        image = render_scoreboard(ranked, avatars, title=title, streak=streak, remaining=remaining)
        file = discord.File(image, filename="loldle.png")
        content = caption(
            ranked,
            starters,
            streak,
            headline,
            recap=recap,
            ping=ping,
            status=status,
            lookup=self.display_name,
        )
        return content, file

    async def send(
        self,
        channel: discord.abc.Messageable,
        *,
        content: str,
        file: discord.File,
        day: str | None,
        play: bool,
        mentions: discord.AllowedMentions,
    ) -> discord.Message:
        sent = await channel.send(
            content=content,
            file=file,
            view=LoldleView(day) if play else None,
            allowed_mentions=mentions,
        )
        logger.info(
            "loldle send channel={} message={} play={} day={}",
            channel.id,
            sent.id,
            play,
            day,
        )
        return sent

    async def edit(
        self,
        message: discord.Message,
        *,
        content: str,
        file: discord.File,
        day: str | None,
        play: bool,
        mentions: discord.AllowedMentions,
    ) -> discord.Message:
        if self.is_components_v2(message):
            logger.warning("loldle edit v2 replace message={} channel={}", message.id, getattr(message.channel, "id", None))
            file.reset()
            return await self.replace(message, content=content, file=file, day=day, play=play, mentions=mentions)
        try:
            edited = await message.edit(
                content=content,
                embed=None,
                attachments=[file],
                view=LoldleView(day) if play else None,
                allowed_mentions=mentions,
            )
            logger.info("loldle edit ok message={} play={}", message.id, play)
            return edited
        except discord.HTTPException as exc:
            logger.exception("loldle edit failed message={} {}, replacing", message.id, http_detail(exc))
            file.reset()
            return await self.replace(message, content=content, file=file, day=day, play=play, mentions=mentions)

    async def replace(
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
        if not isinstance(channel, discord.abc.Messageable):
            raise TypeError("LoLdle board has no messageable channel")
        logger.warning(
            "loldle replace send-then-delete old={} channel={} play={}",
            message.id,
            getattr(channel, "id", None),
            play,
        )
        sent = await self.send(
            channel,
            content=content,
            file=file,
            day=day,
            play=play,
            mentions=mentions,
        )
        try:
            await message.delete()
            logger.warning("loldle deleted old message={} after replace new={}", message.id, sent.id)
        except discord.HTTPException as exc:
            logger.exception("loldle delete after replace failed old={} {}", message.id, http_detail(exc))
        return sent

    async def today(self, channel: discord.abc.Messageable) -> discord.Message | None:
        today = loldle_day()
        board_id = self.store.board_id(channel.id, today)
        fetch = getattr(channel, "fetch_message", None)
        if board_id and fetch is not None:
            try:
                message = await fetch(board_id)
                usable = (
                    not self.is_components_v2(message)
                    and self.is_today_board(message, today)
                    and not self.is_recap_id(channel.id, message.id)
                )
                if usable:
                    return message
                logger.info(
                    "loldle today board skipped channel={} message={} v2={} recap={} ids={}",
                    channel.id,
                    message.id,
                    self.is_components_v2(message),
                    self.is_recap_id(channel.id, message.id),
                    iter_custom_ids(message.components),
                )
            except discord.NotFound:
                self.store.set_board_id(channel.id, None)
            except discord.HTTPException:
                logger.exception("Failed to fetch today's LoLdle board")
        cached = self.cache.get(channel.id)
        if cached is None:
            return None
        if (
            self.is_components_v2(cached)
            or not self.is_today_board(cached, today)
            or self.is_recap_id(channel.id, cached.id)
        ):
            self.cache.pop(channel.id, None)
            return None
        return cached

    def is_components_v2(self, message: discord.Message) -> bool:
        return bool(getattr(message.flags, "components_v2", False))

    def is_recap_id(self, channel_id: int, mid: int | None) -> bool:
        if not mid:
            return False
        for record in (self.store.channel(channel_id).get("days") or {}).values():
            if message_id((record or {}).get("recap_id")) == int(mid):
                return True
        return False

    def is_today_board(self, message: discord.Message, today: str) -> bool:
        if self.is_components_v2(message):
            return False
        if self.bot.user is None or message.author.id != self.bot.user.id:
            return False
        ids = iter_custom_ids(message.components)
        if any(play_id_day(custom_id) not in (None, today) for custom_id in ids):
            return False
        return PLAY_ID in ids or play_custom_id(today) in ids

    def has_static_play(self, message: discord.Message) -> bool:
        return PLAY_ID in iter_custom_ids(message.components)

    async def freeze(self, channel: discord.abc.Messageable, board_id: int | None) -> None:
        if not board_id:
            return
        fetch = getattr(channel, "fetch_message", None)
        if fetch is None:
            return
        try:
            message = await fetch(int(board_id))
            await message.edit(view=None)
            logger.info("loldle freeze board channel={} message={}", getattr(channel, "id", None), board_id)
        except (discord.HTTPException, TypeError, ValueError):
            logger.exception("Failed to freeze yesterday LoLdle board")

    async def restore_play_buttons(self) -> None:
        for target in self.store.play_targets():
            channel = await self.messageable(int(target["channel_id"]))
            fetch = getattr(channel, "fetch_message", None) if channel is not None else None
            if fetch is None:
                continue
            try:
                message = await fetch(int(target["message_id"]))
            except (discord.HTTPException, TypeError, ValueError):
                continue
            if self.has_static_play(message) and not self.is_components_v2(message):
                logger.info(
                    "loldle restore skip already has play channel={} message={} kind={}",
                    target["channel_id"],
                    message.id,
                    target.get("kind"),
                )
                continue
            try:
                await message.edit(view=LoldleView())
                logger.info(
                    "loldle restore attached play channel={} message={} kind={}",
                    target["channel_id"],
                    message.id,
                    target.get("kind"),
                )
            except discord.HTTPException as exc:
                logger.warning(
                    "loldle restore edit failed channel={} message={} {}, reposting",
                    target["channel_id"],
                    message.id,
                    http_detail(exc),
                )
                await self._repost_with_play(channel, message, target)
            except Exception:
                logger.exception("loldle restore failed {}", target)

    async def _repost_with_play(
        self,
        channel: discord.abc.Messageable,
        message: discord.Message,
        target: dict,
    ) -> None:
        files = [await attachment.to_file() for attachment in message.attachments]
        payload: dict = {
            "content": message.content or "",
            "view": LoldleView(),
            "allowed_mentions": discord.AllowedMentions.none(),
        }
        if files:
            payload["files"] = files
        sent = await channel.send(**payload)
        logger.warning(
            "loldle restore reposted old={} new={} kind={}",
            message.id,
            sent.id,
            target.get("kind"),
        )
        try:
            await message.delete()
            logger.warning("loldle restore deleted old message={}", message.id)
        except discord.HTTPException as exc:
            logger.exception("loldle restore delete failed old={} {}", message.id, http_detail(exc))
        channel_id = int(target["channel_id"])
        if target.get("kind") == "board":
            self.store.set_board_id(channel_id, sent.id)
            self.cache[channel_id] = sent
        elif target.get("kind") == "recap":
            self.store.set_recap_id(channel_id, str(target.get("day") or ""), sent.id)
