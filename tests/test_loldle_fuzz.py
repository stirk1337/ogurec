"""Фаззер хранилища: случайные последовательности против простой модели.

Ловит то, что точечные тесты не ловят — порядок событий. Уже поймал: поздняя вчерашняя
победа не пересчитывала стрик, и статистика возвращалась после сброса.
"""

import random
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from ogurec.loldle.day import previous_day
from ogurec.loldle.rules import MODES
from ogurec.loldle.store import RESET_GRACE, LoldleStore
from tests.test_loldle_store import at

CHANNEL = 10
SEEDS = (1, 7, 42, 99, 2024)


class Model:
    """Что обязано лежать в сторе: попытки растут, победа не отменяется, чужой день не в счёт."""

    def __init__(self):
        self.days: dict[str, dict[str, dict[str, list]]] = {}

    def record(self, day: str, user_id: str, modes: dict[str, tuple[int, bool]]) -> None:
        player = self.days.setdefault(day, {}).setdefault(user_id, {})
        for mode, (attempts, done) in modes.items():
            slot = player.setdefault(mode, [0, False])
            slot[0] = max(slot[0], attempts)
            slot[1] = slot[1] or done
            if slot[1]:
                slot[0] = max(slot[0], 1)

    def has_progress(self, day: str, user_id: str) -> bool:
        return any(slot[0] or slot[1] for slot in self.days.get(day, {}).get(user_id, {}).values())

    def forget(self, user_id: str) -> None:
        for players in self.days.values():
            players.pop(user_id, None)

    def won(self, day: str) -> bool:
        return any(slot[1] for player in self.days.get(day, {}).values() for slot in player.values())

    def streak(self, today: str) -> int:
        won = sorted(day for day in self.days if self.won(day))
        if not won or won[-1] not in (today, previous_day(today)):
            return 0
        streak, day, seen = 1, won[-1], set(won)
        while previous_day(day) in seen:
            streak += 1
            day = previous_day(day)
        return streak


def payload(user_id: str, day: str, modes: dict[str, tuple[int, bool]]) -> dict:
    return {
        "id": user_id,
        "name": f"user{user_id}",
        "day": day,
        "progress": {m: {"attempts": a, "done": d, "cells": []} for m, (a, d) in modes.items()},
    }


class StoreFuzzTests(unittest.TestCase):
    def check(self, path: Path, now, model: Model) -> None:
        fresh = LoldleStore(path, clock=lambda: now)          # как после перезапуска бота
        state = fresh.channel(CHANNEL)
        days = state.get("days") or {}
        for day, players in model.days.items():
            stored = (days.get(day) or {}).get("players") or []
            ids = [str(item.get("id")) for item in stored]
            self.assertEqual(len(ids), len(set(ids)), f"дубли игроков в {day}: {ids}")
            for user_id, modes in players.items():
                if not model.has_progress(day, user_id):
                    continue
                found = next((item for item in stored if str(item.get("id")) == user_id), None)
                self.assertIsNotNone(found, f"потерян игрок {user_id} в {day}: ждали {modes}")
                self.assertEqual(found.get("day"), day)
                got = found.get("progress") or {}
                for mode, (attempts, done) in modes.items():
                    cell = got.get(mode) or {}
                    self.assertEqual(bool(cell.get("done")), done, f"{user_id} {day} {mode}")
                    self.assertEqual(int(cell.get("attempts") or 0), attempts, f"{user_id} {day} {mode}")
        self.assertEqual(int(state.get("streak") or 0), model.streak(now.date().isoformat()))

    def test_random_sequences_keep_the_store_consistent(self):
        for seed in SEEDS:
            with self.subTest(seed=seed):
                rng = random.Random(seed)
                model = Model()
                sent: dict[str, dict[str, tuple[int, bool]]] = {}
                with tempfile.TemporaryDirectory() as tmp:
                    path = Path(tmp) / "loldle.json"
                    now = at("2026-09-15", 9)
                    store = LoldleStore(path, clock=lambda: now)
                    for _ in range(200):
                        now = now + timedelta(seconds=rng.randint(int(RESET_GRACE) + 1, 600))
                        store.clock = lambda: now
                        today = now.date().isoformat()
                        op = rng.choices(
                            ["progress", "stale", "advance", "reload", "reset"],
                            weights=[60, 12, 12, 8, 8],
                        )[0]
                        if op == "progress":
                            user_id = str(rng.randint(1, 3))
                            known = sent.setdefault(f"{today}:{user_id}", {})
                            mode = rng.choice(MODES)
                            attempts, done = known.get(mode, (0, False))
                            known[mode] = (attempts + rng.randint(0, 2), done or rng.random() < 0.3)
                            store.upsert_player(CHANNEL, payload(user_id, today, dict(known)))
                            model.record(today, user_id, dict(known))
                        elif op == "stale":
                            user_id = str(rng.randint(1, 3))
                            other = (now + timedelta(days=rng.choice([-2, -1, 1]))).date().isoformat()
                            store.upsert_player(CHANNEL, payload(user_id, other, {"emoji": (9, True)}))
                            if other == previous_day(today) and model.has_progress(other, user_id):
                                model.record(other, user_id, {"emoji": (9, True)})
                        elif op == "advance":
                            now = now + timedelta(days=1)
                            store.clock = lambda: now
                        elif op == "reload":
                            store = LoldleStore(path, clock=lambda: now)
                        elif op == "reset":
                            user_id = str(rng.randint(1, 3))
                            store.remove_player(CHANNEL, user_id)
                            model.forget(user_id)
                            sent = {k: v for k, v in sent.items() if not k.endswith(f":{user_id}")}
                        self.check(path, now, model)


if __name__ == "__main__":
    unittest.main()
