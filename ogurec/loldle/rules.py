"""Правила прогресса: как сливаются пять режимов и что считается победой дня."""

MODES = ("classic", "quote", "ability", "emoji", "splash")


def player_id(player: dict | None) -> int | None:
    try:
        return int((player or {}).get("id"))
    except (TypeError, ValueError):
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
