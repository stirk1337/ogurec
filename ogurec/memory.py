import asyncio
import time
from collections import defaultdict

import aiosqlite
from loguru import logger

MEMORY_SYSTEM_PROMPT = (
    "Ты ведешь досье на участника Discord чата. Тебе дают текущие заметки о нем и его сообщения за сутки. "
    "Верни обновленные заметки: до 10 коротких строк, каждая — один факт о человеке "
    "(что любит, во что играет, как говорит, повторяющиеся шутки, к чему относится болезненно). "
    "Факты только из сообщений, не выдумывай. Старые факты переноси как есть — удаляй только те, "
    "которым свежие сообщения прямо противоречат. "
    "Без нумерации, без вступлений, без пояснений — только строки фактов."
)


class UserMemory:
    """Память по пользователям: сообщения копятся в sqlite, досье пересобирается раз в сутки."""

    def __init__(
        self,
        path: str = "memory.db",
        gpt_client=None,
        model: str = "auto:fast",
        burst_messages: int = 200,
        history_limit: int = 300,
        keep_days: int = 30,
        max_chars: int = 900,
        enabled: bool = True,
    ):
        self.path = path
        self.gpt_client = gpt_client
        self.model = model
        self.burst_messages = burst_messages
        self.history_limit = history_limit
        self.keep_days = keep_days
        self.max_chars = max_chars
        self.enabled = enabled
        self.conn = None
        self._cache: dict[int, str] = {}
        # сообщений с последней пересборки — только для внеочередной пересборки болтунов
        self._since_rebuild: dict[int, int] = defaultdict(int)

    async def init(self):
        self.conn = await aiosqlite.connect(self.path)

        await self.conn.execute("""
        CREATE TABLE IF NOT EXISTS user_facts(
            user_id INTEGER PRIMARY KEY,
            name TEXT,
            facts TEXT,
            updated_at INTEGER
        )
        """)
        await self.conn.execute("""
        CREATE TABLE IF NOT EXISTS messages(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT,
            channel_id INTEGER,
            content TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            discord_id INTEGER
        )
        """)
        async with self.conn.execute("PRAGMA table_info(messages)") as cursor:
            columns = {row[1] for row in await cursor.fetchall()}
        if "discord_id" not in columns:
            await self.conn.execute("ALTER TABLE messages ADD COLUMN discord_id INTEGER")
        await self.conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_discord_id ON messages(discord_id) WHERE discord_id IS NOT NULL"
        )
        await self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_user_time ON messages(user_id, created_at)"
        )
        await self.conn.commit()

        async with self.conn.execute("SELECT user_id, facts FROM user_facts") as cursor:
            async for user_id, facts in cursor:
                self._cache[user_id] = facts or ""

        logger.info(f"Память: загружено досье на {len(self._cache)} пользователей")

    def facts(self, user_id: int) -> str:
        return self._cache.get(user_id, "")

    async def _store_message(
        self,
        user_id: int,
        name: str,
        channel_id: int,
        text: str,
        *,
        discord_id: int | None = None,
        created_at: int | None = None,
    ) -> bool:
        if not self.enabled or not text.strip():
            return False

        cursor = await self.conn.execute(
            "INSERT OR IGNORE INTO messages(user_id, name, channel_id, content, created_at, discord_id) "
            "VALUES(?, ?, ?, ?, ?, ?)",
            (user_id, name, channel_id, text[:500], created_at or int(time.time()), discord_id),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def note_message(
        self,
        user_id: int,
        name: str,
        channel_id: int,
        text: str,
        *,
        discord_id: int | None = None,
        created_at: int | None = None,
    ) -> bool:
        """Кладет сообщение в индекс. True — человек разошелся, досье стоит пересобрать не дожидаясь ночи."""
        if not await self._store_message(
            user_id, name, channel_id, text, discord_id=discord_id, created_at=created_at
        ):
            return False

        self._since_rebuild[user_id] += 1
        if self._since_rebuild[user_id] < self.burst_messages:
            return False

        self._since_rebuild[user_id] = 0
        return True

    async def ingest_messages(self, messages) -> int:
        """Пишет чужие текстовые сообщения в индекс. Не запускает внеочередную пересборку."""
        added = 0
        async for message in _as_async_iter(messages):
            author = message.author
            if getattr(author, "bot", False):
                continue
            text = getattr(message, "content", "") or ""
            if not text.strip():
                continue
            channel = getattr(message, "channel", None)
            created = getattr(message, "created_at", None)
            created_at = int(created.timestamp()) if created is not None else int(time.time())
            if await self._store_message(
                author.id,
                author.name,
                getattr(channel, "id", 0) or 0,
                text,
                discord_id=getattr(message, "id", None),
                created_at=created_at,
            ):
                added += 1
        return added

    async def rebuild(self, user_id: int, name: str):
        """Пересобирает досье одного человека по его последним сообщениям."""
        if not self.enabled or self.gpt_client is None:
            return

        async with self.conn.execute(
            "SELECT content FROM messages WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
            (user_id, self.history_limit),
        ) as cursor:
            rows = await cursor.fetchall()

        if not rows:
            return

        recent = "\n".join(row[0] for row in reversed(rows))
        messages = [
            {"role": "system", "content": MEMORY_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Текущие заметки о {name}:\n{self.facts(user_id) or '(пусто)'}\n\n"
                    f"Его сообщения:\n{recent}"
                ),
            },
        ]

        # пул провайдеров флапает, а пересборка раз в сутки — вторая попытка дешевле пропуска
        for attempt in range(2):
            try:
                result = ""
                async for chunk in self.gpt_client.chat_completion(
                    messages, temperature=0.3, max_tokens=400, model=self.model
                ):
                    result += chunk
                break
            except Exception as e:
                logger.warning(f"Память: не смог обновить досье {name} (попытка {attempt + 1}): {e}")
                result = ""
                await asyncio.sleep(2)

        facts = result.strip()[: self.max_chars]
        if not facts:
            return

        self._cache[user_id] = facts
        self._since_rebuild[user_id] = 0
        await self.conn.execute(
            "INSERT INTO user_facts(user_id, name, facts, updated_at) VALUES(?, ?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET name=excluded.name, facts=excluded.facts, updated_at=excluded.updated_at",
            (user_id, name, facts, int(time.time())),
        )
        await self.conn.commit()
        logger.info(f"Память: досье на {name} обновлено ({len(facts)} символов)")

    async def rebuild_all(self, since_hours: int | None = 24):
        """Пересборка досье. since_hours=None — все, у кого есть сообщения в индексе."""
        if not self.enabled:
            return

        if since_hours is None:
            query = "SELECT user_id, MAX(name) FROM messages GROUP BY user_id"
            params: tuple = ()
        else:
            query = "SELECT user_id, MAX(name) FROM messages WHERE created_at > ? GROUP BY user_id"
            params = (int(time.time()) - since_hours * 3600,)

        async with self.conn.execute(query, params) as cursor:
            users = await cursor.fetchall()
        async with self.conn.execute("SELECT COUNT(*), COUNT(DISTINCT user_id) FROM messages") as cursor:
            total, people = await cursor.fetchone()

        logger.info(
            f"Память: пересборка досье, людей {len(users)} (в индексе {people} людей, {total} сообщений)"
        )
        for user_id, name in users:
            await self.rebuild(user_id, name or str(user_id))
            await asyncio.sleep(5)  # не долбить пул провайдеров очередью подряд

    async def cleanup(self):
        """Индекс сообщений — не архив переписки: старое удаляем."""
        cutoff = int(time.time()) - self.keep_days * 24 * 3600
        cursor = await self.conn.execute("DELETE FROM messages WHERE created_at < ?", (cutoff,))
        await self.conn.commit()
        if cursor.rowcount:
            logger.info(f"Память: удалено {cursor.rowcount} сообщений старше {self.keep_days} дней")


async def _as_async_iter(messages):
    if hasattr(messages, "__aiter__"):
        async for message in messages:
            yield message
        return
    for message in messages:
        yield message
