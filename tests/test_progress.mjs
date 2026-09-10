import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {dirname, join} from "node:path";
import {fileURLToPath} from "node:url";
import test from "node:test";

import {mergeMode, modalWinAttempts, modeSnapshot} from "../ogurec/activity/client/progress.js";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const src = readFileSync(join(root, "ogurec/activity/client/src.js"), "utf8");

test("win screen nodes must stay in the DOM so Vue keeps today's answer", () => {
  assert.doesNotMatch(
    src,
    /querySelectorAll\("\.share, \.complete-share/,
    "removing .complete-share / #modal / #overlay unmounts LoLdle after a win, so the second device never sends done=true",
  );
  assert.match(src, /\$options/);
  assert.match(src, /modeSnapshot/);
  assert.match(src, /vueKeyStorage/);
});

test("first-try win modal counts as 1 done classic", () => {
  const text = "С ПЕРВОЙ ПОПЫТКИ! Ты угадал Афелий. Количество попыток: 1";
  assert.equal(modalWinAttempts(text), 1);
  const won = modeSnapshot({
    mode: "classic",
    pathMode: "classic",
    remembered: {attempts: modalWinAttempts(text)},
    pageWon: true,
  });
  assert.equal(won.done, true);
  assert.equal(won.attempts, 1);
});

test("classic green row counts as win without Vue", () => {
  const cells = [[{k: "g"}, {k: "g"}, {k: "g"}, {k: "g"}, {k: "g"}, {k: "g"}]];
  const won = modeSnapshot({mode: "classic", pathMode: "classic", cells});
  assert.equal(won.done, true);
  assert.equal(won.attempts, 1);
});

test("after the win screen unmounts, storage plus seenReady still counts as done", () => {
  const won = modeSnapshot({
    mode: "classic",
    pathMode: "classic",
    modeReady: false,
    seenReady: true,
    answer: "Ahri",
    answers: ["Annie", "Ahri"],
    cells: [],
  });
  assert.equal(won.done, true);
  assert.equal(won.attempts, 2);
});

test("leftover answers on the hub are not today's win", () => {
  const leftover = modeSnapshot({
    mode: "classic",
    pathMode: "",
    modeReady: false,
    seenReady: false,
    answer: "Ahri",
    answers: ["Ahri"],
    cells: [],
  });
  assert.equal(leftover.done, false);
  assert.equal(leftover.attempts, 0);
});

test("finished Discord invite still sends channel via locationId", () => {
  assert.match(src, /discord\?\.locationId/);
  assert.match(src, /locationId/);
});

test("yellow then done keeps done and the higher attempt count", () => {
  const merged = mergeMode(
    {attempts: 4, done: false, cells: []},
    {attempts: 6, done: true, cells: []},
  );
  assert.equal(merged.done, true);
  assert.equal(merged.attempts, 6);
});
