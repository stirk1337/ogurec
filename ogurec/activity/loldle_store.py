import json
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

MODES = ("classic", "quote", "ability", "emoji", "splash")
LOLDLE_TZ = ZoneInfo("Europe/Paris")
STORE_PATH = Path("loldle.json")


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


def merge_mode(old: dict | None, new: dict | None) -> dict:
    old = old or {}
    new = new or {}
    done = bool(old.get("done") or new.get("done"))
    attempts = max(int(old.get("attempts") or 0), int(new.get("attempts") or 0))
    cells_old = list(old.get("cells") or [])
    cells_new = list(new.get("cells") or [])
    cells = cells_new if len(cells_new) >= len(cells_old) else cells_old
    return {"attempts": max(attempts, 1) if done else attempts, "done": done, "cells": cells}


def merge_player(old: dict | None, new: dict | None) -> dict:
    old = old or {}
    new = new or {}
    old_progress = old.get("progress") or {}
    new_progress = new.get("progress") or {}
    return {
        **old,
        **new,
        "name": new.get("name") or old.get("name"),
        "avatar": new.get("avatar") if new.get("avatar") is not None else old.get("avatar"),
        "progress": {mode: merge_mode(old_progress.get(mode), new_progress.get(mode)) for mode in MODES},
    }


def resolve_channel_id(
    player: dict,
    instance_id: str,
    instances: dict[str, int],
    user_channels: dict[str, int],
) -> int | None:
    raw = player.get("channelId")
    if raw not in (None, "", 0, "0"):
        try:
            return int(raw)
        except (TypeError, ValueError):
            pass
    mapped = instances.get(instance_id)
    if mapped is not None:
        return mapped
    user_id = str(player.get("id") or "")
    return user_channels.get(user_id)


def session_is_live(*, has_sockets: bool, opened: bool, seen_at: float, now: float, grace: float) -> bool:
    if has_sockets:
        return True
    if not opened:
        return False
    return now - seen_at < grace


class LoldleStore:
    def __init__(self, path: Path = STORE_PATH):
        self.path = path
        self.data = {"channels": {}}
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            self.data = json.loads(self.path.read_text())
            self.data.setdefault("channels", {})
        except (OSError, json.JSONDecodeError):
            self.data = {"channels": {}}

    def save(self) -> None:
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2))

    def channel(self, channel_id: int) -> dict:
        key = str(channel_id)
        state = self.data["channels"].get(key)
        if state is None:
            today = loldle_day()
            state = {
                "streak": 0,
                "last_played_day": "",
                "last_reset_day": today,
                "roster_day": today,
                "players": [],
                "starters": [],
            }
            self.data["channels"][key] = state
            self.save()
        return state

    def channels(self) -> list[tuple[int, dict]]:
        return [(int(channel_id), state) for channel_id, state in self.data["channels"].items()]

    def remember(self, channel_id: int, players: list[dict], starters: set[int]) -> dict:
        state = self.channel(channel_id)
        if state.get("last_reset_day") == loldle_day():
            state["roster_day"] = loldle_day()
            state["players"] = players
            state["starters"] = sorted(starters)
            self.save()
        return state

    def mark_played(self, channel_id: int) -> dict:
        state = self.channel(channel_id)
        today = loldle_day()
        if state.get("last_reset_day") != today:
            return state
        if state.get("last_played_day") == today:
            return state
        state["last_played_day"] = today
        state["streak"] = int(state.get("streak") or 0) + 1
        self.save()
        return state

    def close_day(self, channel_id: int) -> tuple[dict, list[dict], set[int]]:
        state = self.channel(channel_id)
        today = loldle_day()
        if state.get("last_reset_day") == today:
            return state, [], set()
        yesterday = previous_day(today)
        if state.get("last_played_day") != yesterday:
            state["streak"] = 0
        recap_players = list(state.get("players") or []) if state.get("roster_day") == yesterday else []
        recap_starters = set(state.get("starters") or []) if state.get("roster_day") == yesterday else set()
        state["last_reset_day"] = today
        state["roster_day"] = today
        state["players"] = []
        state["starters"] = []
        self.save()
        return state, recap_players, recap_starters
