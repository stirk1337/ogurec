import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {dirname, join} from "node:path";
import {fileURLToPath} from "node:url";
import test from "node:test";

import {
  clickGuessIsCorrect,
  enterGuessIsCorrect,
  normalizeChampion,
  textMatchesChampion,
} from "../ogurec/activity/client/guess.js";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");

test("champion names match ignoring quotes and case", () => {
  assert.equal(normalizeChampion("Kai’Sa"), "kai'sa");
  assert.equal(textMatchesChampion("Kai'Sa", "Kai’Sa"), true);
  assert.equal(clickGuessIsCorrect("Ahri", "Ahri"), true);
  assert.equal(clickGuessIsCorrect("Anivia", "Ahri"), false);
});

test("Enter is a win only for the exact or highlighted answer, not a prefix", () => {
  assert.equal(enterGuessIsCorrect("Annie", "Annie", null), true);
  assert.equal(enterGuessIsCorrect("annie", "Annie", "Anivia"), true);
  assert.equal(enterGuessIsCorrect("ann", "Annie", "Annie"), true);
  assert.equal(enterGuessIsCorrect("ann", "Annie", "Anivia"), false);
  assert.equal(enterGuessIsCorrect("ann", "Annie", null), false);
});

test("haptic overlay must not cover guess rows", () => {
  const src = readFileSync(join(root, "ogurec/activity/client/src.js"), "utf8");
  assert.equal(
    src.includes("data-ogurec-haptic"),
    false,
    "checkbox overlay on the correct champion intercepts the tap that should submit the guess",
  );
  assert.match(src, /enterGuessIsCorrect/);
});
