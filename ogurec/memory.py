import time
from collections import defaultdict, deque

import aiosqlite
from loguru import logger

MEMORY_SYSTEM_PROMPT = (
    "Ты ведешь досье на участника Discord чата. Тебе дают текущие заметки о нем и его свежие сообщения. "
    "Верни обновленные заметки: до 8 коротких строк, каждая — один факт о человеке "
    "(что любит, во что играет, как говорит, повторяющиеся шутки, к чему относится болезненно). "
    "Факты только из сообщений, не выдумывай. Старые факты сохраняй, если они не противоречат новым. "
    "Без нумерации, без вступлений, без пояснений — только строки фактов."
)


class UserMemory:
    """Долгая память по пользователям: факты живут в sqlite, обновляются раз в N сообщений."""

    def __init__(
        self,
        path: str = "memory.db",
        gpt_client=None,
        model: str = "auto:fast",
        update_every: int = 15,
        max_chars: int = 700,
        enabled: bool = True,
    ):
        self.path = path
        self.gpt_client = gpt_client
        self.model = model
        self.update_every = update_every
        self.max_chars = max_chars
        self.enabled = enabled
        self.conn = None
        self._cache: dict[int, str] = {}
        self._recent: dict[int, deque] = defaultdict(lambda: deque(maxlen=40))
        self._since_update: dict[int, int] = defaultdict(int)

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
        await self.conn.commit()

        async with self.conn.execute("SELECT user_id, facts FROM user_facts") as cursor:
            async for user_id, facts in cursor:
                self._cache[user_id] = facts or ""

        logger.info(f"Память: загружено досье на {len(self._cache)} пользователей")

    def facts(self, user_id: int) -> str:
        return self._cache.get(user_id, "")

    def note_message(self, user_id: int, name: str, text: str) -> bool:
        """Запоминает сообщение. True — пора обновлять досье."""
        if not self.enabled or not text.strip():
            return False
        self._recent[user_id].append(f"{name}: {text[:300]}")
        self._since_update[user_id] += 1
        if self._since_update[user_id] < self.update_every:
            return False
        # сбрасываем сразу, иначе на каждое следующее сообщение улетит еще одна пересборка
        self._since_update[user_id] = 0
        return True

    async def update(self, user_id: int, name: str):
        """Пересобирает досье пользователя через дешевую модель."""
        if not self.enabled or self.gpt_client is None:
            return
        recent = list(self._recent.get(user_id, ()))
        if not recent:
            return

        messages = [
            {"role": "system", "content": MEMORY_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Текущие заметки о {name}:\n{self.facts(user_id) or '(пусто)'}\n\n"
                    f"Свежие сообщения:\n" + "\n".join(recent)
                ),
            },
        ]
        try:
            result = ""
            async for chunk in self.gpt_client.chat_completion(
                messages, temperature=0.3, max_tokens=300, model=self.model
            ):
                result += chunk
        except Exception as e:
            logger.warning(f"Память: не смог обновить досье {name}: {e}")
            return

        facts = result.strip()[: self.max_chars]
        if not facts:
            return

        self._cache[user_id] = facts
        await self.conn.execute(
            "INSERT INTO user_facts(user_id, name, facts, updated_at) VALUES(?, ?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET name=excluded.name, facts=excluded.facts, updated_at=excluded.updated_at",
            (user_id, name, facts, int(time.time())),
        )
        await self.conn.commit()
        logger.info(f"Память: досье на {name} обновлено ({len(facts)} символов)")
