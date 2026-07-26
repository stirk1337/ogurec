from datetime import datetime as dt
from datetime import timedelta

import discord
from discord import app_commands
from discord.ext import commands, tasks

from ogurec.bot import OgurecBot
from ogurec.config.settings import Settings
from ogurec.utils import (
    TIME_ZONE,
    get_all_users_with_role,
    get_random_formatted_emoji,
    get_random_sticker,
    get_role_by_name,
)


class Rebrand(commands.Cog):
    def __init__(self, bot: OgurecBot, settings: Settings):
        self.bot = bot
        self.users_id = settings.users_discord_id
        self.last_rebranding_date = None  # Храним дату последнего выполнения, чтобы не сработать дважды
        self.remember_rebranding.start()

    def _get_next_rebranding_time(self) -> timedelta:
        """Вычисляет время до следующего срабатывания ребрендинга (четная пятница в 00:01)."""
        now = dt.now(TIME_ZONE)

        # Находим следующую пятницу
        days_until_friday = (4 - now.weekday()) % 7
        if days_until_friday == 0:
            # Если сегодня пятница, проверяем текущее время
            if now.hour < 0 or (now.hour == 0 and now.minute < 1):
                # Еще не прошло 00:01 сегодня
                next_friday = now.replace(hour=0, minute=1, second=0, microsecond=0)
            else:
                # Уже прошло 00:01, берем следующую пятницу
                days_until_friday = 7
                next_friday = (now + timedelta(days=days_until_friday)).replace(
                    hour=0, minute=1, second=0, microsecond=0
                )
        else:
            next_friday = (now + timedelta(days=days_until_friday)).replace(hour=0, minute=1, second=0, microsecond=0)

        # Проверяем четность недели следующей пятницы
        while True:
            week_number = next_friday.isocalendar()[1]
            if week_number % 2 == 0:  # Четная неделя
                break
            # Если нечетная, берем следующую пятницу (через 7 дней)
            next_friday = next_friday + timedelta(days=7)

        # Вычисляем разницу
        time_until = next_friday - now

        return time_until

    def _format_time_until(self, delta: timedelta) -> str:
        """Форматирует timedelta в строку "X дней, Y часов, Z минут"."""
        days = delta.days
        hours, remainder = divmod(delta.seconds, 3600)
        minutes, _ = divmod(remainder, 60)

        parts = []
        if days > 0:
            parts.append(f"{days} {'день' if days == 1 else 'дня' if 2 <= days <= 4 else 'дней'}")
        if hours > 0:
            parts.append(f"{hours} {'час' if hours == 1 else 'часа' if 2 <= hours <= 4 else 'часов'}")
        if minutes > 0 or not parts:
            parts.append(f"{minutes} {'минута' if minutes == 1 else 'минуты' if 2 <= minutes <= 4 else 'минут'}")

        return ", ".join(parts)

    @app_commands.command(description="Отобразить порядок ребрендинга")
    async def rebranding(self, interaction: discord.Interaction):
        embed = discord.Embed(title=interaction.guild.name, color=discord.Color.random())
        now_user = get_all_users_with_role(interaction.guild, "Ребрендинг")[0]
        next_user_place = self.users_id.index(now_user.id) + 1
        if next_user_place >= len(self.users_id):
            next_user_place = 0
        for player_id in self.users_id:
            user = interaction.guild.get_member(player_id)
            embed.add_field(name="", value=user.display_name, inline=False)
        embed.set_author(name=f"Сейчас: {now_user.display_name}", icon_url=now_user.avatar)
        embed.set_thumbnail(url=interaction.guild.icon)
        embed.set_image(url=interaction.guild.banner)
        next_user = interaction.guild.get_member(self.users_id[next_user_place])

        # Вычисляем время до следующего ребрендинга
        time_until = self._get_next_rebranding_time()
        time_str = self._format_time_until(time_until)

        embed.set_footer(
            text=f"Следующий: {next_user.display_name} | До ребрендинга: {time_str}", icon_url=next_user.avatar
        )
        await interaction.response.send_message(embed=embed)

    @tasks.loop(seconds=30)
    async def remember_rebranding(self):
        # Проверяем время в нужном часовом поясе
        now = dt.now(TIME_ZONE)

        # Выполняем только в пятницу в 00:01
        if now.weekday() != 4:  # Пятница
            return

        if now.hour != 0 or now.minute != 1:
            return

        # Проверяем четность номера недели (четная неделя = срабатывает)
        week_number = now.isocalendar()[1]  # Номер недели в году
        if week_number % 2 != 0:  # Нечетная неделя - пропускаем
            return

        # Защита от повторного срабатывания: проверяем, что мы еще не выполняли сегодня
        today_date = now.date()
        if self.last_rebranding_date == today_date:
            return

        # Помечаем, что мы выполнили задачу сегодня
        self.last_rebranding_date = today_date

        channel = self.bot.get_channel(self.bot.settings.bot_chat_id)
        now_user = get_all_users_with_role(channel.guild, "Ребрендинг")[0]
        next_user_place = self.users_id.index(now_user.id) + 1
        if next_user_place >= len(self.users_id):
            next_user_place = 0
        next_user = channel.guild.get_member(self.users_id[next_user_place])
        random_emoji = get_random_formatted_emoji(channel.guild)
        role = get_role_by_name(channel.guild, "Ребрендинг")
        await now_user.remove_roles(role)
        await next_user.add_roles(role)
        await channel.send(f"<@{next_user.id}>, напоминаю, что сегодня ты делаешь Ребрендинг {random_emoji}")
        await channel.send(stickers=[get_random_sticker(channel.guild)])
