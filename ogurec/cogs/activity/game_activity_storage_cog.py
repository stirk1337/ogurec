import aiosqlite
from loguru import logger


class ActivityStorage:
    def __init__(self, path="activity.db"):
        self.path = path
        self.conn = None

    async def init(self):
        self.conn = await aiosqlite.connect(self.path)

        await self.conn.execute("""
        CREATE TABLE IF NOT EXISTS activity(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            game TEXT NOT NULL,
            started_at INTEGER NOT NULL,
            ended_at INTEGER,
            duration INTEGER
        )
        """)

        await self.conn.commit()

    async def start_game(
        self,
        user_id: int,
        game: str,
        started_at: int,
    ):
        await self.conn.execute(
            """
            INSERT INTO activity(user_id, game, started_at)
            VALUES (?, ?, ?)
            """,
            (user_id, game, started_at),
        )
        await self.conn.commit()

    async def end_game(
        self,
        user_id: int,
        ended_at: int,
    ):
        cursor = await self.conn.execute(
            """
            SELECT id, started_at
            FROM activity
            WHERE user_id = ?
              AND ended_at IS NULL
            ORDER BY started_at DESC
            LIMIT 1
            """,
            (user_id,),
        )

        row = await cursor.fetchone()

        if row is None:
            return

        record_id, started_at = row
        duration = ended_at - started_at

        await self.conn.execute(
            """
            UPDATE activity
            SET ended_at = ?,
                duration = ?
            WHERE id = ?
            """,
            (ended_at, duration, record_id),
        )

        await self.conn.commit()

    async def cleanup(self):
        cursor = await self.conn.execute(
            "SELECT COUNT(*) FROM activity"
        )

        before = await cursor.fetchone()

        await self.conn.execute(
            "DELETE FROM activity"
        )

        await self.conn.commit()

        cursor = await self.conn.execute(
            "SELECT COUNT(*) FROM activity"
        )

        after = await cursor.fetchone()

        logger.info(f"Перед удалением активности: {before[0]}, после удаления активности: {after[0]}")

    async def activity_info(self):
        # время считает sql, а не LLM: иначе модель расписывает сложение секунд прямо в отчете
        # незакрытая сессия (играет до сих пор) считается до текущего момента
        async with self.conn.execute(
            """
            SELECT
                user_id,
                game,
                SUM(COALESCE(duration, CAST(strftime('%s', 'now') AS INTEGER) - started_at)) AS total
            FROM activity
            GROUP BY user_id, game
            ORDER BY user_id, total DESC
            """
        ) as cursor:
            rows = await cursor.fetchall()

        by_user: dict[int, list[tuple[str, int]]] = {}
        for user_id, game, total in rows:
            by_user.setdefault(user_id, []).append((game, total))

        lines = []
        for user_id, games in by_user.items():
            lines.append(f"<@{user_id}> — всего {_human_duration(sum(t for _, t in games))}")
            lines.extend(f"- {game} — {_human_duration(total)}" for game, total in games)

        if by_user:
            lines.append(f"Всего на сервере: {_human_duration(sum(t for _, _, t in rows))}")

        return "\n".join(lines)


def _human_duration(seconds: int) -> str:
    if seconds < 60:
        return "меньше минуты"
    hours, minutes = divmod(seconds // 60, 60)
    if not hours:
        return f"{minutes} мин"
    return f"{hours} ч {minutes} мин" if minutes else f"{hours} ч"
