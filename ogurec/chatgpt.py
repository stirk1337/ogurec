import json
from collections.abc import AsyncIterator

import aiohttp

from ogurec.config.settings import Settings

class GPTClientError(Exception):
    pass

class RateLimitError(GPTClientError):
    """Ошибка превышения лимита запросов (429)."""

class GPTClient:
    def __init__(self, api_key: str, settings: Settings):
        self.api_key = api_key
        self.settings = settings
        self.session = aiohttp.ClientSession()
        self.last_model: str | None = None

    async def chat_completion(
        self,
        messages: list[dict],
        temperature: float = 1.0,
        max_tokens: int = 2048,
        top_p: float = 1.0,
        model: str | None = None,
        
    ) -> AsyncIterator[str]:
        payload = {
            "model": model or self.settings.llm_model,
            "messages": messages,
            "temperature": temperature,
            "max_completion_tokens": max_tokens,
            "top_p": top_p,
            "stream": True,
            "reasoning_effort": "none", # OpenAI o1/o3/gpt5, Groq, Cerebras
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        self.last_model = None
        async with self.session.post(self.settings.api_base_url, json=payload, headers=headers) as resp:
            if resp.status != 200:
                text = await resp.text()
                if resp.status in [429, 413, 404]:
                    raise RateLimitError(f"API rate limit exceeded (429): {text}")
                raise GPTClientError(f"API error {resp.status}: {text}")

            async for line in resp.content:
                chunk_text = line.decode().strip()
                if not chunk_text or not chunk_text.startswith("data:"):
                    continue

                if chunk_text == "data: [DONE]":
                    break
                try:
                    payload = json.loads(chunk_text[len("data:") :])
                    if payload.get("error"):
                        err = payload["error"]
                        msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
                        if "429" in str(err) or "rate" in msg.lower():
                            raise RateLimitError(f"API rate limit in stream: {msg}")
                        raise GPTClientError(f"API error in stream: {msg}")
                    if payload.get("model"):
                        self.last_model = payload["model"]
                    delta = payload["choices"][0]["delta"]
                    text = delta.get("content")
                    if text:
                        yield text
                except (RateLimitError, GPTClientError):
                    raise
                except Exception:
                    continue
