import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from ogurec.activity.loldle_store import (
    LoldleStore,
    message_id,
    resolve_channel_id,
)
from ogurec.activity.server import ActivityServer

from tests.test_loldle_store import at, player


class StoreInvariantTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "loldle.json"
        self.now = at("2026-09-08")
        self.store = LoldleStore(self.path, clock=lambda: self.now)

    def tearDown(self):
        self.temp.cleanup()

    def test_remove_only_winner_clears_streak(self):
        self.store.upsert_player(10, player("1", day="2026-09-08", done=True))
        self.assertEqual(self.store.channel(10)["streak"], 1)
        jobs = self.store.remove_player(10, "1")
        self.assertEqual(jobs[0]["players"], [])
        self.assertEqual(self.store.channel(10)["streak"], 0)
        self.assertEqual(self.store.channel(10)["last_played_day"], "")

    def test_due_recaps_keep_day_order(self):
        self.store.upsert_player(10, player("1", day="2026-09-08", done=True))
        self.now = at("2026-09-09")
        self.store.upsert_player(10, player("1", day="2026-09-09", done=True))
        self.now = at("2026-09-10")
        days = [job["day"] for job in self.store.due_recaps()]
        self.assertEqual(days, ["2026-09-08", "2026-09-09"])

    def test_explicit_channel_beats_stale_memory(self):
        channel = resolve_channel_id(
            {"id": "1", "channelId": "5"},
            "room",
            {"room": 99},
            {"1": 99},
            99,
        )
        self.assertEqual(channel, 5)

    def test_message_id_rejects_zero(self):
        self.assertIsNone(message_id(None))
        self.assertIsNone(message_id(0))
        self.assertIsNone(message_id("0"))
        self.assertEqual(message_id("555"), 555)

    def test_starter_without_progress_is_not_a_win(self):
        self.store.add_starter(10, 1)
        self.assertEqual(self.store.channel(10)["streak"], 0)
        self.assertEqual(self.store.today_players(10), [])

    def test_empty_progress_from_finished_invite_still_publishes(self):
        cog = Path("ogurec/cogs/loldle_cog.py").read_text()
        on_progress = cog.split("async def on_progress", 1)[1].split("async def on_reset", 1)[0]
        self.assertIn("self.store.add_starter(channel_id, int(user_id))", on_progress)
        self.assertIn("_schedule_publish", on_progress)
        self.assertNotIn("return existing", on_progress)

    def test_scoreboard_message_uses_container_accent(self):
        from ogurec.cogs.loldle_cog import BOARD_ACCENT, LoldleView

        view = LoldleView("2026-09-09", content="stirk играет в LoLdle")
        self.assertTrue(view.has_components_v2())
        container = view.children[0]
        self.assertEqual(int(container.accent_colour), BOARD_ACCENT)
        kinds = [type(child).__name__ for child in container.children]
        self.assertIn("TextDisplay", kinds)
        self.assertIn("MediaGallery", kinds)
        self.assertIn("ActionRow", kinds)


class FakeWS:
    closed = False

    def __init__(self):
        self.sent = []

    def __hash__(self):
        return id(self)

    def __eq__(self, other):
        return self is other

    async def send_str(self, payload):
        self.sent.append(payload)


class ActivityResetTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.server = ActivityServer(SimpleNamespace())

    async def asyncTearDown(self):
        await self.server.session.close()

    async def test_reset_drops_state_and_notifies_peers(self):
        peer = FakeWS()
        self.server.rooms["room"].add(peer)
        self.server.states["room"]["1"] = {"id": "1", "progress": {"classic": {"done": True}}}
        reset = AsyncMock()
        self.server.on_reset = reset
        await self.server._publish(
            "room",
            json.dumps({"type": "reset", "id": "1", "channelId": "10"}),
        )
        reset.assert_awaited_once()
        self.assertNotIn("1", self.server.states["room"])
        self.assertEqual(json.loads(peer.sent[-1]), {"type": "reset", "id": "1"})

    async def test_reset_still_broadcasts_if_discord_wipe_fails(self):
        from loguru import logger

        peer = FakeWS()
        self.server.rooms["room"].add(peer)
        self.server.states["room"]["1"] = {"id": "1"}
        self.server.on_reset = AsyncMock(side_effect=RuntimeError("discord down"))
        logger.disable("ogurec.activity.server")
        try:
            await self.server._publish("room", json.dumps({"type": "reset", "id": "1"}))
        finally:
            logger.enable("ogurec.activity.server")
        self.assertNotIn("1", self.server.states["room"])
        self.assertEqual(json.loads(peer.sent[-1]), {"type": "reset", "id": "1"})
