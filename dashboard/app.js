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
  killStats: document.querySelector("#kill-stats"),
  kills: document.querySelector("#kill-list"),
  orders: document.querySelector("#order-list"),
  unitList: document.querySelector("#order-unit-list"),
  orderForm: document.querySelector("#order-form"),
  orderStatus: document.querySelector("#order-status"),
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
  kills: null,
  orders: [],
  controlUnits: [],
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

function renderControl() {
  const stats = state.kills || {};
  ui.killStats.replaceChildren();
  [
    ["单位摧毁参与", stats.unit_participations || 0],
    ["Core 摧毁参与", stats.core_participations || 0],
    ["合计", stats.total_participations || 0],
  ].forEach(([label, value]) => {
    const item = document.createElement("span");
    item.textContent = label;
    const number = document.createElement("strong");
    number.textContent = value;
    item.append(number);
    ui.killStats.append(item);
  });

  ui.kills.replaceChildren();
  const recentKills = stats.recent || [];
  if (!recentKills.length) {
    const item = document.createElement("li");
    item.className = "empty-state";
    item.textContent = "暂无摧毁记录";
    ui.kills.append(item);
  } else {
    recentKills.forEach((kill) => {
      const item = document.createElement("li");
      const position = Array.isArray(kill.position) ? ` @ ${kill.position[0]},${kill.position[1]}` : "";
      item.textContent = `t${kill.tick} ${kill.kind === "CORE" ? "Core" : "单位"}${position}`;
      ui.kills.append(item);
    });
  }

  ui.orders.replaceChildren();
  if (!state.orders.length) {
    const item = document.createElement("li");
    item.className = "empty-state";
    item.textContent = "暂无调兵记录";
    ui.orders.append(item);
  } else {
    state.orders.forEach((order) => {
      const item = document.createElement("li");
      item.className = "order-item";
      const unitIds = order.unit_ids?.length
        ? order.unit_ids.map((id) => id.slice(0, 8)).join(", ")
        : "旧订单未指定单位";
      const summary = document.createElement("span");
      summary.textContent = `#${order.id} ${order.unit_type} x${order.unit_count} → (${order.target_x},${order.target_y}) / ${order.status} / ${unitIds}`;
      item.append(summary);
      if (order.status === "PENDING") {
        const cancel = document.createElement("button");
        cancel.type = "button";
        cancel.dataset.cancelOrder = order.id;
        cancel.textContent = "取消";
        cancel.title = "取消订单，单位将在下一 Tick 恢复自主策略";
        item.append(cancel);
      }
      ui.orders.append(item);
    });
  }
  renderUnitPicker();
}

function renderUnitPicker() {
  const selectedType = document.querySelector("#order-unit-type").value;
  const selectedIds = new Set(
    [...ui.unitList.querySelectorAll("input:checked")].map((input) => input.value),
  );
  const units = state.controlUnits
    .filter((unit) => unit.unit_type === selectedType)
    .sort((left, right) => left.id.localeCompare(right.id));
  ui.unitList.replaceChildren();
  if (!units.length) {
    const empty = document.createElement("span");
    empty.className = "empty-state";
    empty.textContent = `当前没有 ${selectedType}`;
    ui.unitList.append(empty);
  } else {
    units.forEach((unit) => {
      const label = document.createElement("label");
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.value = unit.id;
      checkbox.checked = selectedIds.has(unit.id);
      const cargo = unit.unit_type === "WORKER" ? ` / 载货 ${unit.cargo}` : "";
      const text = document.createElement("span");
      text.textContent = `${unit.id.slice(0, 8)} / (${unit.position[0]},${unit.position[1]}) / HP ${unit.hp}${cargo}`;
      label.append(checkbox, text);
      ui.unitList.append(label);
    });
  }
  document.querySelector("#order-count").value = ui.unitList.querySelectorAll("input:checked").length;
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

async function refreshControl() {
  try {
    const [kills, orders, overview] = await Promise.all([
      fetchJson("/api/kills"),
      fetchJson("/api/orders"),
      fetchJson("/api/overview"),
    ]);
    state.kills = kills;
    state.orders = orders || [];
    state.controlUnits = (overview.state?.objects || []).filter(
      (item) => item.kind === "UNIT" && item.controlled === true,
    );
    renderControl();
  } catch (error) {
    ui.orderStatus.textContent = `调兵接口错误 · ${error.message}`;
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
  ["events", "ranking", "control"].forEach((item) => {
    const active = item === name;
    document.querySelector(`#${item}-tab`).classList.toggle("active", active);
    document.querySelector(`#${item}-tab`).setAttribute("aria-selected", active);
    document.querySelector(`#${item}-panel`).classList.toggle("hidden", !active);
  });
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
document.querySelector("#control-tab").addEventListener("click", () => setPanel("control"));
document.querySelectorAll(".ranking-mode").forEach((button) => button.addEventListener("click", () => {
  state.rankingKey = button.dataset.ranking;
  document.querySelectorAll(".ranking-mode").forEach((item) => item.classList.toggle("active", item === button));
  renderRanking();
}));

ui.orderForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  ui.orderStatus.textContent = "提交中…";
  const unitIds = [...ui.unitList.querySelectorAll("input:checked")].map((input) => input.value);
  if (!unitIds.length) {
    ui.orderStatus.textContent = "请先选择至少一个具体单位";
    return;
  }
  const payload = {
    unit_type: document.querySelector("#order-unit-type").value,
    unit_count: unitIds.length,
    unit_ids: unitIds,
    target_x: Number(document.querySelector("#order-x").value),
    target_y: Number(document.querySelector("#order-y").value),
  };
  try {
    const response = await fetch("/api/orders", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.message || result.error || response.statusText);
    ui.orderStatus.textContent = `已提交 #${result.id}，将在下个 Tick 调整动作`;
    await refreshControl();
  } catch (error) {
    ui.orderStatus.textContent = `提交失败 · ${error.message}`;
  }
});

document.querySelector("#order-unit-type").addEventListener("change", renderUnitPicker);
ui.unitList.addEventListener("change", () => {
  document.querySelector("#order-count").value = ui.unitList.querySelectorAll("input:checked").length;
});
ui.orders.addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-cancel-order]");
  if (!button) return;
  button.disabled = true;
  try {
    const response = await fetch(`/api/orders/${encodeURIComponent(button.dataset.cancelOrder)}`, {
      method: "DELETE",
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.message || result.error || response.statusText);
    ui.orderStatus.textContent = `已取消 #${result.id}，所选单位将在下一 Tick 恢复自主策略`;
    await refreshControl();
  } catch (error) {
    button.disabled = false;
    ui.orderStatus.textContent = `取消失败 · ${error.message}`;
  }
});

new ResizeObserver(resizeCanvas).observe(canvas);
refreshTicks();
refreshLeaderboard();
refreshControl();
setInterval(refreshTicks, 5000);
setInterval(refreshLeaderboard, 15000);
setInterval(refreshControl, 5000);
