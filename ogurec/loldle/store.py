"""Файловое хранилище LoLdle: по каналу — дни, игроки, id сообщений и стрик."""

import json
from datetime import datetime
from pathlib import Path
from threading import Lock

from ogurec.config.paths import data_file
from ogurec.loldle.day import loldle_day, loldle_now, previous_day
from ogurec.loldle.ids import coerce_channel_id, message_id
from ogurec.loldle.rules import apply_player_update, day_has_win, player_has_progress, player_wins

STORE_PATH = data_file("loldle.json", "loldle.json")
# ponytail: окно вместо версий у пейлоада — клиент узнаёт о сбросе из бродкаста, но его
# снапшот может уже лететь к нам. Понадобится точнее — нумеровать снапшоты на клиенте.
RESET_GRACE = 5.0


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
        self._resets: dict[tuple[int, str], datetime] = {}
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

    def user_channel(self, user_id: str, now: datetime | None = None) -> int | None:
        """Последний известный канал игрока — только если он известен с сегодня.

        Вчерашняя память отправляла прогресс в канал, где человек сегодня не играл.
        """
        today = loldle_day(self._now(now))
        with self._lock:
            raw = (self.data.get("users") or {}).get(str(user_id)) or {}
            if not isinstance(raw, dict) or raw.get("day") != today:
                return None
            return coerce_channel_id(raw.get("channel_id"))

    def remember_user_channel(self, user_id: str, channel_id: int, now: datetime | None = None) -> None:
        key = str(user_id)
        today = loldle_day(self._now(now))
        with self._lock:
            users = self.data.setdefault("users", {})
            prev = users.get(key) or {}
            if isinstance(prev, dict) and prev.get("day") == today:
                if coerce_channel_id(prev.get("channel_id")) == int(channel_id):
                    return
            users[key] = {"channel_id": int(channel_id), "day": today}
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

    def play_targets(self, now: datetime | None = None) -> list[dict]:
        today = loldle_day(self._now(now))
        targets: list[dict] = []
        for channel_id, state in self.channels():
            board = self.board_id(channel_id, today, now=now)
            if board:
                targets.append(
                    {"channel_id": channel_id, "message_id": board, "kind": "board", "day": today}
                )
            days = state.get("days") or {}
            for day in sorted((item for item in days if item < today), reverse=True):
                recap = message_id((days.get(day) or {}).get("recap_id"))
                if recap:
                    targets.append(
                        {"channel_id": channel_id, "message_id": recap, "kind": "recap", "day": day}
                    )
                    break
        return targets

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

    def _target_day(self, channel_id: int, player: dict, today: str, now: datetime | None) -> str | None:
        """День, в который ложится пейлоад, или None — если это хлам.

        Клиент штампует день сам, и пока пейлоад летит, в Париже может наступить полночь.
        Такой пейлоад кладём в его собственный вчерашний день, но только если игрок там уже
        есть — иначе это просто протухший localStorage, и в сегодня ему точно нельзя.
        """
        claimed = str(player.get("day") or "")
        if claimed == today:
            return today
        if claimed != previous_day(today):
            return None
        record = self._day_record(channel_id, claimed, create=False, now=now)
        user_id = str(player.get("id") or "")
        played = any(str(item.get("id") or "") == user_id for item in (record or {}).get("players") or [])
        return claimed if played else None

    def _upsert_player(self, channel_id: int, player: dict, today: str, now: datetime | None) -> dict | None:
        user_id = str(player.get("id") or "")
        if not user_id:
            return None
        day = self._target_day(channel_id, player, today, now)
        if day is None:
            return None
        if self._just_reset(channel_id, user_id, now):
            return None
        record = self._day_record(channel_id, day, create=False, now=now)
        people = list((record or {}).get("players") or [])
        existing = next((item for item in people if str(item.get("id") or "") == user_id), None)
        merged = apply_player_update(existing, player, day)
        if merged is None:
            return None
        merged["id"] = user_id
        merged["day"] = day
        if existing is None and not player_has_progress(merged):
            return None
        record = self._day_record(channel_id, day, create=True, now=now)
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
        if not player_wins(merged):
            self.save()
        elif day == today:
            self._mark_played(channel_id, day, now)
        else:
            # победа доехала в прошлый день — инкрементальный стрик её не увидит
            self._recompute_streak(self._channel(channel_id, now), today)
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
            self._resets[(int(channel_id), uid)] = self._now(now)
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
        if str(state.get("last_played_day") or "") >= today:
            self.save()
            return
        yesterday = previous_day(today)
        if state.get("last_played_day") == yesterday:
            state["streak"] = int(state.get("streak") or 0) + 1
        else:
            state["streak"] = 1
        state["last_played_day"] = today
        self.save()

    def _just_reset(self, channel_id: int, user_id: str, now: datetime | None) -> bool:
        """Клиент сбросил статистику — его же снапшот, летевший в этот момент, не воскрешает её."""
        at = self._resets.get((int(channel_id), str(user_id)))
        if at is None:
            return False
        if (self._now(now) - at).total_seconds() < RESET_GRACE:
            return True
        self._resets.pop((int(channel_id), str(user_id)), None)
        return False

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
