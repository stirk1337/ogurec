import asyncio
import random
from datetime import datetime, timedelta
from typing import Any

import discord
from discord import Message, app_commands
from discord.ext import commands
from loguru import logger

from ogurec.bot import OgurecBot
from ogurec.chatgpt import GPTClient, RateLimitError
from ogurec.utils import get_random_sticker

MESSAGE_RANDOM_RANGE = 450
REACTION_RANDOM_RANGE = 650
MESSAGE_GUARANTEE_LIMIT = 750
HISTORY_TIMEOUT_MINUTES = 10

BOT_MOODS = [
    "Пиши как гопник",
    "Пиши с жестким негативом",
    "Пиши как гопник с жестким негативом",
    "Пиши с негативом",
    "Пиши как агресивный гопник",
]

# Список моделей для ротации при ошибке 429 (в порядке приоритета)
MODEL_ROTATION = [
    "qwen/qwen3-32b",  # qwen
    "openai/gpt-oss-120b",  # chatgpt
    "openai/gpt-oss-20b",
    "openai/gpt-oss-safeguard-20b",
    "llama-3.1-8b-instant",
    "llama-3.3-70b-versatile",
    "meta-llama/llama-4-maverick-17b-128e-instruct",
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "moonshotai/kimi-k2-instruct",
    "moonshotai/kimi-k2-instruct-0905",
]


class ConversationCog(commands.Cog):
    def __init__(self, bot: OgurecBot, gpt_client: GPTClient):
        self.bot = bot
        self.message_counter = 0
        self.gpt_client = gpt_client
        # История разговоров по каналам: {channel_id: {"messages": [...], "last_activity": datetime}}
        self.conversation_history: dict[int, dict[str, Any]] = {}
        # Задачи для сброса истории
        self.reset_tasks: dict[int, asyncio.Task] = {}
        # Текущая игра бота
        self.current_game: str | None = None

    @staticmethod
    def _roll(*values: int, max_value: int) -> bool:
        return random.randint(1, max_value) in values

    def _get_base_system_message(self, include_mood: bool = False, guild_name: str = None) -> dict:
        """Базовое системное сообщение, которое всегда должно быть в начале истории."""
        from datetime import datetime as dt
        from ogurec.utils import TIME_ZONE
        
        now = dt.now(TIME_ZONE)
        current_date = now.strftime("%d.%m.%Y %H:%M")
        
        content = "Ты Discord бот по имени Ogurec. Ты пишешь от 1 до 10 предложений за 1 ответ. "
        content += f"Текущая дата и время: {current_date}. "

        content += f"Название сервера: {guild_name}. "
        
        # Получаем информацию об игре, в которую играет бот (только при создании истории)
        if self.current_game:
            content += f"Сейчас ты играешь в: {self.current_game}. "
        
        content += "Используй информацию о сервере только когда это уместно и естественно."

        if include_mood:
            mood = random.choice(BOT_MOODS)
            content += f" {mood}."

        return {"role": "system", "content": content}

    def _format_emoji_for_gpt(self, emoji) -> str:
        """Форматирует эмодзи для GPT в формате Discord."""
        if emoji.animated:
            return f"<a:{emoji.name}:{emoji.id}>"
        else:
            return f"<:{emoji.name}:{emoji.id}>"

    def _get_user_info_for_gpt(self, user) -> str:
        """Получить информацию о пользователе для GPT."""
        if not user:
            return ""
        
        info_parts = []
        
        # Основная информация
        info_parts.append(f"Пользователь: {user.display_name} (никнейм: {user.name})")
        
        # Роли пользователя (кроме @everyone)
        roles = [role.name for role in user.roles if role.name != "@everyone" and not role.is_bot_managed()]
        if roles:
            info_parts.append(f"Роли: {', '.join(roles)}")
        
        # Активность пользователя (играет, стримит и т.д.)
        if user.activity:
            if isinstance(user.activity, discord.Game):
                info_parts.append(f"Сейчас играет в: {user.activity.name}")
            elif isinstance(user.activity, discord.Streaming):
                info_parts.append(f"Стримит: {user.activity.name}")
            elif isinstance(user.activity, discord.CustomActivity):
                info_parts.append(f"Кастомный статус: {user.activity.name}")
        
        return ". ".join(info_parts)
    
    def _get_mentioned_users_info(self, message: Message) -> str:
        """Получить информацию о всех упомянутых пользователях в сообщении."""
        if not message.guild or not message.mentions:
            return ""
        
        mentioned_infos = []
        for user in message.mentions:
            # Пропускаем ботов и самого бота
            if user.bot or user.id == self.bot.user.id:
                continue
            
            user_info = self._get_user_info_for_gpt(user)
            if user_info:
                mentioned_infos.append(user_info)
        
        if not mentioned_infos:
            return ""
        
        return "Упомянутые пользователи в сообщении: " + ". ".join(mentioned_infos)

    def _get_emojis_system_message(self, guild) -> dict:
        """Создает системное сообщение со списком доступных эмодзи на сервере."""
        if not guild or not guild.emojis:
            return None

        emoji_list = [self._format_emoji_for_gpt(emoji) for emoji in guild.emojis]
        emoji_text = ", ".join(emoji_list)

        return {
            "role": "system",
            "content": (
                f"Доступные эмодзи на этом сервере: {emoji_text}. "
            ),
        }

    def _get_channel_history(self, channel_id: int) -> list[dict]:
        """Получить историю разговора для канала."""
        if channel_id not in self.conversation_history:
            self.conversation_history[channel_id] = {"messages": [], "last_activity": datetime.now()}
        return self.conversation_history[channel_id]["messages"]

    def get_channel_history(self, channel_id: int) -> list[dict]:
        """Публичный метод для получения истории разговора для канала."""
        return self._get_channel_history(channel_id)

    def ensure_system_messages(self, channel_id: int, guild, is_first_user_message: bool = False) -> None:
        """Публичный метод для инициализации системных сообщений."""
        self._ensure_system_messages(channel_id, guild, is_first_user_message)

    def _ensure_system_messages(self, channel_id: int, guild, is_first_user_message: bool = False) -> None:
        """Убедиться, что в истории есть необходимые системные сообщения."""
        history = self._get_channel_history(channel_id)

        # Проверяем, есть ли уже системные сообщения
        has_base_system = False
        has_emojis_system = False

        for msg in history:
            if msg.get("role") == "system":
                if "Ogurec" in msg.get("content", "") or "Ogurec Bot" in msg.get("content", ""):
                    has_base_system = True
                if "Доступные эмодзи" in msg.get("content", ""):
                    has_emojis_system = True

        # Добавляем базовое системное сообщение, если его нет
        if not has_base_system:
            # 30% шанс выбрать случайное поведение
            include_mood = random.randint(1, 100) <= 30
            # Информация о сервере всегда передается (дата и название)
            guild_name = guild.name if guild else None
            history.insert(0, self._get_base_system_message(include_mood=include_mood, guild_name=guild_name))

        # Добавляем системное сообщение с эмодзи, если это первое пользовательское сообщение
        if not has_emojis_system and guild and is_first_user_message:
            emoji_msg = self._get_emojis_system_message(guild)
            if emoji_msg:
                # Вставляем после базового системного сообщения
                base_index = next(
                    (
                        i
                        for i, msg in enumerate(history)
                        if msg.get("role") == "system" and "Ogurec Bot" in msg.get("content", "")
                    ),
                    len(history),
                )
                history.insert(base_index + 1, emoji_msg)

    def _get_messages_for_gpt(self, channel_id: int, guild, is_first_user_message: bool = False) -> list[dict]:
        """Получить список сообщений для GPT с системными сообщениями в начале."""
        # Убеждаемся, что системные сообщения есть в истории
        self._ensure_system_messages(channel_id, guild, is_first_user_message)

        # Возвращаем всю историю (системные сообщения уже там)
        return self._get_channel_history(channel_id)

    def _update_channel_activity(self, channel_id: int):
        """Обновить время последней активности и отменить задачу сброса."""
        if channel_id not in self.conversation_history:
            self.conversation_history[channel_id] = {"messages": [], "last_activity": datetime.now()}
        else:
            self.conversation_history[channel_id]["last_activity"] = datetime.now()

        # Отменить предыдущую задачу сброса, если она есть
        if channel_id in self.reset_tasks:
            self.reset_tasks[channel_id].cancel()

        # Создать новую задачу для сброса через 10 минут
        self.reset_tasks[channel_id] = asyncio.create_task(self._reset_history_after_timeout(channel_id))

    async def _reset_history_after_timeout(self, channel_id: int):
        """Сбросить историю разговора через 10 минут без активности."""
        try:
            await asyncio.sleep(HISTORY_TIMEOUT_MINUTES * 60)  # 10 минут в секундах

            # Проверить, что прошло 10 минут с последней активности
            if channel_id in self.conversation_history:
                last_activity = self.conversation_history[channel_id]["last_activity"]
                if datetime.now() - last_activity >= timedelta(minutes=HISTORY_TIMEOUT_MINUTES):
                    del self.conversation_history[channel_id]
                    if channel_id in self.reset_tasks:
                        del self.reset_tasks[channel_id]
        except asyncio.CancelledError:
            # Задача была отменена из-за новой активности - это нормально
            pass

    def _add_user_message(self, channel_id: int, content: str):
        """Добавить сообщение пользователя в историю."""
        history = self._get_channel_history(channel_id)
        history.append({"role": "user", "content": content})
        self._update_channel_activity(channel_id)

    def _add_assistant_message(self, channel_id: int, content: str):
        """Добавить ответ бота в историю."""
        history = self._get_channel_history(channel_id)
        history.append({"role": "assistant", "content": content})
        self._update_channel_activity(channel_id)

    def add_assistant_message(self, channel_id: int, content: str):
        """Публичный метод для добавления ответа бота в историю."""
        self._add_assistant_message(channel_id, content)

    async def reply_with_gpt(self, message: Message):
        """
        Отвечает на сообщение пользователя через GPT с эффектом "печатает по частям".
        Запоминает историю разговора и сбрасывает её через час без активности.
        """
        if message.author.bot or not message.content.strip():
            return

        channel_id = message.channel.id

        # Проверяем, будет ли это первое пользовательское сообщение (до добавления текущего)
        history_before = self._get_channel_history(channel_id)
        user_messages_count = sum(1 for msg in history_before if msg.get("role") == "user")
        is_first_user_message = user_messages_count == 0

        # Убеждаемся, что системные сообщения есть (включая эмодзи, если это первое сообщение)
        self._ensure_system_messages(channel_id, message.guild, is_first_user_message)

        # Добавить сообщение пользователя в историю
        self._add_user_message(channel_id, message.content)

        # Получить историю для этого канала с системными сообщениями
        history = self._get_channel_history(channel_id)
        
        # Добавляем информацию об авторе сообщения и упомянутых пользователях в одно сообщение
        author_info = self._get_user_info_for_gpt(message.author)
        mentioned_users_info = self._get_mentioned_users_info(message)
        
        info_parts = []
        if author_info:
            info_parts.append(f"Тебе пишет пользователь: {author_info}. Ты знаешь эту информацию о пользователе, но используй её только иногда, когда это уместно и естественно")
        if mentioned_users_info:
            info_parts.append(mentioned_users_info)
        
        if info_parts:
            combined_info_message = {"role": "user", "content": " ".join(info_parts)}
            # Вставляем перед последним сообщением пользователя
            history.insert(-1, combined_info_message)

        # Отправляем пустое сообщение-плейсхолдер с ответом на сообщение пользователя
        sent_message = await message.channel.send("💬 ...", reference=message)

        content = ""
        buffer = ""

        try:
            async with message.channel.typing():
                async for chunk in self._chat_completion_with_rotation(messages=history, channel_id=channel_id):
                    buffer += chunk

                    # Редактируем сообщение раз в N символов, чтобы не спамить
                    if len(buffer) > 50:
                        content += buffer
                        buffer = ""
                        if len(content) > 2000:  # лимит Discord
                            content = content[-2000:]
                        await sent_message.edit(content=content)

                # Финальный кусок
                if buffer:
                    content += buffer
                    if len(content) > 2000:
                        content = content[-2000:]
                    await sent_message.edit(content=content)

                # Добавить ответ бота в историю
                if content:
                    self._add_assistant_message(channel_id, content)

                    # С шансом 5% отправить случайный стикер с сервера
                    if message.guild and message.guild.stickers and random.randint(1, 100) <= 25:
                        await message.channel.send(stickers=[get_random_sticker(message.guild)])

        except Exception as e:
            # На случай ошибки
            await sent_message.edit(content=f"Бро, ошибка при генерации ответа: {e}")

    async def reply_to_question(self, message: Message) -> bool:
        if self.bot.user.mentioned_in(message) and message.content and message.content[-1] in {"?", "!", "."}:
            await self.reply_with_gpt(message)
            return True
        return False

    async def send_random_phrase(self, message: Message) -> bool:
        if self._roll(1, 2, max_value=MESSAGE_RANDOM_RANGE):
            await self.reply_with_gpt(message)
            return True
        return False

    async def reply_to_ping(self, message: Message) -> bool:
        if not self.bot.user.mentioned_in(message):
            return False

        if not message.guild:
            return False

        await self.reply_with_gpt(message)

        return True

    async def send_random_content(
        self,
        message: Message,
        *,
        emoji: bool,
    ) -> bool:
        trigger = self._roll(1, 2, max_value=MESSAGE_RANDOM_RANGE) or self.message_counter >= MESSAGE_GUARANTEE_LIMIT

        if not trigger or self.bot.user.mentioned_in(message):
            return False

        if not message.guild:
            return False

        self.message_counter = 0
        await self.reply_with_gpt(message)

        return True

    async def add_random_reaction(self, message: Message):
        if not message.guild or not message.guild.emojis:
            return

        value = random.randint(1, REACTION_RANDOM_RANGE)
        if 3 <= value <= 10:
            await asyncio.sleep(random.randint(1, 4))
            await message.add_reaction(random.choice(message.guild.emojis))

    def _remove_topmost_non_system_message(self, channel_id: int) -> bool:
        """
        Удаляет самое верхнее несистемное сообщение из истории чата.
        Возвращает True, если сообщение было удалено, False если несистемных сообщений не осталось.
        """
        history = self._get_channel_history(channel_id)
        
        # Ищем первое несистемное сообщение
        for i, msg in enumerate(history):
            if msg.get("role") != "system":
                history.pop(i)
                logger.info(f"Removed topmost non-system message from history (channel {channel_id})")
                return True
        
        # Если несистемных сообщений нет
        return False

    async def _chat_completion_with_rotation(self, messages: list[dict], channel_id: int):
        """
        Выполняет запрос к GPT с ротацией моделей при ошибке 429.
        Пытается использовать модели из MODEL_ROTATION по очереди.
        Если все модели вернули 429, удаляет верхнее несистемное сообщение и повторяет попытку.
        """
        max_retries = 20  # Максимальное количество попыток удаления сообщений
        
        for retry_attempt in range(max_retries):
            last_error = None
            all_429 = True  # Флаг, что все модели вернули 429

            for model in MODEL_ROTATION:
                try:
                    async for chunk in self.gpt_client.chat_completion(messages=messages, model=model):
                        yield chunk
                    # Если дошли сюда, значит запрос успешен
                    logger.info(f"Success GPT API request with model {model}")
                    return
                except RateLimitError as e:
                    # При ошибке 429 пробуем следующую модель
                    last_error = e
                    continue
                except Exception as e:
                    # При других ошибках считаем, что не все модели вернули 429
                    all_429 = False
                    last_error = e
                    logger.exception(f"Non-429 error with model {model}")
                    continue
            
            # Если все модели вернули 429, удаляем верхнее несистемное сообщение и повторяем
            if all_429 and last_error:
                if self._remove_topmost_non_system_message(channel_id):
                    # Обновляем список сообщений после удаления
                    messages = self._get_channel_history(channel_id)
                    logger.info(f"Retrying after removing message (attempt {retry_attempt + 1})")
                    continue
                else:
                    # Не осталось несистемных сообщений для удаления
                    logger.warning("All models returned 429, but no non-system messages to remove")
                    raise last_error
            
            # Если не все модели вернули 429 или это не 429 ошибка, пробрасываем
            if last_error:
                raise last_error
        
        # Если превысили максимальное количество попыток
        if last_error:
            raise last_error
        raise Exception("Max retries exceeded without success")

    @app_commands.command(description="Сбросить историю чата для этого канала")
    async def reset_history(self, interaction: discord.Interaction):
        """Сбросить историю разговора для текущего канала."""
        channel_id = interaction.channel.id

        # Удаляем историю
        if channel_id in self.conversation_history:
            del self.conversation_history[channel_id]

        # Отменяем задачу сброса, если она есть
        if channel_id in self.reset_tasks:
            self.reset_tasks[channel_id].cancel()
            del self.reset_tasks[channel_id]

        await interaction.response.send_message("✅ История чата сброшена!", ephemeral=True)

    @commands.Cog.listener()
    async def on_message(self, message: Message):
        if message.author.bot:
            return

        # Обновить активность канала при любом сообщении (для сброса таймера)
        if message.content and message.content.strip():
            channel_id = message.channel.id
            self._update_channel_activity(channel_id)

        handlers = (
            self.reply_to_question,
            self.send_random_phrase,
            self.reply_to_ping,
            lambda m: self.send_random_content(m, emoji=False),
            lambda m: self.send_random_content(m, emoji=True),
        )

        for handler in handlers:
            if await handler(message):
                return

        await self.add_random_reaction(message)
        self.message_counter += 1
