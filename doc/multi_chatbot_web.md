# 多用户多会话并发测试 chatbot

本文档对应 `multi_chatbot/` 包，用来在浏览器里同时驱动多个 (用户, 会话)
向 OpenClaw gateway 并发发送预设提问，观察 `FairScheduler` 的真实负载。

## 启动

```bash
# 需要 gateway 已经跑起来（见 doc/gateway_interfaces.md）
bash run_multi_web.sh                   # 默认端口 5273
# 或直接跑模块
uv run python -m multi_chatbot.server --port 5273 --concurrency 4
```

打开 http://127.0.0.1:5273/ 即可进入控制台。

常用 CLI 选项：

- `--concurrency N`：`FairScheduler` 的总 slot 数，默认 4（对齐 OpenClaw 主
  agent 车道数）
- `--gateway-url`：gateway WS URL，默认 `ws://127.0.0.1:18789`
- `--gateway-password` / `--gateway-token`：鉴权凭据，见 `CLAUDE.md`
- `--config-path`：左侧树的持久化文件，默认
  `~/.openclaw-cli/multi-chatbot-config.json`
- `--pool-store`：pool 绑定持久化，默认与单用户 chatbot 共用
  `~/.openclaw-cli/session-map.json`

## 三栏 UI

- **左 · PLAN EDITOR**：树形编辑器，结构为「用户 → 会话 → 预设提问」。
  - 每个节点可重命名；用户和会话节点可折叠。
  - 文本框失焦/输入 400ms 后自动保存（右上角徽标显示「待保存 → 保存中 →
    已保存」）。
  - 删除用户 / 会话会二次确认，预设提问不做确认。
- **中 · CHAT GRID**：按「开始」后，左侧树里所有「含至少一条 questions
  的会话」会变成独立聊天窗口，按 2 列网格排列，每个窗口高度约等于视口高
  度的 1/3，超出部分垂直滚动。
  - 卡头颜色：灰=pending，蓝闪烁=running/blocked，绿=finished，红=error
  - 每个窗口内部独立滚动，不会被别的窗口推走
- **右 · RUN STATUS**：
  - SCHEDULER：进度条展示 `active / capacity`，条上方若出现
    `blocked×N` 说明有 N 个请求在排队等 slot
  - RUN CONTROL：开始 / 停止按钮
  - LANES：所有车道的实时状态列表（running、blocked、finished 等会按优
    先级置顶）

## 协议模型

- 每个浏览器连进 `/ws` 都会有一个独立的 `ConcurrentRunner`，但共享同一个
  `OpenClawSessionPool`（= 同一条到 gateway 的 WS + 同一套 scheduler slot）。
  因此两个页面同时开跑时，它们的总并发上限共用。
- 每条 lane 是一个 asyncio task，串行发送本 lane 的 questions；
  lane 之间并发。
- `send_with_key()` 为每条请求申请 scheduler slot，拿不到就在
  `asyncio.Semaphore` 上挂起，这部分挂起数量就是前端看到的「BLOCKED」。

## 持久化文件

- `~/.openclaw-cli/multi-chatbot-config.json`：左侧树（用户 / 会话 / 提问
  三层）
- `~/.openclaw-cli/session-map.json`：`OpenClawSessionPool.SessionStore`
  的绑定（可能被单用户 chatbot 和 CLI 一起使用）。本 app **不调用**
  `pool.bind()`，而是用 `send_with_key(sessionKey=...)` 直接发，因此不会
  污染 session-map。

## 与 `chatbot/` 的关系

两者并行存在：

| 维度 | `chatbot/` | `multi_chatbot/` |
| --- | --- | --- |
| 前端用户数 | 1 人一页 | 1 人一页，但同时驱动多 (user, session) |
| 聊天窗口数 | 1（当前选中的 session） | N（所有含提问的会话） |
| 后端 send 路径 | `pool.send(user, text)`（走 binding） | `pool.send_with_key(sessionKey, text)` |
| 交互 | 手工输入 + 发送 | 预设清单 + 批量开跑 |
| 端口 | 5173 | 5273 |
