import random

import discord
from discord.ext import commands, tasks

from ogurec.bot import OgurecBot
from ogurec.cogs.rebrand.rebrand_users import USER_STEAM
from ogurec.steam import SteamClient
from ogurec.tenor import TenorClient

GAME_POST_GUARANTEE = 5


class PresenceGameCog(commands.Cog):
    def __init__(self, bot: OgurecBot, tenor_client: TenorClient, steam_client: SteamClient, conversation_cog=None):
        self.bot = bot
        self.tenor_client = tenor_client
        self.steam_client = steam_client
        self.conversation_cog = conversation_cog

        self.game_post_counter = 0

        self.statuses = (
            discord.Status.online,
            discord.Status.idle,
            discord.Status.dnd,
        )

        self.update_presence.start()

    async def _get_random_game_from_library(self) -> tuple[dict | None, int, str]:
        """
        Получает случайную игру из библиотеки случайного пользователя.
        Возвращает (game_info, discord_user_id, game_name).
        """
        # Выбираем случайного пользователя из USER_STEAM
        discord_user_ids = list(USER_STEAM.keys())
        random_discord_id = random.choice(discord_user_ids)
        steam_id = str(USER_STEAM[random_discord_id])

        # Получаем случайную игру из библиотеки пользователя
        game_info = await self.steam_client.get_random_game_from_user(steam_id)

        game_name = game_info["name"]
        return game_info, random_discord_id, game_name

    @tasks.loop(hours=1)
    async def update_presence(self):
        await self.bot.wait_until_ready()

        channel = self.bot.get_channel(self.bot.settings.main_chat_id)
        if not channel:
            return

        # Получаем случайную игру из библиотеки пользователя
        game_info, discord_user_id, game_name = await self._get_random_game_from_library()

        activity = discord.Activity(name=game_name, type=discord.ActivityType.playing)

        await self.bot.change_presence(
            status=random.choice(self.statuses),
            activity=activity,
        )

        # Обновляем текущую игру в ConversationCog
        if self.conversation_cog:
            self.conversation_cog.current_game = game_name

        self.game_post_counter += 1
        trigger = random.randint(1, 50) == 10 or self.game_post_counter >= GAME_POST_GUARANTEE

        if trigger:
            # Генерируем сообщение про игру через GPT
            game_message = await self._generate_game_message(channel, game_name, game_info, discord_user_id)

            # Отправляем сгенерированное сообщение
            await channel.send(game_message)

            # Сохраняем в историю разговора, если есть conversation_cog
            if self.conversation_cog:
                channel_id = channel.id
                self.conversation_cog.add_assistant_message(channel_id, game_message)

            gif_url = await self.tenor_client.get_first_gif_url(game_name)
            await channel.send(gif_url)
            self.game_post_counter = 0

    async def _generate_game_message(
        self, channel, game_name: str, game_info: dict, discord_user_id: int) -> str:
        """Генерирует сообщение про игру через GPT (1 предложение)."""
        channel_id = channel.id

        # Получаем историю с системными сообщениями
        history = self.conversation_cog.get_channel_history(channel_id)

        # Если истории нет, инициализируем системные сообщения
        if not history:
            self.conversation_cog.ensure_system_messages(channel_id, channel.guild, False)
            history = self.conversation_cog.get_channel_history(channel_id)

        hours = game_info.get("playtime_forever", 0) / 60  # Конвертируем минуты в часы

        # Получаем описание игры из Steam Store API
        appid = game_info.get("appid")
        game_description = await self.steam_client.get_game_description(appid)

        # Формируем промпт
        # С шансом 30% обосрать вкус игрока
        roast_taste = random.randint(1, 100) <= 30

        game_prompt = f"Пингани {f"<@{discord_user_id}>"} и напиши сообщение о том, что ты идешь играть в {game_name}. ОБЯЗАТЕЛЬНО опиши игру в своем сообщении, используя эту информацию об игре: {game_description}. Используй описание игры, чтобы рассказать, что это за игра"

        game_prompt += f". ОБЯЗАТЕЛЬНО упомяни, что ты взял эту игру из библиотеки пользователя. У этого пользователя в этой игре {hours:.1f} часов - ОБЯЗАТЕЛЬНО отдельно упомяни что пользователь наиграл {hours:.1f} часов"
        if roast_taste:
            game_prompt += ". ОБЯЗАТЕЛЬНО скажи что-то плохое и токсичное про его выбор игр, высмеяй его вкус"

        history_with_prompt = history + [{"role": "user", "content": game_prompt}]

        # Генерируем ответ через GPT
        content = ""
        async for chunk in self.conversation_cog._chat_completion_with_rotation(
            messages=history_with_prompt, channel_id=channel_id
        ):
            content += chunk

        return content