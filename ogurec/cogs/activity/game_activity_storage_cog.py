from datetime import datetime

import aiosqlite


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
        now = datetime.now()

        # Удалять записи только после 06:00
        if now.hour < 6:
            return

        today_6am = now.replace(
            hour=6,
            minute=0,
            second=0,
            microsecond=0,
        )

        await self.conn.execute(
            """
            DELETE FROM activity
            WHERE started_at < ?
            """,
            (int(today_6am.timestamp()),),
        )

        await self.conn.commit()

    async def activity_info(self):
        async with self.conn.execute(
            """
            SELECT
                user_id,
                game,
                started_at,
                ended_at,
                duration
            FROM activity
            ORDER BY started_at
            """
        ) as cursor:
            rows = await cursor.fetchall()

        content = ""

        for user_id, game, started_at, ended_at, duration in rows:
            content += (
                f"Пользователь {user_id} играл в {game}. "
                f"Начал: {started_at}. "
                f"Закончил: {ended_at}. "
                f"Длительность: {duration} сек.\n"
            )

        return content
