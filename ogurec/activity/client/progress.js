import {textMatchesChampion} from "./guess.js";

export const MODE_IDS = ["classic", "quote", "ability", "emoji", "splash"];

export function guessValue(entry) {
  if (typeof entry === "string") return entry;
  return String(entry?.value || entry?.name || entry?.label || entry?.champion || "");
}

export function modalWinAttempts(text) {
  const raw = String(text || "");
  if (/с первой попытки/i.test(raw)) return 1;
  if (!/ты угадал|you guessed/i.test(raw)) return 0;
  const match = raw.match(/количество попыток:\s*(\d+)/i) || raw.match(/number of attempts:\s*(\d+)/i);
  return match ? Number(match[1]) : 1;
}

export function canTrustLocalPuzzle({mode, pathMode, modeReady, seenReady, cells}) {
  return Boolean(modeReady || seenReady || pathMode === mode || (mode === "classic" && cells?.length));
}

export function answersIncludeChampion(answers, answer) {
  return (answers || []).some((guess) => textMatchesChampion(guessValue(guess), answer));
}

export function classicRowIsWin(cells) {
  const last = cells[cells.length - 1] || [];
  const attrs = last.filter((cell) => cell.k && cell.k !== "i");
  return attrs.length >= 6 && attrs.every((cell) => cell.k === "g");
}

export function modeSnapshot({
  mode,
  pathMode = "",
  modeReady = false,
  seenReady = false,
  remembered = null,
  answer = "",
  answers = [],
  cells = [],
  pageWon = false,
}) {
  const trusted = canTrustLocalPuzzle({mode, pathMode, modeReady, seenReady, cells});
  const guessed = Boolean(answer && answersIncludeChampion(answers, answer));
  const classicWin = mode === "classic" && classicRowIsWin(cells);
  let attempts = 0;
  if (trusted) {
    attempts = answers.length || (mode === "classic" ? cells.length : 0);
  }
  if (remembered) {
    attempts = Math.max(attempts, remembered.attempts || 0);
  }
  const done = Boolean(
    remembered ||
    (trusted && (classicWin || guessed || (pathMode === mode && pageWon))),
  );
  if (done) attempts = Math.max(attempts, remembered?.attempts || 0, 1);
  return {attempts, done, cells: mode === "classic" ? cells : []};
}

export function mergeMode(a = {}, b = {}) {
  const done = Boolean(a.done || b.done);
  const attempts = Math.max(a.attempts || 0, b.attempts || 0);
  const cells = (b.cells?.length || 0) >= (a.cells?.length || 0) ? b.cells || [] : a.cells || [];
  return {attempts: done ? Math.max(attempts, 1) : attempts, done, cells};
}

export function mergeProgress(local = {}, remote = {}) {
  return Object.fromEntries(MODE_IDS.map((mode) => [mode, mergeMode(local[mode], remote[mode])]));
}
