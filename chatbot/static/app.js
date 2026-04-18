"use strict";
/* OpenClaw Diagnostic Console — frontend controller */

const $ = (sel) => document.querySelector(sel);

const state = {
  ws: null,
  user: null,
  sessions: [],
  active: null,      // {id, name, session_key}
  messages: [],      // normalized from server
  pending: null,     // DOM element for the pending assistant bubble
  lanesActive: 0,
};

const els = {
  statusDot: $("#status-dot"),
  statusLabel: $("#status-label"),
  gaugeConcurrency: $("#gauge-concurrency"),
  userInput: $("#user-input"),
  opConnect: $("#op-connect"),
  opHistoryBox: $("#op-history-box"),
  opHistoryList: $("#op-history"),
  opHistoryClear: $("#op-history-clear"),
  newSession: $("#new-session"),
  sessionsList: $("#sessions-list"),
  sessionCount: $("#session-count"),
  activeName: $("#active-name"),
  activeKey: $("#active-key"),
  messages: $("#messages"),
  composer: $("#composer"),
  messageInput: $("#message-input"),
  sendBtn: $("#send-btn"),
  toolsBtn: $("#tools-btn"),
  toolsMenu: $("#tools-menu"),
  suggest: $("#suggest"),
  cotList: $("#cot-list"),
  toolEvents: $("#tool-events"),
  llmState: $("#llm-state"),
  toolState: $("#tool-state"),
  metricActive: $("#metric-active"),
  metricUser: $("#metric-user"),
  metricKey: $("#metric-key"),
  metricLanes: $("#metric-lanes"),
};

const USERS_LS_KEY = "openclaw.chatbot.users";
const MAX_USERS = 16;

// ----------------------------------------------------------------
// WebSocket connect + protocol
// ----------------------------------------------------------------

function wsUrl() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${location.host}/ws`;
}

function setStatus(kind, label) {
  els.statusDot.classList.remove("ok", "err", "warn");
  if (kind) els.statusDot.classList.add(kind);
  els.statusLabel.textContent = label;
}

function connect() {
  setStatus("warn", "CONNECTING");
  const ws = new WebSocket(wsUrl());
  state.ws = ws;

  ws.addEventListener("open", () => {
    setStatus("ok", "LINKED");
    // if user already typed before connect, try auto-hello
    if (state.user) {
      send({ op: "hello", user: state.user });
    }
  });

  ws.addEventListener("close", () => {
    setStatus("err", "DISCONNECTED");
    // simple reconnect after 2s
    setTimeout(connect, 2000);
  });

  ws.addEventListener("error", () => {
    setStatus("err", "ERROR");
  });

  ws.addEventListener("message", (evt) => {
    let msg;
    try {
      msg = JSON.parse(evt.data);
    } catch {
      return;
    }
    handleServerMessage(msg);
  });
}

function send(payload) {
  if (!state.ws || state.ws.readyState !== WebSocket.OPEN) {
    appendSystemMessage("(连接未就绪，消息被丢弃)");
    return;
  }
  state.ws.send(JSON.stringify(payload));
}

function handleServerMessage(msg) {
  switch (msg.op) {
    case "hello.ok":
      state.user = msg.user;
      state.sessions = msg.sessions || [];
      state.active = msg.active || null;
      renderSessions();
      renderActive();
      renderSavedUsers();
      els.metricUser.textContent = msg.user || "—";
      // pull history if session active
      if (state.active) send({ op: "history.refresh" });
      break;
    case "sessions":
      state.sessions = msg.sessions || [];
      state.active = msg.active || null;
      renderSessions();
      renderActive();
      break;
    case "history":
      state.messages = msg.messages || [];
      renderMessages();
      break;
    case "reply.pending":
      state.lanesActive = Math.min(4, state.lanesActive + 1);
      updateLanes();
      els.llmState.textContent = "thinking…";
      appendPendingBubble();
      break;
    case "reply.done":
      state.lanesActive = Math.max(0, state.lanesActive - 1);
      updateLanes();
      els.llmState.textContent = "done";
      finalizePendingBubble(msg.text || "(空回复)");
      if (Array.isArray(msg.events)) renderEvents(msg.events);
      renderChainFromReply(msg.text || "");
      break;
    case "reply.error":
      state.lanesActive = Math.max(0, state.lanesActive - 1);
      updateLanes();
      els.llmState.textContent = "error";
      finalizePendingBubble(`⚠ 发送失败：${msg.message}`, true);
      break;
    case "error":
      appendSystemMessage(`⚠ ${msg.message}`);
      break;
  }
}

// ----------------------------------------------------------------
// rendering
// ----------------------------------------------------------------

function renderSessions() {
  els.sessionsList.innerHTML = "";
  els.sessionCount.textContent = String(state.sessions.length);
  const tpl = $("#tpl-session-item");
  for (const s of state.sessions) {
    const frag = tpl.content.cloneNode(true);
    const li = frag.querySelector("li");
    if (s.active) li.classList.add("active");
    li.dataset.id = s.id;
    frag.querySelector(".session-name").textContent = s.name;
    frag.querySelector(".session-time").textContent = formatTs(s.updated_at);
    const mainBtn = frag.querySelector(".session-main");
    mainBtn.addEventListener("click", () => {
      send({ op: "sessions.switch", id: s.id });
    });
    const delBtn = frag.querySelector(".session-del");
    delBtn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      if (confirm(`删除 session "${s.name}"？`)) {
        send({ op: "sessions.delete", id: s.id });
      }
    });
    els.sessionsList.appendChild(frag);
  }
}

function renderActive() {
  if (state.active) {
    els.activeName.textContent = state.active.name;
    els.activeKey.textContent = state.active.session_key;
    els.metricActive.textContent = state.active.name;
    els.metricKey.textContent = state.active.session_key;
    els.composer.classList.remove("disabled");
    els.sendBtn.disabled = false;
  } else {
    els.activeName.textContent = "— 未选择会话 —";
    els.activeKey.textContent = "no session";
    els.metricActive.textContent = "—";
    els.metricKey.textContent = "—";
    els.sendBtn.disabled = !state.user;
  }
}

function renderMessages() {
  els.messages.innerHTML = "";
  for (const m of state.messages) {
    appendMessageFrom(m);
  }
  scrollToEnd();
}

function appendMessageFrom(m) {
  if (m.role === "user") {
    appendUserMessage(m.text || "");
  } else if (m.role === "assistant") {
    appendAssistantMessage(m.text || "", m.error_message);
  } else {
    appendSystemMessage(m.text || "");
  }
}

function appendUserMessage(text) {
  const tpl = $("#tpl-message-user");
  const frag = tpl.content.cloneNode(true);
  frag.querySelector(".msg-bubble").textContent = text;
  els.messages.appendChild(frag);
  scrollToEnd();
}

function appendAssistantMessage(text, errorMessage) {
  const tpl = $("#tpl-message-assistant");
  const frag = tpl.content.cloneNode(true);
  const bubble = frag.querySelector(".msg-bubble");
  bubble.textContent = text || errorMessage || "(空)";
  if (errorMessage) bubble.classList.add("msg-error");
  els.messages.appendChild(frag);
  scrollToEnd();
}

function appendSystemMessage(text) {
  const tpl = $("#tpl-message-system");
  const frag = tpl.content.cloneNode(true);
  frag.querySelector(".msg-bubble").textContent = text;
  els.messages.appendChild(frag);
  scrollToEnd();
}

function appendPendingBubble() {
  const tpl = $("#tpl-message-assistant");
  const frag = tpl.content.cloneNode(true);
  const li = frag.querySelector("li");
  li.classList.add("msg-pending");
  const bubble = frag.querySelector(".msg-bubble");
  bubble.textContent = `${state.user || "operator"},收到`;
  els.messages.appendChild(frag);
  state.pending = li;
  scrollToEnd();
}

function finalizePendingBubble(text, isError) {
  if (!state.pending) {
    if (isError) {
      appendSystemMessage(text);
    } else {
      appendAssistantMessage(text);
    }
    return;
  }
  state.pending.classList.remove("msg-pending");
  const bubble = state.pending.querySelector(".msg-bubble");
  bubble.textContent = text;
  if (isError) {
    state.pending.className = "msg msg-system";
    state.pending.querySelector(".msg-role").textContent = "SYSTEM";
  }
  state.pending = null;
  scrollToEnd();
}

function renderEvents(events) {
  els.toolEvents.innerHTML = "";
  if (!events.length) {
    els.toolState.textContent = "—";
    return;
  }
  els.toolState.textContent = `${events.length} events`;
  for (const ev of events.slice(0, 12)) {
    const li = document.createElement("li");
    const name = document.createElement("span");
    name.className = "ev-name";
    name.textContent = ev.event || "(unnamed)";
    const payload = document.createElement("span");
    payload.className = "ev-payload";
    payload.textContent = truncate(JSON.stringify(ev.payload ?? {}), 140);
    li.appendChild(name);
    li.appendChild(payload);
    els.toolEvents.appendChild(li);
  }
}

function renderChainFromReply(text) {
  els.cotList.innerHTML = "";
  const segments = splitReasoning(text);
  if (!segments.length) {
    const li = document.createElement("li");
    li.className = "bubble dim";
    li.textContent = "—";
    els.cotList.appendChild(li);
    return;
  }
  segments.forEach((seg, idx) => {
    const li = document.createElement("li");
    li.className = "bubble" + (idx === segments.length - 1 ? " hot" : "");
    li.textContent = seg;
    els.cotList.appendChild(li);
  });
}

function updateLanes() {
  els.metricLanes.textContent = `${state.lanesActive} / 4`;
  els.gaugeConcurrency.textContent = `${state.lanesActive} / 4`;
}

// ----------------------------------------------------------------
// helpers
// ----------------------------------------------------------------

function scrollToEnd() {
  els.messages.scrollTop = els.messages.scrollHeight;
}

function formatTs(ts) {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  const pad = (n) => String(n).padStart(2, "0");
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function truncate(s, n) {
  if (!s) return "";
  if (s.length <= n) return s;
  return s.slice(0, n) + "…";
}

function splitReasoning(text) {
  if (!text) return [];
  // try to split by <think>/<final>; else by line blocks / sentence-like
  const thinkMatch = text.match(/<think>([\s\S]*?)<\/think>/i);
  const finalMatch = text.match(/<final>([\s\S]*?)<\/final>/i);
  const segments = [];
  if (thinkMatch) {
    const lines = thinkMatch[1]
      .split(/\n+/)
      .map((x) => x.trim())
      .filter(Boolean);
    segments.push(...lines.slice(0, 4));
  } else {
    const byLine = text
      .split(/\n+/)
      .map((x) => x.trim())
      .filter(Boolean);
    segments.push(...byLine.slice(0, 3));
  }
  if (finalMatch) {
    segments.push(`✓ ${finalMatch[1].trim()}`);
  }
  return segments.slice(0, 5);
}

// ----------------------------------------------------------------
// operator history (localStorage-backed)
// ----------------------------------------------------------------

function loadSavedUsers() {
  try {
    const raw = localStorage.getItem(USERS_LS_KEY);
    if (!raw) return [];
    const arr = JSON.parse(raw);
    return Array.isArray(arr) ? arr.filter((x) => typeof x === "string" && x.trim()) : [];
  } catch {
    return [];
  }
}

function saveSavedUsers(list) {
  try {
    localStorage.setItem(USERS_LS_KEY, JSON.stringify(list.slice(0, MAX_USERS)));
  } catch {}
}

function addSavedUser(user) {
  if (!user) return;
  const list = loadSavedUsers().filter((u) => u !== user);
  list.unshift(user);
  saveSavedUsers(list);
}

function removeSavedUser(user) {
  saveSavedUsers(loadSavedUsers().filter((u) => u !== user));
  renderSavedUsers();
}

function clearSavedUsers() {
  saveSavedUsers([]);
  renderSavedUsers();
}

function renderSavedUsers() {
  const users = loadSavedUsers();
  if (!users.length) {
    els.opHistoryBox.hidden = true;
    els.opHistoryList.innerHTML = "";
    return;
  }
  els.opHistoryBox.hidden = false;
  els.opHistoryList.innerHTML = "";
  for (const u of users) {
    const li = document.createElement("li");
    li.className = "op-chip" + (u === state.user ? " active" : "");

    const pick = document.createElement("button");
    pick.type = "button";
    pick.className = "pick";
    pick.textContent = u;
    pick.title = `以 ${u} 连接`;
    pick.addEventListener("click", () => {
      els.userInput.value = u;
      onConnectClick();
    });

    const drop = document.createElement("button");
    drop.type = "button";
    drop.className = "drop";
    drop.textContent = "✕";
    drop.title = "从历史中移除";
    drop.setAttribute("aria-label", `remove ${u}`);
    drop.addEventListener("click", (ev) => {
      ev.stopPropagation();
      removeSavedUser(u);
    });

    li.appendChild(pick);
    li.appendChild(drop);
    els.opHistoryList.appendChild(li);
  }
}

// ----------------------------------------------------------------
// UI events
// ----------------------------------------------------------------

function onConnectClick() {
  const user = els.userInput.value.trim();
  if (!user) return;
  state.user = user;
  addSavedUser(user);
  renderSavedUsers();
  if (state.ws && state.ws.readyState === WebSocket.OPEN) {
    send({ op: "hello", user });
  } else {
    connect();
  }
}

function onNewSessionClick() {
  if (!state.user) {
    appendSystemMessage("请先连接 operator。");
    return;
  }
  const name = prompt("新 session 名称：", `诊断 ${new Date().toLocaleString()}`);
  if (name === null) return;
  send({ op: "sessions.new", name: name.trim() });
}

function onComposerSubmit(ev) {
  ev.preventDefault();
  const text = els.messageInput.value.trim();
  if (!text) return;
  if (!state.active) {
    appendSystemMessage("请选择或新建一个 session。");
    return;
  }
  appendUserMessage(text);
  send({ op: "send", text });
  els.messageInput.value = "";
  autosize(els.messageInput);
  els.suggest.hidden = true;
}

function onInputChange() {
  autosize(els.messageInput);
  const v = els.messageInput.value;
  els.suggest.hidden = !/N\d+/i.test(v);
}

function onInputKey(ev) {
  if (ev.key === "Enter" && !ev.shiftKey) {
    ev.preventDefault();
    els.composer.requestSubmit();
  }
}

function autosize(ta) {
  ta.style.height = "auto";
  ta.style.height = Math.min(150, ta.scrollHeight) + "px";
}

function onToolsBtnClick() {
  els.toolsMenu.hidden = !els.toolsMenu.hidden;
}

function onToolSelect(ev) {
  const btn = ev.target.closest("button[data-prompt]");
  if (!btn) return;
  const prompt = btn.dataset.prompt + " ";
  els.messageInput.focus();
  const pos = els.messageInput.selectionStart ?? els.messageInput.value.length;
  const before = els.messageInput.value.slice(0, pos);
  const after = els.messageInput.value.slice(pos);
  els.messageInput.value = before + prompt + after;
  els.messageInput.selectionStart = els.messageInput.selectionEnd = before.length + prompt.length;
  els.toolsMenu.hidden = true;
  autosize(els.messageInput);
}

document.addEventListener("click", (ev) => {
  if (!ev.target.closest(".composer-tools")) {
    els.toolsMenu.hidden = true;
  }
});

// ----------------------------------------------------------------
// init
// ----------------------------------------------------------------

function init() {
  els.opConnect.addEventListener("click", onConnectClick);
  els.userInput.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") onConnectClick();
  });
  els.opHistoryClear.addEventListener("click", () => {
    if (confirm("清空保存的 operator 历史？")) clearSavedUsers();
  });
  els.newSession.addEventListener("click", onNewSessionClick);
  els.composer.addEventListener("submit", onComposerSubmit);
  els.messageInput.addEventListener("input", onInputChange);
  els.messageInput.addEventListener("keydown", onInputKey);
  els.toolsBtn.addEventListener("click", onToolsBtnClick);
  els.toolsMenu.addEventListener("click", onToolSelect);
  autosize(els.messageInput);
  renderSavedUsers();
  // 默认把历史里的第一个填进输入框（如果当前输入框是默认值），省一次手动挑选
  const users = loadSavedUsers();
  if (users.length && els.userInput.value === "alice" && !users.includes("alice")) {
    els.userInput.value = users[0];
  }
  connect();
}

init();
