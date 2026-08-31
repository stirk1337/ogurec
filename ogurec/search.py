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

CASUAL_PATTERNS = re.compile(
    r"^(привет|прив|хай|хелло|хеллоу|салам|здаров[ао]?|ку|йо|спс|спасибо|пасиб|благодарю|"
    r"ок|окей|норм|пон|ясно|ладно|угу|ага|да|нет|ага|неа|"
    r"пока|бб|бай|ббай|до\s*свидания|увидимся|давай|удачи|"
    r"добр(ое|ый)\s*(утро|день|вечер|ночи)|спокойной\s*ночи|сладких\s*снов|"
    r"лол|кек|аха?х[ао]*|хехе?|хихи|прикол|прикольно|ору|ор|ржу|ржака|кринж|имба|гг|изи|го|погнали|летсго|"
    r"го\s*играть|как\s*дела|как\s*ты|как\s*жизнь|че\s*как|чё\s*как|что\s*делаешь|чем\s*занят|как\s*сам|"
    r"обнял|люблю|скучаю|сори|извини|сорян|<3|)[\s!?.]*$",
    re.I,
)
WH_WORDS = re.compile(
    r"\b(кто|что|когда|где|куда|откуда|почему|зачем|сколько|какой|какая|какое|какие|чей|чья|чьё|как|чем|какой|чо|че|расскажи"
    r"who|what|when|where|why|how|which)\b",
    re.I,
)
FACT_TRIGGERS = re.compile(
    r"(чемпионат|турнир|лига|кубок|матч|сч[её]т|результат|финал|полуфинал|плей-офф|выйграл|выиграл|победил|чемпион|приз[её]р|медаль|"
    r"босс|боссы|мини-босс|персона\s*\d|атлус|мегами|тартар|тень|тени|аркана|социалка|"
    r"релиз|вышел|вышла|вышло|когда\s+вышел|дата\s+выхода|когда\s+выйдет|когда\s+релиз|анонс|патч|обновление|версия|дополнение|dlc|трейлер|тизер|"
    r"курс|цена|стоимость|сколько\s+стоит|поч[её]м|курс\s+доллара|курс\s+евро|курс\s+биткоина|цена\s*игр|скидка|акция|распродажа|"
    r"новости|событие|случилось|произошло|инцидент|авария|погода|прогноз|климат|температура|дата|год|месяц|неделя|время|расписание|дедлайн|сегодня|вчера|завтра|20\d{2}|19\d{2}|"
    r"википедия|вики|где\s+находится|адрес|место|локация|координаты|как\s+добраться|расстояние|маршрут|"
    r"кто\s+такой|что\s+такое|что\s+значит|что\s+означает|кто\s+создал|кто\s+автор|кто\s+режисс[её]р|кто\s+акт[её]р|кто\s+исполняет|биография|история\s+создания|состав|участники|"
    r"как\s+называется|как\s+зовут|как\s+пройти|как\s+победить|как\s+установить|как\s+скачать|как\s+настроить|как\s+сделать|как\s+получить|инструкция|туториал|гайд|прохождение|секрет|пасхалка|"
    r"кто\s+главный|что\s+за|почему|зачем|отчего|сколько|сколько\s+серий|сколько\s+длится|сколько\s+весит|сколько\s+времени|"
    r"когда\s+будет|когда\s+выйдет|когда\s+старт|когда\s+начало|где\s+купить|где\s+скачать|где\s+посмотреть|где\s+найти|обзор|отзывы|рейтинг|оценка|топ|список|подборка|"
    r"фильм|сериал|аниме|мультфильм|книга|манга|игра|песня|альбом|трек|группа|исполнитель|артист|персонаж|персонажи|герой|сюжет|концовка|финал\s+игры|"
    r"рост|вес|возраст|население|площадь|высота|глубина|длина|ширина|объ[её]м|формула|теорема|закон|правило|определение|"
    r"championship|tournament|league|cup|match|score|winner|won|champion|final|boss|release|price|course|exchange|rate|news|weather|wikipedia|wiki|where\s+is|who\s+is|what\s+is|how\s+to|when\s+did|how\s+much)",
    re.I,
)
LINK_REQUEST = re.compile(
    r"\b(скинь|скинешь|скиньте|кинь|кинешь|отправь|отправишь|найди|найди\s+мне|дай|дашь|покажи|накинь|закинь)\b.*\b(ссылк\w*|линк\w*|url)\b"
    r"|\b(ссылк\w*|линк\w*)\b.*\b(скинь|кинь|дай|найди|нужна|нужен)\b",
    re.I,
)

_MENTION_RE = re.compile(r"<@!?&?#?\d+>|<a?:\w+:\d+>")

def clean_query_for_search(text: str) -> str:
    q = _MENTION_RE.sub("", text)
    q = re.sub(r"\s+", " ", q).strip()
    return q[:200]

def needs_search(text: str) -> bool:
    if not text or not text.strip():
        return False
    t = text.strip()
    low = t.lower()

    has_wh = bool(WH_WORDS.search(t))
    has_fact = bool(FACT_TRIGGERS.search(t))
    has_q = "?" in t
    
    cleaned = re.sub(r"[^\w\sа-яёa-z]", "", low).strip()
    if (CASUAL_PATTERNS.match(low) or CASUAL_PATTERNS.match(cleaned)):
        return False

    if len(t) > 8 and (has_wh  and ((has_q or has_fact)) or (LINK_REQUEST.search(t))):
        return True
    
    return False

def _is_blocked_url(url: str) -> bool:
    u = url.lower()
    parsed = urllib.parse.urlparse(u)
    domain = parsed.netloc
    if any(d in u for d in VIDEO_BLOCKLIST):
        return True
    if any(u.endswith(ext) for ext in (".mp4", ".avi", ".mov", ".webm", ".mkv")):
        return True
    if "vk.com/video" in u or "vkvideo.ru" in u:
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
            logger.info(f"SearXNG after blocklist={len(filtered)} for '{query[:40]}'")
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
    def __init__(self, enabled: bool = True, max_results: int = 5, context_chars: int = 3500, **kwargs):
        if "enable" in kwargs:
            enabled = kwargs["enable"]
        self.enabled = enabled
        self.max_results = max_results
        self.context_chars = context_chars


    def should_search(self, text: str) -> bool:
        if not self.enabled:
            return False
        return needs_search(text)

    async def search(self, query: str) -> Optional[str]:
        if not self.enabled:
            return None
        q = clean_query_for_search(query)
        if not q:
            return None
        
        try:
            result = await asyncio.to_thread(_search_sync, q, self.max_results)
            
            return result
        except Exception as e:
            logger.warning(f"не смог найти '{q}': {e}")
            return None
