import time

import aiosqlite


class GifStorage:
    def __init__(self, path="gifs.db"):
        self.path = path
        self.conn = None

    async def init(self):
        self.conn = await aiosqlite.connect(self.path)

        await self.conn.execute("""
        CREATE TABLE IF NOT EXISTS gifs(
            id INTEGER PRIMARY KEY,
            url TEXT UNIQUE,
            added_at INTEGER
        )
        """)

        await self.conn.commit()

    async def add(self, url: str):
        await self.conn.execute("INSERT OR IGNORE INTO gifs(url, added_at) VALUES(?, ?)", (url, int(time.time())))

    async def cleanup(self):
        week = 7 * 24 * 3600

        await self.conn.execute("DELETE FROM gifs WHERE added_at < ?", (int(time.time()) - week,))

        await self.conn.commit()

    async def random(self):
        await self.cleanup()

        async with self.conn.execute("SELECT url FROM gifs ORDER BY RANDOM() LIMIT 1") as cursor:
            row = await cursor.fetchone()

        return row[0] if row else None
