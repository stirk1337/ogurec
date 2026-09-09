import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from threading import Lock
from zoneinfo import ZoneInfo

from ogurec.config.paths import data_file

MODES = ("classic", "quote", "ability", "emoji", "splash")
LOLDLE_TZ = ZoneInfo("Europe/Paris")
STORE_PATH = data_file("loldle.json", "loldle.json")
PLAY_ID = "loldle:play"


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


def play_custom_id(day: str) -> str:
    return f"{PLAY_ID}:{day}"


def play_id_day(custom_id: str | None) -> str | None:
    if not custom_id:
        return None
    prefix = f"{PLAY_ID}:"
    if custom_id.startswith(prefix):
        day = custom_id[len(prefix) :]
        return day or None
    if custom_id == PLAY_ID:
        return None
    return None


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


def player_wins(player: dict | None) -> int:
    progress = (player or {}).get("progress") or {}
    return sum(1 for mode in MODES if (progress.get(mode) or {}).get("done"))


def player_has_progress(player: dict | None) -> bool:
    progress = (player or {}).get("progress") or {}
    return any(
        (progress.get(mode) or {}).get("done") or int((progress.get(mode) or {}).get("attempts") or 0)
        for mode in MODES
    )


def day_has_win(record: dict | None) -> bool:
    return any(player_wins(item) for item in (record or {}).get("players") or [])


def apply_player_update(old: dict | None, new: dict | None, today: str) -> dict | None:
    incoming = new or {}
    if incoming.get("day") != today:
        return None
    if old and old.get("day") == today:
        merged = merge_player(old, incoming)
        merged["day"] = today
        return merged
    progress = incoming.get("progress") or {}
    return {
        **incoming,
        "day": today,
        "progress": {mode: merge_mode(None, progress.get(mode)) for mode in MODES},
    }


def coerce_channel_id(raw) -> int | None:
    if raw in (None, "", 0, "0"):
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value or None


def parse_instance_channel(instance_id: str | None) -> int | None:
    if not instance_id:
        return None
    text = str(instance_id)
    match = re.search(r"-gc-\d+-(\d+)$", text) or re.search(r"-pc-(\d+)$", text)
    return int(match.group(1)) if match else None


def resolve_channel_id(
    player: dict,
    instance_id: str,
    instances: dict[str, int],
    user_channels: dict[str, int],
    stored_user_channel: int | None = None,
) -> int | None:
    user_id = str(player.get("id") or "")
    for candidate in (
        coerce_channel_id(player.get("channelId")),
        instances.get(instance_id),
        parse_instance_channel(instance_id),
        parse_instance_channel(player.get("instanceId")),
        user_channels.get(user_id),
        stored_user_channel,
    ):
        if candidate:
            return int(candidate)
    return None


def session_is_live(*, has_sockets: bool, opened: bool, seen_at: float, now: float, grace: float) -> bool:
    if has_sockets:
        return True
    if not opened:
        return False
    return now - seen_at < grace


def message_id(raw) -> int | None:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value or None


def _empty_day() -> dict:
    return {"players": [], "starters": [], "board_id": None, "recap_id": None, "recapped": False}


def migrate_channel(state: dict) -> dict:
    days = state.get("days")
    if not isinstance(days, dict):
        days = {}
        state["days"] = days
    roster_day = state.get("roster_day")
    players = state.get("players")
    starters = state.get("starters")
    if roster_day and roster_day not in days and (players or starters):
        days[roster_day] = {
            "players": list(players or []),
            "starters": [int(item) for item in (starters or []) if str(item).isdigit()],
            "board_id": None,
            "recap_id": None,
            "recapped": False,
        }
    for key in ("players", "starters", "roster_day", "last_reset_day"):
        state.pop(key, None)
    state.setdefault("streak", 0)
    state.setdefault("last_played_day", "")
    state.setdefault("pending_recap_day", None)
    for day, record in list(days.items()):
        if not isinstance(record, dict):
            days[day] = _empty_day()
            continue
        record.setdefault("players", [])
        record.setdefault("starters", [])
        record.setdefault("board_id", None)
        record.setdefault("recap_id", None)
        record.setdefault("recapped", False)
    return state


class LoldleStore:
    def __init__(self, path: Path = STORE_PATH, clock=None):
        self.path = path
        self.clock = clock or loldle_now
        self.data = {"channels": {}, "users": {}}
        self._lock = Lock()
        self.load()

    def _now(self, now: datetime | None = None) -> datetime:
        return now or self.clock()

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            self.data = json.loads(self.path.read_text())
            self.data.setdefault("channels", {})
            self.data.setdefault("users", {})
        except (OSError, json.JSONDecodeError):
            self.data = {"channels": {}, "users": {}}
        for state in self.data["channels"].values():
            if isinstance(state, dict):
                migrate_channel(state)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2))
        tmp.replace(self.path)

    def channel(self, channel_id: int, now: datetime | None = None) -> dict:
        with self._lock:
            return self._channel(channel_id, now)

    def _channel(self, channel_id: int, now: datetime | None = None) -> dict:
        key = str(channel_id)
        state = self.data["channels"].get(key)
        if state is None:
            state = {
                "streak": 0,
                "last_played_day": "",
                "days": {},
                "pending_recap_day": None,
            }
            self.data["channels"][key] = state
            self.save()
        migrate_channel(state)
        if self._sync_missed_streak(state, loldle_day(self._now(now))):
            self.save()
        return state

    def channels(self) -> list[tuple[int, dict]]:
        with self._lock:
            items = []
            for channel_id, state in self.data["channels"].items():
                if isinstance(state, dict):
                    migrate_channel(state)
                    items.append((int(channel_id), state))
            return items

    def user_channel(self, user_id: str) -> int | None:
        with self._lock:
            raw = (self.data.get("users") or {}).get(str(user_id)) or {}
            return coerce_channel_id(raw.get("channel_id") if isinstance(raw, dict) else raw)

    def remember_user_channel(self, user_id: str, channel_id: int) -> None:
        key = str(user_id)
        with self._lock:
            users = self.data.setdefault("users", {})
            prev = users.get(key) or {}
            current = prev.get("channel_id") if isinstance(prev, dict) else prev
            if coerce_channel_id(current) == int(channel_id):
                return
            users[key] = {"channel_id": int(channel_id)}
            self.save()

    def day_record(self, channel_id: int, day: str, *, create: bool = False, now: datetime | None = None) -> dict | None:
        with self._lock:
            return self._day_record(channel_id, day, create=create, now=now)

    def _day_record(self, channel_id: int, day: str, *, create: bool = False, now: datetime | None = None) -> dict | None:
        state = self._channel(channel_id, now)
        record = state["days"].get(day)
        if record is None and create:
            record = _empty_day()
            state["days"][day] = record
            self.save()
        return record

    def today_players(self, channel_id: int, now: datetime | None = None) -> list[dict]:
        today = loldle_day(self._now(now))
        record = self.day_record(channel_id, today, now=now)
        if record is None:
            return []
        return list(record.get("players") or [])

    def today_starters(self, channel_id: int, now: datetime | None = None) -> set[int]:
        today = loldle_day(self._now(now))
        record = self.day_record(channel_id, today, now=now)
        if record is None:
            return set()
        return {int(item) for item in (record.get("starters") or []) if str(item).lstrip("-").isdigit()}

    def board_id(self, channel_id: int, day: str | None = None, now: datetime | None = None) -> int | None:
        target = day or loldle_day(self._now(now))
        record = self.day_record(channel_id, target, now=now)
        if record is None:
            return None
        return message_id(record.get("board_id"))

    def set_board_id(self, channel_id: int, board_id: int | None, now: datetime | None = None) -> dict:
        today = loldle_day(self._now(now))
        with self._lock:
            record = self._day_record(channel_id, today, create=True, now=now)
            assert record is not None
            record["board_id"] = board_id
            self.save()
            return record

    def set_recap_id(self, channel_id: int, day: str, recap_id: int | None, now: datetime | None = None) -> dict | None:
        with self._lock:
            record = self._day_record(channel_id, day, create=True, now=now)
            if record is None:
                return None
            record["recap_id"] = recap_id
            self.save()
            return record

    def add_starter(self, channel_id: int, user_id: int, now: datetime | None = None) -> dict:
        today = loldle_day(self._now(now))
        with self._lock:
            record = self._day_record(channel_id, today, create=True, now=now)
            assert record is not None
            starters = [int(item) for item in (record.get("starters") or []) if str(item).lstrip("-").isdigit()]
            if user_id not in starters:
                starters.append(user_id)
                record["starters"] = starters
                self.save()
            return record

    def upsert_player(self, channel_id: int, player: dict, now: datetime | None = None) -> dict | None:
        today = loldle_day(self._now(now))
        with self._lock:
            return self._upsert_player(channel_id, player, today, now)

    def _upsert_player(self, channel_id: int, player: dict, today: str, now: datetime | None) -> dict | None:
        user_id = str(player.get("id") or "")
        if not user_id:
            return None
        if apply_player_update(None, player, today) is None:
            return None
        record = self._day_record(channel_id, today, create=False, now=now)
        people = list((record or {}).get("players") or [])
        existing = next((item for item in people if str(item.get("id") or "") == user_id), None)
        merged = apply_player_update(existing, player, today)
        if merged is None:
            return None
        merged["id"] = user_id
        merged["day"] = today
        if existing is None and not player_has_progress(merged):
            return None
        record = self._day_record(channel_id, today, create=True, now=now)
        assert record is not None
        people = list(record.get("players") or [])
        if existing is None:
            people.append(merged)
        else:
            people = [merged if str(item.get("id") or "") == user_id else item for item in people]
        record["players"] = people
        try:
            starter_id = int(user_id)
            starters = [int(item) for item in (record.get("starters") or []) if str(item).lstrip("-").isdigit()]
            if starter_id not in starters:
                starters.append(starter_id)
                record["starters"] = starters
        except ValueError:
            pass
        if player_wins(merged):
            self._mark_played(channel_id, today, now)
        else:
            self.save()
        return merged

    def remove_player(self, channel_id: int, user_id: str, now: datetime | None = None) -> list[dict]:
        uid = str(user_id)
        today = loldle_day(self._now(now))
        affected: list[dict] = []
        with self._lock:
            state = self._channel(channel_id, now)
            for day, record in (state.get("days") or {}).items():
                players = list(record.get("players") or [])
                starters = [
                    int(item)
                    for item in (record.get("starters") or [])
                    if str(item).lstrip("-").isdigit()
                ]
                had_player = any(str(item.get("id") or "") == uid for item in players)
                had_starter = uid in {str(item) for item in starters}
                if not had_player and not had_starter:
                    continue
                record["players"] = [item for item in players if str(item.get("id") or "") != uid]
                record["starters"] = [item for item in starters if str(item) != uid]
                affected.append(
                    {
                        "day": day,
                        "board_id": message_id(record.get("board_id")),
                        "recap_id": message_id(record.get("recap_id")),
                        "recapped": bool(record.get("recapped")),
                        "players": list(record["players"]),
                        "starters": set(record["starters"]),
                    }
                )
            self._recompute_streak(state, today)
            self.save()
            return affected

    def _recompute_streak(self, state: dict, today: str) -> None:
        del today
        won = sorted(day for day, record in (state.get("days") or {}).items() if day_has_win(record))
        if not won:
            state["streak"] = 0
            state["last_played_day"] = ""
            return
        last = won[-1]
        streak = 1
        day = last
        won_set = set(won)
        while previous_day(day) in won_set:
            streak += 1
            day = previous_day(day)
        state["streak"] = streak
        state["last_played_day"] = last

    def mark_played(self, channel_id: int, now: datetime | None = None) -> dict:
        today = loldle_day(self._now(now))
        with self._lock:
            state = self._channel(channel_id, now)
            self._mark_played(channel_id, today, now)
            return state

    def _mark_played(self, channel_id: int, today: str, now: datetime | None) -> None:
        state = self._channel(channel_id, now)
        if state.get("last_played_day") == today:
            self.save()
            return
        yesterday = previous_day(today)
        if state.get("last_played_day") == yesterday:
            state["streak"] = int(state.get("streak") or 0) + 1
        else:
            state["streak"] = 1
        state["last_played_day"] = today
        self.save()

    def player(self, channel_id: int, user_id: str, now: datetime | None = None) -> dict | None:
        today = loldle_day(self._now(now))
        record = self.day_record(channel_id, today, now=now)
        if record is None:
            return None
        return next((item for item in (record.get("players") or []) if str(item.get("id") or "") == str(user_id)), None)

    def due_recaps(self, now: datetime | None = None) -> list[dict]:
        current = self._now(now)
        today = loldle_day(current)
        jobs = []
        with self._lock:
            for channel_id, state in list(self.data["channels"].items()):
                if not isinstance(state, dict):
                    continue
                migrate_channel(state)
                changed = self._sync_missed_streak(state, today)
                days = state.get("days") or {}
                pending_days = sorted(day for day, record in days.items() if day < today and not record.get("recapped"))
                pending = pending_days[0] if pending_days else None
                if state.get("pending_recap_day") != pending:
                    state["pending_recap_day"] = pending
                    changed = True
                if changed:
                    self.save()
                for day in pending_days:
                    record = days[day]
                    players = list(record.get("players") or [])
                    starters = {int(item) for item in (record.get("starters") or []) if str(item).lstrip("-").isdigit()}
                    jobs.append(
                        {
                            "channel_id": int(channel_id),
                            "day": day,
                            "players": players,
                            "starters": starters,
                            "board_id": record.get("board_id"),
                            "streak": int(state.get("streak") or 0),
                            "played": bool(players or starters),
                        }
                    )
        return jobs

    def mark_recapped(self, channel_id: int, day: str, now: datetime | None = None) -> dict | None:
        with self._lock:
            record = self._day_record(channel_id, day, create=False, now=now)
            if record is None:
                return None
            record["recapped"] = True
            state = self._channel(channel_id, now)
            if state.get("pending_recap_day") == day:
                remaining = sorted(
                    item
                    for item, value in (state.get("days") or {}).items()
                    if item < loldle_day(self._now(now)) and not value.get("recapped")
                )
                state["pending_recap_day"] = remaining[0] if remaining else None
            self.save()
            return record

    def freeze_day(self, channel_id: int, day: str, now: datetime | None = None) -> int | None:
        record = self.day_record(channel_id, day, now=now)
        if record is None:
            return None
        return message_id(record.get("board_id"))

    def _sync_missed_streak(self, state: dict, today: str) -> bool:
        last = state.get("last_played_day") or ""
        yesterday = previous_day(today)
        if last in (today, yesterday):
            return False
        if int(state.get("streak") or 0) == 0:
            return False
        state["streak"] = 0
        return True
