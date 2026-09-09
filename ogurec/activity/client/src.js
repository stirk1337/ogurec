import {DiscordSDK} from "@discord/embedded-app-sdk";
import CryptoJS from "crypto-js";
import {clickGuessIsCorrect, enterGuessIsCorrect} from "./guess.js";
import {createResetSession, isResetAck, resetPayload} from "./reset.js";

const modes = [
  ["classic", "Классика"],
  ["quote", "Цитата"],
  ["ability", "Умение"],
  ["emoji", "Эмодзи"],
  ["splash", "Сплеш"],
];
const clientId = document.querySelector('meta[name="discord-client-id"]')?.content || "";
const panel = document.createElement("aside");
panel.className = "ogurec-party is-closed";
panel.setAttribute("aria-live", "polite");
panel.innerHTML = `
  <button type="button" class="ogurec-toggle" aria-expanded="false" aria-controls="ogurec-party-body">
    <span class="ogurec-faces"></span>
    <strong>Вместе</strong>
    <span class="ogurec-total">0</span>
  </button>
  <div id="ogurec-party-body" class="ogurec-party-body">
    <div class="ogurec-players">Ждём игроков…</div>
    <button type="button" class="ogurec-reset">Сбросить вашу статистику</button>
    <div class="ogurec-reset-confirm" hidden>
      <p>Прогресс сотрётся из Discord, с картинки в чате, cookies и localStorage.</p>
      <div class="ogurec-reset-actions">
        <button type="button" class="ogurec-reset-no">Отмена</button>
        <button type="button" class="ogurec-reset-yes">Сбросить</button>
      </div>
    </div>
  </div>
`;
document.body.append(panel);

const gate = document.createElement("div");
gate.className = "ogurec-gate";
gate.innerHTML = `
  <div class="ogurec-gate-card">
    <strong>Играют вместе</strong>
    <div class="ogurec-progress" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="8" aria-labelledby="ogurec-gate-status">
      <span class="ogurec-progress-fill" style="width:8%"></span>
      <span class="ogurec-progress-pct">8%</span>
    </div>
    <p class="ogurec-gate-status" id="ogurec-gate-status">Подключение к Discord…</p>
  </div>
`;
document.body.classList.add("ogurec-locked");
document.body.append(gate);

const toggle = panel.querySelector(".ogurec-toggle");
function setPartyOpen(open) {
  panel.classList.toggle("is-open", open);
  panel.classList.toggle("is-closed", !open);
  toggle.setAttribute("aria-expanded", String(open));
  toggle.setAttribute("aria-label", open ? "Скрыть табло" : "Показать, кто играет");
  localStorage.setItem("ogurecPartyOpen", open ? "1" : "0");
}
toggle.addEventListener("click", () => setPartyOpen(panel.classList.contains("is-closed")));
setPartyOpen(localStorage.getItem("ogurecPartyOpen") === "1");

const resetButton = panel.querySelector(".ogurec-reset");
const resetConfirm = panel.querySelector(".ogurec-reset-confirm");
const resetYes = panel.querySelector(".ogurec-reset-yes");
const resetNo = panel.querySelector(".ogurec-reset-no");
const resetSession = createResetSession();
let resetting = false;
let knownDone = null;
let winBuzzTimer = 0;
let iosBuzzedMode = "";

const players = new Map();
const championSlug = {
  "Aurelion Sol": "AurelionSol",
  "Bel'Veth": "Belveth",
  "Cho'Gath": "Chogath",
  "Dr. Mundo": "DrMundo",
  "Jarvan IV": "JarvanIV",
  "Kai'Sa": "Kaisa",
  "Kha'Zix": "Khazix",
  "Kog'Maw": "KogMaw",
  "K'Sante": "KSante",
  "LeBlanc": "Leblanc",
  "Lee Sin": "LeeSin",
  "Master Yi": "MasterYi",
  "Miss Fortune": "MissFortune",
  "Nunu & Willump": "Nunu",
  "Rek'Sai": "RekSai",
  "Renata Glasc": "Renata",
  "Tahm Kench": "TahmKench",
  "Twisted Fate": "TwistedFate",
  "Vel'Koz": "Velkoz",
  "Wukong": "MonkeyKing",
  "Xin Zhao": "XinZhao",
};
let ws;
let user;
let discord;
let channelId = "";
let lastClassicCells = [];
let lastClassicDay = "";
let remoteProgress = {};
let remoteDay = "";
let lastSent = "";
let socketGen = 0;
let reconnectTimer = 0;
let heartbeat = 0;

function readJson(key, fallback) {
  try {
    return JSON.parse(localStorage.getItem(key) || JSON.stringify(fallback));
  } catch {
    return fallback;
  }
}

function cellKind(className) {
  if (className.includes("square-good")) return "g";
  if (className.includes("square-partial")) return "p";
  if (className.includes("square-bad")) return "b";
  if (className.includes("square-superior")) return "u";
  if (className.includes("square-inferior")) return "d";
  return "i";
}

function dragonVersion() {
  try {
    return document.querySelector("#app").__vue__.$store.state.general.dragonVersion || "16.17.1";
  } catch {
    return "16.17.1";
  }
}

function championIconUrl(name) {
  if (!name) return "";
  const slug = championSlug[name] || String(name).replace(/['’.]/g, "").replace(/ & .*$/, "").replace(/ /g, "");
  return `/ogurec/proxy/ddragon/cdn/${dragonVersion()}/img/champion/${slug}.png`;
}

function classicCells() {
  const day = loldleDay();
  if (lastClassicDay !== day) {
    lastClassicCells = [];
    lastClassicDay = day;
  }
  if (!modeReady("classic")) return [];
  const root = document.querySelector(".classic-answers-container");
  if (!root) return lastClassicCells;
  const names = readJson("classic_answers", []);
  const rows = [...root.querySelectorAll(".classic-answer")].map((row, index) => {
    const squares = [...row.querySelectorAll(".square-container > .square")]
      .filter((el) => getComputedStyle(el).display !== "none");
    const cells = squares.map((el, cellIndex) => {
      const kind = cellKind(el.className);
      const name = typeof names[index] === "string" ? names[index] : names[index]?.value;
      return {
        k: kind,
        src: cellIndex === 0 ? championIconUrl(name) : "",
      };
    }).filter((cell) => cell.k);
    const attrs = cells.filter((cell) => cell.k !== "i");
    if (cells[0] && cells[0].k === "i" && attrs.length) {
      cells[0].k = attrs.every((cell) => cell.k === "g") ? "g" : "b";
    }
    return cells;
  }).filter((row) => row.length);
  if (rows.length) lastClassicCells = rows;
  return rows.length ? rows : lastClassicCells;
}

function walkVue(visit) {
  const root = document.querySelector("#app")?.__vue__;
  if (!root) return;
  const stack = [root];
  const seen = new Set();
  while (stack.length) {
    const node = stack.pop();
    if (!node || seen.has(node)) continue;
    seen.add(node);
    visit(node);
    for (const child of node.$children || []) stack.push(child);
  }
}

const LOLDLE_KEY = "QhDZJfngdx";

function pathMode() {
  const slug = location.pathname.toLowerCase().split("/").filter(Boolean)[0] || "";
  return modes.some(([mode]) => mode === slug) ? slug : "";
}

function vueMode(mode) {
  let found = null;
  walkVue((vm) => {
    if (vm.options?.keyStorage?.answers === `${mode}_answers`) found = vm;
  });
  return found;
}

function modeReady(mode) {
  const vm = vueMode(mode);
  return Boolean(vm && vm.todayAnswerDecrypted);
}

function guessValue(entry) {
  if (typeof entry === "string") return entry;
  return String(entry?.value || entry?.name || "");
}

function todayChampion(mode) {
  const live = vueMode(mode)?.todayAnswerDecrypted;
  if (live) return String(live);
  if (!modeReady(mode)) return "";
  const encrypted = localStorage.getItem(`${mode}_today_answer`);
  if (!encrypted) return "";
  try {
    return CryptoJS.AES.decrypt(encrypted, LOLDLE_KEY).toString(CryptoJS.enc.Utf8);
  } catch {
    return "";
  }
}

function loldleDay() {
  return new Date().toLocaleDateString("en-CA", {timeZone: "Europe/Paris"});
}

function rememberedWon() {
  const day = loldleDay();
  const data = readJson("ogurecWon", {});
  if (data.day !== day) return {day, modes: {}};
  return {day, modes: data.modes || {}};
}

function wonRecord(mode) {
  const value = rememberedWon().modes[mode];
  if (value && typeof value === "object") return value;
  if (typeof value === "number") return {attempts: value, answer: ""};
  return null;
}

function rememberWon(mode, attempts, answer) {
  const data = rememberedWon();
  const next = {
    attempts: Math.max(1, attempts || wonRecord(mode)?.attempts || 1),
    answer: answer || todayChampion(mode) || wonRecord(mode)?.answer || "",
  };
  const prev = wonRecord(mode);
  if (prev && prev.attempts === next.attempts && prev.answer === next.answer) return;
  data.modes[mode] = next;
  localStorage.setItem("ogurecWon", JSON.stringify(data));
}

function pageWon(mode) {
  const vm = vueMode(mode);
  if (!vm) return false;
  return Boolean(vm.won || vm.finished || vm.endFinished);
}

function liveAnswers(mode) {
  if (!modeReady(mode)) return [];
  const stored = readJson(`${mode}_answers`, []);
  const vue = vueMode(mode)?.answers;
  const fromVue = Array.isArray(vue) ? vue : [];
  return fromVue.length >= stored.length ? fromVue : stored;
}

function guessedToday(mode) {
  const answer = todayChampion(mode);
  if (!answer) return false;
  return liveAnswers(mode).some((guess) => guessValue(guess) === answer);
}

function modeDone(mode, attempts, cells) {
  const remembered = wonRecord(mode);
  if (remembered) return true;
  if (!modeReady(mode)) return false;
  const answer = todayChampion(mode);
  const last = cells[cells.length - 1] || [];
  const attrs = last.filter((cell) => cell.k && cell.k !== "i");
  const classicWin = mode === "classic" && attrs.length >= 6 && attrs.every((cell) => cell.k === "g");
  if (classicWin) {
    rememberWon(mode, attempts || cells.length, answer);
    return true;
  }
  if (guessedToday(mode)) {
    rememberWon(mode, attempts, answer);
    return true;
  }
  if (pathMode() === mode && pageWon(mode)) {
    rememberWon(mode, attempts || 1, answer);
    return true;
  }
  return false;
}

function cachedProgress() {
  const data = readJson("ogurecProgress", {});
  if (data.day !== loldleDay()) return {};
  return data.progress || {};
}

function persistProgress(progress) {
  localStorage.setItem("ogurecProgress", JSON.stringify({day: loldleDay(), progress}));
}

function mergeProgress(local, remote) {
  return Object.fromEntries(
    modes.map(([mode]) => {
      const a = local?.[mode] || {};
      const b = remote?.[mode] || {};
      const done = !!(a.done || b.done);
      const attempts = Math.max(a.attempts || 0, b.attempts || 0);
      const cells = (b.cells?.length || 0) >= (a.cells?.length || 0) ? b.cells || [] : a.cells || [];
      return [mode, {attempts: done ? Math.max(attempts, 1) : attempts, done, cells}];
    }),
  );
}

function localProgress() {
  const live = Object.fromEntries(
    modes.map(([mode]) => {
      const remembered = wonRecord(mode);
      if (!modeReady(mode) && !remembered) {
        return [mode, {attempts: 0, done: false, cells: []}];
      }
      const cells = mode === "classic" ? classicCells() : [];
      const answers = liveAnswers(mode);
      let attempts = answers.length || (mode === "classic" ? cells.length : 0);
      const done = modeDone(mode, attempts, cells);
      if (done) attempts = attempts || remembered?.attempts || 1;
      return [mode, {attempts, done, cells}];
    }),
  );
  return mergeProgress(live, cachedProgress());
}

function sameDayProgress(payload) {
  return payload?.day === loldleDay() ? payload.progress || {} : {};
}

function progress() {
  return mergeProgress(localProgress(), sameDayProgress({day: remoteDay, progress: remoteProgress}));
}

function finishedCount(player) {
  return modes.filter(([mode]) => player.progress?.[mode]?.done).length;
}

function isIos() {
  const ua = navigator.userAgent || "";
  return /iP(hone|od|ad)/.test(ua) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
}

function vibratePattern(pattern) {
  try {
    const vibrate = navigator.vibrate || navigator.webkitVibrate;
    if (typeof vibrate !== "function") return false;
    return Boolean(vibrate.call(navigator, pattern));
  } catch {
    return false;
  }
}

function iosSwitchTick() {
  try {
    const label = document.createElement("label");
    label.setAttribute("aria-hidden", "true");
    Object.assign(label.style, {
      position: "fixed",
      left: "0",
      top: "0",
      width: "1px",
      height: "1px",
      overflow: "hidden",
      pointerEvents: "none",
      opacity: "0.01",
    });
    const input = document.createElement("input");
    input.type = "checkbox";
    input.setAttribute("switch", "");
    input.tabIndex = -1;
    label.append(input);
    document.body.append(label);
    label.click();
    input.click();
    label.remove();
  } catch {}
}

function buzzWin(allDone) {
  if (document.hidden) return;
  if (isIos()) {
    iosSwitchTick();
    setTimeout(iosSwitchTick, 70);
    if (allDone) setTimeout(iosSwitchTick, 150);
    return;
  }
  vibratePattern(allDone ? [55, 45, 80, 45, 80, 45, 160] : [40, 35, 90]);
}

function seedKnownDone(nextProgress = progress()) {
  if (!knownDone) {
    knownDone = new Set(modes.filter(([mode]) => nextProgress?.[mode]?.done).map(([mode]) => mode));
  }
  return knownDone;
}

function celebrateWins(nextProgress) {
  if (document.body.classList.contains("ogurec-locked") || resetting) return;
  const doneModes = modes.filter(([mode]) => nextProgress?.[mode]?.done).map(([mode]) => mode);
  seedKnownDone(nextProgress);
  const fresh = doneModes.filter((mode) => !knownDone.has(mode));
  if (!fresh.length) return;
  for (const mode of fresh) knownDone.add(mode);
  if (document.hidden) return;
  if (fresh.length === 1 && iosBuzzedMode === fresh[0]) return;
  buzzWin(doneModes.length >= modes.length);
}

function scheduleWinBuzz() {
  celebrateWins(progress());
  cancelAnimationFrame(winBuzzTimer);
  winBuzzTimer = requestAnimationFrame(() => celebrateWins(progress()));
  setTimeout(() => celebrateWins(progress()), 140);
  setTimeout(() => celebrateWins(progress()), 420);
}

function eventLooksLikeCorrectGuess(event) {
  const mode = pathMode();
  if (!mode) return false;
  seedKnownDone();
  if (knownDone.has(mode) || wonRecord(mode)) return false;
  const answer = todayChampion(mode);
  if (!answer) return false;
  if (event.key === "Enter") {
    const field = document.activeElement;
    const typed = field && "value" in field ? field.value : "";
    const highlighted = document.querySelector("[aria-selected='true'], li.active, .highlighted, .selected");
    return enterGuessIsCorrect(typed, answer, highlighted?.innerText || "");
  }
  const node = event.target?.closest?.("li, button, [role='option'], a, div, span");
  if (!node || node.closest(".ogurec-party, .ogurec-gate, .ogurec-reset, .classic-answers-container, .classic-answer")) {
    return false;
  }
  return clickGuessIsCorrect(node.innerText || node.textContent, answer);
}

function maybeBuzzCorrectGuess(event) {
  if (document.body.classList.contains("ogurec-locked") || resetting) return;
  const mode = pathMode();
  if (!mode || iosBuzzedMode === mode) return;
  if (!isIos() && event.type !== "keydown") return;
  if (!eventLooksLikeCorrectGuess(event)) return;
  iosBuzzedMode = mode;
  const nextDone = modes.filter(([item]) => item === mode || knownDone?.has(item) || progress()?.[item]?.done).length;
  buzzWin(nextDone >= modes.length);
}

function ruCount(n, one, few, many) {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return `${n} ${one}`;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return `${n} ${few}`;
  return `${n} ${many}`;
}

function modeStatus(result) {
  const attempts = result.attempts || 0;
  if (result.done) {
    if (attempts === 1) return "с 1-й";
    if (attempts === 2) return "со 2-й";
    if (attempts === 3) return "с 3-й";
    return `за ${ruCount(attempts, "попытку", "попытки", "попыток")}`;
  }
  if (attempts) return ruCount(attempts, "попытка", "попытки", "попыток");
  return "ещё нет";
}

function render() {
  const list = [...players.values()];
  panel.querySelector(".ogurec-total").textContent = String(list.length);
  const faces = panel.querySelector(".ogurec-faces");
  faces.replaceChildren(
    ...list.slice(0, 3).map((player) => {
      if (!player.avatar) {
        const mark = document.createElement("span");
        mark.textContent = (player.name || "?").slice(0, 1);
        return mark;
      }
      const avatar = document.createElement("img");
      avatar.src = `https://cdn.discordapp.com/avatars/${player.id}/${player.avatar}.png?size=32`;
      avatar.alt = "";
      return avatar;
    }),
  );
  const root = panel.querySelector(".ogurec-players");
  if (!list.length) {
    root.textContent = "Ждём игроков…";
    return;
  }
  root.replaceChildren(
    ...list.map((player) => {
      const row = document.createElement("div");
      row.className = "ogurec-player";
      const identity = document.createElement("div");
      identity.className = "ogurec-identity";
      if (player.avatar) {
        const avatar = document.createElement("img");
        avatar.src = `https://cdn.discordapp.com/avatars/${player.id}/${player.avatar}.png?size=64`;
        avatar.alt = "";
        identity.append(avatar);
      }
      const name = document.createElement("span");
      name.textContent = player.name;
      identity.append(name);
      const done = finishedCount(player);
      const badge = document.createElement("em");
      badge.textContent = `${done}/5`;
      identity.append(badge);
      const scores = document.createElement("div");
      scores.className = "ogurec-scores";
      for (const [mode, label] of modes) {
        const result = player.progress?.[mode] || {attempts: 0, done: false};
        const cell = document.createElement("i");
        cell.className = result.done ? "done" : result.attempts ? "active" : "idle";
        cell.title = `${label}: ${modeStatus(result)}`;
        scores.append(cell);
      }
      row.append(identity, scores);
      return row;
    }),
  );
}

function setGate(text, failed = false, value = null) {
  const status = gate.querySelector(".ogurec-gate-status");
  const bar = gate.querySelector(".ogurec-progress");
  const fill = gate.querySelector(".ogurec-progress-fill");
  const pct = gate.querySelector(".ogurec-progress-pct");
  const amount = failed ? 100 : Math.max(0, Math.min(100, value ?? 8));
  if (status) status.textContent = text;
  if (fill) fill.style.width = `${amount}%`;
  if (pct) pct.textContent = `${Math.round(amount)}%`;
  if (bar) {
    bar.setAttribute("aria-valuenow", String(Math.round(amount)));
    bar.setAttribute("aria-valuetext", failed ? text : `${Math.round(amount)}% — ${text}`);
  }
  gate.classList.toggle("is-failed", failed);
}

function unlockGame() {
  setGate("Готово", false, 100);
  window.setTimeout(() => {
    document.body.classList.remove("ogurec-locked");
    gate.remove();
    seedKnownDone();
  }, 180);
}

function waitForSocket(socket) {
  return new Promise((resolve, reject) => {
    const fail = () => reject(new Error("Нет сессии «Играют вместе»"));
    const timer = setTimeout(fail, 15000);
    socket.addEventListener("open", () => {
      clearTimeout(timer);
      resolve();
    }, {once: true});
    socket.addEventListener("error", () => {
      clearTimeout(timer);
      fail();
    }, {once: true});
  });
}

function channelFromInstance(instanceId) {
  const text = String(instanceId || "");
  const guild = text.match(/-gc-\d+-(\d+)$/);
  if (guild) return guild[1];
  const priv = text.match(/-pc-(\d+)$/);
  return priv ? priv[1] : "";
}

function readChannelId() {
  const instance = discord?.instanceId || "";
  const params = new URLSearchParams(location.search);
  return String(
    discord?.channelId ||
    channelId ||
    params.get("channel_id") ||
    params.get("channelId") ||
    channelFromInstance(instance) ||
    "",
  );
}

function snapshot() {
  const instance = String(discord?.instanceId || "");
  channelId = readChannelId();
  return {
    id: user.id,
    name: user.global_name || user.username,
    avatar: user.avatar,
    channelId,
    instanceId: instance,
    day: loldleDay(),
    progress: progress(),
  };
}

function publish(force = false) {
  if (!user || resetting) return;
  const state = snapshot();
  persistProgress(state.progress);
  celebrateWins(state.progress);
  players.set(user.id, state);
  render();
  const payload = JSON.stringify(state);
  heartbeat += 1;
  if (!force && payload === lastSent && heartbeat % 5 !== 0) return;
  lastSent = payload;
  if (ws?.readyState === WebSocket.OPEN) ws.send(payload);
  const done = finishedCount(state);
  discord?.commands.setActivity({
    activity: {
      type: 0,
      details: `${done}/5 режимов`,
      state: modes.map(([mode, label]) => {
        const result = state.progress[mode];
        if (result.done) return `${label} ${result.attempts || 1}`;
        if (result.attempts) return `${label} ${result.attempts}`;
        return null;
      }).find(Boolean) || "Классика",
      party: {size: [Math.max(1, players.size), 8]},
    },
  }).catch(() => {});
}

function cookieDomains() {
  const host = location.hostname;
  const parts = host.split(".").filter(Boolean);
  const domains = ["", host];
  for (let i = 0; i <= Math.max(0, parts.length - 2); i += 1) {
    const domain = parts.slice(i).join(".");
    domains.push(domain, `.${domain}`);
  }
  return [...new Set(domains)];
}

function clearCookies() {
  const expire = "expires=Thu, 01 Jan 1970 00:00:00 GMT";
  const paths = ["/", "/ogurec", location.pathname || "/", ""];
  for (const cookie of document.cookie.split(";")) {
    const name = cookie.split("=")[0].trim();
    if (!name) continue;
    for (const domain of cookieDomains()) {
      for (const path of paths) {
        const domainPart = domain ? `domain=${domain};` : "";
        const pathPart = path ? `path=${path};` : "";
        document.cookie = `${name}=;${expire};${pathPart}${domainPart}`;
      }
    }
  }
}

function deleteDatabase(name) {
  if (!name) return Promise.resolve();
  return new Promise((resolve) => {
    try {
      const request = indexedDB.deleteDatabase(name);
      request.onsuccess = request.onerror = request.onblocked = () => resolve();
    } catch {
      resolve();
    }
  });
}

async function clearIndexedDb() {
  try {
    if (indexedDB.databases) {
      const dbs = await indexedDB.databases();
      await Promise.all((dbs || []).map((db) => deleteDatabase(db?.name)));
    }
  } catch {}
}

async function clearCaches() {
  try {
    if (!window.caches?.keys) return;
    const keys = await caches.keys();
    await Promise.all(keys.map((key) => caches.delete(key)));
  } catch {}
}

async function clearBrowserData() {
  try { localStorage.clear(); } catch {}
  try { sessionStorage.clear(); } catch {}
  try { clearCookies(); } catch {}
  await clearIndexedDb();
  await clearCaches();
}

function waitForResetAck(userId, ms = 20000) {
  if (!userId || !ws || ws.readyState !== WebSocket.OPEN) return Promise.resolve(false);
  return new Promise((resolve) => {
    const finish = (ok) => {
      clearTimeout(timer);
      ws.removeEventListener("message", onMessage);
      resolve(ok);
    };
    const timer = setTimeout(() => finish(false), ms);
    function onMessage(event) {
      if (isResetAck(event.data, userId)) finish(true);
    }
    ws.addEventListener("message", onMessage);
  });
}

function syncResetUi() {
  const asking = resetSession.phase === "confirm" || resetSession.phase === "wiping";
  panel.classList.toggle("is-confirming", asking);
  resetConfirm.hidden = !asking;
  resetButton.hidden = asking;
}

function setResetConfirmOpen(open) {
  if (open) resetSession.request();
  else resetSession.cancel();
  syncResetUi();
  if (resetSession.phase === "confirm") setPartyOpen(true);
}

async function resetMyStats() {
  if (resetting || !resetSession.beginWipe()) return;
  resetting = true;
  syncResetUi();
  resetButton.disabled = true;
  resetYes.disabled = true;
  resetNo.disabled = true;
  resetYes.textContent = "Сбрасываем…";
  const ack = user ? waitForResetAck(user.id) : Promise.resolve(false);
  if (user && ws?.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(resetPayload({
      id: user.id,
      channelId: readChannelId(),
      instanceId: String(discord?.instanceId || ""),
      day: loldleDay(),
    })));
  }
  await ack;
  try {
    await discord?.commands.setActivity({activity: {type: 0, details: "", state: ""}});
  } catch {}
  if (user) players.delete(user.id);
  remoteProgress = {};
  remoteDay = "";
  lastSent = "";
  lastClassicCells = [];
  knownDone = new Set();
  iosBuzzedMode = "";
  await clearBrowserData();
  location.reload();
}

resetButton.addEventListener("click", (event) => {
  event.stopPropagation();
  setResetConfirmOpen(true);
});
resetNo.addEventListener("click", (event) => {
  event.stopPropagation();
  setResetConfirmOpen(false);
});
resetYes.addEventListener("click", (event) => {
  event.stopPropagation();
  resetMyStats();
});

function playTestBeep() {
  const AudioCtx = window.AudioContext || window.webkitAudioContext;
  if (!AudioCtx) return Promise.reject(new Error("no audio"));
  const ctx = new AudioCtx();
  const osc = ctx.createOscillator();
  const gain = ctx.createGain();
  osc.frequency.value = 880;
  gain.gain.value = 0.12;
  osc.connect(gain).connect(ctx.destination);
  return ctx.resume().then(() => {
    osc.start();
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.4);
    osc.stop(ctx.currentTime + 0.45);
  });
}

function playTestSound() {
  const audio = new Audio("/ogurec/test-sound.wav");
  audio.play().catch(() => playTestBeep());
}

const CLASSIC_TITLES = {
  "Чемпион": "Чемп",
  "Champion": "Champ",
  "Позиция": "Поз.",
  "Position": "Pos.",
  "Виды": "Вид",
  "Species": "Kind",
  "Ресурс": "Рес.",
  "Resource": "Res.",
  "Тип диапазона": "Тип",
  "Range type": "Range",
  "Регион(ы)": "Регион",
  "Region(s)": "Region",
  "Год выпуска": "Год",
  "Release year": "Year",
};
const CLASSIC_LEGEND = {
  "Неправильно": "Неправ.",
  "Incorrect": "Wrong",
};

function classicLang() {
  return localStorage.getItem("currentLocale") === "EN" ? "en" : "ru";
}

function applyClassicLang() {
  const lang = classicLang();
  document.documentElement.lang = lang;
  for (const node of document.querySelectorAll(".scrollable-answers-fit, .classic-answers-container, .tuto-colors")) {
    node.setAttribute("lang", lang);
  }
}

function rewriteCopy(nodes, map) {
  for (const node of nodes) {
    const text = node.textContent.replace(/\s+/g, " ").trim();
    const next = map[text];
    if (next && node.textContent !== next) node.textContent = next;
  }
}

function classicCellOverflows(cell) {
  if (cell.scrollHeight > cell.clientHeight + 1) return true;
  for (const node of cell.querySelectorAll("span, div")) {
    if (node.childElementCount || !node.textContent.trim()) continue;
    node.style.setProperty("white-space", "nowrap", "important");
    const overflow = node.scrollWidth > node.clientWidth + 1;
    node.style.removeProperty("white-space");
    if (overflow) return true;
  }
  return false;
}

function fitClassicCells() {
  for (const cell of document.querySelectorAll(".scrollable-answers-fit .square:not(.square-title) .square-content")) {
    if (cell.querySelector("img, canvas")) continue;
    cell.style.removeProperty("font-size");
    let px = parseFloat(getComputedStyle(cell).fontSize) || 9;
    for (let i = 0; i < 8 && classicCellOverflows(cell); i += 1) {
      px = Math.max(6.5, px - 0.5);
      cell.style.setProperty("font-size", `${px}px`, "important");
    }
  }
}

let classicFitFrame = 0;
function scheduleClassicFit() {
  applyClassicLang();
  rewriteCopy(document.querySelectorAll(".square-title .square-content, .square-title .square-content-fit"), CLASSIC_TITLES);
  rewriteCopy(
    [...document.querySelectorAll(".tuto-color-container")].flatMap((node) => [...node.querySelectorAll("*")].filter((el) => !el.childElementCount)),
    CLASSIC_LEGEND,
  );
  cancelAnimationFrame(classicFitFrame);
  classicFitFrame = requestAnimationFrame(fitClassicCells);
}

function shortenClassicTitles() {
  scheduleClassicFit();
}

function mountTestSound() {
  const roots = document.querySelectorAll(".audio-player-top");
  for (const root of roots) {
    if (root.querySelector(".ogurec-test-sound")) continue;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "ogurec-test-sound";
    button.textContent = "Тест звука";
    button.title = "Проверка, что звук в Discord вообще играет";
    button.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      playTestSound();
    });
    root.append(button);
  }
}

function removeUnrelated() {
  document.querySelector(".hub-games-container")?.remove();
  document.querySelector(".hub-end")?.remove();
  document.querySelector(".worldsMayhemBanner")?.remove();
  document.querySelector(".worlds-mayhem")?.remove();
  document.querySelector(".foot")?.remove();
  document.querySelector(".button-worlds-badge")?.closest(".button-game")?.remove();
  document.querySelector("#menu > .buttons-container:has(.button-worlds-badge)")?.remove();
  document.querySelectorAll(".share, .complete-share, .settings.top-button, .store-buttons, #modal, #overlay, .overlay-container, .container-app-forwarder").forEach((el) => el.remove());
  if (location.pathname.toLowerCase().includes("worlds")) location.replace("/");
}

function onSocketMessage(event) {
  const state = JSON.parse(event.data);
  if (!state?.id) return;
  if (state.type === "reset") {
    players.delete(state.id);
    if (user && state.id === user.id) {
      remoteProgress = {};
      remoteDay = "";
    }
    render();
    return;
  }
  if (user && state.id === user.id) {
    if (state.day === loldleDay()) {
      remoteDay = state.day;
      remoteProgress = state.progress || {};
      persistProgress(progress());
    }
    players.set(user.id, snapshot());
  } else if (!state.day || state.day === loldleDay()) {
    players.set(state.id, state);
  }
  render();
}

function openSocket() {
  const instance = discord.instanceId;
  const gen = ++socketGen;
  const socket = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ogurec/socket?instance=${encodeURIComponent(instance)}`);
  ws = socket;
  socket.addEventListener("open", () => {
    lastSent = "";
    publish(true);
  });
  socket.addEventListener("message", onSocketMessage);
  socket.addEventListener("close", () => {
    if (gen !== socketGen) return;
    if (!user || document.body.classList.contains("ogurec-locked")) return;
    clearTimeout(reconnectTimer);
    reconnectTimer = setTimeout(openSocket, 800);
  });
  socket.addEventListener("error", () => {
    socket.close();
  });
  return socket;
}

async function connectDiscord() {
  if (!clientId) throw new Error("DISCORD_CLIENT_ID is not configured");
  setGate("Подключение к Discord…", false, 12);
  discord = new DiscordSDK(clientId);
  await discord.ready();
  channelId = readChannelId();
  setGate("Входим…", false, 38);
  const {code} = await discord.commands.authorize({
    client_id: clientId,
    response_type: "code",
    prompt: "none",
    scope: ["identify"],
  });
  setGate("Получаем доступ…", false, 58);
  const tokenResponse = await fetch("/ogurec/token", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({code}),
  });
  const token = await tokenResponse.json();
  if (!token.access_token) {
    throw new Error(token.error_description || token.error || `OAuth token: HTTP ${tokenResponse.status}`);
  }
  setGate("Открываем сессию…", false, 76);
  ({user} = await discord.commands.authenticate({access_token: token.access_token}));
  channelId = readChannelId();
  setGate("Собираем игроков…", false, 90);
  await waitForSocket(openSocket());
}

async function start() {
  try {
    await connectDiscord();
    unlockGame();
    publish(true);
    setInterval(() => publish(), 1000);
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") publish(true);
    });
    window.addEventListener("online", () => publish(true));
  } catch (error) {
    setGate(error.message, true);
    panel.querySelector(".ogurec-players").textContent = "Нет подключения";
  }
}

new MutationObserver(() => {
  removeUnrelated();
  mountTestSound();
  shortenClassicTitles();
}).observe(document.body, {childList: true, subtree: true, characterData: true});
removeUnrelated();
mountTestSound();
shortenClassicTitles();
document.addEventListener("click", (event) => {
  if (!document.body.classList.contains("ogurec-locked")) return;
  if (event.target.closest(".ogurec-party, .ogurec-gate")) return;
  event.preventDefault();
  event.stopPropagation();
}, true);
document.addEventListener("pointerdown", maybeBuzzCorrectGuess, true);
document.addEventListener("click", (event) => {
  if (document.body.classList.contains("ogurec-locked")) return;
  if (event.target.closest(".ogurec-party, .ogurec-gate, .ogurec-reset, .ogurec-reset-confirm")) return;
  maybeBuzzCorrectGuess(event);
  scheduleWinBuzz();
}, true);
document.addEventListener("keydown", (event) => {
  if (document.body.classList.contains("ogurec-locked")) event.preventDefault();
  else if (event.key === "Enter") {
    maybeBuzzCorrectGuess(event);
    scheduleWinBuzz();
  }
}, true);
start();
