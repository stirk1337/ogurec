import asyncio
import json
import re
import time
import urllib.parse
import urllib.request
from typing import Optional

from loguru import logger

SEARXNG_BASE = "https://searxng.stirk1337.ru"
SEARXNG_TIMEOUT = 15

VIDEO_BLOCKLIST = (
    "youtube.com", "youtu.be", "tiktok.com", "instagram.com", "vimeo.com",
    "dailymotion.com", "twitch.tv", "rutube.ru", "bilibili.com", "coub.com",
)

# Системное сообщение для LLM-классификатора (используется в is_question_llm)
LLM_SEARCH_SYSTEM_PROMPT = (
    "Ты классификатор вопросов. Прочитав сообщение пользователя, определи является ли оно вопросом. "
    "Если это вопрос — верни только одну букву Y. Если это не вопрос — верни только одну букву N."
    "Если вопрос адресован боту и касается его состояния/действий (например: \"как дела\", \"что делаешь\", "
    "\"чем занят\", \"как ты\", \"что делаешь сейчас\", \"как настроение\", \"как поживаешь\") — верни N, поиск не нужен."
    "Отвечай строго одной буквой Y или N без пояснений, пробелов и переносов."
)

async def is_question_llm(text: str, gpt_client) -> str:
    """
    Одна функция которая определяет нужен ли поиск через chat_completion (auto:fast, temperature=0).
    Возвращает 'Y' если нужен поиск (вопрос), 'N' если нет.
    Использует системное сообщение LLM_SEARCH_SYSTEM_PROMPT.
    """
    if not text or not text.strip():
        return False
    if gpt_client is None:
        return False
    messages = [
        {"role": "system", "content": LLM_SEARCH_SYSTEM_PROMPT},
        {"role": "user", "content": text[:500]},
    ]
    try:
        result = ""
        async for chunk in gpt_client.chat_completion(messages, temperature=0, max_tokens=5, model="auto:fast"):
            result += chunk
        cleaned = result.strip().upper()
        logger.info(result)
        if not cleaned:
            return False
        return True if cleaned[0] == "Y" else False
    except Exception as e:
        logger.warning(f"is_question_llm error: {e}")
        return False

def _is_blocked_url(url: str) -> bool:
    u = url.lower()
    parsed = urllib.parse.urlparse(u)
    domain = parsed.netloc
    if any(d in u for d in VIDEO_BLOCKLIST):
        return True
    if any(u.endswith(ext) for ext in (".mp4", ".avi", ".mov", ".webm", ".mkv")):
        return True
    if "video" in u:
        return True
    if domain.endswith(".ua") or ".ua:" in domain:
        return True
    return False

def _searxng_fetch(query: str) -> list[dict]:
    params = urllib.parse.urlencode({"q": query, "format": "json", "language": "auto", "safesearch": 0})
    url = f"{SEARXNG_BASE.rstrip('/')}/search?{params}"
    with urllib.request.urlopen(url, timeout=SEARXNG_TIMEOUT) as resp:
        if resp.status != 200:
            raise RuntimeError(f"SearXNG {resp.status}")
        data = json.load(resp)
        return data.get("results", [])

def _searxng_search_sync(query: str, max_results: int = 5) -> Optional[str]:
    last_err = None
    for attempt in range(2):
        try:
            results = _searxng_fetch(query)
            logger.info(f"SearXNG резов={len(results)} для '{query[:60]}'")
            if not results:
                logger.info(f"SearXNG нет резов '{query}'")
                return None
            filtered = [r for r in results if not _is_blocked_url(r.get("url", ""))]
            logger.info(f"SearXNG после фильтрации={len(filtered)} for '{query[:40]}'")
            filtered = filtered[:max_results]
            if not filtered:
                logger.info("SearXNG весь рез отфильтрован")
                return None
            parts: list[str] = []
            for r in filtered:
                title = r.get("title", "").strip() or "Без названия"
                href = r.get("url", "").strip()
                content = r.get("content", "").strip() or r.get("snippet", "").strip()
                if not content:
                    continue
                parts.append(f"{title}\n{content}\n{href}")
            if not parts:
                return None
            return "\n\n---\n\n".join(parts)
        except Exception as e:
            last_err = e
            msg = str(e).lower()
            if "403" in msg or "429" in msg or "too many" in msg:
                logger.warning(f"SearXNG 403/429 '{query[:30]}' на попытке {attempt+1}")
                time.sleep(2 + attempt)
                continue
            logger.warning(f"SearXNG не прошел '{query[:30]}': {e}")
            time.sleep(0.5)
            break
    if last_err:
        logger.warning(f"SearXNG не прошел '{query}': {last_err}")
    return None

def _ddgs_fallback(query: str, max_results: int = 5) -> Optional[str]:
    try:
        from ddgs import DDGS
    except ImportError:
        try:
            from duckduckgo_search import DDGS
        except ImportError:
            return None
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=10, region="wt-wt", timelimit="y"))
        if not results:
            return None
        filtered = [r for r in results if not _is_blocked_url(r.get("href", ""))]
        filtered = filtered[:max_results]
        if not filtered:
            return None
        parts = []
        for r in filtered:
            title = r.get("title", "") or "Без названия"
            href = r.get("href", "")
            content = r.get("body", "") or r.get("content", "")
            if not content:
                continue
            parts.append(f"{title}\n{content}\n{href}")
        return "\n\n---\n\n".join(parts) if parts else None
    except Exception as e:
        logger.warning(f"DDGS не смог найти: {e}")
        return None

def _search_sync(query: str, max_results: int = 5) -> Optional[str]:
    res = _searxng_search_sync(query, max_results)
    if res:
        return res
    logger.info(f"SearXNG не работает, переходим на DDGS '{query[:60]}'")
    return _ddgs_fallback(query, max_results)

class SearchService:
    def __init__(self, enabled: bool = True, max_results: int = 5, context_chars: int = 3500, gpt_client=None, **kwargs):
        if "enable" in kwargs:
            enabled = kwargs["enable"]
        self.enabled = enabled
        self.max_results = max_results
        self.context_chars = context_chars
        self.gpt_client = gpt_client

    async def should_search_llm(self, text: str) -> str:
        """LLM-версия should_search: возвращает 'Y'/'N' через is_question_llm (auto:fast, temp 0)."""
        if not self.enabled:
            return False
        return await is_question_llm(text, self.gpt_client)

    async def search(self, text: str) -> Optional[str]:
        if not self.enabled:
            return None
        try:
            result = await asyncio.to_thread(_search_sync, text, self.max_results)
            
            return result
        except Exception as e:
            logger.warning(f"не смог найти '{text}': {e}")
            return None
