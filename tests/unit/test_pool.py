from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import pytest

from openclaw_client.pool import (
    FairScheduler,
    FrameRouter,
    OpenClawSessionPool,
    PoolError,
    SessionBinding,
    SessionStore,
    _send_via_router,
    derive_session_key,
)


# ---------------------------------------------------------------------------
# SessionStore
# ---------------------------------------------------------------------------


def test_session_store_round_trip(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "session-map.json")
    store.load()
    assert store.all() == {}

    store.set("alice", "thread-1", "chat:alice:thread-1")
    store.set("bob", "thread-9", "chat:bob:thread-9")

    reloaded = SessionStore(tmp_path / "session-map.json")
    reloaded.load()
    alice = reloaded.get("alice")
    assert alice is not None
    assert alice.chat_session_id == "thread-1"
    assert alice.openclaw_session_key == "chat:alice:thread-1"
    assert reloaded.get("bob").openclaw_session_key == "chat:bob:thread-9"


def test_session_store_switch_session(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "session-map.json")
    store.load()
    store.set("alice", "thread-1", "chat:alice:thread-1")
    time.sleep(0.01)
    store.set("alice", "thread-2", "chat:alice:thread-2")

    binding = store.get("alice")
    assert binding.chat_session_id == "thread-2"
    assert binding.openclaw_session_key == "chat:alice:thread-2"


def test_session_store_delete(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "session-map.json")
    store.load()
    store.set("alice", "t", "chat:alice:t")
    assert store.delete("alice") is True
    assert store.get("alice") is None
    assert store.delete("alice") is False


def test_session_store_rejects_unknown_version(tmp_path: Path) -> None:
    path = tmp_path / "session-map.json"
    path.write_text(json.dumps({"version": 999, "users": {}}))
    store = SessionStore(path)
    with pytest.raises(PoolError, match="unsupported"):
        store.load()


def test_derive_session_key() -> None:
    assert derive_session_key("alice", "thread-42") == "chat:alice:thread-42"


# ---------------------------------------------------------------------------
# FrameRouter
# ---------------------------------------------------------------------------


class FakeWebSocket:
    """把预置帧按 `async for` 吐出来，支持 send/close 记录。"""

    def __init__(self, frames: list[dict[str, Any]] | None = None) -> None:
        self._incoming: asyncio.Queue[str | None] = asyncio.Queue()
        for frame in frames or []:
            self._incoming.put_nowait(json.dumps(frame, ensure_ascii=False))
        self.sent: list[dict[str, Any]] = []
        self.closed = False

    def push(self, frame: dict[str, Any]) -> None:
        self._incoming.put_nowait(json.dumps(frame, ensure_ascii=False))

    def push_raw(self, value: str) -> None:
        self._incoming.put_nowait(value)

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


@pytest.mark.asyncio
async def test_frame_router_dispatches_res_by_id() -> None:
    ws = FakeWebSocket()
    router = FrameRouter(ws)
    router.start()
    sub_a = await router.subscribe("req-1", "chat:alice:s1")
    sub_b = await router.subscribe("req-2", "chat:bob:s1")

    ws.push({"type": "res", "id": "req-2", "ok": True, "payload": {"who": "bob"}})
    ws.push({"type": "res", "id": "req-1", "ok": True, "payload": {"who": "alice"}})

    frame_b = await asyncio.wait_for(sub_b.queue.get(), timeout=1)
    frame_a = await asyncio.wait_for(sub_a.queue.get(), timeout=1)

    assert frame_a["payload"]["who"] == "alice"
    assert frame_b["payload"]["who"] == "bob"

    await router.close()


@pytest.mark.asyncio
async def test_frame_router_dispatches_events_by_session_key() -> None:
    ws = FakeWebSocket()
    router = FrameRouter(ws)
    router.start()
    sub_alice = await router.subscribe("req-a", "chat:alice:s1")
    sub_bob = await router.subscribe("req-b", "chat:bob:s1")

    ws.push({"type": "event", "event": "agent.delta", "payload": {"sessionKey": "chat:alice:s1", "text": "hi"}})
    ws.push({"type": "event", "event": "agent.delta", "payload": {"sessionKey": "chat:bob:s1", "text": "hello"}})

    frame_a = await asyncio.wait_for(sub_alice.queue.get(), timeout=1)
    frame_b = await asyncio.wait_for(sub_bob.queue.get(), timeout=1)

    assert frame_a["payload"]["text"] == "hi"
    assert frame_b["payload"]["text"] == "hello"
    assert sub_alice.queue.empty()
    assert sub_bob.queue.empty()

    await router.close()


@pytest.mark.asyncio
async def test_frame_router_close_fails_waiters() -> None:
    ws = FakeWebSocket()
    router = FrameRouter(ws)
    router.start()
    sub = await router.subscribe("req-1", "chat:alice:s1")

    await router.close(exc=ConnectionError("boom"))
    frame = await asyncio.wait_for(sub.queue.get(), timeout=1)
    assert frame.get("__router_closed__") is True


@pytest.mark.asyncio
async def test_frame_router_unsubscribe_removes_indexes() -> None:
    ws = FakeWebSocket()
    router = FrameRouter(ws)
    router.start()
    sub = await router.subscribe("req-1", "chat:alice:s1")
    await router.unsubscribe(sub)
    assert router._subs_by_id == {}
    assert router._subs_by_session == {}
    await router.close()


# ---------------------------------------------------------------------------
# FairScheduler
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fair_scheduler_caps_concurrency() -> None:
    scheduler = FairScheduler(capacity=4)
    assert scheduler.capacity == 4

    in_flight = 0
    peak = 0
    order: list[int] = []

    async def worker(i: int) -> None:
        nonlocal in_flight, peak
        async with scheduler.slot(f"user-{i}"):
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.05)
            order.append(i)
            in_flight -= 1

    await asyncio.gather(*(worker(i) for i in range(10)))
    assert peak == 4
    assert sorted(order) == list(range(10))


@pytest.mark.asyncio
async def test_fair_scheduler_rejects_non_positive() -> None:
    with pytest.raises(ValueError):
        FairScheduler(capacity=0)


# ---------------------------------------------------------------------------
# Pool facade (no network) + _send_via_router behavior
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_via_router_collects_events_and_returns_response() -> None:
    ws = FakeWebSocket()
    router = FrameRouter(ws)
    router.start()

    async def producer() -> None:
        await asyncio.sleep(0.01)
        sent = ws.sent[0]
        req_id = sent["id"]
        ws.push({"type": "event", "event": "agent.delta", "payload": {"sessionKey": "chat:alice:s1", "text": "你好"}})
        ws.push({"type": "res", "id": req_id, "ok": True, "payload": {"accepted": True}})
        ws.push(
            {
                "type": "event",
                "event": "agent.completed",
                "payload": {"sessionKey": "chat:alice:s1", "text": "，世界", "done": True},
            }
        )

    producer_task = asyncio.create_task(producer())
    try:
        result = await _send_via_router(
            router=router,
            session_key="chat:alice:s1",
            message="你好",
            response_timeout=5.0,
            settle_timeout=0.5,
            fallback_history=False,
            subscribe_transcript=False,
        )
    finally:
        await producer_task
        await router.close()

    assert result.response["payload"]["accepted"] is True
    assert result.reply_text == "你好，世界"
    assert ws.sent[0]["method"] == "agent"
    assert ws.sent[0]["params"]["sessionKey"] == "chat:alice:s1"


@pytest.mark.asyncio
async def test_send_via_router_fails_on_connection_close() -> None:
    ws = FakeWebSocket()
    router = FrameRouter(ws)
    router.start()

    async def closer() -> None:
        await asyncio.sleep(0.01)
        await router.close(exc=ConnectionError("gone"))

    closer_task = asyncio.create_task(closer())
    try:
        with pytest.raises(PoolError, match="connection closed"):
            await _send_via_router(
                router=router,
                session_key="chat:alice:s1",
                message="hi",
                response_timeout=5.0,
                settle_timeout=0.5,
                fallback_history=False,
                subscribe_transcript=False,
            )
    finally:
        await closer_task


def test_pool_bind_and_switch(tmp_path: Path) -> None:
    pool = OpenClawSessionPool(store_path=tmp_path / "session-map.json")
    pool.bind("alice", "thread-1")
    pool.bind("bob", "thread-9", session_key="chat:bob:custom")

    assert pool.binding("alice").openclaw_session_key == "chat:alice:thread-1"
    assert pool.binding("bob").openclaw_session_key == "chat:bob:custom"

    pool.bind("alice", "thread-2")
    assert pool.binding("alice").chat_session_id == "thread-2"
    assert pool.binding("alice").openclaw_session_key == "chat:alice:thread-2"

    assert set(pool.bindings().keys()) == {"alice", "bob"}


def test_pool_send_without_binding_raises(tmp_path: Path) -> None:
    pool = OpenClawSessionPool(store_path=tmp_path / "session-map.json")
    with pytest.raises(PoolError, match="no bound session"):
        asyncio.run(pool.send("stranger", "hi"))


@pytest.mark.asyncio
async def test_pool_send_routes_via_injected_router(tmp_path: Path, monkeypatch) -> None:
    ws = FakeWebSocket()
    router = FrameRouter(ws)
    router.start()

    pool = OpenClawSessionPool(store_path=tmp_path / "session-map.json")
    pool.bind("alice", "thread-1")

    async def fake_get_router() -> FrameRouter:
        return router

    async def fake_start() -> None:
        return None

    async def fake_stop() -> None:
        return None

    monkeypatch.setattr(pool._supervisor, "get_router", fake_get_router)
    monkeypatch.setattr(pool._supervisor, "start", fake_start)
    monkeypatch.setattr(pool._supervisor, "stop", fake_stop)

    async def producer() -> None:
        await asyncio.sleep(0.01)
        req_id = ws.sent[0]["id"]
        ws.push({"type": "res", "id": req_id, "ok": True, "payload": {"accepted": True}})
        ws.push(
            {
                "type": "event",
                "event": "agent.completed",
                "payload": {"sessionKey": "chat:alice:thread-1", "text": "ok", "done": True},
            }
        )

    producer_task = asyncio.create_task(producer())
    try:
        async with pool:
            result = await pool.send(
                "alice",
                "hi",
                response_timeout=5.0,
                fallback_history=False,
                subscribe_transcript=False,
            )
    finally:
        await producer_task
        await router.close()

    assert result.reply_text == "ok"


@pytest.mark.asyncio
async def test_pool_history_returns_messages(tmp_path: Path, monkeypatch) -> None:
    ws = FakeWebSocket()
    router = FrameRouter(ws)
    router.start()

    pool = OpenClawSessionPool(store_path=tmp_path / "session-map.json")
    pool.bind("alice", "thread-1")

    async def fake_get_router() -> FrameRouter:
        return router

    monkeypatch.setattr(pool._supervisor, "get_router", fake_get_router)

    async def producer() -> None:
        await asyncio.sleep(0.01)
        req_id = ws.sent[0]["id"]
        ws.push(
            {
                "type": "res",
                "id": req_id,
                "ok": True,
                "payload": {
                    "messages": [
                        {"role": "user", "content": "hi"},
                        {"role": "assistant", "content": "hello"},
                    ]
                },
            }
        )

    producer_task = asyncio.create_task(producer())
    try:
        messages = await pool.history("alice")
    finally:
        await producer_task
        await router.close()

    assert messages[0]["role"] == "user"
    assert messages[1]["content"] == "hello"
    assert ws.sent[0]["method"] == "chat.history"
