import {DiscordSDK} from "@discord/embedded-app-sdk";
import CryptoJS from "crypto-js";

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
  </div>
`;
document.body.append(panel);

const gate = document.createElement("div");
gate.className = "ogurec-gate";
gate.innerHTML = `
  <div class="ogurec-gate-card">
    <div class="ogurec-loader" aria-hidden="true"><i></i><i></i><i></i><i></i><i></i></div>
    <strong>Играют вместе</strong>
    <p class="ogurec-gate-status">Подключение к Discord…</p>
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
  const slug = location.pathname.toLowerCase().split("/").filter(Boolean)[0] || "classic";
  return modes.some(([mode]) => mode === slug) ? slug : "";
}

function guessValue(entry) {
  if (typeof entry === "string") return entry;
  return String(entry?.value || entry?.name || "");
}

function todayChampion(mode) {
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

function pageWon() {
  if (document.querySelector(".finished")) return true;
  let won = false;
  walkVue((vm) => {
    if (vm.won || vm.finished || vm.endFinished || vm.options?.won) won = true;
  });
  return won;
}

function liveAnswers(mode) {
  const stored = readJson(`${mode}_answers`, []);
  let vue = [];
  walkVue((vm) => {
    if (vm.options?.keyStorage?.answers === `${mode}_answers` && Array.isArray(vm.answers)) {
      vue = vm.answers;
    }
  });
  return vue.length >= stored.length ? vue : stored;
}

function guessedToday(mode) {
  const answer = todayChampion(mode);
  if (!answer) return false;
  return liveAnswers(mode).some((guess) => guessValue(guess) === answer);
}

function modeDone(mode, attempts, cells) {
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
  if (pathMode() === mode && pageWon()) {
    rememberWon(mode, attempts || 1, answer);
    return true;
  }
  const remembered = wonRecord(mode);
  if (remembered && (!remembered.answer || !answer || remembered.answer === answer)) {
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
      const cells = mode === "classic" ? classicCells() : [];
      const answers = liveAnswers(mode);
      let attempts = answers.length || (mode === "classic" ? cells.length : 0);
      const done = modeDone(mode, attempts, cells);
      if (done) attempts = attempts || wonRecord(mode)?.attempts || 1;
      return [mode, {attempts, done, cells}];
    }),
  );
  return mergeProgress(live, cachedProgress());
}

function progress() {
  return mergeProgress(localProgress(), remoteProgress);
}

function finishedCount(player) {
  return modes.filter(([mode]) => player.progress?.[mode]?.done).length;
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

function setGate(text, failed = false) {
  const status = gate.querySelector(".ogurec-gate-status");
  if (status) status.textContent = text;
  gate.classList.toggle("is-failed", failed);
}

function unlockGame() {
  document.body.classList.remove("ogurec-locked");
  gate.remove();
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

function snapshot() {
  channelId = String(discord?.channelId || channelId || "");
  return {
    id: user.id,
    name: user.global_name || user.username,
    avatar: user.avatar,
    channelId,
    progress: progress(),
  };
}

function publish(force = false) {
  if (!user) return;
  const state = snapshot();
  persistProgress(state.progress);
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
  if (user && state.id === user.id) {
    remoteProgress = state.progress || {};
    persistProgress(progress());
    players.set(user.id, snapshot());
  } else {
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
  setGate("Подключение к Discord…");
  discord = new DiscordSDK(clientId);
  await discord.ready();
  channelId = discord.channelId || channelId || "";
  setGate("Входим…");
  const {code} = await discord.commands.authorize({
    client_id: clientId,
    response_type: "code",
    prompt: "none",
    scope: ["identify"],
  });
  const tokenResponse = await fetch("/ogurec/token", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({code}),
  });
  const token = await tokenResponse.json();
  if (!token.access_token) {
    throw new Error(token.error_description || token.error || `OAuth token: HTTP ${tokenResponse.status}`);
  }
  setGate("Открываем сессию…");
  ({user} = await discord.commands.authenticate({access_token: token.access_token}));
  channelId = discord.channelId || channelId || "";
  setGate("Собираем игроков…");
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
}).observe(document.body, {childList: true, subtree: true});
removeUnrelated();
mountTestSound();
document.addEventListener("click", (event) => {
  if (!document.body.classList.contains("ogurec-locked")) return;
  if (event.target.closest(".ogurec-party, .ogurec-gate")) return;
  event.preventDefault();
  event.stopPropagation();
}, true);
document.addEventListener("keydown", (event) => {
  if (document.body.classList.contains("ogurec-locked")) event.preventDefault();
}, true);
start();
