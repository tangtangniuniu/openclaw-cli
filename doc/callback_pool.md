# OpenClaw 回调库 (`callback_pool`)

在 `OpenClawSessionPool` 之上的一层薄封装，专为第三方程序集成设计：

- 输入 `(user, session)`；库内部维护与 gateway `sessionKey` 的**双向映射**。
- Gateway 推来的每一帧事件都会反向解析为 `(user, session)` 并回调用户注入的
  `callback_handler`。
- 按 handler 返回值做 4 种短路处理（另加一种默认继续），对齐需求文档的语义。

代码位置：[`src/openclaw_client/callback.py`](../src/openclaw_client/callback.py)。
示例脚本：[`demo/callback_pool_demo.py`](../demo/callback_pool_demo.py)。
单元测试：[`tests/unit/test_callback.py`](../tests/unit/test_callback.py)。

## 1. handler 返回值约定

```python
from openclaw_client import OpenClawCallbackPool

async def handler(user: str, session: str, event: dict):
    ...
    return <callback_return>
```

| 返回值                | 行为                                                                 |
| --------------------- | -------------------------------------------------------------------- |
| `None` / `("pass",_)` | 默认：事件累积，参与后续 reply 抽取                                  |
| `("used", None)`      | 事件已被消费：不累积，不参与 reply，`send` 继续等后续事件            |
| `("modify", data)`    | `send` 立即返回，结果为 `data`（`to_dict()` 时若是 dict 则原样返回） |
| `("async", id)`       | `send` 立即返回 `{"jobid": str(id)}`                                 |
| `("fail", errmsg)`    | `send` 立即返回 `{"ok": False, "error": str(errmsg)}`                |

handler 自身抛出的异常会被**统一包成** `("fail", ...)`，不会冒泡到调用者。

## 2. API

```python
from openclaw_client import OpenClawCallbackPool, CallbackOutcome

async with OpenClawCallbackPool(
    url="ws://127.0.0.1:18789",
    password="zxt2000",
    # token=...,                    # 如 Gateway 配置为 token 模式
    # concurrency=4,                # 与 pool 对齐
    # store_path="...",             # 底层 pool 的映射持久化
) as pool:
    pool.bind("alice", "thread-1")  # 可选：显式建立映射；不传时 send 会自动 bind
    outcome: CallbackOutcome = await pool.send(
        "alice", "thread-1", "你好",
        callback_handler=handler,   # 省略即等价于 pool.send_with_key
    )
    print(outcome.to_dict())
```

### 双向映射

```python
pool.bind("alice", "thread-1")                      # 默认 sessionKey = chat:alice:thread-1
pool.bind("bob",   "s1", session_key="custom-key")  # 也支持自定义 sessionKey
pool.session_key_of("alice", "thread-1")            # "chat:alice:thread-1"
pool.user_session_of("chat:alice:thread-1")         # ("alice", "thread-1")
pool.unbind("alice", "thread-1")
pool.bindings()                                     # 全量快照
```

反向映射由库自己维护（内存字典），并不写入 `session-map.json`。底层的
`OpenClawSessionPool` 仍照常读写其自己的持久化。

### `CallbackOutcome`

```python
@dataclass
class CallbackOutcome:
    kind: Literal["result", "modify", "async", "fail"]
    result: RequestResult | None   # kind == "result"
    data: Any                      # kind == "modify"
    jobid: str | None              # kind == "async"
    error: str | None              # kind == "fail"
```

- `outcome.ok` 在 `kind != "fail"` 时为 True。
- `outcome.to_dict()` 按 kind 转成 HTTP/RPC 友好的 dict，参考上表的形态。

## 3. 和 `OpenClawSessionPool` 的关系

`OpenClawCallbackPool` 默认**自己拥有一个** `OpenClawSessionPool` 实例并管理其
启停；也可以传入 `pool=<existing>` 复用外部已有的 pool（此时 `start/stop` 不会
再触发底层 supervisor 的启停）。

```python
outer = OpenClawSessionPool(password="zxt2000")
cb = OpenClawCallbackPool(pool=outer)
await outer.start()
# …
await outer.stop()
```

实现上 `send` 会：

1. 通过底层 pool 的 `FairScheduler` 拿一个 slot（保持全局并发约束 4 不变）。
2. 通过底层 supervisor 拿共享 `FrameRouter`。
3. 发出一条 `agent` 请求，然后在事件循环里按 handler 返回值处理每一帧。

## 4. 典型集成形态

一个对外 HTTP 服务可以把回包交给本库：

```python
async def chat_endpoint(req):
    async def handler(user, session, event):
        if is_internal_system_event(event):
            return ("used", None)
        if should_redirect_to_queue(event):
            jobid = enqueue(event)
            return ("async", jobid)
        return None

    outcome = await pool.send(
        req.user, req.session, req.message, callback_handler=handler
    )
    return json_response(outcome.to_dict())
```

- 普通对话：返回 `{"ok": true, "reply": "...", "events": [...]}`。
- 异步作业：返回 `{"jobid": "..."}`，客户端再去 `/jobs/<id>` 轮询。
- handler 决定的定制回包：返回 `data`。
- handler 自身报错 / pool 错误：返回 `{"ok": false, "error": "..."}`。
