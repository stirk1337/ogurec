export function normalizeChampion(value) {
  return String(value || "")
    .replace(/['’]/g, "'")
    .replace(/\s+/g, " ")
    .trim()
    .toLowerCase();
}

export function textMatchesChampion(raw, answer) {
  const want = normalizeChampion(answer);
  if (!want) return false;
  const compact = String(valueOrText(raw)).replace(/\s+/g, " ").trim();
  if (!compact || compact.length > 80) return false;
  const lines = compact.split("\n").map((line) => normalizeChampion(line)).filter(Boolean);
  return lines.some((line) => line === want || line.startsWith(`${want} `));
}

function valueOrText(raw) {
  return raw == null ? "" : String(raw);
}

export function enterGuessIsCorrect(typed, answer, highlightedText) {
  const want = normalizeChampion(answer);
  const got = normalizeChampion(typed);
  if (!want) return false;
  if (got === want) return true;
  return Boolean(highlightedText && textMatchesChampion(highlightedText, answer));
}

export function clickGuessIsCorrect(label, answer) {
  return textMatchesChampion(label, answer);
}
