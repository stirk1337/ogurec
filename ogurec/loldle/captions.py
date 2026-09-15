"""Текст под картинкой табло. Чистые функции: имя игрока достаёт переданный lookup."""

from collections.abc import Callable

from ogurec.loldle.day import format_day, loldle_day, ru_days
from ogurec.loldle.rules import player_id, player_wins

NameLookup = Callable[[int], str | None]


def player_name(player: dict | None, user_id: int | None, *, ping: bool, lookup: NameLookup | None = None) -> str:
    if ping and user_id is not None:
        return f"<@{user_id}>"
    if player and player.get("name"):
        return str(player["name"])
    if user_id is not None and lookup is not None:
        found = lookup(user_id)
        if found:
            return found
    return "Игрок"


def index_by_id(players: list[dict]) -> dict[int, dict]:
    return {user_id: player for player in players if (user_id := player_id(player)) is not None}


def names_for(user_ids, by_id: dict[int, dict], *, ping: bool = False, lookup: NameLookup | None = None) -> list[str]:
    return [player_name(by_id.get(user_id), user_id, ping=ping, lookup=lookup) for user_id in user_ids]


def playing_line(names: list[str]) -> str:
    if not names:
        return "Кто-то играет в LoLdle"
    if len(names) == 1:
        return f"{names[0]} играет в LoLdle"
    if len(names) == 2:
        return f"{names[0]} и {names[1]} играют в LoLdle"
    return f"{', '.join(names[:-1])} и {names[-1]} играют в LoLdle"


def score_lines(players: list[dict], *, ping: bool, lookup: NameLookup | None = None) -> list[str]:
    best = max((player_wins(player) for player in players), default=0)
    lines = []
    for player in players:
        done = player_wins(player)
        label = player_name(player, player_id(player), ping=ping, lookup=lookup)
        crown = "👑 " if done == best and done else ""
        lines.append(f"{crown}**{done}/5** — {label}")
    return lines


def caption(
    players: list[dict],
    starters: set[int],
    streak: int,
    headline: str | None = None,
    *,
    recap: bool = False,
    ping: bool | None = None,
    status: bool = False,
    lookup: NameLookup | None = None,
) -> str:
    by_id = index_by_id(players)
    mention = recap if ping is None else ping
    ordered = list(starters) + [user_id for user_id in by_id if user_id not in starters]
    names = names_for(ordered, by_id, ping=mention, lookup=lookup)
    if status:
        lines = [headline or f"LoLdle · {format_day(loldle_day())}"]
        if not players:
            lines.append("Сегодня ещё никто не играл — нажми Играть")
        if streak > 0:
            lines.append(f"🔥 Стрик сервера: {ru_days(streak)}")
        return "\n".join(lines + score_lines(players, ping=False, lookup=lookup))
    lines = [headline or playing_line(names)]
    if not recap:
        return lines[0]
    if names:
        lines.append(" ".join(names))
    if streak <= 0:
        lines.append("Стрик сброшен — за день никто не угадал ни один режим")
    else:
        lines.append(f"🔥 Стрик сервера: {ru_days(streak)}")
    if not players:
        return "\n".join(lines)
    return "\n".join(lines + score_lines(players, ping=mention, lookup=lookup))
