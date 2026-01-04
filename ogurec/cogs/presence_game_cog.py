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

        try:
            # Получаем случайную игру из библиотеки пользователя
            game_info = await self.steam_client.get_random_game_from_user(steam_id)

            if not game_info:
                # Fallback если игры не найдены
                return None, random_discord_id, "Baldur's Gate 3"

            game_name = game_info.get("name", "Baldur's Gate 3")
            return game_info, random_discord_id, game_name
        except Exception:
            # Fallback при ошибке Steam API
            return None, random_discord_id, "Baldur's Gate 3"

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
        self, channel, game_name: str, game_info: dict = None, discord_user_id: int = None
    ) -> str:
        """Генерирует сообщение про игру через GPT (1 предложение)."""
        if not self.conversation_cog or not self.conversation_cog.gpt_client:
            # Fallback на старое сообщение, если GPT недоступен
            return f"Жёстко иду играть в {game_name}"

        channel_id = channel.id

        # Получаем историю с системными сообщениями
        history = self.conversation_cog.get_channel_history(channel_id)

        # Если истории нет, инициализируем системные сообщения
        if not history:
            self.conversation_cog.ensure_system_messages(channel_id, channel.guild, False)
            history = self.conversation_cog.get_channel_history(channel_id)

        # Формируем информацию об игре и пользователе
        hours = None
        game_description = None
        if game_info:
            hours = game_info.get("playtime_forever", 0) / 60  # Конвертируем минуты в часы
            # Получаем описание игры из Steam Store API
            appid = game_info.get("appid")
            if appid:
                try:
                    game_description = await self.steam_client.get_game_description(appid)
                except Exception:
                    # Если не удалось получить описание, продолжаем без него
                    pass

        user_mention = None
        if discord_user_id:
            user = channel.guild.get_member(discord_user_id)
            if user:
                user_mention = f"<@{discord_user_id}>"

        # Формируем промпт
        # С шансом 10% обосрать вкус игрока
        roast_taste = random.randint(1, 100) <= 10

        # Начинаем с базового промпта
        if game_description:
            game_prompt = f"Напиши сообщение о том, что ты идешь играть в {game_name}. ОБЯЗАТЕЛЬНО опиши игру в своем сообщении, используя эту информацию об игре: {game_description}. Используй описание игры, чтобы рассказать, что это за игра"
        else:
            game_prompt = f"Напиши одно предложение о том, что ты идешь играть в {game_name}"

        if user_mention:
            if hours is not None:
                game_prompt += f". ОБЯЗАТЕЛЬНО упомяни, что ты взял эту игру из библиотеки пользователя. У этого пользователя в этой игре {hours:.1f} часов - ОБЯЗАТЕЛЬНО упомяни это количество часов в своем сообщении. Не говори что ты играл столько часов, говори что у пользователя столько часов"
                if roast_taste:
                    game_prompt += ". ОБЯЗАТЕЛЬНО обосри вкус этого игрока - скажи что-то токсичное и развязное про его выбор игр, высмеяй его вкус"
            else:
                game_prompt += ". Упомяни, что ты взял эту игру из библиотеки пользователя"
                if roast_taste:
                    game_prompt += ". ОБЯЗАТЕЛЬНО обосри вкус этого игрока - скажи что-то токсичное и развязное про его выбор игр, высмеяй его вкус"

            game_prompt += ". НЕ добавляй пинг пользователя в сообщение"

        history_with_prompt = history + [{"role": "user", "content": game_prompt}]

        try:
            # Генерируем ответ через GPT
            content = ""
            async for chunk in self.conversation_cog._chat_completion_with_rotation(
                messages=history_with_prompt
            ):
                content += chunk
                # Ограничиваем длину (примерно до 300 символов, так как может быть пинг)
                if len(content) > 300:
                    # Обрезаем до последнего предложения
                    last_period = content.rfind(".")
                    last_exclamation = content.rfind("!")
                    last_question = content.rfind("?")
                    last_sentence_end = max(last_period, last_exclamation, last_question)
                    if last_sentence_end > 0:
                        content = content[: last_sentence_end + 1]
                    break

            # Убираем лишние пробелы
            content = content.strip()

            # Удаляем все упоминания пользователя из сообщения GPT (если он их добавил)
            if user_mention:
                # Удаляем все упоминания пользователя
                content = content.replace(user_mention, "").strip()
                # Удаляем лишние пробелы
                while "  " in content:
                    content = content.replace("  ", " ")
                # Добавляем пинг один раз в конце
                content += f" {user_mention}"

            return content if content else f"Жёстко иду играть в {game_name}"
        except Exception:
            # Fallback на старое сообщение при ошибке
            fallback = f"Жёстко иду играть в {game_name}"
            if user_mention:
                fallback += f" {user_mention}"
            return fallback
