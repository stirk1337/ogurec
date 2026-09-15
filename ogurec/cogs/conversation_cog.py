import asyncio
import json
import random
from datetime import datetime, timedelta, time
from typing import Any
from datetime import datetime as dt
from collections import defaultdict
import asyncio
from ogurec.utils import TIME_ZONE
import discord
from discord import Message, app_commands
from discord.ext import commands, tasks
from loguru import logger

from ogurec.bot import OgurecBot
from ogurec.chatgpt import GPTClient, RateLimitError
from ogurec.cogs.activity.game_activity_storage_cog import ActivityStorage
from ogurec.cogs.gif_storage_cog import GifStorage
from ogurec.config.settings import Settings
from ogurec.decision import decide_action
from ogurec.memory import UserMemory
from ogurec.search import SearchService
from ogurec.utils import TIME_ZONE, fix_discord_format, get_random_sticker

GIF_RANDOM_RANGE = 600
# история живет дольше тишины, после которой бот пишет сам (proactive_silence_minutes)
HISTORY_TIMEOUT_MINUTES = 60

BOT_MOODS = [
    "сегодня ты злой и цепляешься к словам",
    "сегодня ты ленивый, отвечаешь коротко и нехотя",
    "сегодня ты в духе, подъебываешь по-доброму",
    "сегодня ты гопник, разговариваешь свысока",
    "сегодня тебе на всех насрать, отвечаешь сухо",
]

# примеры вместо списка запретов: стиль модель считывает с них лучше
STYLE_EXAMPLES = """Примеры того, как ты пишешь:
Егор: кто в доту?
Ты: зашел бы, но после твоего прошлого мида я лучше посплю

Слава: народ, комп не включается
Ты: в розетку воткни, гений

Рома: я сегодня 6 часов в алане просидел
Ты: шесть часов и фонарик так и не нашел?

Стирк: го катку
Ты: го, только не ной потом"""


class ConversationCog(commands.Cog):
    def __init__(
        self,
        bot: OgurecBot,
        gpt_client: GPTClient,
        gif_storage: GifStorage,
        settings: Settings,
        activity_storage: ActivityStorage,
        search_service: SearchService | None = None,
        memory: UserMemory | None = None,
    ):
        self.bot = bot
        self.gpt_client = gpt_client

        self.settings = settings
        self.activity_storage = activity_storage
        self.search_service = search_service
        # История разговоров по каналам: {channel_id: {"messages": [...], "last_activity": datetime}}
        self.conversation_history: dict[int, dict[str, Any]] = {}
        # Задачи для сброса истории
        self.reset_tasks: dict[int, asyncio.Task] = {}
        # Текущая игра бота (статус Discord), передаётся в промпт
        self.current_game: str | None = None
        self.gif_storage = gif_storage

        self.channel_locks = defaultdict(asyncio.Lock)
        self.memory = memory

        # сообщения копятся пачкой, бот отвечает один раз на всю пачку
        self.pending: dict[int, list[Message]] = defaultdict(list)
        self.batch_tasks: dict[int, asyncio.Task] = {}
        self.batch_busy: set[int] = set()
        # время последних ответов на канал — для потолка ответов в минуту
        self.reply_times: dict[int, list[datetime]] = defaultdict(list)

        # настроение держится часами, а не выбирается заново на каждый ответ
        self.mood: str = random.choice(BOT_MOODS)
        self.mood_until: datetime = datetime.now() + timedelta(hours=settings.mood_hours)

        self.last_report_date = None
        self.generate_report.start()
        self.proactive_loop.start()

    def _current_mood(self) -> str:
        """Настроение живет несколько часов, а не меняется от сообщения к сообщению."""
        now = datetime.now()
        if now >= self.mood_until:
            self.mood = random.choice(BOT_MOODS)
            self.mood_until = now + timedelta(hours=self.settings.mood_hours)
            logger.info(f"Настроение сменилось: {self.mood} (до {self.mood_until:%H:%M})")
        return self.mood

    @staticmethod
    def _roll(*values: int, max_value: int) -> bool:
        return random.randint(1, max_value) in values

    async def save_gifs(self, message):
        for word in message.content.split():
            if (
                word.startswith("https://klipy.com/gifs/")
                or word.startswith("https://tenor.com/view/")
                or word.startswith("https://cdn.discordapp.com/")
                or word.startswith("https://media.discordapp.net/")
                or word.startswith("https://media.giphy.com/")
                or word.startswith("https://i.giphy.com/")
            ):
                url = word.split("?")[0].rstrip("/")

                await self.gif_storage.add(url)

    def _get_base_system_message(self, include_mood: bool = True, guild_name: str = None) -> dict:
        """Базовое системное сообщение: характер и примеры вместо списка запретов."""
        now = dt.now(TIME_ZONE)

        content = (
            "Ты Ogurec — участник этого Discord чата, а не ассистент. "
            "Пишешь как человек в чате: 1-3 предложения, без списков, заголовков и таблиц, "
            "без вежливых формул и предложений помочь. Токсишь и подъебываешь, но по делу. "
            "Не пересказываешь то, что и так видно в чате, и не объясняешь свои шутки.\n"
            f"{STYLE_EXAMPLES}\n"
        )
        content += f"Сейчас {now:%d.%m.%Y %H:%M}, сервер называется {guild_name}. "
        content += "Это справка — не пересказывай ее без повода. "

        if self.current_game:
            content += f"Ты сейчас играешь в {self.current_game}. "

        if include_mood:
            content += f"Твое настроение: {self._current_mood()}."

        return {"role": "system", "content": content}

    def _format_emoji_for_gpt(self, emoji) -> str:
        """Форматирует эмодзи для GPT в формате Discord."""
        if emoji.animated:
            return f"<a:{emoji.name}:{emoji.id}>"
        else:
            return f"<:{emoji.name}:{emoji.id}>"

    def _get_user_info_for_gpt(self, user, guild=None) -> str:
        """Получить информацию о пользователе для GPT."""
        info_parts = []

        # Основная информация
        info_parts.append(f"Пользователь: {user.display_name} (никнейм: {user.name})")

        # Используем guild.get_member() для получения полной информации об активности
        member = guild.get_member(user.id)

        # Получаем активности из member.activities
        activities = member.activities
        for activity in activities:
            if isinstance(activity, discord.Game):
                info_parts.append(f"Сейчас играет в: {activity.name}")
            elif isinstance(activity, discord.Streaming):
                info_parts.append(
                    f"Стримит на {activity.platform}: название стрима: {activity.name} ссылка на стрим {activity.url}"
                )
            elif isinstance(activity, discord.CustomActivity):
                info_parts.append(f"Кастомный статус: {activity.name}")
            elif isinstance(activity, discord.Spotify):
                info_parts.append(f"Слушает трек Spotify: {activity.title} автора {activity.artist}")

        facts = self.memory.facts(user.id) if self.memory else ""
        if facts:
            info_parts.append("Что ты про него помнишь: " + facts.replace("\n", "; "))

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

            user_info = self._get_user_info_for_gpt(user, message.guild)
            if user_info:
                mentioned_infos.append(user_info)

        if not mentioned_infos:
            return ""

        return "Упомянутые пользователи в сообщении: " + ". ".join(mentioned_infos)

    def _get_emojis_system_message(self, guild) -> dict:
        """Создает системное сообщение со списком доступных эмодзи на сервере."""
        emoji_list = [self._format_emoji_for_gpt(emoji) for emoji in guild.emojis]
        random.shuffle(emoji_list)
        emoji_list = emoji_list[:10]
        emoji_text = ", ".join(emoji_list) if emoji_list else "(на сервере нет кастомных эмодзи)"

        return {
            "role": "system",
            "content": (
                "Обычные эмодзи (Unicode, встроенные в текст) пользователи пишут как есть — это нормально, "
                "не комментируй их как «ошибку» и не говори, что эмодзи «нет на сервере». "
                "Кастомные эмодзи других серверов в чужих сообщениях тебе видны как текст — тоже не выдумывай проверок по списку ниже. "
                "Когда ТЫ вставляешь в ответ кастомные эмодзи именно этого сервера, используй формат <:имя:id> "
                "или <a:имя:id> для анимированных. "
                f"Примеры доступных эмодзи (кастомных) на этом discord сервере — случайная десятка, не полный список: {emoji_text}."
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
                c = msg.get("content", "")
                if "Доступные эмодзи" in c or "случайная десятка, не полный список" in c:
                    has_emojis_system = True

        # Добавляем базовое системное сообщение, если его нет
        if not has_base_system:
            guild_name = guild.name if guild else None
            history.insert(0, self._get_base_system_message(guild_name=guild_name))

        # Добавляем системное сообщение с эмодзи, если это первое пользовательское сообщение
        if not has_emojis_system and guild and is_first_user_message:
            emoji_msg = self._get_emojis_system_message(guild)
            if emoji_msg:
                # Вставляем после базового системного сообщения
                base_index = next(
                    (
                        i
                        for i, msg in enumerate(history)
                        if msg.get("role") == "system" and "Ogurec" in msg.get("content", "")
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

    def _add_user_message(self, channel_id: int, content: str, user_name: str):
        """Добавить сообщение пользователя в историю."""
        history = self._get_channel_history(channel_id)
        history.append({"role": "user", "content": content, "name": user_name})
        self._update_channel_activity(channel_id)

    def _add_assistant_message(self, channel_id: int, content: str):
        """Добавить ответ бота в историю."""
        history = self._get_channel_history(channel_id)
        history.append({"role": "assistant", "content": content})
        self._update_channel_activity(channel_id)

    def add_assistant_message(self, channel_id: int, content: str):
        """Публичный метод для добавления ответа бота в историю."""
        self._add_assistant_message(channel_id, content)

    async def reply_with_gpt(self, message: Message, random_phrase: bool = False):
        """Ответить на сообщение вне общей очереди пачек (ручной вызов)."""
        if message.author.bot or not message.content.strip():
            return

        channel_id = message.channel.id

        async with self.channel_locks[channel_id]:
            await self._reply_with_gpt_locked(message, channel_id, random_phrase)

    def _cooldown_ok(self, channel_id: int) -> bool:
        """Не больше settings.replies_per_minute ответов в минуту на канал."""
        now = datetime.now()
        recent = [t for t in self.reply_times[channel_id] if now - t < timedelta(minutes=1)]
        self.reply_times[channel_id] = recent
        return len(recent) < self.settings.replies_per_minute

    def _recent_lines(self, channel_id: int, limit: int = 12) -> list[str]:
        """Последние реплики канала в виде 'кто: что' — вход для решения о реплике."""
        lines = []
        for msg in self._get_channel_history(channel_id)[-limit:]:
            role = msg.get("role")
            if role == "system":
                continue
            who = "Ogurec" if role == "assistant" else msg.get("name", "кто-то")
            lines.append(f"{who}: {msg.get('content', '')[:200]}")
        return lines

    def _schedule_batch(self, channel_id: int):
        """Перезапускает таймер: отвечаем, когда чат замолчал на reply_debounce_seconds."""
        if channel_id in self.batch_busy:
            return  # ответ уже готовится, новую пачку подхватим после него

        task = self.batch_tasks.get(channel_id)
        if task and not task.done():
            task.cancel()
        self.batch_tasks[channel_id] = asyncio.create_task(self._process_batch(channel_id))

    async def _process_batch(self, channel_id: int):
        """Ждет паузу в чате, потом решает: ответить, реакция или промолчать."""
        try:
            await asyncio.sleep(self.settings.reply_debounce_seconds)
        except asyncio.CancelledError:
            return  # пришло новое сообщение — пачка соберется заново

        batch = self.pending.pop(channel_id, [])
        if not batch:
            return

        self.batch_busy.add(channel_id)
        try:
            await self._handle_batch(channel_id, batch)
        finally:
            self.batch_busy.discard(channel_id)
            if self.pending[channel_id]:
                self._schedule_batch(channel_id)

    async def _handle_batch(self, channel_id: int, batch: list[Message]):
        last = batch[-1]
        mentioned = any(self.bot.user.mentioned_in(m) for m in batch)

        if mentioned:
            action = "reply"
        elif not self._cooldown_ok(channel_id):
            logger.info(f"Потолок ответов в минуту, канал {channel_id}")
            action = "react" if random.randint(1, 100) <= 30 else "skip"
        else:
            action = await decide_action(
                self._recent_lines(channel_id), self.gpt_client, self.settings.fast_model
            )

        try:
            if action == "reply":
                self.reply_times[channel_id].append(datetime.now())
                # пауза "он прочитал и печатает", чтобы ответ не прилетал мгновенно
                await asyncio.sleep(random.uniform(0.5, self.settings.reply_delay_seconds))
                async with self.channel_locks[channel_id]:
                    await self._reply_with_gpt_locked(last, channel_id, random_phrase=not mentioned)
            elif action == "react":
                await self.add_random_reaction(last)
        except Exception:
            logger.exception("Не смог обработать пачку сообщений")

        asyncio.create_task(self.send_random_gif(last))

    async def _reply_with_gpt_locked(self, message: Message, channel_id, random_phrase: bool):
        """
        Отвечает на сообщение пользователя через GPT с эффектом "печатает по частям".
        Запоминает историю разговора и сбрасывает её через час без активности.
        """
        # сообщения уже в истории: их кладет on_message, когда они приходят
        has_user_mention = any(
            not u.bot and u.id != self.bot.user.id
            for u in getattr(message, "mentions", [])
        )
        search_context: str | None = None
        search_query: str | None = None
        if has_user_mention:
            logger.info("search skipped: user mention detected")
        else:
            search_query = await self.search_service.search_query(message.content)
            if search_query:
                try:
                    logger.info(f"search triggered: {search_query}")
                    search_context = await self.search_service.search(search_query)
                    if search_context:
                        logger.info(f"search ok, chars={len(search_context)}")
                    else:
                        logger.info("search returned no results")
                except Exception as e:
                    logger.warning(f"search error: {e}")

        # Получить историю для этого канала с системными сообщениями
        history = self._get_channel_history(channel_id)
        
        # Добавляем информацию об авторе сообщения и упомянутых пользователях в одно сообщение
        author_info = self._get_user_info_for_gpt(message.author, message.guild)
        mentioned_users_info = self._get_mentioned_users_info(message)

        info_parts = []
        if random_phrase:
            info_parts.append(
                "К тебе не обращались — ты сам влезаешь в разговор. Ответь по теме последних сообщений, не принимай их на свой счет."
            )
        elif author_info:
            info_parts.append(
                f"Тебе пишет пользователь: {author_info}. Ты знаешь эту информацию о пользователе, но используй её только иногда, когда это уместно и естественно"
            )
        
        if mentioned_users_info:
            info_parts.append(mentioned_users_info)

        # Собираем messages для GPT: история + временный контекст (в историю не сохраняем)
        messages_for_gpt = list(history)
        if info_parts:
            messages_for_gpt.append({"role": "system", "content": " ".join(info_parts)})
        if search_context:
            search_msg = {
                "role": "system",
                "content": (
                    f"Результаты веб-поиска по запросу \"{search_query}\":\n"
                    f"{search_context}\n"
                    "Используй эту информацию для ответа. Если в результатах нет ответа — честно скажи что не нашел. "
                    "Не выдумывай факты, опирайся на поиск."
                    "Если твой овтет основан на данных поиска, то не пиши, что этот ответ сгенерирован на данных из поиска. Если ответа из поиска не нашлось, то отправь ссылку на ккакой-то из сайтов."
                ),
            }
            messages_for_gpt.append(search_msg)
        
        # Отправляем пустое сообщение-плейсхолдер с ответом на сообщение пользователя
        sent_message = await message.channel.send("💬 ...", reference=message)

        content = ""
        buffer = ""

        try:
            async with message.channel.typing():
                async for chunk in self._chat_completion_with_rotation(messages=messages_for_gpt, channel_id=channel_id):
                    buffer += chunk

                    # Редактируем сообщение раз в N символов, чтобы не спамить
                    if len(buffer) > 50:
                        content += buffer
                        buffer = ""
                        if len(content) > 2000:  # лимит Discord
                            content = content[-2000:]
                        await sent_message.edit(content=fix_discord_format(content, message.guild))

                # Финальный кусок
                if buffer:
                    content += buffer
                    if len(content) > 2000:
                        content = content[-2000:]
                    await sent_message.edit(content=fix_discord_format(content, message.guild))
                
                # Добавить ответ бота в историю
                if content:
                    self._add_assistant_message(channel_id, fix_discord_format(content, message.guild))

                    # С шансом 5% отправить случайный стикер с сервера
                    if message.guild and message.guild.stickers and random.randint(1, 100) <= 25:
                        await message.channel.send(stickers=[get_random_sticker(message.guild)])

        except Exception as e:
            # На случай ошибки
            await sent_message.edit(content=f"Бро, ошибка при генерации ответа: {e}")

    async def send_random_gif(self, message: Message) -> bool:
        if not self._roll(1, 2, max_value=GIF_RANDOM_RANGE):
            return False

        url = await self.gif_storage.random()

        if not url:
            return False

        await asyncio.sleep(random.randint(2, 60))
        await message.reply(url)

        return True

    async def add_random_reaction(self, message: Message):
        if not message.guild or not message.guild.emojis:
            return

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

    async def _chat_completion_with_rotation(
        self,
        messages: list[dict],
        channel_id: int | None = None,
    ):
        """
        Выполняет запрос к GPT.
        """
        max_retries = 20  # Максимальное количество попыток удаления сообщений

        for retry_attempt in range(max_retries):
            logger.info(retry_attempt)
            last_error = None
            e_429 = False
            try:
                async for chunk in self.gpt_client.chat_completion(messages=messages, model="auto:smart"):
                    yield chunk
                # Если дошли сюда, значит запрос успешен
                logger.info(f"Success GPT API request, with model {self.gpt_client.last_model or 'unknown'}")
                return
            except RateLimitError as e:
                # При ошибке 429 удаляем сообщение и повторяем (см. проверку ниже)
                e_429 = True
                last_error = e
                logger.info(f"{e}")
            except Exception as e:
                # При других ошибках считаем, что это не 429
                last_error = e
                logger.info(e)
                logger.exception(f"Non-429 error, {e}")

            if e_429 and last_error:
                if channel_id and self._remove_topmost_non_system_message(channel_id):
                    # Обновляем список сообщений после удаления
                    messages = self._get_channel_history(channel_id)
                    logger.info(f"Retrying after removing message (attempt {retry_attempt + 1})")
                    continue
                else:
                    # Не осталось несистемных сообщений для удаления
                    logger.warning("All models returned 429, but no non-system messages to remove")
                    raise last_error
                
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

        await self.save_gifs(message)

        if not message.content or not message.content.strip():
            return

        channel_id = message.channel.id

        # бот читает чат всегда, даже когда молчит
        history = self._get_channel_history(channel_id)
        is_first_user_message = not any(msg.get("role") == "user" for msg in history)
        self._ensure_system_messages(channel_id, message.guild, is_first_user_message)
        self._add_user_message(channel_id, message.content, message.author.name)

        if self.memory and self.memory.note_message(message.author.id, message.author.name, message.content):
            asyncio.create_task(self.memory.update(message.author.id, message.author.name))

        self.pending[channel_id].append(message)
        self._schedule_batch(channel_id)

    @tasks.loop(minutes=5)
    async def proactive_loop(self):
        """Бот сам пишет в замолчавший чат — по кубику, а не по расписанию."""
        if not self.settings.proactive_enabled:
            return

        now = datetime.now()
        silence = timedelta(minutes=self.settings.proactive_silence_minutes)

        for channel_id, data in list(self.conversation_history.items()):
            try:
                messages = data.get("messages", [])
                if now - data["last_activity"] < silence:
                    continue
                # лезем только в чат, где с ботом реально общались
                if sum(1 for m in messages if m.get("role") == "user") < 5:
                    continue
                # последним говорил бот — не долбить в пустоту
                if next((m for m in reversed(messages) if m.get("role") != "system"), {}).get("role") == "assistant":
                    continue
                if random.randint(1, 100) > self.settings.proactive_chance:
                    continue

                channel = self.bot.get_channel(channel_id)
                if channel is None:
                    continue

                await self._send_proactive(channel, channel_id, int((now - data["last_activity"]).total_seconds() // 60))
            except Exception:
                logger.exception(f"Не смог вбросить сообщение в канал {channel_id}")

    async def _send_proactive(self, channel, channel_id: int, silence_minutes: int):
        """Одна реплика в тишину: по последней теме чата, без приветствий."""
        async with self.channel_locks[channel_id]:
            messages = list(self._get_channel_history(channel_id))
            messages.append({
                "role": "system",
                "content": (
                    f"В чате тишина уже {silence_minutes} минут. Напиши одно короткое сообщение сам: "
                    "подколи по последней теме разговора или спроси что-то по ней. "
                    "Без приветствий, без 'чем могу помочь', не упоминай что было тихо."
                ),
            })

            content = ""
            async for chunk in self._chat_completion_with_rotation(messages=messages, channel_id=channel_id):
                content += chunk

            content = fix_discord_format(content.strip()[:2000], getattr(channel, "guild", None))
            if not content:
                return

            await channel.send(content)
            self._add_assistant_message(channel_id, content)
            self.reply_times[channel_id].append(datetime.now())
            logger.info(f"Проактивное сообщение в канал {channel_id} после {silence_minutes} минут тишины")

    @proactive_loop.before_loop
    async def before_proactive_loop(self):
        await self.bot.wait_until_ready()

    @staticmethod
    def _format_totals(totals: list[tuple[int, str, int, int]]) -> dict[int, list[str]]:
        """Строки отчета собирает код: цифры LLM не трогает вообще."""
        by_user: dict[int, list[str]] = {}
        for user_id, game, seconds, sessions in totals:
            hours = seconds / 3600
            line = f"• {game} — {hours:.1f} ч"
            if sessions > 1:
                line += f" ({sessions} захода)"
            by_user.setdefault(user_id, []).append(line)
        return by_user

    async def _report_comments(self, by_user: dict[int, list[str]], guild) -> dict[int, str]:
        """Просит у модели только по одной подколке на человека, в JSON."""
        facts = "\n\n".join(
            f"user_id {user_id}:\n" + "\n".join(lines) for user_id, lines in by_user.items()
        )
        messages = [
            self._get_base_system_message(guild_name=guild.name if guild else None),
            {
                "role": "user",
                "content": (
                    "Вот за сколько часов кто во что вчера играл:\n\n"
                    f"{facts}\n\n"
                    "Напиши на каждого по одной короткой подколке (до 15 слов), опираясь на его игры. "
                    "Цифры не повторяй — их и так видно. "
                    'Ответ строго в JSON: {"user_id": "подколка"}. Без текста вокруг JSON.'
                ),
            },
        ]

        raw = ""
        async for chunk in self._chat_completion_with_rotation(messages=messages, channel_id=None):
            raw += chunk

        raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning(f"Отчет: модель вернула не JSON: {raw[:200]}")
            return {}
        return {int(k): str(v) for k, v in parsed.items() if str(k).isdigit()}

    @tasks.loop(seconds=30)
    async def generate_report(self):
        """Ежедневный отчет: часы считает sql, LLM добавляет только комментарии."""
        channel = None
        try:
            now = dt.now(TIME_ZONE)

            if now.hour != 6:
                return

            today = now.date()

            if self.last_report_date == today:
                return

            logger.info("генерация отчета")
            channel = self.bot.get_channel(self.settings.main_chat_id)
            if not channel:
                logger.info("Канал для отчета не найден")
                return

            by_user = self._format_totals(await self.activity_storage.activity_totals())
            if not by_user:
                await channel.send("Вчера никто никуда не заходил. Мертвый сервер.")
                self.last_report_date = today
                return

            try:
                comments = await self._report_comments(by_user, channel.guild)
            except Exception as e:
                logger.warning(f"Отчет: не смог получить комментарии: {e}")
                comments = {}

            parts = ["Отчет за вчера:"]
            for user_id, lines in by_user.items():
                block = f"\n<@{user_id}>"
                comment = comments.get(user_id, "").strip()
                if comment:
                    block += f" {fix_discord_format(comment, channel.guild)}"
                parts.append(block + "\n" + "\n".join(lines))

            await channel.send("\n".join(parts)[:2000])
            self.last_report_date = today
        except Exception as e:
            logger.exception(f"Ошибка генерации отчета: {e}")
            if channel:
                await channel.send(f"Ошибка генерации отчета: {e}")

    @generate_report.before_loop 
    async def before_generate_report(self): 
        await self.bot.wait_until_ready()
        logger.info("теперь репорт может быть сгенерен.")

    @generate_report.error 
    async def generate_report_error(self, error): 
        logger.exception(f"генерация репорта крашнулась: {error}")