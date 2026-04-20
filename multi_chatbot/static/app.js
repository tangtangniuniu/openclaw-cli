"use strict";
/* OpenClaw 多用户多会话并发测试 — 前端控制器 */

// ----------------------------------------------------------------
// 工具 + DOM 引用
// ----------------------------------------------------------------

const $ = (sel, root) => (root || document).querySelector(sel);
const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

const newId = () =>
  (crypto && crypto.randomUUID
    ? crypto.randomUUID().replace(/-/g, "").slice(0, 12)
    : Math.random().toString(16).slice(2, 14));

const state = {
  ws: null,
  // 左侧树：{ users: [{ id, name, sessions: [{ id, name, questions: [{id, text}] }] }] }
  config: { users: [] },
  saveTimer: null,
  saving: false,
  // 运行时：key -> lane 状态对象
  lanes: new Map(),
  // key -> DOM card 引用
  cards: new Map(),
  // key -> { pendingLi } 的当前等待中的气泡
  cardPending: new Map(),
  scheduler: { capacity: 4, active: 0, queued: 0 },
  running: false,
};

const els = {
  statusDot: $("#status-dot"),
  statusLabel: $("#status-label"),
  gaugeConcurrency: $("#gauge-concurrency"),
  gaugeBlocked: $("#gauge-blocked"),
  tree: $("#tree"),
  btnAddUser: $("#btn-add-user"),
  editorSaveState: $("#editor-save-state"),
  centerTitle: $("#center-title-text"),
  chipLanes: $("#chip-lanes"),
  chipProgress: $("#chip-progress"),
  chatGrid: $("#chat-grid"),
  chatEmpty: $("#chat-empty"),
  schedulerState: $("#scheduler-state"),
  concBar: $("#conc-bar"),
  concFill: $("#conc-fill"),
  metricCapacity: $("#metric-capacity"),
  metricActive: $("#metric-active"),
  metricQueued: $("#metric-queued"),
  metricLanesTotal: $("#metric-lanes-total"),
  metricLanesDone: $("#metric-lanes-done"),
  btnStart: $("#btn-start"),
  btnStop: $("#btn-stop"),
  laneList: $("#lane-list"),
  lanesCount: $("#lanes-count"),
};

// ----------------------------------------------------------------
// WebSocket 连接 + 协议
// ----------------------------------------------------------------

function wsUrl() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${location.host}/ws`;
}

function setLinkStatus(kind, label) {
  els.statusDot.classList.remove("ok", "err", "warn");
  if (kind) els.statusDot.classList.add(kind);
  els.statusLabel.textContent = label;
}

function wsConnect() {
  const ws = new WebSocket(wsUrl());
  state.ws = ws;
  setLinkStatus("warn", "CONNECTING");

  ws.addEventListener("open", () => {
    setLinkStatus("ok", "CONNECTED");
    send({ op: "hello" });
  });
  ws.addEventListener("close", () => {
    setLinkStatus("err", "DISCONNECTED");
    state.ws = null;
    setTimeout(wsConnect, 1500);
  });
  ws.addEventListener("error", () => {
    setLinkStatus("err", "ERROR");
  });
  ws.addEventListener("message", (ev) => {
    try {
      const msg = JSON.parse(ev.data);
      onMessage(msg);
    } catch (e) {
      console.error("bad message", e, ev.data);
    }
  });
}

function send(payload) {
  if (!state.ws || state.ws.readyState !== WebSocket.OPEN) return false;
  state.ws.send(JSON.stringify(payload));
  return true;
}

function onMessage(msg) {
  switch (msg.op) {
    case "hello.ok":
      applyConfig(msg.config || { users: [] });
      applyScheduler(msg.scheduler || state.scheduler);
      break;
    case "config":
      if (msg.saved) {
        // 保存回执：仅更新保存徽标，不要重渲染树 —— 否则正在输入的
        // <input> / <textarea> 会被销毁重建，光标丢失，看起来像「会话
        // 刚加好就被折叠、无法输入」。
        markSaved();
      } else {
        applyConfig(msg.config || { users: [] });
      }
      break;
    case "run.started":
      onRunStarted(msg.lanes || []);
      break;
    case "run.stopped":
      onRunStopped(msg.lanes || []);
      if (msg.scheduler) applyScheduler(msg.scheduler);
      break;
    case "lane":
      applyLane(msg.lane);
      break;
    case "lane.question.pending":
      onLaneQuestionPending(msg);
      break;
    case "lane.question.reply":
      onLaneQuestionReply(msg);
      break;
    case "lane.question.error":
      onLaneQuestionError(msg);
      break;
    case "stats":
      if (msg.scheduler) applyScheduler(msg.scheduler);
      if (Array.isArray(msg.lanes)) msg.lanes.forEach(applyLane);
      if (typeof msg.running === "boolean") setRunning(msg.running);
      renderRightLanes();
      refreshProgressChip();
      break;
    case "error":
      console.warn("server error", msg.message);
      setLinkStatus("err", `ERR: ${msg.message}`.slice(0, 48));
      break;
    default:
      console.log("unhandled", msg);
  }
}

// ----------------------------------------------------------------
// 左侧：树形编辑器
// ----------------------------------------------------------------

function applyConfig(cfg) {
  state.config = normalizeConfig(cfg);
  renderTree();
}

function normalizeConfig(cfg) {
  const users = Array.isArray(cfg.users) ? cfg.users : [];
  return {
    users: users.map((u) => ({
      id: u.id || newId(),
      name: typeof u.name === "string" ? u.name : "",
      sessions: (Array.isArray(u.sessions) ? u.sessions : []).map((s) => ({
        id: s.id || newId(),
        name: typeof s.name === "string" ? s.name : "",
        questions: (Array.isArray(s.questions) ? s.questions : []).map((q) => ({
          id: q.id || newId(),
          text: typeof q.text === "string" ? q.text : "",
        })),
      })),
    })),
  };
}

function scheduleSave() {
  markDirty();
  if (state.saveTimer) clearTimeout(state.saveTimer);
  state.saveTimer = setTimeout(() => {
    state.saveTimer = null;
    state.saving = true;
    els.editorSaveState.classList.remove("err");
    els.editorSaveState.classList.add("saving");
    els.editorSaveState.textContent = "保存中…";
    send({ op: "config.save", config: state.config });
  }, 400);
}

function markDirty() {
  els.editorSaveState.classList.remove("err");
  els.editorSaveState.classList.add("saving");
  els.editorSaveState.textContent = "待保存…";
}

function markSaved() {
  state.saving = false;
  els.editorSaveState.classList.remove("saving", "err");
  els.editorSaveState.textContent = "已保存";
}

function renderTree() {
  els.tree.innerHTML = "";
  state.config.users.forEach((u) => {
    const userNode = buildUserNode(u);
    els.tree.appendChild(userNode);
  });
}

function buildUserNode(user) {
  const tpl = $("#tpl-user-node");
  const frag = tpl.content.cloneNode(true);
  const root = frag.querySelector(".tree-user");
  const nameInput = $(".tree-name", root);
  const caret = $(".tree-caret", root);
  const addBtn = $(".tree-add", root);
  const delBtn = $(".tree-del", root);
  const children = $(".tree-sessions", root);

  nameInput.value = user.name;
  nameInput.addEventListener("input", () => {
    user.name = nameInput.value;
    scheduleSave();
  });
  caret.addEventListener("click", () => {
    root.classList.toggle("collapsed");
    caret.setAttribute(
      "aria-expanded",
      root.classList.contains("collapsed") ? "false" : "true"
    );
  });
  addBtn.addEventListener("click", () => {
    const session = {
      id: newId(),
      name: `会话 ${user.sessions.length + 1}`,
      questions: [],
    };
    user.sessions.push(session);
    children.appendChild(buildSessionNode(user, session));
    scheduleSave();
  });
  delBtn.addEventListener("click", () => {
    if (!confirm(`删除用户 "${user.name || "(未命名)"}" 及其全部会话？`))
      return;
    state.config.users = state.config.users.filter((u) => u.id !== user.id);
    root.remove();
    scheduleSave();
  });

  user.sessions.forEach((s) => {
    children.appendChild(buildSessionNode(user, s));
  });
  return root;
}

function buildSessionNode(user, session) {
  const tpl = $("#tpl-session-node");
  const frag = tpl.content.cloneNode(true);
  const root = frag.querySelector(".tree-session");
  const nameInput = $(".tree-name", root);
  const caret = $(".tree-caret", root);
  const addBtn = $(".tree-add", root);
  const delBtn = $(".tree-del", root);
  const children = $(".tree-questions", root);

  nameInput.value = session.name;
  nameInput.addEventListener("input", () => {
    session.name = nameInput.value;
    scheduleSave();
  });
  caret.addEventListener("click", () => {
    root.classList.toggle("collapsed");
    caret.setAttribute(
      "aria-expanded",
      root.classList.contains("collapsed") ? "false" : "true"
    );
  });
  addBtn.addEventListener("click", () => {
    const q = { id: newId(), text: "" };
    session.questions.push(q);
    const node = buildQuestionNode(session, q);
    children.appendChild(node);
    $(".tree-qtext", node).focus();
    scheduleSave();
  });
  delBtn.addEventListener("click", () => {
    if (!confirm(`删除会话 "${session.name || "(未命名)"}" 及其预设提问？`))
      return;
    user.sessions = user.sessions.filter((s) => s.id !== session.id);
    root.remove();
    scheduleSave();
  });

  session.questions.forEach((q) => {
    children.appendChild(buildQuestionNode(session, q));
  });
  return root;
}

function buildQuestionNode(session, question) {
  const tpl = $("#tpl-question-node");
  const frag = tpl.content.cloneNode(true);
  const root = frag.querySelector(".tree-question");
  const txt = $(".tree-qtext", root);
  const delBtn = $(".tree-del", root);

  txt.value = question.text;
  autoGrow(txt);
  txt.addEventListener("input", () => {
    question.text = txt.value;
    autoGrow(txt);
    scheduleSave();
  });
  delBtn.addEventListener("click", () => {
    session.questions = session.questions.filter((q) => q.id !== question.id);
    root.remove();
    scheduleSave();
  });
  return root;
}

function autoGrow(textarea) {
  textarea.style.height = "auto";
  textarea.style.height = Math.min(120, textarea.scrollHeight + 2) + "px";
}

// Add-user button
els.btnAddUser.addEventListener("click", () => {
  const user = {
    id: newId(),
    name: `user-${state.config.users.length + 1}`,
    sessions: [],
  };
  state.config.users.push(user);
  els.tree.appendChild(buildUserNode(user));
  scheduleSave();
});

// ----------------------------------------------------------------
// 中间：聊天窗口网格
// ----------------------------------------------------------------

function ensureCard(lane) {
  let card = state.cards.get(lane.key);
  if (card) return card;
  if (els.chatEmpty && els.chatEmpty.parentNode) {
    els.chatEmpty.remove();
    els.chatGrid.classList.remove("empty");
  }
  const tpl = $("#tpl-chat-card");
  const frag = tpl.content.cloneNode(true);
  card = frag.querySelector(".chat-card");
  $(".chat-user", card).textContent = lane.user;
  $(".chat-session", card).textContent = lane.session_name || lane.session_id;
  els.chatGrid.appendChild(card);
  state.cards.set(lane.key, card);
  return card;
}

function updateCardStatus(lane) {
  const card = ensureCard(lane);
  card.dataset.status = lane.status;
  const statusText = humanStatus(lane);
  $(".chat-status", card).textContent = statusText;
  $(".chat-progress", card).textContent = `${lane.done} / ${lane.total}`;
  return card;
}

function humanStatus(lane) {
  switch (lane.status) {
    case "pending": return "pending";
    case "running": return "running";
    case "blocked": return "blocked";
    case "finished": return "done";
    case "error": return "error";
    case "cancelled": return "cancelled";
    default: return lane.status;
  }
}

function appendUserBubble(card, text) {
  const tpl = $("#tpl-chat-msg-user");
  const frag = tpl.content.cloneNode(true);
  const li = frag.querySelector("li");
  $(".cm-bubble", li).textContent = text;
  $(".chat-log", card).appendChild(li);
  scrollLogToBottom(card);
}

function appendPendingBubble(card, label) {
  const tpl = $("#tpl-chat-msg-pending");
  const frag = tpl.content.cloneNode(true);
  const li = frag.querySelector("li");
  const statusEl = $(".cm-status", li);
  statusEl.textContent = label || "pending";
  statusEl.classList.add(label || "pending");
  $(".chat-log", card).appendChild(li);
  scrollLogToBottom(card);
  return li;
}

function appendAssistantBubble(card, text, durationMs) {
  const tpl = $("#tpl-chat-msg-assistant");
  const frag = tpl.content.cloneNode(true);
  const li = frag.querySelector("li");
  $(".cm-bubble", li).textContent = text || "(空回复)";
  if (typeof durationMs === "number") {
    $(".cm-dur", li).textContent = `${durationMs} ms`;
  }
  $(".chat-log", card).appendChild(li);
  scrollLogToBottom(card);
}

function appendErrorBubble(card, text) {
  const tpl = $("#tpl-chat-msg-error");
  const frag = tpl.content.cloneNode(true);
  const li = frag.querySelector("li");
  $(".cm-bubble", li).textContent = text;
  $(".chat-log", card).appendChild(li);
  scrollLogToBottom(card);
}

function scrollLogToBottom(card) {
  const log = $(".chat-log", card);
  // 延迟到下一帧，确保 layout 完成
  requestAnimationFrame(() => {
    log.scrollTop = log.scrollHeight;
  });
}

// ----------------------------------------------------------------
// 运行时事件处理
// ----------------------------------------------------------------

function onRunStarted(lanes) {
  state.lanes.clear();
  state.cards.clear();
  state.cardPending.clear();
  els.chatGrid.innerHTML = "";
  lanes.forEach((lane) => {
    state.lanes.set(lane.key, lane);
    const card = ensureCard(lane);
    updateCardStatus(lane);
  });
  setRunning(true);
  renderRightLanes();
  refreshProgressChip();
  els.centerTitle.textContent = `RUN · ${lanes.length} 个车道并发中`;
}

function onRunStopped(lanes) {
  lanes.forEach(applyLane);
  setRunning(false);
  renderRightLanes();
  refreshProgressChip();
  els.centerTitle.textContent = `RUN · 结束 · ${lanes.length} 个车道`;
}

function applyLane(lane) {
  if (!lane || !lane.key) return;
  state.lanes.set(lane.key, lane);
  updateCardStatus(lane);
}

function onLaneQuestionPending(msg) {
  const lane = state.lanes.get(msg.key);
  if (!lane) return;
  const card = ensureCard(lane);
  appendUserBubble(card, msg.question || "");
  // clear any stale pending bubble（理论上不应存在）
  const stale = state.cardPending.get(msg.key);
  if (stale) stale.remove();
  const pendingLi = appendPendingBubble(card, "blocked");
  state.cardPending.set(msg.key, pendingLi);
}

function onLaneQuestionReply(msg) {
  const lane = state.lanes.get(msg.key);
  if (!lane) return;
  const card = ensureCard(lane);
  const pendingLi = state.cardPending.get(msg.key);
  if (pendingLi) {
    pendingLi.remove();
    state.cardPending.delete(msg.key);
  }
  appendAssistantBubble(card, msg.reply || "(空回复)", msg.duration_ms);
}

function onLaneQuestionError(msg) {
  const lane = state.lanes.get(msg.key);
  if (!lane) return;
  const card = ensureCard(lane);
  const pendingLi = state.cardPending.get(msg.key);
  if (pendingLi) {
    pendingLi.remove();
    state.cardPending.delete(msg.key);
  }
  appendErrorBubble(card, msg.message || "unknown error");
}

// ----------------------------------------------------------------
// 右侧：并发 / lane list
// ----------------------------------------------------------------

function applyScheduler(sched) {
  state.scheduler = Object.assign({}, state.scheduler, sched);
  const { capacity, active, queued } = state.scheduler;
  els.gaugeConcurrency.textContent = `${active} / ${capacity}`;
  els.gaugeBlocked.textContent = `${queued}`;
  els.metricCapacity.textContent = String(capacity);
  els.metricActive.textContent = String(active);
  els.metricQueued.textContent = String(queued);
  const pct = capacity > 0 ? Math.min(100, (active / capacity) * 100) : 0;
  els.concFill.style.width = pct + "%";
  if (queued > 0) els.concBar.classList.add("has-queue");
  else els.concBar.classList.remove("has-queue");
  els.schedulerState.textContent = queued > 0 ? `blocked×${queued}` : (active > 0 ? "busy" : "idle");
}

function renderRightLanes() {
  const lanes = Array.from(state.lanes.values());
  els.lanesCount.textContent = String(lanes.length);
  els.laneList.innerHTML = "";
  // Sort: running > blocked > pending > finished > error
  const order = { running: 0, blocked: 1, pending: 2, finished: 3, error: 4, cancelled: 5 };
  lanes.sort((a, b) => (order[a.status] ?? 9) - (order[b.status] ?? 9));
  lanes.forEach((lane) => {
    const tpl = $("#tpl-lane-item");
    const frag = tpl.content.cloneNode(true);
    const li = frag.querySelector(".lane-item");
    li.dataset.status = lane.status;
    $(".lane-name", li).textContent = `${lane.user} · ${lane.session_name || lane.session_id}`;
    $(".lane-progress", li).textContent = `${lane.done} / ${lane.total}`;
    const sub = humanStatus(lane) + (lane.current_question ? ` · ${truncate(lane.current_question, 32)}` : "");
    $(".lane-status-text", li).textContent = sub;
    els.laneList.appendChild(li);
  });
}

function refreshProgressChip() {
  const lanes = Array.from(state.lanes.values());
  const done = lanes.filter((l) => l.status === "finished").length;
  const total = lanes.length;
  els.chipLanes.textContent = `${total} lanes`;
  els.chipProgress.textContent = `${done} / ${total}`;
  els.metricLanesTotal.textContent = String(total);
  els.metricLanesDone.textContent = String(done);
}

function truncate(text, n) {
  if (!text) return "";
  return text.length > n ? text.slice(0, n) + "…" : text;
}

function setRunning(running) {
  state.running = running;
  els.btnStart.disabled = running;
  els.btnStop.disabled = !running;
}

// ----------------------------------------------------------------
// 开始 / 停止
// ----------------------------------------------------------------

els.btnStart.addEventListener("click", () => {
  const plan = buildPlan(state.config);
  if (plan.length === 0) {
    alert("请先在左侧添加用户 / 会话 / 至少一个预设提问。");
    return;
  }
  // 如果还有未保存的改动，先强制 flush 一次保存
  if (state.saveTimer) {
    clearTimeout(state.saveTimer);
    state.saveTimer = null;
    send({ op: "config.save", config: state.config });
  }
  send({ op: "run.start", plan });
});

els.btnStop.addEventListener("click", () => {
  send({ op: "run.stop" });
});

function buildPlan(config) {
  const out = [];
  (config.users || []).forEach((u) => {
    const userName = (u.name || "").trim();
    if (!userName) return;
    (u.sessions || []).forEach((s) => {
      const sessionName = (s.name || "").trim() || s.id;
      const questions = (s.questions || [])
        .map((q) => (q.text || "").trim())
        .filter(Boolean);
      if (questions.length === 0) return;
      out.push({
        user: userName,
        session_id: s.id,
        session_name: sessionName,
        questions,
      });
    });
  });
  return out;
}

// ----------------------------------------------------------------
// 初始化
// ----------------------------------------------------------------

function boot() {
  setLinkStatus(null, "DISCONNECTED");
  setRunning(false);
  applyScheduler({ capacity: 4, active: 0, queued: 0 });
  wsConnect();
}

boot();
