"""Кнопка Играть. Статический custom_id переживает перезапуск, динамический — старые сообщения с датой."""

import re

import discord
from loguru import logger

from ogurec.loldle.day import loldle_day
from ogurec.loldle.diag import http_detail, play_ctx
from ogurec.loldle.ids import PLAY_ID, play_custom_id


async def handle_play(interaction: discord.Interaction) -> None:
    logger.info("loldle play start {}", play_ctx(interaction))
    if not interaction.response.is_done():
        try:
            await interaction.response.launch_activity()
            logger.info("loldle launch ok {}", play_ctx(interaction))
        except Exception as exc:
            logger.exception("loldle launch failed {} {}", play_ctx(interaction), http_detail(exc))
            if not interaction.response.is_done():
                try:
                    await interaction.response.send_message("Не получилось запустить LoLdle.", ephemeral=True)
                except discord.HTTPException as send_exc:
                    logger.exception(
                        "loldle launch fallback failed {} {}",
                        play_ctx(interaction),
                        http_detail(send_exc),
                    )
            return
    else:
        logger.warning("loldle play already responded {}", play_ctx(interaction))
    cog = interaction.client.get_cog("Loldle")
    touch_session = getattr(cog, "touch_session", None)
    if touch_session is None:
        logger.warning("loldle play without cog {}", play_ctx(interaction))
        return
    try:
        await touch_session(interaction)
    except Exception as exc:
        logger.exception("loldle session after launch failed {} {}", play_ctx(interaction), http_detail(exc))


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
        logger.info("loldle dynamic play day={} {}", self.day, play_ctx(interaction))
        await handle_play(interaction)


class LoldleView(discord.ui.View):
    def __init__(self, day: str | None = None):
        super().__init__(timeout=None)
        self.day = day or loldle_day()

    @discord.ui.button(label="Играть", style=discord.ButtonStyle.success, custom_id=PLAY_ID)
    async def play(self, interaction: discord.Interaction, _button: discord.ui.Button):
        logger.info("loldle persistent play {}", play_ctx(interaction))
        await handle_play(interaction)

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        item: discord.ui.Item,
        /,
    ) -> None:
        logger.exception(
            "loldle view error item={} {}",
            getattr(item, "custom_id", type(item).__name__),
            play_ctx(interaction),
        )
