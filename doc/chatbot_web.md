# Chatbot Web 前端

一个扁平 FUI 风格的多用户多 session chatbot 网页，直接接入
`OpenClawSessionPool`。单端口同时服务 HTTP（静态页面）和 WebSocket
（前后端消息通道），基于现有依赖，不引入新包。

- 入口：`python -m chatbot.server`
- 静态资源：`chatbot/static/{index.html,app.css,app.js}`
- 映射：
  - pool 的 `SessionStore` 仍然记录「当前活跃绑定」
  - 新增 `chatbot.user_sessions.UserSessionsStore` 记录「每用户拥有的 session 列表」

视觉风格参考 `doc/diagnostic_agent_video_prompts.md`：纯白底 + 科技蓝，LLM
琥珀金强调，左 session 列表 / 中对话区 / 右 inspector（LLM 思考链 + 工具事件
+ 池指标）。

## 启动

```bash
# 默认端口 5173；gateway 用 password=zxt2000
uv run python -m chatbot.server

# 指定 gateway token / 自定义端口
uv run python -m chatbot.server \
  --gateway-token YOUR_TOKEN \
  --port 5173

# 所有选项
uv run python -m chatbot.server --help
```

启动后打开浏览器访问 `http://127.0.0.1:5173/`。左上 `OPERATOR ID` 输入用户
名（任意字符串，按 `CONNECT` 会把它作为 pool 的 user key），然后
`NEW DIAGNOSTIC SESSION` 新建会话，即可开始发消息。

## 前后端消息协议（text frame / JSON）

客户端 → 服务端：

```jsonc
{"op": "hello", "user": "alice"}
{"op": "sessions.list"}
{"op": "sessions.new", "name": "诊断 N105 丢包"}
{"op": "sessions.switch", "id": "dfc24fda0d43"}
{"op": "sessions.delete", "id": "dfc24fda0d43"}
{"op": "sessions.rename", "id": "dfc24fda0d43", "name": "新名字"}
{"op": "send", "text": "诊断南山区西丽 N105 网元丢包问题"}
{"op": "history.refresh"}
```

服务端 → 客户端：

```jsonc
{"op": "hello.ok", "user": "alice", "sessions": [...], "active": {...} | null}
{"op": "sessions", "sessions": [...], "active": {...} | null}
{"op": "history", "messages": [{"role":"user","text":"...","timestamp":...}, ...]}
{"op": "reply.pending"}
{"op": "reply.done", "text": "...", "events": [{event, payload}, ...]}
{"op": "reply.error", "message": "..."}
{"op": "error", "message": "..."}
```

`events` 是 pool 收到的 agent 事件截断到前 16 条，用于前端 inspector 面板展示。

## 持久化

| 文件 | 存什么 |
| --- | --- |
| `~/.openclaw-cli/session-map.json`（pool 默认路径） | 用户当前活跃绑定 `{user → chat_session_id, openclaw_session_key}` |
| `~/.openclaw-cli/user-sessions.json` | 每用户的 session 列表 `{user → [{id,name,created_at,updated_at}]}` |

两边分离的原因：pool 的 store 语义是「谁是当前」；前端的 store 语义是
「历史上有哪些」。切换 session 只改 pool 绑定，不动 user-sessions 列表；删除
session 会同时清理两边。

## 前端行为

- WS 断开会每 2 秒自动重连。
- operator 输入框下方有 `RECENT OPERATORS` chip 列表：成功 CONNECT 过的
  operator id 会被记到浏览器 localStorage（key `openclaw.chatbot.users`，
  上限 16 条）。点击 chip 即切换并连接；右侧 `✕` 从历史里移除；顶部 `clear`
  清空全部。首次加载且输入框仍是默认值时，会自动填上最近一个 operator。
- 输入框左侧的 `+` 场景菜单**默认折叠**，点击 `+` 切换显示；菜单外点击或
  选中其中一项会自动关闭。
- 选 operator → `hello` → 渲染该用户的 session 列表 + 当前活跃。
- 切换 session → pool.bind 覆盖 → 前端自动调 `history.refresh` 拉历史。
- 发送消息：`send` 后立即在消息流插入一条 `{operatorId},收到` 的占位
  bubble（例如 `alice,收到`），收到 `reply.done` 时替换为真实回复，同时右侧
  inspector 把事件列表和 CoT 更新。
- **Tool call / Tool output 卡片**：`chat.history` 里以 `role=assistant + 
  type=toolCall` / `role=toolResult` 两种形态成对出现；前端按 tool 名把输入
  输出各自渲染成一张独立卡片，嵌在消息流里。卡片头可整体折叠，折叠时显示
  首行预览（比如 tool_call 的 JSON 第一行）；展开时内容自适应高度，且最少
  显示约 4 行文本再滚动。内部 `TOOL INPUT` / `TOOL OUTPUT` 小节也能单独
  折叠，JSON 会自动格式化。
- **思考块**：assistant 消息里的 `{type: "thinking"}` part、或回复文本中
  `<think>…</think>` 片段，会被抽成独立的「Thinking」卡片（琥珀金边框），
  默认收起并显示首行摘要；点击展开看完整推理。
- **非 user/assistant 消息**：`toolResult` / `toolUse` / `developer` 这种
  role 若仍有剩余纯文本，会用灰色系统气泡展示，role 名作为标签显示在气泡顶部。
- **实时流式**：每次 `send` 会通过 `sessions.messages.subscribe` 订阅
  gateway 的 transcript 通道；在消息逐步生成过程中，前端按以下 op 实时
  推送并渲染：
  - `stream.lifecycle`：`start` / `end`，更新右上角 LLM 状态文案；
  - `stream.message`：每当 gateway 提交一条新消息（toolCall、toolResult、
    thinking、assistant 终稿），立刻派一条过来，前端按
    `message_id` (`__openclaw.id`) 去重，避免多次渲染同一条消息；
  - `stream.delta`：assistant 生成过程中的增量文字，前端合并到「活动气泡」
    里覆盖显示（不新增节点、不插入换行），看起来像一边说一边打字。
  收到 `role=assistant` 且 `parts` 里只有 `tool_call` / `thinking`（没可见
  正文）的 message 时，前端会**清掉** `{operatorId},收到` 占位气泡，把下
  一次 `stream.delta` 开新气泡用。
  reply 完成后再做一次 `history.refresh` 拿 gateway 权威视图兜底。
- **布局 / 滚动**：顶层三栏 grid 用 `grid-template-rows: minmax(0, 1fr)`
  锁死行高；`.messages` 自己 `overflow-y: auto` 独立滚动；消息/卡片
  `flex: 0 0 auto`，任何长度的 tool pre/thinking 文本都**不会被 flex 压扁**，
  超出就出现纵向滚动条，可以拖拉查看历史。
- `<final>…</final>` 标签（模型用作最终答复标识）在渲染时会被剥掉，
  只显示内部文字；`<think>…</think>` 仍会抽成 Thinking 卡片。
- **右侧 inspector 的 Tool events 面板**：把原始事件流压缩成每行一条的紧凑
  展示，而不是每个 token 都占一行：
  - `session.message`：一行展示 role + content 里的 part 类型（例如
    `assistant · toolCall·text`），detail 里跟一段内容摘要；
  - `agent.assistant` 流式 delta 被合并为同一条 `×N` 计数；
  - `chat.delta` 同样合并成一条；
  - `agent.lifecycle` 保留 start/end 两行；
  - `health` / `tick` 这类噪声事件直接过滤掉。
  顶部状态文字显示 `<condensed_rows> · <raw_count> raw`，方便对比。
- **右上角开关（持久到 localStorage，key `openclaw.chatbot.prefs`）**：
  - 🧠 `THINK`：切换是否显示 Thinking 卡片，默认关。
  - 🔧 `TOOLS`：切换是否显示 Tool call / Tool output 卡片，默认开。
  关/开只改 CSS 可见性，不会清空已加载的消息。
- 若回复里包含 `<think>…</think>` / `<final>…</final>` 这类常见标签，CoT
  会把 `<think>` 按行切成几个 bubble，把 `<final>` 作为最后高亮那一条。

## 已知限制 / 后续方向

- 不是 SSE 流式：reply 是「pending → done」两步展示。要真流式需给 pool 的
  `send_message` 加 `on_event` 回调，把 delta 帧通过 WS 推给前端。
- `sessions.delete` 会从前端列表移除，但 OpenClaw 侧的 session 历史保留在
  gateway 上（由 gateway 自己的 retention 控制）。
- `rename` 当前没提供 UI 按钮，但协议已支持；可在前端加一个右键菜单或编辑
  图标后直接发 `sessions.rename`。
- 真要暴露到公网必须加鉴权（默认信任本机）——当前 WS/HTTP 无任何验证。
