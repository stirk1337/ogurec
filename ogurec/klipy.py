import aiohttp
from urllib.parse import quote


KLIPY_API_URL = "https://api.klipy.com/api/v1/{app_key}/gifs/search"


class KlipyClientError(Exception):
    pass


class KlipyClient:
    def __init__(self, app_key: str, customer_id: str):
        self.app_key = app_key
        self.customer_id = customer_id
        self.session = aiohttp.ClientSession()

    async def get_first_gif_url(self, query: str) -> str:
        url = KLIPY_API_URL.format(
            app_key=self.app_key
        )

        params = {
            "q": query,
            "page": 1,
            "per_page": 1,
            "customer_id": self.customer_id,
            "locale": "en",
            "content_filter": "medium",
        }

        async with self.session.get(url, params=params) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise KlipyClientError(
                    f"Klipy API error {resp.status}: {text}"
                )

            data = await resp.json()

        try:
            return data["data"]["data"][0]["file"]["hd"]["gif"]["url"]

        except (KeyError, IndexError, TypeError) as e:
            raise KlipyClientError(
                f"GIF not found: {data}"
            ) from e