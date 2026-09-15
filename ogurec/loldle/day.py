"""Парижские сутки LoLdle: загадка меняется в полночь по Europe/Paris."""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

LOLDLE_TZ = ZoneInfo("Europe/Paris")


def loldle_now() -> datetime:
    return datetime.now(LOLDLE_TZ)


def loldle_day(now: datetime | None = None) -> str:
    return (now or loldle_now()).date().isoformat()


def next_loldle(now: datetime | None = None) -> datetime:
    current = now or loldle_now()
    return (current + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)


def format_until_next(now: datetime | None = None) -> str:
    total = max(0, int((next_loldle(now) - (now or loldle_now())).total_seconds()))
    hours, rem = divmod(total, 3600)
    minutes = rem // 60
    if hours:
        return f"{hours} ч {minutes} мин"
    return f"{minutes} мин"


def previous_day(day: str) -> str:
    return (date.fromisoformat(day) - timedelta(days=1)).isoformat()


def format_day(day: str) -> str:
    return date.fromisoformat(day).strftime("%d.%m.%Y")


def ru_days(n: int) -> str:
    mod10 = n % 10
    mod100 = n % 100
    if mod10 == 1 and mod100 != 11:
        return f"{n} день"
    if 2 <= mod10 <= 4 and (mod100 < 12 or mod100 > 14):
        return f"{n} дня"
    return f"{n} дней"
