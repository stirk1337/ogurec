import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from ogurec.activity.loldle_store import (
    LoldleStore,
    apply_player_update,
    loldle_day,
    merge_player,
    parse_instance_channel,
    play_custom_id,
    play_id_day,
    player_wins,
    previous_day,
    resolve_channel_id,
)

PARIS = ZoneInfo("Europe/Paris")


def at(day: str, hour: int = 12) -> datetime:
    year, month, number = (int(part) for part in day.split("-"))
    return datetime(year, month, number, hour, 0, 0, tzinfo=PARIS)


def player(user_id: str, *, day: str, done: bool = True, name: str = "Игрок") -> dict:
    return {
        "id": user_id,
        "name": name,
        "day": day,
        "progress": {
            "classic": {"attempts": 3 if done else 1, "done": done, "cells": []},
            "quote": {"attempts": 0, "done": False, "cells": []},
            "ability": {"attempts": 0, "done": False, "cells": []},
            "emoji": {"attempts": 0, "done": False, "cells": []},
            "splash": {"attempts": 0, "done": False, "cells": []},
        },
    }


class LoldleStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "loldle.json"
        self.now = at("2026-09-08")
        self.store = LoldleStore(self.path, clock=lambda: self.now)

    def tearDown(self):
        self.temp.cleanup()

    def test_upsert_after_midnight_without_close_keeps_yesterday(self):
        yesterday = player("1", day="2026-09-08", name="A")
        self.assertIsNotNone(self.store.upsert_player(10, yesterday))
        old = self.store.day_record(10, "2026-09-08")
        self.assertEqual(len(old["players"]), 1)
        self.assertEqual(old["players"][0]["name"], "A")

        self.now = at("2026-09-09", 0)
        today = player("1", day="2026-09-09", name="A-today", done=False)
        self.assertIsNotNone(self.store.upsert_player(10, today))

        yesterday_record = self.store.day_record(10, "2026-09-08")
        today_record = self.store.day_record(10, "2026-09-09")
        self.assertEqual(yesterday_record["players"][0]["name"], "A")
        self.assertTrue(yesterday_record["players"][0]["progress"]["classic"]["done"])
        self.assertEqual(today_record["players"][0]["name"], "A-today")
        self.assertFalse(today_record["players"][0]["progress"]["classic"]["done"])
        self.assertEqual(len(self.store.today_players(10)), 1)
        self.assertFalse(self.store.today_players(10)[0]["progress"]["classic"]["done"])

    def test_second_device_done_overwrites_yellow(self):
        self.now = at("2026-09-09")
        yellow = player("1", day="2026-09-09", done=False)
        yellow["progress"]["classic"]["attempts"] = 4
        self.store.upsert_player(10, yellow)
        done = player("1", day="2026-09-09", done=True)
        done["progress"]["classic"]["attempts"] = 1
        stored = self.store.upsert_player(10, done)
        self.assertTrue(stored["progress"]["classic"]["done"])
        self.assertEqual(stored["progress"]["classic"]["attempts"], 4)

    def test_upsert_does_not_require_reset(self):
        self.now = at("2026-09-09")
        stored = self.store.upsert_player(10, player("7", day="2026-09-09"))
        self.assertIsNotNone(stored)
        self.assertEqual(self.store.today_players(10)[0]["id"], "7")
        self.assertEqual(self.store.channel(10)["last_played_day"], "2026-09-09")

    def test_progress_without_day_is_rejected(self):
        payload = player("1", day="2026-09-08")
        payload.pop("day")
        self.assertIsNone(self.store.upsert_player(10, payload))
        self.assertEqual(self.store.today_players(10), [])

    def test_progress_from_other_day_is_rejected(self):
        self.assertIsNone(self.store.upsert_player(10, player("1", day="2026-09-07")))
        self.assertEqual(self.store.today_players(10), [])
        self.assertIsNone(self.store.day_record(10, "2026-09-07"))

    def test_apply_player_update_does_not_or_done_across_days(self):
        old = player("1", day="2026-09-08", done=True)
        new = player("1", day="2026-09-09", done=False)
        self.assertIsNone(apply_player_update(old, new, "2026-09-08"))
        fresh = apply_player_update(old, new, "2026-09-09")
        self.assertIsNotNone(fresh)
        self.assertEqual(fresh["day"], "2026-09-09")
        self.assertFalse(fresh["progress"]["classic"]["done"])
        merged_same_day = merge_player(old, new)
        self.assertTrue(merged_same_day["progress"]["classic"]["done"])

    def test_streak_increments_only_on_win(self):
        self.store.upsert_player(10, player("1", day="2026-09-08", done=False))
        self.assertEqual(self.store.channel(10)["streak"], 0)
        self.store.upsert_player(10, player("1", day="2026-09-08", done=True))
        self.assertEqual(self.store.channel(10)["streak"], 1)

        self.now = at("2026-09-09")
        self.store.upsert_player(10, player("1", day="2026-09-09", done=True))
        self.assertEqual(self.store.channel(10)["streak"], 2)

    def test_skipped_day_resets_streak(self):
        self.store.upsert_player(10, player("1", day="2026-09-08", done=True))
        self.assertEqual(self.store.channel(10)["streak"], 1)
        self.now = at("2026-09-10")
        state = self.store.channel(10)
        self.assertEqual(state["streak"], 0)
        self.store.upsert_player(10, player("1", day="2026-09-10", done=True))
        self.assertEqual(self.store.channel(10)["streak"], 1)

    def test_migrated_yesterday_roster_is_not_today(self):
        self.path.write_text(
            """
            {
              "channels": {
                "42": {
                  "streak": 4,
                  "last_played_day": "2026-09-08",
                  "last_reset_day": "2026-09-08",
                  "roster_day": "2026-09-08",
                  "players": [{"id": "1", "name": "Old", "progress": {"classic": {"done": true, "attempts": 2, "cells": []}}}],
                  "starters": [1]
                }
              }
            }
            """,
            encoding="utf-8",
        )
        self.now = at("2026-09-09")
        store = LoldleStore(self.path, clock=lambda: self.now)
        self.assertEqual(store.today_players(42), [])
        yesterday = store.day_record(42, "2026-09-08")
        self.assertIsNotNone(yesterday)
        self.assertEqual(yesterday["players"][0]["name"], "Old")
        self.assertNotIn("players", store.channel(42))
        self.assertNotIn("roster_day", store.channel(42))

    def test_due_recaps_without_discord_channel(self):
        self.store.upsert_player(99, player("1", day="2026-09-08", done=True))
        self.now = at("2026-09-09", 0)
        jobs = self.store.due_recaps()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["channel_id"], 99)
        self.assertEqual(jobs[0]["day"], "2026-09-08")
        self.assertTrue(jobs[0]["played"])
        self.assertEqual(jobs[0]["players"][0]["id"], "1")
        self.store.mark_recapped(99, "2026-09-08")
        self.assertEqual(self.store.due_recaps(), [])
        self.assertTrue(self.store.day_record(99, "2026-09-08")["recapped"])

    def test_reload_keeps_day_slots(self):
        self.store.upsert_player(10, player("1", day="2026-09-08"))
        self.now = at("2026-09-09")
        self.store.upsert_player(10, player("2", day="2026-09-09", done=False))
        reloaded = LoldleStore(self.path, clock=lambda: self.now)
        self.assertEqual(len(reloaded.day_record(10, "2026-09-08")["players"]), 1)
        self.assertEqual(reloaded.today_players(10)[0]["id"], "2")

    def test_board_id_stays_on_its_day(self):
        self.store.set_board_id(10, 111)
        self.assertEqual(self.store.board_id(10, "2026-09-08"), 111)
        self.now = at("2026-09-09")
        self.store.set_board_id(10, 222)
        self.assertEqual(self.store.board_id(10, "2026-09-08"), 111)
        self.assertEqual(self.store.board_id(10), 222)

    def test_remember_user_channel(self):
        self.store.remember_user_channel("1", 99)
        self.assertEqual(self.store.user_channel("1"), 99)
        self.store.remember_user_channel("1", 99)
        reloaded = LoldleStore(self.path, clock=lambda: self.now)
        self.assertEqual(reloaded.user_channel("1"), 99)

    def test_remove_player_wipes_days_and_streak(self):
        self.store.upsert_player(10, player("1", day="2026-09-08", done=True))
        self.store.set_board_id(10, 111)
        self.store.set_recap_id(10, "2026-09-08", 555)
        self.now = at("2026-09-09")
        self.store.upsert_player(10, player("1", day="2026-09-09", done=True))
        self.store.upsert_player(10, player("2", day="2026-09-09", done=True, name="B"))
        self.store.set_board_id(10, 222)
        self.assertEqual(self.store.channel(10)["streak"], 2)
        jobs = self.store.remove_player(10, "1")
        self.assertEqual({job["day"] for job in jobs}, {"2026-09-08", "2026-09-09"})
        yesterday = next(job for job in jobs if job["day"] == "2026-09-08")
        today_job = next(job for job in jobs if job["day"] == "2026-09-09")
        self.assertEqual(yesterday["board_id"], 111)
        self.assertEqual(yesterday["recap_id"], 555)
        self.assertEqual(yesterday["players"], [])
        self.assertEqual(today_job["board_id"], 222)
        self.assertEqual(today_job["players"][0]["id"], "2")
        self.assertEqual(self.store.today_players(10)[0]["id"], "2")
        self.assertIsNone(self.store.day_record(10, "2026-09-08")["players"] or None)
        self.assertEqual(self.store.channel(10)["streak"], 1)
        self.assertEqual(self.store.channel(10)["last_played_day"], "2026-09-09")
        empty = player("3", day="2026-09-09", done=False)
        empty["progress"]["classic"]["attempts"] = 0
        self.assertIsNone(self.store.upsert_player(10, empty))


class HelperTests(unittest.TestCase):
    def test_loldle_day_uses_paris_date(self):
        self.assertEqual(loldle_day(at("2026-09-09", 0)), "2026-09-09")
        self.assertEqual(previous_day("2026-09-09"), "2026-09-08")

    def test_play_ids(self):
        self.assertEqual(play_custom_id("2026-09-09"), "loldle:play:2026-09-09")
        self.assertEqual(play_id_day("loldle:play:2026-09-09"), "2026-09-09")
        self.assertIsNone(play_id_day("loldle:play"))
        self.assertIsNone(play_id_day("other"))

    def test_iter_custom_ids_walks_nested_rows(self):
        from types import SimpleNamespace

        from ogurec.activity.loldle_store import first_text_display, iter_custom_ids

        nested = SimpleNamespace(
            custom_id=None,
            children=[
                SimpleNamespace(custom_id=None, children=[], content="stirk играет", type=SimpleNamespace(name="text_display")),
                SimpleNamespace(
                    custom_id=None,
                    children=[SimpleNamespace(custom_id="loldle:play:2026-09-09", children=[])],
                ),
            ],
        )
        self.assertEqual(iter_custom_ids([nested]), ["loldle:play:2026-09-09"])
        self.assertEqual(first_text_display([nested]), "stirk играет")

    def test_player_wins(self):
        self.assertEqual(player_wins(player("1", day="2026-09-08", done=True)), 1)
        self.assertEqual(player_wins(player("1", day="2026-09-08", done=False)), 0)

    def test_parse_instance_channel(self):
        instance = "i-1276580072400224306-gc-912952092627435520-912954213460484116"
        self.assertEqual(parse_instance_channel(instance), 912954213460484116)
        self.assertEqual(parse_instance_channel("i-1-pc-42"), 42)
        self.assertEqual(parse_instance_channel("gc-912952092627435520-912954213460484116"), 912954213460484116)
        self.assertEqual(parse_instance_channel("pc-42"), 42)
        self.assertIsNone(parse_instance_channel("room-abc"))

    def test_native_invite_resolves_channel_from_instance(self):
        instance = "i-1276580072400224306-gc-912952092627435520-912954213460484116"
        channel = resolve_channel_id({"id": "1", "channelId": ""}, instance, {}, {}, None)
        self.assertEqual(channel, 912954213460484116)

    def test_finished_invite_resolves_channel_from_location(self):
        channel = resolve_channel_id(
            {
                "id": "1",
                "channelId": "",
                "locationId": "gc-912952092627435520-912954213460484116",
            },
            "new-instance-without-channel",
            {},
            {},
            None,
        )
        self.assertEqual(channel, 912954213460484116)


class ScoreboardTests(unittest.TestCase):
    def test_title_and_recap_render(self):
        from PIL import Image

        from ogurec.activity.scoreboard import render_scoreboard

        with_timer = render_scoreboard([], title="LoLdle · 09.09.2026", remaining=True)
        recap = render_scoreboard([], title="Итоги · 08.09.2026", remaining=False)
        filled = render_scoreboard(
            [player("1", day="2026-09-08", name="A")],
            title="Итоги · 08.09.2026",
            remaining=False,
        )
        self.assertGreater(Image.open(with_timer).height, Image.open(recap).height)
        self.assertGreater(Image.open(filled).height, Image.open(recap).height)
        self.assertGreaterEqual(Image.open(filled).width, 800)
        self.assertGreaterEqual(Image.open(with_timer).width, 800)
        stripe = Image.open(filled).getpixel((0, Image.open(filled).height // 2))
        self.assertEqual(stripe[:3], (200, 170, 110))


if __name__ == "__main__":
    unittest.main()
