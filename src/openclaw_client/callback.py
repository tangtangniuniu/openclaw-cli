"""第三方集成用的回调库：在 `OpenClawSessionPool` 之上封装事件级反向回调。

设计目标
========

- 输入：`(user, session)` 元组。库内部维护与 gateway `sessionKey` 的双向映射。
- 输出：gateway 推送的每个事件都会反向解析为 `(user, session)` 并调用用户注入的
  `callback_handler(user, session, event)`。
- 按 handler 返回值做 4 种短路处理（另加一种默认继续）：

    * `None` 或 `("pass", _)` ：默认流程，事件累积用于最终 reply 抽取。
    * `("used", None)`         ：事件已被消费，不累积，继续等待后续事件。
    * `("modify", data)`       ：立即结束本次 `send`，把 `data` 作为结果返回。
    * `("async", jobid)`       ：立即结束本次 `send`，返回 `{"jobid": jobid}`。
    * `("fail", errmsg)`       ：立即结束本次 `send`，返回错误信息。

用法
====

    async def handler(user, session, event):
        if event.get("event") == "agent.delta":
            return ("used", None)  # 流式增量：消费掉，不进入最终结果
        return None                 # 其余事件走默认流程

    async with OpenClawCallbackPool(password="zxt2000") as pool:
        pool.bind("alice", "chat-1")
        outcome = await pool.send(
            "alice", "chat-1", "你好", callback_handler=handler
        )
        print(outcome.to_dict())
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal

from openclaw_client.client import OpenClawGatewayClient, RequestResult
from openclaw_client.pool import (
    DEFAULT_CONCURRENCY,
    DEFAULT_PASSWORD,
    DEFAULT_RESPONSE_TIMEOUT,
    DEFAULT_SETTLE_TIMEOUT,
    DEFAULT_URL,
    FrameRouter,
    OpenClawSessionPool,
    PoolError,
    _coerce_int,
    _poll_history,
    derive_session_key,
)

_logger = logging.getLogger("openclaw_client.callback")


CallbackStatus = Literal["pass", "used", "modify", "async", "fail"]
# 回调返回：`(status, payload)` 或 `None`。payload 的语义按 status 区分：
# - used:   忽略
# - modify: 任意对象，原样返给 send() 的调用者
# - async:  jobid（字符串或可 str() 的对象）
# - fail:   errmsg 字符串
CallbackReturn = tuple[CallbackStatus, Any] | None
CallbackHandler = Callable[
    [str, str, dict[str, Any]],
    Awaitable[CallbackReturn],
]


@dataclass(slots=True)
class CallbackOutcome:
    """`send` 的语义化返回值，可通过 `to_dict()` 转成对外接口常用的 dict 形态。"""

    kind: Literal["result", "modify", "async", "fail"]
    result: RequestResult | None = None
    data: Any = None
    jobid: str | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.kind != "fail"

    def to_dict(self) -> dict[str, Any]:
        """转成适合 HTTP/RPC 回包的 dict。

        - `result`: `{"ok": True, "reply": ..., "events": [...]}`
        - `modify`: 若 data 本身是 dict 则原样返回；否则包一层 `{"data": data}`
        - `async`:  `{"jobid": id}`
        - `fail`:   `{"ok": False, "error": msg}`
        """
        if self.kind == "async":
            return {"jobid": self.jobid}
        if self.kind == "fail":
            return {"ok": False, "error": self.error}
        if self.kind == "modify":
            if isinstance(self.data, dict):
                return self.data
            return {"data": self.data}
        assert self.result is not None
        return {
            "ok": True,
            "reply": self.result.reply_text,
            "events": self.result.events,
        }


class OpenClawCallbackPool:
    """把 `OpenClawSessionPool` 暴露成带反向事件回调的第三方调用接口。

    - `bind(user, session, session_key=None)` 建立 `(user, session) ↔ sessionKey` 双向映射。
    - `send(user, session, msg, callback_handler=...)` 发消息，每个 gateway 事件都会
      反向解析出 `(user, session)` 并回调 handler。
    """

    def __init__(
        self,
        *,
        url: str = DEFAULT_URL,
        password: str | None = DEFAULT_PASSWORD,
        token: str | None = None,
        store_path: Path | str | None = None,
        concurrency: int = DEFAULT_CONCURRENCY,
        pool: OpenClawSessionPool | None = None,
    ) -> None:
        """构造。可传已有 `OpenClawSessionPool` 实例复用其连接；否则内部新建一个。"""
        if pool is not None:
            self._pool = pool
            self._owns_pool = False
        else:
            self._pool = OpenClawSessionPool(
                url=url,
                password=password,
                token=token,
                store_path=store_path,
                concurrency=concurrency,
            )
            self._owns_pool = True
        # (user, session) -> sessionKey
        self._forward: dict[tuple[str, str], str] = {}
        # sessionKey -> (user, session)
        self._reverse: dict[str, tuple[str, str]] = {}

    # -- 生命周期 --

    async def __aenter__(self) -> OpenClawCallbackPool:
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.stop()

    async def start(self) -> None:
        if self._owns_pool:
            await self._pool.start()

    async def stop(self) -> None:
        if self._owns_pool:
            await self._pool.stop()

    # -- 映射 --

    def bind(
        self,
        user: str,
        session: str,
        *,
        session_key: str | None = None,
    ) -> str:
        """建立 `(user, session) ↔ sessionKey` 的双向映射。返回最终使用的 sessionKey。

        不指定 `session_key` 时用 `derive_session_key(user, session)` 生成确定性的
        `chat:<user>:<session>` 形态。
        """
        if not user or not session:
            raise ValueError("user and session are required")
        key = session_key or derive_session_key(user, session)
        old = self._forward.get((user, session))
        if old is not None and old != key:
            self._reverse.pop(old, None)
        self._forward[(user, session)] = key
        self._reverse[key] = (user, session)
        return key

    def unbind(self, user: str, session: str) -> bool:
        key = self._forward.pop((user, session), None)
        if key is None:
            return False
        self._reverse.pop(key, None)
        return True

    def session_key_of(self, user: str, session: str) -> str | None:
        return self._forward.get((user, session))

    def user_session_of(self, session_key: str) -> tuple[str, str] | None:
        return self._reverse.get(session_key)

    def bindings(self) -> dict[tuple[str, str], str]:
        return dict(self._forward)

    # -- 发送 --

    async def send(
        self,
        user: str,
        session: str,
        message: str,
        *,
        callback_handler: CallbackHandler | None = None,
        response_timeout: float = DEFAULT_RESPONSE_TIMEOUT,
        settle_timeout: float = DEFAULT_SETTLE_TIMEOUT,
        fallback_history: bool = True,
    ) -> CallbackOutcome:
        """按 `(user, session)` 查 sessionKey 后发消息，每个事件调用 `callback_handler`。

        `callback_handler` 为空时等价于 `pool.send_with_key`，返回 kind=`"result"` 的 outcome。
        异常与超时统一包装成 `CallbackOutcome(kind="fail", error=...)`。
        """
        if (user, session) not in self._forward:
            # 自动绑定一个默认 sessionKey，便于临时使用
            self.bind(user, session)
        session_key = self._forward[(user, session)]

        async with self._pool._scheduler.slot(f"{user}:{session}"):
            router = await self._pool._supervisor.get_router()
            return await self._run_send(
                router=router,
                user=user,
                session=session,
                session_key=session_key,
                message=message,
                callback_handler=callback_handler,
                response_timeout=response_timeout,
                settle_timeout=settle_timeout,
                fallback_history=fallback_history,
            )

    async def _run_send(
        self,
        *,
        router: FrameRouter,
        user: str,
        session: str,
        session_key: str,
        message: str,
        callback_handler: CallbackHandler | None,
        response_timeout: float,
        settle_timeout: float,
        fallback_history: bool,
    ) -> CallbackOutcome:
        req_id = f"req-{uuid.uuid4().hex}"
        idem_key = f"req-{uuid.uuid4().hex}"
        sub = await router.subscribe(req_id, session_key)
        try:
            params = {
                "sessionKey": session_key,
                "message": message,
                "deliver": True,
                "bestEffortDeliver": True,
                "idempotencyKey": idem_key,
            }
            await router.send_request(req_id, "agent", params)

            events: list[dict[str, Any]] = []
            response: dict[str, Any] | None = None
            loop = asyncio.get_running_loop()
            deadline = loop.time() + response_timeout

            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    if response is not None:
                        return await self._finalize(
                            router=router,
                            response=response,
                            events=events,
                            session_key=session_key,
                            message=message,
                            fallback_history=fallback_history,
                            deadline=deadline,
                        )
                    return CallbackOutcome(
                        kind="fail", error="timed out waiting for agent response"
                    )

                recv_timeout = remaining if response is None else min(remaining, settle_timeout)
                try:
                    frame = await asyncio.wait_for(sub.queue.get(), timeout=recv_timeout)
                except asyncio.TimeoutError:
                    if response is not None:
                        return await self._finalize(
                            router=router,
                            response=response,
                            events=events,
                            session_key=session_key,
                            message=message,
                            fallback_history=fallback_history,
                            deadline=deadline,
                        )
                    return CallbackOutcome(
                        kind="fail", error="timed out waiting for agent response"
                    )

                if frame.get("__router_closed__"):
                    return CallbackOutcome(
                        kind="fail",
                        error=f"connection closed: {frame.get('error')}",
                    )

                frame_type = frame.get("type")
                if frame_type == "event":
                    # 事件优先走回调；handler 返回 (modify/async/fail) 直接终止。
                    short_circuit, was_used = await self._dispatch_callback(
                        callback_handler, user, session, frame
                    )
                    if short_circuit is not None:
                        return short_circuit
                    if not was_used:
                        events.append(frame)
                    if response is not None and OpenClawGatewayClient._is_terminal_agent_event(
                        frame
                    ):
                        return await self._finalize(
                            router=router,
                            response=response,
                            events=events,
                            session_key=session_key,
                            message=message,
                            fallback_history=fallback_history,
                            deadline=deadline,
                        )
                    continue
                if frame_type == "res":
                    if not frame.get("ok"):
                        return CallbackOutcome(
                            kind="fail", error=f"agent request failed: {frame}"
                        )
                    response = frame
                    reply = OpenClawGatewayClient.extract_reply_text([frame])
                    if reply:
                        return CallbackOutcome(
                            kind="result",
                            result=RequestResult(
                                response=frame, events=events, reply_text=reply
                            ),
                        )
        finally:
            await router.unsubscribe(sub)

    async def _dispatch_callback(
        self,
        callback_handler: CallbackHandler | None,
        user: str,
        session: str,
        event: dict[str, Any],
    ) -> tuple[CallbackOutcome | None, bool]:
        """调用 handler，返回 `(终止 outcome 或 None, 事件是否被消费)`。"""
        if callback_handler is None:
            return None, False
        try:
            result = await callback_handler(user, session, event)
        except Exception as exc:
            _logger.exception("callback_handler raised: %r", exc)
            return (
                CallbackOutcome(kind="fail", error=f"callback raised: {exc!r}"),
                False,
            )
        if result is None:
            return None, False
        if not isinstance(result, tuple) or len(result) != 2:
            _logger.warning("callback_handler returned unexpected value: %r", result)
            return None, False
        status, payload = result
        if status == "pass":
            return None, False
        if status == "used":
            return None, True
        if status == "modify":
            return CallbackOutcome(kind="modify", data=payload), True
        if status == "async":
            return CallbackOutcome(kind="async", jobid=str(payload)), True
        if status == "fail":
            return CallbackOutcome(kind="fail", error=str(payload)), True
        _logger.warning("callback_handler unknown status: %r", status)
        return None, False

    async def _finalize(
        self,
        *,
        router: FrameRouter,
        response: dict[str, Any],
        events: list[dict[str, Any]],
        session_key: str,
        message: str,
        fallback_history: bool,
        deadline: float,
    ) -> CallbackOutcome:
        reply = OpenClawGatewayClient.extract_reply_text(events)
        if not reply and fallback_history:
            accepted_at = _coerce_int((response.get("payload") or {}).get("acceptedAt"))
            try:
                reply = await _poll_history(
                    router=router,
                    session_key=session_key,
                    message=message,
                    accepted_at=accepted_at,
                    deadline=deadline,
                )
            except PoolError as exc:
                _logger.info("history fallback failed: %r", exc)
                reply = ""
        return CallbackOutcome(
            kind="result",
            result=RequestResult(response=response, events=events, reply_text=reply),
        )


__all__ = [
    "CallbackHandler",
    "CallbackOutcome",
    "CallbackReturn",
    "CallbackStatus",
    "OpenClawCallbackPool",
]
