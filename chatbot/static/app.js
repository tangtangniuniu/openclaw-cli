"use strict";
/* OpenClaw Diagnostic Console — frontend controller */

const $ = (sel) => document.querySelector(sel);

const state = {
  ws: null,
  user: null,
  sessions: [],
  active: null,      // {id, name, session_key}
  messages: [],      // normalized from server
  pending: null,     // DOM element for the streaming assistant bubble
  liveText: "",      // accumulated streaming text
  lanesActive: 0,
  showThinking: false,
  showTools: true,
  streamSeen: new Set(), // dedupe keys for stream.message
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
  toggleThinking: $("#toggle-thinking"),
  toggleTools: $("#toggle-tools"),
};

const USERS_LS_KEY = "openclaw.chatbot.users";
const PREFS_LS_KEY = "openclaw.chatbot.prefs";
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
      // 一旦收到权威历史，清空流式去重 key，让下次发送能重新接收
      state.streamSeen = new Set();
      break;
    case "reply.pending":
      state.lanesActive = Math.min(4, state.lanesActive + 1);
      updateLanes();
      els.llmState.textContent = "thinking…";
      // 清空本轮 stream 去重
      state.streamSeen = new Set();
      // 移除上一个回合残留的 pending 气泡（如果有）
      if (state.pending && state.pending.parentNode) {
        state.pending.remove();
      }
      state.pending = null;
      // 立刻放一个 "alice,收到" 占位气泡，流式 delta/message 来了就接管它
      appendPendingBubble();
      break;
    case "stream.lifecycle":
      // 仅用来更新右上角的 LLM 状态文案
      els.llmState.textContent = msg.phase === "start" ? "running…" : "wrapping up…";
      break;
    case "stream.message": {
      if (!msg.message) break;
      applyStreamMessage(msg.message);
      break;
    }
    case "stream.delta": {
      ensureLiveAssistant();
      appendLiveDelta(msg.delta || "", msg.text || null);
      break;
    }
    case "reply.done":
      state.lanesActive = Math.max(0, state.lanesActive - 1);
      updateLanes();
      els.llmState.textContent = "done";
      finalizeLiveAssistant(msg.text || "(空回复)");
      if (Array.isArray(msg.events)) renderEvents(msg.events);
      renderChainFromReply(msg.text || "");
      break;
    case "reply.error":
      state.lanesActive = Math.max(0, state.lanesActive - 1);
      updateLanes();
      els.llmState.textContent = "error";
      finalizeLiveAssistant(`⚠ 发送失败：${msg.message}`, true);
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
  const parts = Array.isArray(m.parts) ? m.parts : [];
  // 1) thinking parts first
  for (const p of parts) {
    if (p && p.kind === "thinking") appendThinkingCard(p.text || "");
  }
  // 2) tool parts as collapsible cards
  for (const p of parts) {
    if (!p) continue;
    if (p.kind === "tool_call") appendToolCard(p, "tool_call");
    if (p.kind === "tool_output") appendToolCard(p, "tool_output");
  }
  // 3) main text bubble (<think> stripped out into a thinking card)
  const { thinking, visible } = splitThinkFromText(m.text || "");
  if (thinking) appendThinkingCard(thinking);
  if (!visible) return;
  if (m.role === "user") {
    appendUserMessage(visible);
  } else if (m.role === "assistant") {
    appendAssistantMessage(visible, m.error_message);
  } else {
    // 任何其它 role（system/developer/toolResult/toolUse/自定义…）都渲染一个
    // 带 role 标签的系统气泡；tool 消息里真正结构化的输入输出已经在 parts
    // 里单独成卡，这里的 visible 只是剩余的纯文本或未被识别的内容。
    appendSystemMessage(visible, m.role);
  }
}

function splitThinkFromText(text) {
  if (!text) return { thinking: "", visible: "" };
  const matches = [...text.matchAll(/<think>([\s\S]*?)<\/think>/gi)];
  const thinking = matches.map((m) => m[1].trim()).filter(Boolean).join("\n\n");
  let visible = text.replace(/<think>[\s\S]*?<\/think>/gi, "");
  // 剥掉 <final>…</final> 的包裹标签，里面的内容就是最终回复
  visible = visible.replace(/<\/?final>/gi, "").trim();
  return { thinking, visible };
}

function appendUserMessage(text) {
  const tpl = $("#tpl-message-user");
  const frag = tpl.content.cloneNode(true);
  frag.querySelector(".msg-bubble").textContent = text;
  insertMessageNode(frag);
  scrollToEnd();
}

function appendAssistantMessage(text, errorMessage) {
  const tpl = $("#tpl-message-assistant");
  const frag = tpl.content.cloneNode(true);
  const bubble = frag.querySelector(".msg-bubble");
  bubble.textContent = text || errorMessage || "(空)";
  if (errorMessage) bubble.classList.add("msg-error");
  insertMessageNode(frag);
  scrollToEnd();
}

function appendSystemMessage(text, role) {
  const tpl = $("#tpl-message-system");
  const frag = tpl.content.cloneNode(true);
  if (role && typeof role === "string") {
    frag.querySelector(".msg-role").textContent = role.toUpperCase();
  }
  frag.querySelector(".msg-bubble").textContent = text;
  insertMessageNode(frag);
  scrollToEnd();
}

function appendPendingBubble() {
  const tpl = $("#tpl-message-assistant");
  const frag = tpl.content.cloneNode(true);
  const li = frag.querySelector("li");
  li.classList.add("msg-pending");
  const bubble = frag.querySelector(".msg-bubble");
  bubble.textContent = `${state.user || "operator"},收到`;
  els.messages.appendChild(frag); // pending 总是在末尾
  state.pending = li;
  state.liveText = "";
  scrollToEnd();
  return li;
}

// 第一次收到 stream.delta 或 stream.message(role=assistant) 时创建一个活动气泡；
// 若上次已有 pending，则复用它并把 "收到" 占位文字清空。
function ensureLiveAssistant() {
  if (state.pending && state.pending.parentNode) {
    if (state.pending.classList.contains("msg-pending")) {
      state.pending.querySelector(".msg-bubble").textContent = "";
      state.pending.classList.remove("msg-pending");
      state.liveText = "";
    }
    return state.pending;
  }
  const tpl = $("#tpl-message-assistant");
  const frag = tpl.content.cloneNode(true);
  const li = frag.querySelector("li");
  frag.querySelector(".msg-bubble").textContent = "";
  els.messages.appendChild(frag);
  state.pending = li;
  state.liveText = "";
  scrollToEnd();
  return li;
}

function appendLiveDelta(delta, full) {
  const li = ensureLiveAssistant();
  const bubble = li.querySelector(".msg-bubble");
  if (typeof full === "string" && full.length >= state.liveText.length) {
    // 优先用完整文本，确保和网关累积的全文一致
    state.liveText = full;
  } else {
    state.liveText += delta || "";
  }
  const { visible } = splitThinkFromText(state.liveText);
  bubble.textContent = visible || state.liveText;
  scrollToEnd();
}

function finalizeLiveAssistant(finalText, isError) {
  if (!state.pending) {
    if (isError) appendSystemMessage(finalText);
    else if (finalText && finalText.trim()) appendAssistantMessage(finalText);
    return;
  }
  const bubble = state.pending.querySelector(".msg-bubble");
  // 如果 pending 还是 "alice,收到" 占位，不要把占位文字当 "打印过的内容"
  const liveWas = state.liveText;
  const candidate = finalText || liveWas || "";
  const { thinking, visible } = splitThinkFromText(candidate);
  const shown = visible || candidate;

  if (thinking) {
    const thinkingCard = buildThinkingCard(thinking);
    state.pending.parentNode.insertBefore(thinkingCard, state.pending);
  }

  if (!shown || !shown.trim()) {
    // 收尾时没有任何实际文字（这条 message 只有 tool_call / thinking）——
    // 直接把占位 bubble 移除，让 tool 卡片自己占位。
    state.pending.remove();
    state.pending = null;
    state.liveText = "";
    return;
  }

  state.pending.classList.remove("msg-pending");
  bubble.textContent = shown;
  if (isError) {
    state.pending.className = "msg msg-system";
    state.pending.querySelector(".msg-role").textContent = "SYSTEM";
  }
  state.pending = null;
  state.liveText = "";
  scrollToEnd();
}

function buildThinkingCard(text) {
  const tpl = $("#tpl-thinking-card");
  const frag = tpl.content.cloneNode(true);
  const li = frag.querySelector(".think-card");
  const body = frag.querySelector(".think-body");
  const preview = frag.querySelector(".tool-preview");
  body.textContent = text;
  if (preview) preview.textContent = firstLine(text);
  const head = frag.querySelector(".think-head");
  head.addEventListener("click", () => {
    li.classList.toggle("expanded");
    body.hidden = !li.classList.contains("expanded");
  });
  return li;
}

// 实时应用 stream.message（来自 gateway 的 session.message / chat state=final）
function applyStreamMessage(message) {
  if (!message) return;
  // user message 已经在本地 onComposerSubmit 即时渲染，避免重复
  if (message.role === "user") return;

  const key =
    message.message_id ||
    JSON.stringify([
      message.role,
      message.timestamp,
      (message.text || "").slice(0, 40),
      (message.parts || [])
        .map((p) => `${p.kind}:${p.name || ""}:${p.id || ""}`)
        .join("|"),
    ]);
  if (state.streamSeen.has(key)) return;
  state.streamSeen.add(key);

  const parts = Array.isArray(message.parts) ? message.parts : [];
  const { thinking: inlineThink, visible } = splitThinkFromText(message.text || "");

  // 1) thinking part / inline <think> 块 —— 各自一张折叠卡
  for (const p of parts) {
    if (p && p.kind === "thinking") appendThinkingCard(p.text || "");
  }
  if (inlineThink) appendThinkingCard(inlineThink);

  // 2) tool_call / tool_output —— 各自一张折叠卡
  for (const p of parts) {
    if (!p) continue;
    if (p.kind === "tool_call") appendToolCard(p, "tool_call");
    if (p.kind === "tool_output") appendToolCard(p, "tool_output");
  }

  // 3) 主文本气泡：assistant 用活动气泡收尾；其它 role 用系统气泡
  if (message.role === "assistant") {
    if (visible && visible.trim()) {
      finalizeLiveAssistant(visible, false);
    } else if (parts.length > 0) {
      // 只有 tool_call / thinking 的 assistant 消息：把占位 bubble 清掉
      if (state.pending) {
        state.pending.remove();
        state.pending = null;
        state.liveText = "";
      }
    }
  } else if (visible && visible.trim()) {
    appendSystemMessage(visible, message.role);
  }
}

function renderEvents(events) {
  els.toolEvents.innerHTML = "";
  if (!events.length) {
    els.toolState.textContent = "—";
    return;
  }
  const rows = condenseEvents(events);
  els.toolState.textContent = `${rows.length} · ${events.length} raw`;
  for (const row of rows) {
    const li = document.createElement("li");
    if (row.kind) li.dataset.kind = row.kind;
    const badge = document.createElement("span");
    badge.className = "ev-badge";
    badge.textContent = row.badge;
    const label = document.createElement("span");
    label.className = "ev-label";
    label.textContent = row.label;
    const detail = document.createElement("span");
    detail.className = "ev-detail";
    detail.textContent = row.detail || "";
    li.appendChild(badge);
    li.appendChild(label);
    if (row.detail) li.appendChild(detail);
    if (row.count > 1) {
      const count = document.createElement("span");
      count.className = "ev-count";
      count.textContent = "×" + row.count;
      li.appendChild(count);
    }
    els.toolEvents.appendChild(li);
  }
}

function condenseEvents(events) {
  const out = [];
  const pushOrMerge = (kind, row) => {
    const last = out[out.length - 1];
    if (last && last.kind === kind) {
      last.count++;
      if (row.detail) last.detail = row.detail; // keep last seen sample
      return;
    }
    out.push({ kind, count: 1, ...row });
  };

  for (const ev of events) {
    const name = ev.event || "(unnamed)";
    const payload = ev.payload || {};
    if (name === "health" || name === "tick") continue;

    if (name === "agent") {
      const stream = payload.stream;
      const data = payload.data || {};
      if (stream === "assistant") {
        const delta = String(data.delta || "").replace(/\n/g, "⏎");
        pushOrMerge("agent-assistant", {
          badge: "AGENT",
          label: "assistant · delta",
          detail: truncate(delta, 48),
        });
        continue;
      }
      if (stream === "lifecycle") {
        out.push({
          kind: "agent-lifecycle",
          count: 1,
          badge: "AGENT",
          label: `lifecycle · ${data.phase || "?"}`,
          detail: "",
        });
        continue;
      }
      out.push({
        kind: "agent-other",
        count: 1,
        badge: "AGENT",
        label: stream || "agent",
        detail: truncate(JSON.stringify(data), 64),
      });
      continue;
    }

    if (name === "chat") {
      const state = payload.state;
      const msg = payload.message || {};
      if (state === "delta") {
        pushOrMerge("chat-delta", {
          badge: "CHAT",
          label: `${msg.role || "?"} · delta`,
          detail: "",
        });
        continue;
      }
      out.push({
        kind: "chat-final",
        count: 1,
        badge: "CHAT",
        label: `${msg.role || "?"} · ${state || ""}`,
        detail: briefContent(msg.content),
      });
      continue;
    }

    if (name === "session.message") {
      const msg = payload.message || {};
      const cTypes = Array.isArray(msg.content)
        ? msg.content
            .map((x) => (x && x.type) || "?")
            .filter(Boolean)
            .join("·")
        : typeof msg.content === "string"
        ? "text"
        : "";
      out.push({
        kind: `session-${msg.role || "unknown"}`,
        count: 1,
        badge: "MSG",
        label: `${msg.role || "?"}${cTypes ? " · " + cTypes : ""}`,
        detail: briefContent(msg.content),
      });
      continue;
    }

    out.push({
      kind: "misc",
      count: 1,
      badge: name.slice(0, 8).toUpperCase(),
      label: name,
      detail: truncate(JSON.stringify(payload), 64),
    });
  }
  return out;
}

function briefContent(content) {
  if (!content) return "";
  if (typeof content === "string") {
    return truncate(content.replace(/\s+/g, " "), 80);
  }
  if (Array.isArray(content)) {
    const bits = content
      .map((x) => {
        if (!x) return "";
        if (x.type === "text") return truncate(String(x.text || ""), 40);
        if (x.type === "toolCall" || x.type === "tool_use" || x.type === "tool_call") {
          const args = x.arguments ?? x.input ?? x.args ?? {};
          return `${x.name || "?"}(${truncate(JSON.stringify(args), 32)})`;
        }
        if (x.type === "toolResult" || x.type === "tool_result") return "⇐ " + truncate(String(x.content || ""), 40);
        if (x.type === "thinking") return "⊙ " + truncate(String(x.thinking || ""), 40);
        return x.type || "";
      })
      .filter(Boolean);
    return truncate(bits.join(" · "), 100);
  }
  return truncate(JSON.stringify(content), 80);
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
// Tool / thinking cards
// ----------------------------------------------------------------

function appendToolCard(part, kind) {
  const tpl = $("#tpl-tool-card");
  const frag = tpl.content.cloneNode(true);
  const li = frag.querySelector(".tool-card");
  li.dataset.kind = kind;

  const label = kind === "tool_call" ? "Tool call" : "Tool output";
  li.querySelector(".tool-kind").textContent = label;
  li.querySelector(".tool-name").textContent = part.name || "";

  // head: 折叠/展开整个卡
  const head = li.querySelector(".tool-head");
  head.addEventListener("click", () => {
    li.classList.toggle("collapsed");
    head.setAttribute(
      "aria-expanded",
      li.classList.contains("collapsed") ? "false" : "true"
    );
  });

  const inputSec = li.querySelector(".tool-input");
  const outputSec = li.querySelector(".tool-output");
  const preview = li.querySelector(".tool-preview");
  let previewText = "";

  if (part.input !== undefined && part.input !== null && kind === "tool_call") {
    inputSec.hidden = false;
    const formatted = prettyJson(part.input);
    inputSec.querySelector(".tool-input-pre").textContent = formatted;
    wireSectionToggle(inputSec);
    previewText = firstLine(formatted);
  }
  if (part.output !== undefined && part.output !== null && kind === "tool_output") {
    outputSec.hidden = false;
    const formatted = prettyJson(part.output);
    outputSec.querySelector(".tool-output-pre").textContent = formatted;
    wireSectionToggle(outputSec);
    previewText = firstLine(formatted);
  }

  if (previewText) {
    preview.textContent = previewText;
  }

  // 默认折叠 output（内容通常较大），call 默认展开
  if (kind === "tool_output") li.classList.add("collapsed");

  insertMessageNode(frag);
  scrollToEnd();
}

function appendThinkingCard(text) {
  if (!text) return;
  const tpl = $("#tpl-thinking-card");
  const frag = tpl.content.cloneNode(true);
  const li = frag.querySelector(".think-card");
  const body = frag.querySelector(".think-body");
  const preview = frag.querySelector(".tool-preview");
  body.textContent = text;
  if (preview) preview.textContent = firstLine(text);
  const head = frag.querySelector(".think-head");
  head.addEventListener("click", () => {
    li.classList.toggle("expanded");
    const expanded = li.classList.contains("expanded");
    body.hidden = !expanded;
    head.setAttribute("aria-expanded", expanded ? "true" : "false");
  });
  insertMessageNode(frag);
  scrollToEnd();
}

function wireSectionToggle(section) {
  const head = section.querySelector(".tool-section-head");
  head.addEventListener("click", () => {
    section.classList.toggle("collapsed");
    head.setAttribute(
      "aria-expanded",
      section.classList.contains("collapsed") ? "false" : "true"
    );
  });
}

function firstLine(s, n = 120) {
  if (!s) return "";
  const first = String(s).split(/\n/).map((x) => x.trim()).find((x) => x.length > 0) || "";
  return first.length > n ? first.slice(0, n) + "…" : first;
}

function prettyJson(value) {
  if (typeof value === "string") {
    const trimmed = value.trim();
    if (trimmed.startsWith("{") || trimmed.startsWith("[")) {
      try {
        return JSON.stringify(JSON.parse(trimmed), null, 2);
      } catch {}
    }
    return value;
  }
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

// ----------------------------------------------------------------
// Preferences (show thinking / tools) — localStorage backed
// ----------------------------------------------------------------

function loadPrefs() {
  try {
    const raw = localStorage.getItem(PREFS_LS_KEY);
    if (!raw) return {};
    const obj = JSON.parse(raw);
    return obj && typeof obj === "object" ? obj : {};
  } catch {
    return {};
  }
}

function savePrefs() {
  try {
    localStorage.setItem(
      PREFS_LS_KEY,
      JSON.stringify({
        showThinking: state.showThinking,
        showTools: state.showTools,
      })
    );
  } catch {}
}

function applyToggles() {
  els.messages.classList.toggle("hide-think", !state.showThinking);
  els.messages.classList.toggle("hide-tools", !state.showTools);
  els.toggleThinking.classList.toggle("on", state.showThinking);
  els.toggleThinking.classList.toggle("toggle-thinking", true);
  els.toggleThinking.setAttribute(
    "aria-pressed",
    state.showThinking ? "true" : "false"
  );
  els.toggleTools.classList.toggle("on", state.showTools);
  els.toggleTools.setAttribute(
    "aria-pressed",
    state.showTools ? "true" : "false"
  );
}

// ----------------------------------------------------------------
// helpers
// ----------------------------------------------------------------

function scrollToEnd() {
  els.messages.scrollTop = els.messages.scrollHeight;
}

// 插入新消息时，若有活跃 pending（流式占位气泡），把新节点放在 pending 之前，
// 让 pending 保持在末尾表示"下一个要填进来的回复"。
function insertMessageNode(nodeOrFrag) {
  if (state.pending && state.pending.parentNode === els.messages) {
    els.messages.insertBefore(nodeOrFrag, state.pending);
  } else {
    els.messages.appendChild(nodeOrFrag);
  }
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
  // restore prefs first so first render respects them
  const prefs = loadPrefs();
  state.showThinking = !!prefs.showThinking;
  state.showTools = prefs.showTools !== false; // default true

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
  els.toggleThinking.addEventListener("click", () => {
    state.showThinking = !state.showThinking;
    savePrefs();
    applyToggles();
  });
  els.toggleTools.addEventListener("click", () => {
    state.showTools = !state.showTools;
    savePrefs();
    applyToggles();
  });
  autosize(els.messageInput);
  renderSavedUsers();
  applyToggles();
  // 默认把历史里的第一个填进输入框（如果当前输入框是默认值），省一次手动挑选
  const users = loadSavedUsers();
  if (users.length && els.userInput.value === "alice" && !users.includes("alice")) {
    els.userInput.value = users[0];
  }
  connect();
}

init();
