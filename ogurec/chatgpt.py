import json
from collections.abc import AsyncIterator

import aiohttp

API_URL = "https://api.groq.com/openai/v1/chat/completions"


class GPTClientError(Exception):
    pass


class RateLimitError(GPTClientError):
    """Ошибка превышения лимита запросов (429)."""
    pass


class GPTClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.session = aiohttp.ClientSession()

    async def chat_completion(
        self,
        messages: list[dict],
        model: str = "=",
        temperature: float = 1.0,
        max_tokens: int = 8192,
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

        # Для модели qwen устанавливаем reasoning_effort = "none"
        if "qwen" in model.lower():
            payload["reasoning_effort"] = "none"

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        async with self.session.post(API_URL, json=payload, headers=headers) as resp:
            if resp.status != 200:
                text = await resp.text()
                if resp.status == 429:
                    raise RateLimitError(f"Groq API rate limit exceeded (429): {text}")
                raise GPTClientError(f"Groq API error {resp.status}: {text}")

            async for line in resp.content:
                chunk_text = line.decode().strip()
                if not chunk_text or not chunk_text.startswith("data:"):
                    continue

                try:
                    payload = json.loads(chunk_text[len("data:") :])
                    delta = payload["choices"][0]["delta"]
                    text = delta.get("content")
                    if text:
                        yield text
                except json.JSONDecodeError:
                    continue
