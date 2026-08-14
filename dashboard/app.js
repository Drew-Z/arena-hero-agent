"use strict";

const canvas = document.querySelector("#map");
const context = canvas.getContext("2d", { alpha: false });
const MAP_CHUNK_SIZE = 32;
const MAP_LAYERS = ["explored", "obstacles", "resource_history"];
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
  killHeading: document.querySelector("#kill-heading"),
  killStats: document.querySelector("#kill-stats"),
  kills: document.querySelector("#kill-list"),
  losses: document.querySelector("#loss-list"),
  revenge: document.querySelector("#revenge-list"),
  orders: document.querySelector("#order-list"),
  unitList: document.querySelector("#order-unit-list"),
  orderForm: document.querySelector("#order-form"),
  orderStatus: document.querySelector("#order-status"),
  orderSelectionMode: document.querySelector("#order-selection-mode"),
  orderDistanceField: document.querySelector("#order-distance-field"),
  orderMinDistance: document.querySelector("#order-min-distance"),
  pickTarget: document.querySelector("#pick-order-target"),
  orderX: document.querySelector("#order-x"),
  orderY: document.querySelector("#order-y"),
  cursorPosition: document.querySelector("#cursor-position"),
  hoverTooltip: document.querySelector("#hover-tooltip"),
  productionForm: document.querySelector("#production-form"),
  productionStatus: document.querySelector("#production-status"),
  allianceForm: document.querySelector("#alliance-form"),
  allianceStatus: document.querySelector("#alliance-status"),
  expeditionForm: document.querySelector("#expedition-form"),
  expeditionStatus: document.querySelector("#expedition-status"),
  expeditionList: document.querySelector("#expedition-list"),
  pickExpeditionTarget: document.querySelector("#pick-expedition-target"),
};

const colors = {
  background: "#090c0f",
  grid: "#141a1f",
  explored: "#1d252c",
  obstacle: "#53616c",
  resource: "#40cc87",
  resourceHistory: "#b88f24",
  friendly: "#3db8e3",
  ally: "#ffb7c5",
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
  pointerStart: null,
  pickingTarget: false,
  orderTarget: null,
  controlConfig: { production: null, alliance: { rally_radius: 12 }, expeditions: [] },
  layers: { explored: true, obstacles: true, resources: true, history: true, routes: false },
  pickMode: null,
  viewport: { width: 1, height: 1 },
  mapIndex: Object.fromEntries(MAP_LAYERS.map((name) => [name, new Map()])),
  unitFilter: "ALL",
  useRelativeCoords: false,
};
// 获取己方 Core 的绝对坐标
function getCorePosition() {
  const core = controlledCore();
  return core?.position || null;
}

// 绝对坐标 -> 相对坐标
function toRelativePos(worldPos) {
  const corePos = getCorePosition();
  if (!corePos) return worldPos;
  return [worldPos[0] - corePos[0], worldPos[1] - corePos[1]];
}

// 相对坐标 -> 绝对坐标 (发送给后台用)
function toAbsolutePos(relPos) {
  const corePos = getCorePosition();
  if (!corePos) return relPos;
  return [corePos[0] + relPos[0], corePos[1] + relPos[1]];
}

// 格式化坐标文本显示
function formatCoordDisplay(worldPos) {
  const [wx, wy] = worldPos;
  const rel = toRelativePos(worldPos);
  
  if (state.useRelativeCoords && rel) {
    const rx = rel[0] >= 0 ? `+${rel[0]}` : rel[0];
    const ry = rel[1] >= 0 ? `+${rel[1]}` : rel[1];
    return `Δ x ${rx} · y ${ry} (绝对: ${wx}, ${wy})`;
  }
  return `x ${wx} · y ${wy}`;
}

let drawFrame = 0;
//悬停计时相关变量
let hoverTimer = null;
let currentHoverCell = null;
const HOVER_DELAY = 1000; // 悬停触发延迟（单位：毫秒，可根据需求调整）

function clearHover() {
  if (hoverTimer) {
    clearTimeout(hoverTimer);
    hoverTimer = null;
  }
  currentHoverCell = null;
  if (ui.hoverTooltip) {
    ui.hoverTooltip.classList.add("hidden");
  }
}

function showHoverTooltip(x, y) {
  if (!ui.hoverTooltip) return;
  // 计算方格在 Canvas 上的屏幕坐标
  const [sx, sy] = screenPosition([x, y]);
  
  // 🌟 支持悬停提示框显示相对坐标
  const rel = toRelativePos([x, y]);
  if (state.useRelativeCoords && rel) {
    const rx = rel[0] >= 0 ? `+${rel[0]}` : rel[0];
    const ry = rel[1] >= 0 ? `+${rel[1]}` : rel[1];
    ui.hoverTooltip.textContent = `相对 Core: (${rx}, ${ry})`;
  } else {
    ui.hoverTooltip.textContent = `坐标: (${x}, ${y})`;
  }
  ui.hoverTooltip.style.left = `${sx}px`;
  ui.hoverTooltip.style.top = `${sy}px`;
  ui.hoverTooltip.classList.remove("hidden");
}

function shouldDrawObject(item) {
  // 核心 CORE 永远显示
  if (item.kind === "CORE") return true;
  // 全部显示模式
  if (state.unitFilter === "ALL") return true;
  // 按指定兵种过滤
  return item.kind === "UNIT" && item.unit_type === state.unitFilter;
}
function selectUnitInForm(unit, isMultiSelect = false) {
  const typeSelect = document.querySelector("#order-unit-type");
  const unitType = unit.kind === "CORE" ? "CORE" : unit.unit_type;

  // 如果点击了不同兵种，自动切换列表
  if (typeSelect.value !== unitType) {
    typeSelect.value = unitType;
    renderUnitPicker();
  }

  const checkbox = ui.unitList.querySelector(`input[value="${unit.id}"]`);

  if (isMultiSelect) {
    // 多选模式：累加当前单位的勾选状态
    if (checkbox) {
      checkbox.checked = !checkbox.checked;
    }
  } else {
    // 单选模式：取消其他勾选，只保留当前这 1 个单位
    ui.unitList.querySelectorAll("input:checked").forEach((cb) => {
      if (cb !== checkbox) cb.checked = false;
    });
    if (checkbox) checkbox.checked = true;
  }

  // 统计已选中的单位总数
  const checkedBoxes = ui.unitList.querySelectorAll("input:checked");
  document.querySelector("#order-count").value = checkedBoxes.length;

  setPanel("control");
  setTargetPicking(true, "order");
  
  const orderForm = document.querySelector("#order-form");
  if (orderForm) {
    orderForm.classList.remove("section-collapsed");
  }

  if (checkedBoxes.length > 0) {
    ui.orderStatus.textContent = `已选中 ${checkedBoxes.length} 个 ${unitType}（按住 Ctrl 可继续多选），请点击地图标记目的地`;
  } else {
    ui.orderStatus.textContent = "未选中任何单位，请点击选择单位";
  }
}
function drawSelectedUnitsHighlight() {
  if (!state.pickingTarget) return;
  const checkedIds = new Set(
    [...ui.unitList.querySelectorAll("input:checked")].map((input) => input.value)
  );
  if (!checkedIds.size) return;

  const objects = state.overview?.state?.objects || [];
  context.save();
  context.strokeStyle = colors.beacon; // 黄金色高亮
  context.lineWidth = 2;
  context.setLineDash([4, 4]);

  for (const item of objects) {
    if (checkedIds.has(item.id) && item.position) {
      const [x, y] = screenPosition(item.position);
      const radius = Math.max(8, state.view.scale * 0.6);
      context.beginPath();
      context.arc(x, y, radius, 0, Math.PI * 2);
      context.stroke();
    }
  }
  context.restore();
}
function resizeCanvas() {
  const ratio = Math.max(1, window.devicePixelRatio || 1);
  const rect = canvas.getBoundingClientRect();
  state.viewport = { width: rect.width, height: rect.height };
  canvas.width = Math.max(1, Math.round(rect.width * ratio));
  canvas.height = Math.max(1, Math.round(rect.height * ratio));
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  draw();
}

function screenPosition(position) {
  return [
    state.viewport.width / 2 + (position[0] - state.view.x) * state.view.scale,
    state.viewport.height / 2 + (position[1] - state.view.y) * state.view.scale,
  ];
}

function worldPosition(clientX, clientY) {
  const rect = canvas.getBoundingClientRect();
  return [
    Math.round(state.view.x + (clientX - rect.left - rect.width / 2) / state.view.scale),
    Math.round(state.view.y + (clientY - rect.top - rect.height / 2) / state.view.scale),
  ];
}

function visibleAt(position) {
  const [x, y] = screenPosition(position);
  const margin = state.view.scale * 2;
  return x >= -margin && y >= -margin
    && x <= state.viewport.width + margin && y <= state.viewport.height + margin;
}

function indexCells(name, cells, reset = false) {
  const index = state.mapIndex[name];
  if (reset) index.clear();
  cells.forEach((cell) => {
    const key = `${Math.floor(cell[0] / MAP_CHUNK_SIZE)},${Math.floor(cell[1] / MAP_CHUNK_SIZE)}`;
    if (!index.has(key)) index.set(key, []);
    index.get(key).push(cell);
  });
}

function drawIndexedCells(name, color, size = 1) {
  const halfWidth = state.viewport.width / state.view.scale / 2 + 2;
  const halfHeight = state.viewport.height / state.view.scale / 2 + 2;
  const left = Math.floor((state.view.x - halfWidth) / MAP_CHUNK_SIZE);
  const right = Math.floor((state.view.x + halfWidth) / MAP_CHUNK_SIZE);
  const top = Math.floor((state.view.y - halfHeight) / MAP_CHUNK_SIZE);
  const bottom = Math.floor((state.view.y + halfHeight) / MAP_CHUNK_SIZE);
  for (let chunkX = left; chunkX <= right; chunkX += 1) {
    for (let chunkY = top; chunkY <= bottom; chunkY += 1) {
      (state.mapIndex[name].get(`${chunkX},${chunkY}`) || [])
        .forEach((cell) => drawCell(cell, color, size));
    }
  }
}

function scheduleDraw() {
  if (drawFrame) return;
  drawFrame = requestAnimationFrame(() => {
    drawFrame = 0;
    draw();
  });
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
  const rect = state.viewport;
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

function drawCore(item, relation, historical = false) {
  const [x, y] = screenPosition(item.position || [item.x, item.y]);
  const size = Math.max(7, state.view.scale * 0.82);
  context.save();
  context.globalAlpha = historical ? 0.52 : 1;
  context.setLineDash(historical ? [4, 3] : []);
  const friendly = relation === "friendly";
  const allied = relation === "ally";
  context.fillStyle = historical ? colors.oldCore : allied ? colors.ally : friendly ? colors.friendly : colors.enemy;
  context.fillRect(x - size / 2, y - size / 2, size, size);
  context.strokeStyle = historical ? "#cb7178" : colors.label;
  context.lineWidth = historical ? 1 : 1.5;
  context.strokeRect(x - size / 2, y - size / 2, size, size);
  if ((!friendly || allied) && state.view.scale >= 5) {
    const name = item.owner_username ? `@${item.owner_username}` : allied ? "盟友 Core" : "敌方 Core";
    const age = historical ? ` · 最后发现 ${item.age_ticks}T 前` : "";
    context.fillStyle = historical ? "#c78388" : allied ? "#ffd7df" : "#ff9b9f";
    context.font = "11px Segoe UI, Microsoft YaHei, sans-serif";
    context.fillText(`${name}${age}`, x + size / 2 + 5, y + 4);
  }
  context.restore();
}

function drawUnit(item, relation) {
  const [x, y] = screenPosition(item.position);
  const radius = Math.max(2.5, state.view.scale * 0.28);
  const friendly = relation === "friendly";
  const allied = relation === "ally";
  context.fillStyle = allied ? colors.ally : friendly ? colors.friendly : colors.enemy;
  context.strokeStyle = allied ? "#ffe0e7" : friendly ? "#a8e8ff" : "#ffb0b3";
  context.lineWidth = 1;
  context.beginPath();
  if (item.unit_type === "VANGUARD") {
    context.moveTo(x, y - radius * 1.45);
    context.lineTo(x + radius * 1.45, y);
    context.lineTo(x, y + radius * 1.45);
    context.lineTo(x - radius * 1.45, y);
    context.closePath();
  } else if (item.unit_type === "RANGER") {
    // 游侠 RANGER：向上箭头三角形（远程/弓箭）
    const r = radius * 1.35;
    context.moveTo(x, y - r * 0.95);          // 顶尖箭头
    context.lineTo(x + r * 0.82, y + r * 0.7); // 右下角
    context.lineTo(x - r * 0.82, y + r * 0.7); // 左下角
    context.closePath();
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

function drawOrderTarget() {
  if (!state.orderTarget || !visibleAt(state.orderTarget)) return;
  const [x, y] = screenPosition(state.orderTarget);
  const size = Math.max(7, state.view.scale * 0.55);
  context.save();
  context.strokeStyle = colors.beacon;
  context.fillStyle = colors.beacon;
  context.lineWidth = 2;
  context.beginPath();
  context.moveTo(x - size, y);
  context.lineTo(x + size, y);
  context.moveTo(x, y - size);
  context.lineTo(x, y + size);
  context.stroke();
  context.font = "11px Segoe UI, Microsoft YaHei, sans-serif";
  context.fillText(`${state.orderTarget[0]}, ${state.orderTarget[1]}`, x + size + 4, y - 4);
  context.restore();
}

function drawRoutes(overview) {
  if (!state.layers.routes) return;
  const units = new Map(
    (overview.state.objects || [])
      .filter((item) => item.kind === "UNIT" && item.controlled)
      .map((item) => [item.id, item]),
  );
  const routes = [];
  (state.orders || []).filter((item) => item.status === "PENDING").forEach((order) => {
    order.unit_ids.forEach((id) => routes.push([id, [order.target_x, order.target_y]]));
  });
  (state.controlConfig.expeditions || []).filter((item) => item.enabled).forEach((expedition) => {
    const memberIds = overview.strategy.expedition_members?.[String(expedition.id)] || [];
    memberIds.forEach((id) => routes.push([id, [expedition.target_x, expedition.target_y]]));
  });
  context.save();
  context.strokeStyle = "rgba(240,200,76,0.48)";
  context.setLineDash([5, 5]);
  routes.forEach(([id, target]) => {
    const unit = units.get(id);
    if (!unit) return;
    const [x1, y1] = screenPosition(unit.position);
    const [x2, y2] = screenPosition(target);
    context.beginPath();
    context.moveTo(x1, y1);
    context.lineTo(x2, y2);
    context.stroke();
  });
  context.restore();
}

function draw() {
  const rect = state.viewport;
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
  if (state.layers.explored) drawIndexedCells("explored", colors.explored);
  if (state.layers.obstacles) drawIndexedCells("obstacles", colors.obstacle, 0.72);
  if (state.layers.history) drawIndexedCells("resource_history", colors.resourceHistory, 0.34);
  if (state.layers.routes) Object.values(overview.trails || {}).forEach(drawTrail);

  const objects = overview.state.objects || [];
  const allianceObjects = overview.alliance_objects || [];
  const allianceIds = new Set(allianceObjects.map((item) => item.id));
  (state.layers.history ? overview.enemy_core_history : [])
    .filter((item) => !item.currently_visible && !allianceIds.has(item.core_id))
    .forEach((item) => drawCore(item, "enemy", true));

  const objectById = new Map();
  for (const item of objects) {
    if (state.layers.resources && item.kind === "RESOURCE") item.positions.forEach((position) => drawCell(position, colors.resource, 0.5));
    if (item.id) objectById.set(item.id, item);
  }
  for (const item of objects) {
    if (allianceIds.has(item.id) || item.relation === "ALLY") continue;
    if (!shouldDrawObject(item)) continue; //过滤掉非当前兵种的单位
    if (item.kind === "CORE") drawCore(item, item.controlled ? "friendly" : "enemy");
    if (item.kind === "UNIT") drawUnit(item, item.controlled ? "friendly" : "enemy");
  }
  for (const item of allianceObjects) {
    if (!shouldDrawObject(item)) continue; //过滤盟友非当前兵种单位
    if (item.kind === "CORE") drawCore(item, "ally");
    if (item.kind === "UNIT") drawUnit(item, "ally");
  }
  drawPlan(overview, objectById);
  drawRoutes(overview);

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
  drawOrderTarget();
  drawSelectedUnitsHighlight();
}

function setTargetPicking(active, mode = "order") {
  state.pickingTarget = active;
  state.pickMode = active ? mode : null;
  ui.pickTarget.classList.toggle("active", active);
  ui.pickExpeditionTarget.classList.toggle("active", active && mode === "expedition");
  ui.pickTarget.textContent = active ? "点击地图选择目标（可拖动）" : "在地图上选择目标";
  canvas.classList.toggle("picking-target", active);
}

function fitMap() {
  const cells = [...state.mapIndex.explored.values()].flat();
  if (!cells.length) return;
  let minX = cells[0][0]; let maxX = minX; let minY = cells[0][1]; let maxY = minY;
  cells.forEach(([x, y]) => {
    minX = Math.min(minX, x); maxX = Math.max(maxX, x);
    minY = Math.min(minY, y); maxY = Math.max(maxY, y);
  });
  state.view.x = (minX + maxX) / 2;
  state.view.y = (minY + maxY) / 2;
  state.view.scale = Math.max(1.5, Math.min(32,
    Math.min(state.viewport.width / Math.max(1, maxX - minX + 4), state.viewport.height / Math.max(1, maxY - minY + 4))));
  updateMetrics();
  draw();
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
  const enemies = Number.isInteger(overview.enemy_count)
    ? overview.enemy_count
    : game.objects.filter((item) => item.controlled === false && item.relation !== "ALLY").length;
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
  const username = ownUsername();
  ui.killHeading.textContent = username ? `我的战果 @${username}` : "我的战果";
  ui.killStats.replaceChildren();
  [
    ["单位摧毁参与", stats.unit_participations || 0],
    ["Core 摧毁参与", stats.core_participations || 0],
    ["合计", stats.total_participations || 0],
    ["遭受攻击", stats.attacks_received || 0],
    ["单位阵亡", stats.units_lost || 0],
    ["Core 阵亡", stats.cores_lost || 0],
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
      const username = kill.username ? ` @${kill.username}` : "";
      item.textContent = `t${kill.tick} ${kill.kind === "CORE" ? "Core" : "单位"}${username}${position}`;
      ui.kills.append(item);
    });
  }

  ui.losses.replaceChildren();
  const attacks = stats.attacks || [];
  (attacks.length ? attacks : [{ empty: true }]).forEach((loss) => {
    const item = document.createElement("li");
    if (loss.empty) {
      item.className = "empty-state";
      item.textContent = "暂无受击记录";
    } else {
      const position = Array.isArray(loss.position) ? ` @ ${loss.position[0]},${loss.position[1]}` : "";
      const result = loss.outcome === "DESTROYED" ? "摧毁" : "攻击";
      const attacker = loss.username ? `被 @${loss.username} ${result}` : `${result}者身份未公开`;
      item.textContent = `t${loss.tick} ${loss.kind === "CORE" ? "Core" : "单位"} ${attacker}${position}`;
    }
    ui.losses.append(item);
  });

  ui.revenge.replaceChildren();
  const revengeTargets = stats.revenge_targets || [];
  (revengeTargets.length ? revengeTargets : [{ empty: true }]).forEach((target) => {
    const item = document.createElement("li");
    if (target.empty) {
      item.className = "empty-state";
      item.textContent = "暂无可确认仇敌";
    } else {
      item.textContent = `@${target.username} · 仇恨 ${target.score}`;
    }
    ui.revenge.append(item);
  });

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
  renderExpeditions();
}

function renderExpeditions() {
  ui.expeditionList.replaceChildren();
  const expeditions = state.controlConfig.expeditions || [];
  (expeditions.length ? expeditions : [{ empty: true }]).forEach((expedition) => {
    const item = document.createElement("li");
    if (expedition.empty) {
      item.className = "empty-state";
      item.textContent = "暂无远征队";
    } else {
      item.className = "order-item";
      const summary = document.createElement("span");
      summary.textContent = `${expedition.enabled ? "启用" : "暂停"} · ${expedition.name} · ${expedition.ranger_count}R ${expedition.vanguard_count}V → (${expedition.target_x},${expedition.target_y})`;
      const edit = document.createElement("button");
      edit.type = "button"; edit.dataset.editExpedition = expedition.id; edit.textContent = "编辑";
      const remove = document.createElement("button");
      remove.type = "button"; remove.dataset.deleteExpedition = expedition.id; remove.textContent = "删除";
      item.append(summary, edit, remove);
    }
    ui.expeditionList.append(item);
  });
}

function renderUnitPicker() {
  const selectedType = document.querySelector("#order-unit-type").value;
  if (selectedType === "CORE" && ui.orderSelectionMode.value === "DISTANT") {
    ui.orderSelectionMode.value = "MANUAL";
  }
  const selectionMode = ui.orderSelectionMode.value;
  const distantOption = ui.orderSelectionMode.querySelector('option[value="DISTANT"]');
  distantOption.disabled = selectedType === "CORE";
  const core = state.controlUnits.find((unit) => unit.kind === "CORE");
  const minDistance = Math.max(0, Number(ui.orderMinDistance.value) || 0);
  const selectedIds = new Set(
    [...ui.unitList.querySelectorAll("input:checked")].map((input) => input.value),
  );
  const units = state.controlUnits
    .filter((unit) => (
      selectedType === "CORE"
        ? unit.kind === "CORE"
        : unit.kind === "UNIT" && unit.unit_type === selectedType
    ))
    .sort((left, right) => left.id.localeCompare(right.id));
  ui.unitList.replaceChildren();
  ui.orderDistanceField.classList.toggle("hidden", selectionMode !== "DISTANT");
  if (!units.length) {
    const empty = document.createElement("span");
    empty.className = "empty-state";
    empty.textContent = `当前没有可派遣的 ${selectedType}`;
    ui.unitList.append(empty);
  } else {
    units.forEach((unit) => {
      const label = document.createElement("label");
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.value = unit.id;
      const coreDistance = core
        ? Math.abs(unit.position[0] - core.position[0]) + Math.abs(unit.position[1] - core.position[1])
        : null;
      checkbox.checked = selectionMode === "ALL"
        || (selectionMode === "DISTANT" && coreDistance !== null && coreDistance >= minDistance)
        || (selectionMode === "MANUAL" && selectedIds.has(unit.id));
      const cargo = unit.unit_type === "WORKER" ? ` / 载货 ${unit.cargo}` : "";
      const distance = unit.kind === "UNIT" && coreDistance !== null ? ` / 距 Core ${coreDistance}` : "";
      const text = document.createElement("span");
      text.textContent = `${unit.id.slice(0, 8)} / (${unit.position[0]},${unit.position[1]}) / HP ${unit.hp}${cargo}${distance}`;
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
  const incremental = tick === null && state.live && state.overview?.available;
  const query = tick !== null
    ? `?tick=${encodeURIComponent(tick)}`
    : incremental ? `?since_tick=${encodeURIComponent(state.overview.tick)}` : "";
  const overview = await fetchJson(`/api/overview${query}`);
  if (overview.history_delta && state.overview?.available) {
    for (const name of MAP_LAYERS) {
      indexCells(name, overview[name]);
      state.overview[name].push(...overview[name]);
      overview[name] = state.overview[name];
    }
  } else {
    for (const name of MAP_LAYERS) indexCells(name, overview[name] || [], true);
  }
  state.overview = overview;
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
    const [kills, orders, overview, controlConfig] = await Promise.all([
      fetchJson("/api/kills"),
      fetchJson("/api/orders"),
      fetchJson("/api/overview?history=0"),
      fetchJson("/api/control-config"),
    ]);
    state.kills = kills;
    state.orders = orders || [];
    state.controlConfig = controlConfig;
    state.controlUnits = (overview.state?.objects || []).filter(
      (item) => ["CORE", "UNIT"].includes(item.kind) && item.controlled === true,
    );
    renderControl();
    const production = controlConfig.production;
    if (production && document.activeElement?.form !== ui.productionForm) {
      document.querySelector("#production-worker").value = production.worker_weight;
      document.querySelector("#production-vanguard").value = production.vanguard_weight;
      document.querySelector("#production-ranger").value = production.ranger_weight;
    }
    const alliance = controlConfig.alliance;
    if (alliance && document.activeElement?.form !== ui.allianceForm) {
      document.querySelector("#alliance-rally-radius").value = alliance.rally_radius;
    }
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
  clearHover(); 
  state.dragging = true;
  state.pointer = [event.clientX, event.clientY];
  state.pointerStart = [event.clientX, event.clientY];
  canvas.classList.add("dragging");
  canvas.setPointerCapture(event.pointerId);
});
canvas.addEventListener("pointerleave", () => {
  clearHover();
})
canvas.addEventListener("pointermove", (event) => {
  if (!state.dragging) return;
  state.view.x -= (event.clientX - state.pointer[0]) / state.view.scale;
  state.view.y -= (event.clientY - state.pointer[1]) / state.view.scale;
  state.pointer = [event.clientX, event.clientY];
  scheduleDraw();
});
canvas.addEventListener("pointerup", (event) => {
  const moved = state.pointerStart
    && Math.hypot(event.clientX - state.pointerStart[0], event.clientY - state.pointerStart[1]) > 4;
  state.dragging = false;
  state.pointerStart = null;
  canvas.classList.remove("dragging");
  canvas.releasePointerCapture(event.pointerId);
   if (!moved) {
    const worldPos = worldPosition(event.clientX, event.clientY);
    // 判断是否按下了 Ctrl (Windows/Linux) 或 Cmd (Mac) 键
    const isCtrlPressed = event.ctrlKey || event.metaKey;

    // 检查点击位置是否有己方单位/核心
    const objects = state.overview?.state?.objects || [];
    const clickedUnit = objects.find((item) =>
      item.controlled &&
      shouldDrawObject(item) &&
      ["UNIT", "CORE"].includes(item.kind) &&
      Math.hypot(item.position[0] - worldPos[0], item.position[1] - worldPos[1]) <= 1.2
    );

    // 1. 优先处理点击单位：进入单选或 Ctrl 多选模式
    if (clickedUnit) {
      selectUnitInForm(clickedUnit, isCtrlPressed);
      draw();
      return;
    }

    // 2. 如果点击了空白地图：设置目的地并提交派遣（集体发送所有选中单位）
    if (state.pickingTarget) {
      state.orderTarget = worldPos;
      //根据当前模式填入相对或绝对坐标
      const displayPos = state.useRelativeCoords ? toRelativePos(worldPos) : worldPos;

      if (state.pickMode === "expedition") {
        document.querySelector("#expedition-x").value = displayPos[0];
        document.querySelector("#expedition-y").value = displayPos[1];
        ui.expeditionStatus.textContent = `目标已选择：${displayPos[0]}, ${displayPos[1]}`;
        setTargetPicking(false);
      } else {
        [ui.orderX.value, ui.orderY.value] = displayPos;
        setTargetPicking(false);

        // 提交表单（自动打包所有勾选的单位 ID）
        ui.orderForm.requestSubmit();
      }
      draw();
      return;
    }
  }
});
canvas.addEventListener("wheel", (event) => {
  clearHover(); 
  event.preventDefault();
  state.view.scale = Math.max(1.5, Math.min(32, state.view.scale * (event.deltaY < 0 ? 1.14 : 0.88)));
  updateMetrics();
  scheduleDraw();
}, { passive: false });

// 兵种筛选按钮切换监听
document.querySelectorAll("[data-unit-filter]").forEach((button) => {
  button.addEventListener("click", () => {
    state.unitFilter = button.dataset.unitFilter;
    document.querySelectorAll("[data-unit-filter]").forEach((btn) => {
      btn.classList.toggle("active", btn === button);
    });
    draw();
  });
});
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
document.querySelector("#fit-map").addEventListener("click", fitMap);
document.querySelector("#home-map").addEventListener("click", () => centerMap(true));
document.querySelectorAll("[data-map-layer]").forEach((input) => input.addEventListener("change", () => {
  state.layers[input.dataset.mapLayer] = input.checked;
  draw();
}));
ui.pickTarget.addEventListener("click", () => setTargetPicking(!state.pickingTarget, "order"));
ui.pickExpeditionTarget.addEventListener("click", () => setTargetPicking(!state.pickingTarget, "expedition"));
[ui.orderX, ui.orderY].forEach((input) => input.addEventListener("change", () => {
  const position = [Number(ui.orderX.value), Number(ui.orderY.value)];
  state.orderTarget = position.every(Number.isSafeInteger) ? position : null;
  draw();
}));

ui.orderForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  ui.orderStatus.textContent = "提交中…";
  const unitIds = [...ui.unitList.querySelectorAll("input:checked")].map((input) => input.value);
  if (!unitIds.length) {
    ui.orderStatus.textContent = "请先选择具体核心或至少一个具体单位";
    return;
  }
  // 将输入框坐标换算回发送给后端的绝对坐标
  const inputPos = [Number(ui.orderX.value), Number(ui.orderY.value)];
  const absPos = state.useRelativeCoords ? toAbsolutePos(inputPos) : inputPos;
  const payload = {
    unit_type: document.querySelector("#order-unit-type").value,
    unit_count: unitIds.length,
    unit_ids: unitIds,
    target_x: absPos[0], // 发给后端的永远是真实的绝对坐标
    target_y: absPos[1],
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
    setTargetPicking(false);
    await refreshControl();
  } catch (error) {
    ui.orderStatus.textContent = `提交失败 · ${error.message}`;
  }
});

document.querySelector("#order-unit-type").addEventListener("change", renderUnitPicker);
ui.orderSelectionMode.addEventListener("change", renderUnitPicker);
ui.orderMinDistance.addEventListener("input", () => {
  if (ui.orderSelectionMode.value === "DISTANT") renderUnitPicker();
});
ui.unitList.addEventListener("change", () => {
  ui.orderSelectionMode.value = "MANUAL";
  ui.orderDistanceField.classList.add("hidden");
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

canvas.addEventListener("pointermove", (event) => {
  const [x, y] = worldPosition(event.clientX, event.clientY);
  ui.cursorPosition.textContent = formatCoordDisplay([x, y]); 
  if (state.dragging) {
    clearHover();
    return;
  }

  if (!currentHoverCell || currentHoverCell[0] !== x || currentHoverCell[1] !== y) {
    clearHover(); 
    currentHoverCell = [x, y];
    
    hoverTimer = setTimeout(() => {
      showHoverTooltip(x, y);
    }, HOVER_DELAY);
  }
});

ui.productionForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const payload = {
    worker_weight: Number(document.querySelector("#production-worker").value),
    vanguard_weight: Number(document.querySelector("#production-vanguard").value),
    ranger_weight: Number(document.querySelector("#production-ranger").value),
  };
  try {
    const response = await fetch("/api/control-config", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.message || response.statusText);
    ui.productionStatus.textContent = "生产比例已保存，将在下个 Tick 生效";
    await refreshControl();
  } catch (error) {
    ui.productionStatus.textContent = `保存失败 · ${error.message}`;
  }
});

ui.allianceForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const payload = {
    rally_radius: Number(document.querySelector("#alliance-rally-radius").value),
  };
  try {
    const response = await fetch("/api/alliance-config", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.message || response.statusText);
    ui.allianceStatus.textContent = `靠拢距离已设为 ${result.rally_radius} 格，将在下个 Tick 生效`;
    await refreshControl();
  } catch (error) {
    ui.allianceStatus.textContent = `保存失败 · ${error.message}`;
  }
});

ui.expeditionForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const rawId = document.querySelector("#expedition-id").value;
  // 将远征队输入的坐标换算回给后端的绝对坐标
  const inputPos = [
    Number(document.querySelector("#expedition-x").value),
    Number(document.querySelector("#expedition-y").value)
  ];
  const absPos = state.useRelativeCoords ? toAbsolutePos(inputPos) : inputPos;
  const payload = {
    id: rawId ? Number(rawId) : null,
    name: document.querySelector("#expedition-name").value,
    ranger_count: Number(document.querySelector("#expedition-ranger").value),
    vanguard_count: Number(document.querySelector("#expedition-vanguard").value),
    target_x: absPos[0],
    target_y: absPos[1],
    enabled: document.querySelector("#expedition-enabled").checked,
  };
  try {
    const response = await fetch("/api/expeditions", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.message || response.statusText);
    ui.expeditionStatus.textContent = "远征队已保存，将在下个 Tick 生效";
    document.querySelector("#expedition-id").value = "";
    await refreshControl();
  } catch (error) {
    ui.expeditionStatus.textContent = `保存失败 · ${error.message}`;
  }
});

ui.expeditionList.addEventListener("click", async (event) => {
  const edit = event.target.closest("button[data-edit-expedition]");
  const remove = event.target.closest("button[data-delete-expedition]");
  if (edit) {
    const expedition = state.controlConfig.expeditions.find((item) => item.id === Number(edit.dataset.editExpedition));
    if (!expedition) return;
    document.querySelector("#expedition-id").value = expedition.id;
    document.querySelector("#expedition-name").value = expedition.name;
    document.querySelector("#expedition-ranger").value = expedition.ranger_count;
    document.querySelector("#expedition-vanguard").value = expedition.vanguard_count;
    document.querySelector("#expedition-x").value = expedition.target_x;
    document.querySelector("#expedition-y").value = expedition.target_y;
    document.querySelector("#expedition-enabled").checked = expedition.enabled;
    return;
  }
  if (!remove) return;
  try {
    const response = await fetch(`/api/expeditions/${encodeURIComponent(remove.dataset.deleteExpedition)}`, { method: "DELETE" });
    const result = await response.json();
    if (!response.ok) throw new Error(result.message || response.statusText);
    await refreshControl();
  } catch (error) {
    ui.expeditionStatus.textContent = `删除失败 · ${error.message}`;
  }
});

new ResizeObserver(resizeCanvas).observe(canvas);
refreshTicks();
refreshLeaderboard();
refreshControl();
setInterval(refreshTicks, 5000);
setInterval(refreshLeaderboard, 15000);
setInterval(refreshControl, 5000);

let isAllCollapsed = false;

// 折叠 / 展开指定栏目
function toggleSection(element, forceState) {
  if (!element) return;
  if (typeof forceState === "boolean") {
    element.classList.toggle("section-collapsed", forceState);
  } else {
    element.classList.toggle("section-collapsed");
  }
}

// “全部折叠 / 展开” 按钮事件
document.querySelector("#toggle-all-control")?.addEventListener("click", () => {
  isAllCollapsed = !isAllCollapsed;
  const sections = document.querySelectorAll("#control-panel .config-form, #control-panel .order-form, #control-panel .control-section");
  sections.forEach((sec) => toggleSection(sec, isAllCollapsed));
  document.querySelector("#toggle-all-control").textContent = isAllCollapsed ? "全部展开" : "全部折叠";
});

// 点击单个栏目标题进行折叠/展开
document.querySelector("#control-panel")?.addEventListener("click", (event) => {
  const header = event.target.closest("h3");
  if (!header) return;
  const section = header.closest(".config-form, .order-form, .control-section");
  if (section) {
    toggleSection(section);
  }
});

// 相对坐标模式开关监听
document.querySelector("#toggle-relative-coord")?.addEventListener("change", (event) => {
  state.useRelativeCoords = event.target.checked;
  draw();
});