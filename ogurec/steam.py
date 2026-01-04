
import aiohttp

STEAM_API_URL = "https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/"
STEAM_STORE_API_URL = "https://store.steampowered.com/api/appdetails"


class SteamClientError(Exception):
    pass


class SteamClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.session = aiohttp.ClientSession()

    async def get_owned_games(self, steam_id: str) -> list[dict]:
        """Получить список игр пользователя Steam."""
        params = {"key": self.api_key, "steamid": steam_id, "include_appinfo": 1, "include_played_free_games": 1}

        async with self.session.get(STEAM_API_URL, params=params) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise SteamClientError(f"Steam API error {resp.status}: {text}")

            data = await resp.json()
            return data.get("response", {}).get("games", [])

    async def get_random_game_from_user(self, steam_id: str) -> dict | None:
        """Получить случайную игру из библиотеки пользователя."""

        games = await self.get_owned_games(steam_id)

        if not games:
            return None

        # Фильтруем игры с ненулевым временем игры (playtime_forever в минутах)
        games_with_playtime = [game for game in games if game.get("playtime_forever", 0) > 0]

        if not games_with_playtime:
            return None

        # Выбираем случайную игру
        import random

        return random.choice(games_with_playtime)

    async def get_game_description(self, appid: int) -> str | None:
        """Получить краткое описание игры из Steam Store API."""
        params = {"appids": appid, "l": "russian"}  # l - язык, russian для русского описания

        try:
            async with self.session.get(STEAM_STORE_API_URL, params=params) as resp:
                if resp.status != 200:
                    return None

                data = await resp.json()
                app_data = data.get(str(appid), {})
                
                if not app_data.get("success", False):
                    return None

                game_data = app_data.get("data", {})
                short_description = game_data.get("short_description")
                
                return short_description if short_description else None
        except Exception:
            return None

    async def close(self):
        """Закрыть сессию."""
        await self.session.close()
