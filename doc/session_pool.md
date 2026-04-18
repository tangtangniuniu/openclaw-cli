# OpenClaw Session Pool

把 chatbot 侧的 `(user, chat_session_id)` 稳定映射到 OpenClaw Gateway 的
`sessionKey`，对外提供「按用户发消息」的最小 API，内部：

- 单条共享 WebSocket + 自动重连（指数退避 1s→30s）
- 帧路由器多路复用 `req id` / `sessionKey`，支持真正的 4 并发
- 公平调度（FIFO）限制主 agent 车道并发
- 映射持久化到 `~/.openclaw-cli/session-map.json`，重启后自动恢复
- 用户切换 session → 调用 `bind()` 覆盖当前绑定

模块：`src/openclaw_client/pool.py`
示例：`demo/session_pool_demo.py`

## 快速开始

```python
from openclaw_client import OpenClawSessionPool

async def main():
    async with OpenClawSessionPool(
        url="ws://127.0.0.1:18789",
        token="YOUR_GATEWAY_TOKEN",   # 或 password="..."
        concurrency=4,
    ) as pool:
        # 绑定（幂等；再次调用即切换）
        pool.bind("alice", "thread-1")
        pool.bind("bob", "thread-9")

        # 并发发送；内部自动限流到 concurrency
        result = await pool.send("alice", "你好")
        print(result.reply_text)
```

## API

```python
class OpenClawSessionPool:
    def __init__(
        *,
        url: str = "ws://127.0.0.1:18789",
        password: str | None = "zxt2000",
        token: str | None = None,
        store_path: Path | str | None = None,  # 默认 ~/.openclaw-cli/session-map.json
        concurrency: int = 4,
    )

    async def start() -> None                     # 启动后台 supervisor
    async def stop() -> None
    async def __aenter__() / __aexit__           # 推荐用 async with

    # 绑定管理（同步，写入会立刻落盘）
    def bind(user, chat_session_id, *, session_key=None) -> SessionBinding
    def unbind(user) -> bool
    def binding(user) -> SessionBinding | None
    def bindings() -> dict[str, SessionBinding]

    # 发消息（要求 bind 过）
    async def send(
        user,
        message,
        *,
        response_timeout: float = 60.0,
        settle_timeout: float = 1.0,
        fallback_history: bool = True,
    ) -> RequestResult
```

`SessionBinding` 字段：`user`、`chat_session_id`、`openclaw_session_key`、
`updated_at`（Unix 秒）。

默认 `session_key` 派生规则：`chat:{user}:{chat_session_id}`（
`derive_session_key()`）。也可以手动传 `session_key=` 指定任意字符串。

## 架构分层

```
OpenClawSessionPool        对外 API：bind/send
  ├── SessionStore         JSON 持久化（原子写：tmp + rename）
  ├── FairScheduler        asyncio.Semaphore(4) + FIFO
  └── ConnectionSupervisor 长连接 + 握手 + 重连
        └── FrameRouter    后台 reader task，按 req_id / sessionKey 分发帧
```

### SessionStore

- 结构 `user -> SessionBinding`，`bind()`/`unbind()` 立刻 `os.replace` 到磁盘。
- `load()` 做版本校验（`STORE_VERSION=1`），版本不匹配会抛 `PoolError`。
- 文件格式：

  ```json
  {
    "version": 1,
    "users": {
      "alice": {
        "chat_session_id": "thread-1",
        "openclaw_session_key": "chat:alice:thread-1",
        "updated_at": 1744934400
      }
    }
  }
  ```

### FrameRouter

为「一个 WS 上跑多路并发请求」设计，是现有 `OpenClawGatewayClient` 的串行
recv 模型的替代。流程：

1. `subscribe(req_id, session_key)` → 返回一个 `asyncio.Queue`（`_Subscription`
   内部字段）。
2. 后台 reader task `_read_loop` 持续 `async for` 读帧，`res` 按 `id` 投递，
   `event` 按 `payload.sessionKey` 投递给所有订阅者。
3. 发送走 `send_request(req_id, method, params)`；内部有 `_send_lock` 确保
   WS `send` 不并发（`websockets` 不保证并发安全）。
4. 连接断开/抛异常 → `close(exc)` 把 `{"__router_closed__": True, "error": ...}`
   广播给所有等待者，上层据此抛 `PoolError`。

事件没带 `sessionKey` 的情况下（如 `tick`、`shutdown` 等系统级事件）目前会被
router 丢弃，不影响 agent 对话流。

### ConnectionSupervisor

- `start()` 启动 `_supervise` task。
- 生命周期：`connect → _handshake → 新建 FrameRouter → wait_closed → backoff → 重连`。
- 握手复用 `openclaw_client.device_auth.build_signed_device`，按
  `operator_token → token → password` 的顺序尝试 auth variant，直到 gateway
  接受一个签名候选。
- 重连时**不需要重新绑定 sessionKey**，因为 OpenClaw 的 session 是 gateway 侧
  概念，和连接无关。上层在重连期间的 `send()` 会阻塞在 `get_router()`，等连
  上后自动继续。

### FairScheduler

`asyncio.Semaphore(capacity)` + 上下文管理器，`slot(user)` 内持有一个 slot。

**为什么不做 per-user round-robin？**
单用户单 session 单请求的假设下，每个用户最多有 1 个在飞请求，所以「用户级
公平」等价于「请求级 FIFO」，`Semaphore` 自带的 FIFO 就够了。后续若允许同一
用户同时多请求，可以把 `slot(key)` 换成按 key 排序的 round-robin 实现。

## 自动重连行为

- 连接异常/读循环终止 → `FrameRouter.close` → Supervisor 循环 `backoff` 睡眠
  后重连。
- 正在飞的请求会收到 `__router_closed__` 帧，`send()` 返回 `PoolError`。调用
  方可以捕获后重试。
- 重连后 `supervisor.get_router()` 会返回新 router；后续请求立即能发。
- Backoff：初次 1s，每次失败 ×2，上限 30s；成功连上后重置为 1s。

## 与现有 `OpenClawGatewayClient` 的边界

- `client.py`（串行、单请求）**没有被改动**，现有单测全部通过；适合一次性脚
  本或 `openclaw_client.cli` 这种场景。
- `pool.py` 是并发多用户场景的上层；复用 `device_auth` 的签名工具、`client.py`
  的 `extract_reply_text` 与 `_is_terminal_agent_event` 两个静态方法，以及
  `chat.history` 回退文本抽取，不重复实现这些 heuristic。

## 使用 Demo

```bash
# 并发发给 3 个用户，持久化到 /tmp/pool.json
uv run python demo/session_pool_demo.py \
  --token $OPENCLAW_TOKEN \
  --store /tmp/pool.json \
  --users alice:thread-1 bob:thread-9 carol:thread-3 \
  --message "请用一句话自我介绍"

# 列出当前映射
uv run python demo/session_pool_demo.py --store /tmp/pool.json --list

# 切换 alice 的当前 session
uv run python demo/session_pool_demo.py --store /tmp/pool.json --switch alice:thread-42
```

并发度由 `--concurrency` 控制，默认 4（对齐 OpenClaw 主 agent 车道）。

## 测试

- 单元：`uv run pytest tests/unit/test_pool.py -q`
  - Store 持久化往返与切换
  - FrameRouter 按 id / sessionKey 分发
  - Scheduler 并发上限
  - `_send_via_router` 的事件 / 响应收集、连接断开抛错
  - `OpenClawSessionPool.bind` + `send`（注入假 router）
- 实机联调：上面的 demo 脚本，或直接在 Python 里 `async with OpenClawSessionPool(...)`。
