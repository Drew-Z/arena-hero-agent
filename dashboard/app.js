"use strict";

const canvas = document.querySelector("#map");
const context = canvas.getContext("2d", { alpha: false });
const ui = {
  tick: document.querySelector("#metric-tick"),
  resources: document.querySelector("#metric-resources"),
  population: document.querySelector("#metric-population"),
  force: document.querySelector("#metric-force"),
  posture: document.querySelector("#metric-posture"),
  enemies: document.querySelector("#metric-enemies"),
  status: document.querySelector("#map-status"),
  slider: document.querySelector("#tick-slider"),
  play: document.querySelector("#toggle-play"),
  live: document.querySelector("#live-tick"),
  events: document.querySelector("#event-list"),
  rankings: document.querySelector("#ranking-list"),
};

const colors = {
  background: "#090c0f",
  grid: "#141a1f",
  explored: "#1d252c",
  obstacle: "#53616c",
  resource: "#40cc87",
  resourceHistory: "#b88f24",
  friendly: "#3db8e3",
  enemy: "#ee6268",
  oldCore: "#873f44",
  beacon: "#f0c84c",
  label: "#e6edf1",
};

const state = {
  ticks: [],
  selectedIndex: -1,
  overview: null,
  leaderboard: null,
  rankingKey: "damage_dealt",
  live: true,
  playing: false,
  playTimer: null,
  centered: false,
  view: { x: 0, y: 0, scale: 9 },
  dragging: false,
  pointer: null,
};

function resizeCanvas() {
  const ratio = Math.max(1, window.devicePixelRatio || 1);
  const rect = canvas.getBoundingClientRect();
  canvas.width = Math.max(1, Math.round(rect.width * ratio));
  canvas.height = Math.max(1, Math.round(rect.height * ratio));
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  draw();
}

function screenPosition(position) {
  const rect = canvas.getBoundingClientRect();
  return [
    rect.width / 2 + (position[0] - state.view.x) * state.view.scale,
    rect.height / 2 + (position[1] - state.view.y) * state.view.scale,
  ];
}

function visibleAt(position) {
  const [x, y] = screenPosition(position);
  const rect = canvas.getBoundingClientRect();
  const margin = state.view.scale * 2;
  return x >= -margin && y >= -margin && x <= rect.width + margin && y <= rect.height + margin;
}

function drawCell(position, color, size = 1) {
  if (!visibleAt(position)) return;
  const [x, y] = screenPosition(position);
  const cell = Math.max(1, state.view.scale * size);
  context.fillStyle = color;
  context.fillRect(x - cell / 2, y - cell / 2, cell, cell);
}

function drawGrid() {
  if (state.view.scale < 7) return;
  const rect = canvas.getBoundingClientRect();
  const left = Math.floor(state.view.x - rect.width / state.view.scale / 2);
  const right = Math.ceil(state.view.x + rect.width / state.view.scale / 2);
  const top = Math.floor(state.view.y - rect.height / state.view.scale / 2);
  const bottom = Math.ceil(state.view.y + rect.height / state.view.scale / 2);
  context.strokeStyle = colors.grid;
  context.lineWidth = 1;
  context.beginPath();
  for (let x = left; x <= right; x += 1) {
    const [sx] = screenPosition([x, 0]);
    context.moveTo(Math.round(sx) + 0.5, 0);
    context.lineTo(Math.round(sx) + 0.5, rect.height);
  }
  for (let y = top; y <= bottom; y += 1) {
    const [, sy] = screenPosition([0, y]);
    context.moveTo(0, Math.round(sy) + 0.5);
    context.lineTo(rect.width, Math.round(sy) + 0.5);
  }
  context.stroke();
}

function drawTrail(points) {
  if (!Array.isArray(points) || points.length < 2) return;
  context.strokeStyle = "rgba(61,184,227,0.24)";
  context.lineWidth = 1.2;
  context.beginPath();
  points.forEach((position, index) => {
    const [x, y] = screenPosition(position);
    if (index === 0) context.moveTo(x, y);
    else context.lineTo(x, y);
  });
  context.stroke();
}

function drawCore(item, friendly, historical = false) {
  const [x, y] = screenPosition(item.position || [item.x, item.y]);
  const size = Math.max(7, state.view.scale * 0.82);
  context.save();
  context.globalAlpha = historical ? 0.52 : 1;
  context.setLineDash(historical ? [4, 3] : []);
  context.fillStyle = historical ? colors.oldCore : friendly ? colors.friendly : colors.enemy;
  context.fillRect(x - size / 2, y - size / 2, size, size);
  context.strokeStyle = historical ? "#cb7178" : colors.label;
  context.lineWidth = historical ? 1 : 1.5;
  context.strokeRect(x - size / 2, y - size / 2, size, size);
  if (!friendly && state.view.scale >= 5) {
    const name = item.owner_username ? `@${item.owner_username}` : "敌方 Core";
    const age = historical ? ` · 最后发现 ${item.age_ticks}T 前` : "";
    context.fillStyle = historical ? "#c78388" : "#ff9b9f";
    context.font = "11px Segoe UI, Microsoft YaHei, sans-serif";
    context.fillText(`${name}${age}`, x + size / 2 + 5, y + 4);
  }
  context.restore();
}

function drawUnit(item, friendly) {
  const [x, y] = screenPosition(item.position);
  const radius = Math.max(2.5, state.view.scale * 0.28);
  context.fillStyle = friendly ? colors.friendly : colors.enemy;
  context.strokeStyle = friendly ? "#a8e8ff" : "#ffb0b3";
  context.lineWidth = 1;
  context.beginPath();
  if (item.unit_type === "VANGUARD") {
    context.moveTo(x, y - radius * 1.45);
    context.lineTo(x + radius * 1.45, y);
    context.lineTo(x, y + radius * 1.45);
    context.lineTo(x - radius * 1.45, y);
    context.closePath();
  } else if (item.unit_type === "RANGER") {
    context.rect(x - radius, y - radius, radius * 2, radius * 2);
  } else {
    context.arc(x, y, radius, 0, Math.PI * 2);
  }
  context.fill();
  context.stroke();
}

function drawPlan(overview, objectById) {
  const deltas = { UP: [0, -1], DOWN: [0, 1], LEFT: [-1, 0], RIGHT: [1, 0] };
  const actions = overview.plan?.unit_actions || {};
  context.strokeStyle = "rgba(240,200,76,0.7)";
  context.lineWidth = 1.5;
  for (const [id, action] of Object.entries(actions)) {
    if (action.type !== "MOVE" || !deltas[action.direction]) continue;
    const object = objectById.get(id);
    if (!object) continue;
    const destination = [object.position[0] + deltas[action.direction][0], object.position[1] + deltas[action.direction][1]];
    const [x1, y1] = screenPosition(object.position);
    const [x2, y2] = screenPosition(destination);
    context.beginPath();
    context.moveTo(x1, y1);
    context.lineTo(x2, y2);
    context.stroke();
  }
}

function draw() {
  const rect = canvas.getBoundingClientRect();
  context.fillStyle = colors.background;
  context.fillRect(0, 0, rect.width, rect.height);
  drawGrid();
  const overview = state.overview;
  if (!overview?.available) {
    context.fillStyle = "#76838b";
    context.font = "14px Segoe UI, Microsoft YaHei, sans-serif";
    context.textAlign = "center";
    context.fillText("等待 Agent 历史数据", rect.width / 2, rect.height / 2);
    context.textAlign = "start";
    return;
  }
  overview.explored.forEach(([x, y]) => drawCell([x, y], colors.explored));
  overview.obstacles.forEach(([x, y]) => drawCell([x, y], colors.obstacle, 0.72));
  overview.resource_history.forEach(([x, y]) => drawCell([x, y], colors.resourceHistory, 0.34));
  Object.values(overview.trails || {}).forEach(drawTrail);

  const objects = overview.state.objects || [];
  overview.enemy_core_history
    .filter((item) => !item.currently_visible)
    .forEach((item) => drawCore(item, false, true));

  const objectById = new Map();
  for (const item of objects) {
    if (item.kind === "RESOURCE") item.positions.forEach((position) => drawCell(position, colors.resource, 0.5));
    if (item.id) objectById.set(item.id, item);
  }
  for (const item of objects) {
    if (item.kind === "CORE") drawCore(item, item.controlled);
    if (item.kind === "UNIT") drawUnit(item, item.controlled);
  }
  drawPlan(overview, objectById);

  const beacon = overview.state.champion_beacon;
  if (beacon?.position) {
    const [x, y] = screenPosition(beacon.position);
    const size = Math.max(5, state.view.scale * 0.5);
    context.fillStyle = colors.beacon;
    context.beginPath();
    context.moveTo(x, y - size);
    context.lineTo(x + size, y);
    context.lineTo(x, y + size);
    context.lineTo(x - size, y);
    context.closePath();
    context.fill();
  }
}

function controlledCore() {
  return state.overview?.state?.objects?.find((item) => item.kind === "CORE" && item.controlled);
}

function centerMap(force = false) {
  const core = controlledCore();
  if (!core || (state.centered && !force)) return;
  state.view.x = core.position[0];
  state.view.y = core.position[1];
  state.centered = true;
  draw();
}

function updateMetrics() {
  const overview = state.overview;
  if (!overview?.available) return;
  const game = overview.state;
  const units = game.objects.filter((item) => item.kind === "UNIT" && item.controlled);
  const workers = units.filter((item) => item.unit_type === "WORKER").length;
  const vanguards = units.filter((item) => item.unit_type === "VANGUARD").length;
  const rangers = units.filter((item) => item.unit_type === "RANGER").length;
  const enemies = game.objects.filter((item) => item.controlled === false).length;
  ui.tick.textContent = overview.tick;
  ui.resources.textContent = `${game.resources}/${Math.max(10, game.population * 5)}`;
  ui.population.textContent = game.population;
  ui.force.textContent = `${workers}W ${vanguards}V ${rangers}R`;
  ui.posture.textContent = overview.strategy.phase || overview.strategy.posture || "--";
  ui.enemies.textContent = enemies;
  const mode = state.live ? "实时" : "历史";
  ui.status.textContent = `${mode} · 已探索 ${overview.explored.length} · 历史 Core ${overview.enemy_core_history.length} · 缩放 ${state.view.scale.toFixed(1)}`;
}

function eventClass(type) {
  if (type.includes("DAMAGED") || type.includes("DESTROYED") || type.includes("SHOT") || type.includes("SWEEP")) return "combat";
  if (type.includes("FAILED") || type.includes("OVERFLOW")) return "warning";
  return "success";
}

function renderEvents() {
  const events = state.overview?.state?.events || [];
  ui.events.replaceChildren();
  if (!events.length) {
    const item = document.createElement("li");
    item.className = "empty-state";
    item.textContent = "当前 Tick 无事件";
    ui.events.append(item);
    return;
  }
  [...events].reverse().forEach((event) => {
    const item = document.createElement("li");
    const tick = document.createElement("span");
    tick.className = "event-tick";
    tick.textContent = `t${event.tick}`;
    const text = document.createElement("span");
    text.className = eventClass(event.event_type);
    const position = event.position ? ` @ ${event.position[0]},${event.position[1]}` : "";
    const reason = event.reason_code ? ` / ${event.reason_code}` : "";
    text.textContent = `${event.event_type}${reason}${position}`;
    item.append(tick, text);
    ui.events.append(item);
  });
}

function ownUsername() {
  return controlledCore()?.owner_username || "";
}

function renderRanking() {
  ui.rankings.replaceChildren();
  const entries = state.leaderboard?.[state.rankingKey] || [];
  if (!entries.length) {
    const item = document.createElement("li");
    item.className = "empty-state";
    item.textContent = state.leaderboard?.available === false ? "排行榜暂不可用" : "暂无排名";
    ui.rankings.append(item);
    return;
  }
  const me = ownUsername().toLowerCase();
  entries.forEach((entry) => {
    const item = document.createElement("li");
    if (entry.username.toLowerCase() === me) item.className = "me";
    const rank = document.createElement("span");
    rank.className = "rank";
    rank.textContent = `#${entry.rank}`;
    const username = document.createElement("span");
    username.className = "username";
    username.textContent = `@${entry.username}`;
    const score = document.createElement("span");
    score.className = "score";
    score.textContent = entry.score.toLocaleString();
    item.append(rank, username, score);
    ui.rankings.append(item);
  });
}

async function fetchJson(url) {
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.json();
}

async function loadOverview(tick = null) {
  const query = tick === null ? "" : `?tick=${encodeURIComponent(tick)}`;
  state.overview = await fetchJson(`/api/overview${query}`);
  if (state.overview.available) {
    centerMap(false);
    updateMetrics();
    renderEvents();
    renderRanking();
  }
  draw();
}

async function refreshTicks() {
  try {
    const payload = await fetchJson("/api/ticks?limit=1024");
    const previousLatest = state.ticks.at(-1)?.tick;
    state.ticks = payload.ticks || [];
    ui.slider.max = Math.max(0, state.ticks.length - 1);
    if (state.live) {
      state.selectedIndex = state.ticks.length - 1;
      ui.slider.value = Math.max(0, state.selectedIndex);
      const latest = state.ticks.at(-1)?.tick;
      if (latest !== previousLatest || !state.overview) await loadOverview();
    }
  } catch (error) {
    ui.status.textContent = `历史接口错误 · ${error.message}`;
  }
}

async function refreshLeaderboard() {
  try {
    state.leaderboard = await fetchJson("/api/leaderboard");
    renderRanking();
  } catch (error) {
    state.leaderboard = { available: false, error: error.message };
    renderRanking();
  }
}

async function selectIndex(index) {
  if (!state.ticks.length) return;
  state.selectedIndex = Math.max(0, Math.min(index, state.ticks.length - 1));
  state.live = state.selectedIndex === state.ticks.length - 1;
  ui.live.classList.toggle("active", state.live);
  ui.slider.value = state.selectedIndex;
  await loadOverview(state.ticks[state.selectedIndex].tick);
}

function togglePlay() {
  state.playing = !state.playing;
  ui.play.textContent = state.playing ? "Ⅱ" : "▶";
  ui.play.title = state.playing ? "暂停历史" : "播放历史";
  clearInterval(state.playTimer);
  if (state.playing) {
    state.live = false;
    ui.live.classList.remove("active");
    state.playTimer = setInterval(() => {
      if (state.selectedIndex >= state.ticks.length - 1) {
        state.playing = false;
        ui.play.textContent = "▶";
        clearInterval(state.playTimer);
        return;
      }
      selectIndex(state.selectedIndex + 1);
    }, 700);
  }
}

function setPanel(name) {
  const events = name === "events";
  document.querySelector("#events-tab").classList.toggle("active", events);
  document.querySelector("#ranking-tab").classList.toggle("active", !events);
  document.querySelector("#events-tab").setAttribute("aria-selected", events);
  document.querySelector("#ranking-tab").setAttribute("aria-selected", !events);
  document.querySelector("#events-panel").classList.toggle("hidden", !events);
  document.querySelector("#ranking-panel").classList.toggle("hidden", events);
}

canvas.addEventListener("pointerdown", (event) => {
  state.dragging = true;
  state.pointer = [event.clientX, event.clientY];
  canvas.classList.add("dragging");
  canvas.setPointerCapture(event.pointerId);
});
canvas.addEventListener("pointermove", (event) => {
  if (!state.dragging) return;
  state.view.x -= (event.clientX - state.pointer[0]) / state.view.scale;
  state.view.y -= (event.clientY - state.pointer[1]) / state.view.scale;
  state.pointer = [event.clientX, event.clientY];
  draw();
});
canvas.addEventListener("pointerup", (event) => {
  state.dragging = false;
  canvas.classList.remove("dragging");
  canvas.releasePointerCapture(event.pointerId);
});
canvas.addEventListener("wheel", (event) => {
  event.preventDefault();
  state.view.scale = Math.max(1.5, Math.min(32, state.view.scale * (event.deltaY < 0 ? 1.14 : 0.88)));
  updateMetrics();
  draw();
}, { passive: false });

document.querySelector("#previous-tick").addEventListener("click", () => selectIndex(state.selectedIndex - 1));
document.querySelector("#next-tick").addEventListener("click", () => selectIndex(state.selectedIndex + 1));
document.querySelector("#toggle-play").addEventListener("click", togglePlay);
document.querySelector("#live-tick").addEventListener("click", () => selectIndex(state.ticks.length - 1));
document.querySelector("#center-map").addEventListener("click", () => centerMap(true));
document.querySelector("#zoom-in").addEventListener("click", () => { state.view.scale = Math.min(32, state.view.scale * 1.25); updateMetrics(); draw(); });
document.querySelector("#zoom-out").addEventListener("click", () => { state.view.scale = Math.max(1.5, state.view.scale * 0.8); updateMetrics(); draw(); });
ui.slider.addEventListener("input", () => selectIndex(Number(ui.slider.value)));
document.querySelector("#events-tab").addEventListener("click", () => setPanel("events"));
document.querySelector("#ranking-tab").addEventListener("click", () => setPanel("ranking"));
document.querySelectorAll(".ranking-mode").forEach((button) => button.addEventListener("click", () => {
  state.rankingKey = button.dataset.ranking;
  document.querySelectorAll(".ranking-mode").forEach((item) => item.classList.toggle("active", item === button));
  renderRanking();
}));

new ResizeObserver(resizeCanvas).observe(canvas);
refreshTicks();
refreshLeaderboard();
setInterval(refreshTicks, 5000);
setInterval(refreshLeaderboard, 15000);
