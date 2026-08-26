import json
from collections.abc import AsyncIterator

import aiohttp

API_URL = "https://freellmapi.stirk1337.ru/v1/chat/completions"

class GPTClientError(Exception):
    pass

class RateLimitError(GPTClientError):
    """Ошибка превышения лимита запросов (429)."""

class GPTClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.session = aiohttp.ClientSession()
        self.last_model: str | None = None

    async def chat_completion(
        self,
        messages: list[dict],
        model: str = "auto:smart",
        temperature: float = 1.0,
        max_tokens: int = 2048,
        top_p: float = 1.0,
    ) -> AsyncIterator[str]:
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_completion_tokens": max_tokens,
            "top_p": top_p,
            "stream": True,
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        async with self.session.post(API_URL, json=payload, headers=headers) as resp:
            if resp.status != 200:
                text = await resp.text()
                if resp.status in [429, 413, 404]:
                    raise RateLimitError(f"API rate limit exceeded (429): {text}")
                raise GPTClientError(f"API error {resp.status}: {text}")

            async for line in resp.content:
                chunk_text = line.decode().strip()
                if not chunk_text or not chunk_text.startswith("data:"):
                    continue

                try:
                    payload = json.loads(chunk_text[len("data:") :])
                    if payload.get("model"):
                        self.last_model = payload["model"]
                    delta = payload["choices"][0]["delta"]
                    text = delta.get("content")
                    if text:
                        yield text
                except:
                    continue
