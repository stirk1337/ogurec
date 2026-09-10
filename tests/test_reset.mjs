import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {dirname, join} from "node:path";
import {fileURLToPath} from "node:url";
import test from "node:test";

import {createResetSession, isResetAck, resetPayload, storageKeysToClear} from "../ogurec/activity/client/reset.js";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const src = readFileSync(join(root, "ogurec/activity/client/src.js"), "utf8");

test("Discord Activity blocks window.confirm, so reset must use an in-app confirm", () => {
  const discordConfirm = () => false;
  assert.equal(discordConfirm("Сбросить?"), false, "iframe without allow-modals");
  assert.doesNotMatch(
    src,
    /window\.confirm\s*\(/,
    "window.confirm returns false immediately in Discord Activity, so the button appears to do nothing",
  );
  assert.match(src, /ogurec-reset-yes/);
  assert.match(src, /createResetSession/);
});

test("in-app confirm is required before wipe, cancel returns to idle", () => {
  const session = createResetSession();
  assert.equal(session.beginWipe(), false);
  assert.equal(session.request(), "confirm");
  assert.equal(session.cancel(), "idle");
  assert.equal(session.request(), "confirm");
  assert.equal(session.beginWipe(), true);
  assert.equal(session.phase, "wiping");
  assert.equal(session.cancel(), "wiping");
});

test("reset websocket payload and ack match the server", () => {
  const payload = resetPayload({id: "1", channelId: "10", instanceId: "abc", day: "2026-09-09"});
  assert.equal(payload.type, "reset");
  assert.equal(isResetAck(JSON.stringify({type: "reset", id: "1"}), "1"), true);
  assert.equal(isResetAck(JSON.stringify({type: "reset", id: "2"}), "1"), false);
  assert.equal(isResetAck("not-json", "1"), false);
});

test("reset must not wipe Discord SDK / LoLdle boot storage", () => {
  assert.doesNotMatch(src, /localStorage\.clear\s*\(/, "full localStorage.clear breaks LoLdle JSON and locale after reload");
  assert.doesNotMatch(src, /sessionStorage\.clear\s*\(/);
  assert.doesNotMatch(src, /indexedDB\.deleteDatabase/, "deleting all IndexedDB DBs breaks the Discord Activity SDK");
  assert.doesNotMatch(src, /caches\.delete/, "wiping Cache Storage makes chunk loads fail after reload");
  assert.match(src, /shouldClearStorageKey|storageKeysToClear/);
  const kept = storageKeysToClear([
    "currentLocale",
    "ogurecLocale",
    "fit_to_screen",
    "classic_answers",
    "quote_today_answer",
    "ogurecWon",
    "ogurecProgress",
    "discord_sdk",
  ]);
  assert.deepEqual(kept.sort(), ["classic_answers", "ogurecProgress", "ogurecWon", "quote_today_answer"]);
});
