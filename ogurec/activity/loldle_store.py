import json
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

LOLDLE_TZ = ZoneInfo("Europe/Paris")
STORE_PATH = Path("loldle.json")


def loldle_now() -> datetime:
    return datetime.now(LOLDLE_TZ)


def loldle_day(now: datetime | None = None) -> str:
    return (now or loldle_now()).date().isoformat()


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
