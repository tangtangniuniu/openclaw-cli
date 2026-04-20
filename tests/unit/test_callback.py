"""针对 `openclaw_client.callback.OpenClawCallbackPool` 的单元测试。

与 test_pool.py 一样用 FakeWebSocket + FrameRouter 的组合，把 pool 的 supervisor
替换成直接返回 router 的 mock，只测回调分支逻辑，不走真实 gateway。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from openclaw_client.callback import (
    CallbackOutcome,
    OpenClawCallbackPool,
)
from openclaw_client.pool import FrameRouter


# ---------------------------------------------------------------------------
# 公用 fake WebSocket（与 test_pool.py 同构，单独复制避免跨文件耦合）
# ---------------------------------------------------------------------------


class FakeWebSocket:
    def __init__(self) -> None:
        self._incoming: asyncio.Queue[str | None] = asyncio.Queue()
        self.sent: list[dict[str, Any]] = []
        self.closed = False

    def push(self, frame: dict[str, Any]) -> None:
        self._incoming.put_nowait(json.dumps(frame, ensure_ascii=False))

    def end(self) -> None:
        self._incoming.put_nowait(None)

    async def send(self, message: str) -> None:
        self.sent.append(json.loads(message))

    async def close(self) -> None:
        self.closed = True
        self._incoming.put_nowait(None)

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        value = await self._incoming.get()
        if value is None:
            raise StopAsyncIteration
        return value


def _make_pool(tmp_path: Path, router: FrameRouter) -> OpenClawCallbackPool:
    pool = OpenClawCallbackPool(store_path=tmp_path / "session-map.json")

    async def fake_get_router() -> FrameRouter:
        return router

    async def fake_start() -> None:
        return None

    async def fake_stop() -> None:
        return None

    # 直接把 supervisor 替成返回我们的 router，避免真连 gateway
    pool._pool._supervisor.get_router = fake_get_router  # type: ignore[attr-defined]
    pool._pool._supervisor.start = fake_start  # type: ignore[attr-defined]
    pool._pool._supervisor.stop = fake_stop  # type: ignore[attr-defined]
    return pool


async def _drive_frames(ws: FakeWebSocket, events: list[dict[str, Any]], *, send_res: bool = True) -> None:
    """等第一帧发出来，拿到 req_id 后把一批事件/res 推回去。"""
    while not ws.sent:
        await asyncio.sleep(0.005)
    req_id = ws.sent[0]["id"]
    for ev in events:
        ws.push(ev)
    if send_res:
        ws.push({"type": "res", "id": req_id, "ok": True, "payload": {"accepted": True}})


# ---------------------------------------------------------------------------
# 映射
# ---------------------------------------------------------------------------


def test_bind_and_reverse_mapping(tmp_path: Path) -> None:
    pool = OpenClawCallbackPool(store_path=tmp_path / "session-map.json")
    key = pool.bind("alice", "thread-1")
    assert key == "chat:alice:thread-1"
    assert pool.session_key_of("alice", "thread-1") == "chat:alice:thread-1"
    assert pool.user_session_of("chat:alice:thread-1") == ("alice", "thread-1")


def test_bind_custom_session_key_and_unbind(tmp_path: Path) -> None:
    pool = OpenClawCallbackPool(store_path=tmp_path / "session-map.json")
    pool.bind("bob", "s1", session_key="custom-key")
    assert pool.session_key_of("bob", "s1") == "custom-key"
    assert pool.user_session_of("custom-key") == ("bob", "s1")

    # 重绑会清掉旧 reverse
    pool.bind("bob", "s1", session_key="other-key")
    assert pool.user_session_of("custom-key") is None
    assert pool.user_session_of("other-key") == ("bob", "s1")

    assert pool.unbind("bob", "s1") is True
    assert pool.session_key_of("bob", "s1") is None
    assert pool.unbind("bob", "s1") is False


def test_bind_rejects_empty(tmp_path: Path) -> None:
    pool = OpenClawCallbackPool(store_path=tmp_path / "session-map.json")
    with pytest.raises(ValueError):
        pool.bind("", "s")
    with pytest.raises(ValueError):
        pool.bind("u", "")


# ---------------------------------------------------------------------------
# CallbackOutcome.to_dict()
# ---------------------------------------------------------------------------


def test_outcome_to_dict_async() -> None:
    out = CallbackOutcome(kind="async", jobid="job-123")
    assert out.to_dict() == {"jobid": "job-123"}
    assert out.ok is True


def test_outcome_to_dict_fail() -> None:
    out = CallbackOutcome(kind="fail", error="boom")
    assert out.to_dict() == {"ok": False, "error": "boom"}
    assert out.ok is False


def test_outcome_to_dict_modify_dict() -> None:
    out = CallbackOutcome(kind="modify", data={"foo": 1})
    assert out.to_dict() == {"foo": 1}


def test_outcome_to_dict_modify_scalar() -> None:
    out = CallbackOutcome(kind="modify", data="plain")
    assert out.to_dict() == {"data": "plain"}


# ---------------------------------------------------------------------------
# send() 的 4 种回调分支
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pass_through_normal_flow(tmp_path: Path) -> None:
    """handler 返回 None ⇒ 走默认流程，返回 result outcome，事件全量累积。"""
    ws = FakeWebSocket()
    router = FrameRouter(ws)
    router.start()
    pool = _make_pool(tmp_path, router)
    pool.bind("alice", "thread-1")

    seen: list[tuple[str, str, str]] = []

    async def handler(user: str, session: str, event: dict[str, Any]):
        seen.append((user, session, event.get("event", "")))
        return None

    async def producer() -> None:
        await _drive_frames(
            ws,
            [
                {
                    "type": "event",
                    "event": "agent.delta",
                    "payload": {"sessionKey": "chat:alice:thread-1", "text": "你好"},
                },
                {
                    "type": "event",
                    "event": "agent.completed",
                    "payload": {
                        "sessionKey": "chat:alice:thread-1",
                        "text": "，世界",
                        "done": True,
                    },
                },
            ],
        )

    producer_task = asyncio.create_task(producer())
    try:
        outcome = await pool.send(
            "alice",
            "thread-1",
            "你好",
            callback_handler=handler,
            response_timeout=5.0,
            settle_timeout=0.3,
            fallback_history=False,
        )
    finally:
        await producer_task
        await router.close()

    assert outcome.kind == "result"
    assert outcome.result is not None
    assert outcome.result.reply_text == "你好，世界"
    # 所有事件都被反向映射到 (alice, thread-1)
    assert seen == [
        ("alice", "thread-1", "agent.delta"),
        ("alice", "thread-1", "agent.completed"),
    ]
    # 事件也全量进了 result.events
    assert len(outcome.result.events) == 2


@pytest.mark.asyncio
async def test_used_drops_event_from_accumulation(tmp_path: Path) -> None:
    """handler 返回 ("used", None) ⇒ 事件不累积到 result.events，流程继续。"""
    ws = FakeWebSocket()
    router = FrameRouter(ws)
    router.start()
    pool = _make_pool(tmp_path, router)
    pool.bind("alice", "thread-1")

    async def handler(user: str, session: str, event: dict[str, Any]):
        if event.get("event") == "agent.delta":
            return ("used", None)
        return None

    async def producer() -> None:
        await _drive_frames(
            ws,
            [
                {
                    "type": "event",
                    "event": "agent.delta",
                    "payload": {"sessionKey": "chat:alice:thread-1", "text": "drop me"},
                },
                {
                    "type": "event",
                    "event": "agent.completed",
                    "payload": {
                        "sessionKey": "chat:alice:thread-1",
                        "text": "final",
                        "done": True,
                    },
                },
            ],
        )

    producer_task = asyncio.create_task(producer())
    try:
        outcome = await pool.send(
            "alice",
            "thread-1",
            "hi",
            callback_handler=handler,
            response_timeout=5.0,
            settle_timeout=0.3,
            fallback_history=False,
        )
    finally:
        await producer_task
        await router.close()

    assert outcome.kind == "result"
    assert outcome.result is not None
    # agent.delta 被 used 消费，只剩 agent.completed 进入 events
    assert len(outcome.result.events) == 1
    assert outcome.result.events[0]["event"] == "agent.completed"
    # reply 只从未被消费的事件里抽取
    assert outcome.result.reply_text == "final"


@pytest.mark.asyncio
async def test_modify_returns_data_immediately(tmp_path: Path) -> None:
    """handler 返回 ("modify", data) ⇒ 立即结束 send，返回 data。"""
    ws = FakeWebSocket()
    router = FrameRouter(ws)
    router.start()
    pool = _make_pool(tmp_path, router)
    pool.bind("alice", "thread-1")

    async def handler(user: str, session: str, event: dict[str, Any]):
        if event.get("event") == "agent.delta":
            return ("modify", {"custom": "payload", "from": user})
        return None

    async def producer() -> None:
        await _drive_frames(
            ws,
            [
                {
                    "type": "event",
                    "event": "agent.delta",
                    "payload": {"sessionKey": "chat:alice:thread-1", "text": "x"},
                },
            ],
            send_res=False,
        )

    producer_task = asyncio.create_task(producer())
    try:
        outcome = await pool.send(
            "alice",
            "thread-1",
            "hi",
            callback_handler=handler,
            response_timeout=5.0,
            settle_timeout=0.3,
            fallback_history=False,
        )
    finally:
        await producer_task
        await router.close()

    assert outcome.kind == "modify"
    assert outcome.data == {"custom": "payload", "from": "alice"}
    assert outcome.to_dict() == {"custom": "payload", "from": "alice"}


@pytest.mark.asyncio
async def test_async_returns_jobid(tmp_path: Path) -> None:
    """handler 返回 ("async", id) ⇒ 立即返回 {jobid: id}。"""
    ws = FakeWebSocket()
    router = FrameRouter(ws)
    router.start()
    pool = _make_pool(tmp_path, router)
    pool.bind("alice", "thread-1")

    async def handler(user: str, session: str, event: dict[str, Any]):
        return ("async", "job-42")

    async def producer() -> None:
        await _drive_frames(
            ws,
            [
                {
                    "type": "event",
                    "event": "agent.delta",
                    "payload": {"sessionKey": "chat:alice:thread-1", "text": "x"},
                },
            ],
            send_res=False,
        )

    producer_task = asyncio.create_task(producer())
    try:
        outcome = await pool.send(
            "alice",
            "thread-1",
            "hi",
            callback_handler=handler,
            response_timeout=5.0,
            settle_timeout=0.3,
            fallback_history=False,
        )
    finally:
        await producer_task
        await router.close()

    assert outcome.kind == "async"
    assert outcome.jobid == "job-42"
    assert outcome.to_dict() == {"jobid": "job-42"}


@pytest.mark.asyncio
async def test_fail_returns_error(tmp_path: Path) -> None:
    """handler 返回 ("fail", errmsg) ⇒ 立即返回 fail outcome。"""
    ws = FakeWebSocket()
    router = FrameRouter(ws)
    router.start()
    pool = _make_pool(tmp_path, router)
    pool.bind("alice", "thread-1")

    async def handler(user: str, session: str, event: dict[str, Any]):
        return ("fail", "validation error")

    async def producer() -> None:
        await _drive_frames(
            ws,
            [
                {
                    "type": "event",
                    "event": "agent.delta",
                    "payload": {"sessionKey": "chat:alice:thread-1", "text": "x"},
                },
            ],
            send_res=False,
        )

    producer_task = asyncio.create_task(producer())
    try:
        outcome = await pool.send(
            "alice",
            "thread-1",
            "hi",
            callback_handler=handler,
            response_timeout=5.0,
            settle_timeout=0.3,
            fallback_history=False,
        )
    finally:
        await producer_task
        await router.close()

    assert outcome.kind == "fail"
    assert outcome.error == "validation error"
    assert outcome.ok is False
    assert outcome.to_dict() == {"ok": False, "error": "validation error"}


@pytest.mark.asyncio
async def test_handler_exception_wrapped_as_fail(tmp_path: Path) -> None:
    """handler 里抛出的异常不应让 send 冒泡，而是转成 fail outcome。"""
    ws = FakeWebSocket()
    router = FrameRouter(ws)
    router.start()
    pool = _make_pool(tmp_path, router)
    pool.bind("alice", "thread-1")

    async def handler(user: str, session: str, event: dict[str, Any]):
        raise RuntimeError("handler blew up")

    async def producer() -> None:
        await _drive_frames(
            ws,
            [
                {
                    "type": "event",
                    "event": "agent.delta",
                    "payload": {"sessionKey": "chat:alice:thread-1", "text": "x"},
                },
            ],
            send_res=False,
        )

    producer_task = asyncio.create_task(producer())
    try:
        outcome = await pool.send(
            "alice",
            "thread-1",
            "hi",
            callback_handler=handler,
            response_timeout=5.0,
            settle_timeout=0.3,
            fallback_history=False,
        )
    finally:
        await producer_task
        await router.close()

    assert outcome.kind == "fail"
    assert outcome.error is not None
    assert "handler blew up" in outcome.error


@pytest.mark.asyncio
async def test_no_handler_behaves_like_plain_send(tmp_path: Path) -> None:
    """不传 callback_handler ⇒ 等价于默认 send，result 中包含全部事件。"""
    ws = FakeWebSocket()
    router = FrameRouter(ws)
    router.start()
    pool = _make_pool(tmp_path, router)
    pool.bind("alice", "thread-1")

    async def producer() -> None:
        await _drive_frames(
            ws,
            [
                {
                    "type": "event",
                    "event": "agent.completed",
                    "payload": {"sessionKey": "chat:alice:thread-1", "text": "done", "done": True},
                },
            ],
        )

    producer_task = asyncio.create_task(producer())
    try:
        outcome = await pool.send(
            "alice",
            "thread-1",
            "hi",
            response_timeout=5.0,
            settle_timeout=0.3,
            fallback_history=False,
        )
    finally:
        await producer_task
        await router.close()

    assert outcome.kind == "result"
    assert outcome.result is not None
    assert outcome.result.reply_text == "done"


@pytest.mark.asyncio
async def test_auto_bind_on_first_send(tmp_path: Path) -> None:
    """未显式 bind 过的 (user, session) 在 send 时会被自动绑定到默认 sessionKey。"""
    ws = FakeWebSocket()
    router = FrameRouter(ws)
    router.start()
    pool = _make_pool(tmp_path, router)

    async def producer() -> None:
        await _drive_frames(
            ws,
            [
                {
                    "type": "event",
                    "event": "agent.completed",
                    "payload": {
                        "sessionKey": "chat:carol:t",
                        "text": "hi",
                        "done": True,
                    },
                }
            ],
        )

    producer_task = asyncio.create_task(producer())
    try:
        outcome = await pool.send(
            "carol",
            "t",
            "hi",
            response_timeout=5.0,
            settle_timeout=0.3,
            fallback_history=False,
        )
    finally:
        await producer_task
        await router.close()

    assert outcome.kind == "result"
    assert pool.session_key_of("carol", "t") == "chat:carol:t"
