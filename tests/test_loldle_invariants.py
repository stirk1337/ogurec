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

    def test_loldle_slash_must_not_send_components_v2(self):
        import json
        from io import BytesIO

        import discord
        from discord.http import handle_message_parameters

        from ogurec.cogs.loldle_cog import LoldleView

        view = LoldleView("2026-09-09")
        self.assertFalse(
            view.has_components_v2(),
            "LayoutView + embed=None on /loldle sends embeds:[] with IS_COMPONENTS_V2; Discord rejects it",
        )
        params = handle_message_parameters(
            content="stirk играет в LoLdle",
            attachments=[discord.File(BytesIO(b"png"), filename="loldle.png")],
            view=view,
        )
        raw = params.multipart[0]["value"] if params.multipart else json.dumps(params.payload)
        payload = json.loads(raw)
        self.assertFalse(bool((payload.get("flags") or 0) & (1 << 15)), payload)
        self.assertNotIn("embeds", payload)

    def test_loldle_slash_posts_via_followup(self):
        cog = Path("ogurec/cogs/loldle_cog.py").read_text()
        show = cog.split("async def show_today", 1)[1].split("async def on_progress", 1)[0]
        self.assertIn("followup.send", show)
        self.assertNotIn("edit_original_response", show)

    def test_v2_scoreboard_is_not_editable_today_board(self):
        from ogurec.cogs.loldle_cog import Loldle

        cog = Loldle.__new__(Loldle)
        cog.bot = SimpleNamespace(user=SimpleNamespace(id=42))
        message = SimpleNamespace(
            author=SimpleNamespace(id=42),
            flags=SimpleNamespace(components_v2=True),
            components=[SimpleNamespace(custom_id="loldle:play:2026-09-09", children=[])],
        )
        self.assertFalse(cog._is_today_board(message, "2026-09-09"))

    def test_static_play_id_is_today_board(self):
        from ogurec.cogs.loldle_cog import Loldle

        cog = Loldle.__new__(Loldle)
        cog.bot = SimpleNamespace(user=SimpleNamespace(id=42))
        message = SimpleNamespace(
            author=SimpleNamespace(id=42),
            flags=SimpleNamespace(components_v2=False),
            components=[SimpleNamespace(custom_id="loldle:play", children=[])],
        )
        self.assertTrue(cog._is_today_board(message, "2026-09-09"))

    def test_play_button_uses_persistent_static_id(self):
        from ogurec.activity.loldle_store import PLAY_ID
        from ogurec.cogs.loldle_cog import LoldleView

        view = LoldleView("2026-09-09")
        self.assertTrue(view.is_persistent())
        self.assertEqual([item.custom_id for item in view.children], [PLAY_ID])
        load = Path("ogurec/cogs/loldle_cog.py").read_text().split("async def cog_load", 1)[1].split(
            "async def cog_unload", 1
        )[0]
        self.assertIn("add_view(LoldleView())", load)
        handle = Path("ogurec/cogs/loldle_cog.py").read_text().split("async def handle_play", 1)[1].split(
            "class PlayButton", 1
        )[0]
        self.assertLess(handle.find("launch_activity"), handle.find("touch_session"))

    def test_day_recap_has_play_button_for_today(self):
        cog = Path("ogurec/cogs/loldle_cog.py").read_text()
        recap = cog.split("async def _post_recap", 1)[1].split("async def _publish", 1)[0]
        self.assertIn("play=True", recap)
        self.assertIn("day=loldle_day()", recap)
        self.assertNotIn("play=False", recap)
        rewrite = cog.split("async def _rewrite_day_card", 1)[1].split("def _channel", 1)[0]
        self.assertIn("play = recap_id is not None and mid == recap_id", rewrite)
        self.assertIn("day=loldle_day() if play else None", rewrite)


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
