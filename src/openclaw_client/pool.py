"""OpenClaw 会话池：把 chatbot 的 (user, sessionId) 映射到 gateway 的 sessionKey。

设计要点：
- 单条共享 WebSocket 连接（自动重连、指数退避）
- 帧路由器：后台 reader task 按 req_id 分发 res，按 sessionKey 分发 event
- 公平调度：asyncio.Semaphore(capacity) + FIFO 等待队列
- 映射持久化：~/.openclaw-cli/session-map.json，原子写
- 用户切换 session：调用 bind() 覆盖当前绑定
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

import websockets
from websockets.asyncio.client import ClientConnection

from openclaw_client.client import (
    DEFAULT_OPERATOR_SCOPES,
    OpenClawGatewayClient,
    RequestResult,
)
from openclaw_client.device_auth import (
    DeviceIdentity,
    build_signed_device,
    load_operator_token,
)


DEFAULT_STORE_PATH = Path.home() / ".openclaw-cli" / "session-map.json"
DEFAULT_URL = "ws://127.0.0.1:18789"
DEFAULT_PASSWORD = "zxt2000"
DEFAULT_CONCURRENCY = 4
DEFAULT_RESPONSE_TIMEOUT = 120.0
DEFAULT_SETTLE_TIMEOUT = 30.0
RECONNECT_BACKOFF_MIN = 1.0
RECONNECT_BACKOFF_MAX = 30.0
STORE_VERSION = 1

_logger = logging.getLogger("openclaw_client.pool")


class PoolError(RuntimeError):
    """会话池层错误。"""


@dataclass(slots=True)
class SessionBinding:
    user: str
    chat_session_id: str
    openclaw_session_key: str
    updated_at: int


# ---------------------------------------------------------------------------
# 持久化存储
# ---------------------------------------------------------------------------


class SessionStore:
    """`user -> SessionBinding` 的 JSON 持久化。线程不安全（按 asyncio 单线程使用）。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._bindings: dict[str, SessionBinding] = {}

    def load(self) -> None:
        if not self.path.exists():
            self._bindings = {}
            return
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        version = raw.get("version")
        if version != STORE_VERSION:
            raise PoolError(f"unsupported session store version: {version!r}")
        users = raw.get("users") or {}
        self._bindings = {
            user: SessionBinding(
                user=user,
                chat_session_id=str(entry["chat_session_id"]),
                openclaw_session_key=str(entry["openclaw_session_key"]),
                updated_at=int(entry.get("updated_at") or 0),
            )
            for user, entry in users.items()
            if isinstance(entry, dict) and "chat_session_id" in entry and "openclaw_session_key" in entry
        }

    def save(self) -> None:
        payload = {
            "version": STORE_VERSION,
            "users": {
                user: {
                    "chat_session_id": binding.chat_session_id,
                    "openclaw_session_key": binding.openclaw_session_key,
                    "updated_at": binding.updated_at,
                }
                for user, binding in self._bindings.items()
            },
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_fd, tmp_name = tempfile.mkstemp(
            prefix=self.path.name,
            suffix=".tmp",
            dir=str(self.path.parent),
        )
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as fp:
                json.dump(payload, fp, ensure_ascii=False, indent=2)
            os.replace(tmp_name, self.path)
        except Exception:
            Path(tmp_name).unlink(missing_ok=True)
            raise

    def get(self, user: str) -> SessionBinding | None:
        return self._bindings.get(user)

    def set(self, user: str, chat_session_id: str, openclaw_session_key: str) -> SessionBinding:
        binding = SessionBinding(
            user=user,
            chat_session_id=chat_session_id,
            openclaw_session_key=openclaw_session_key,
            updated_at=int(time.time()),
        )
        self._bindings[user] = binding
        self.save()
        return binding

    def delete(self, user: str) -> bool:
        if user not in self._bindings:
            return False
        del self._bindings[user]
        self.save()
        return True

    def all(self) -> dict[str, SessionBinding]:
        return dict(self._bindings)


def derive_session_key(user: str, chat_session_id: str) -> str:
    return f"chat:{user}:{chat_session_id}"


# ---------------------------------------------------------------------------
# 帧路由器
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _Subscription:
    req_id: str
    session_key: str
    queue: asyncio.Queue[dict[str, Any]]


class FrameRouter:
    """单条 WS 连接上的帧分发器。

    - `res` 帧按 `id` 投递给注册的 subscription
    - `event` 帧按 payload 里的 sessionKey 投递给订阅该 sessionKey 的 subscription
    - 读循环抛异常时，把异常广播到所有 subscription 并标记 closed
    """

    _CLOSED_SENTINEL: dict[str, Any] = {"__router_closed__": True}

    def __init__(self, ws: ClientConnection) -> None:
        self._ws = ws
        self._subs_by_id: dict[str, _Subscription] = {}
        self._subs_by_session: dict[str, list[_Subscription]] = {}
        self._lock = asyncio.Lock()
        self._send_lock = asyncio.Lock()
        self._reader_task: asyncio.Task[None] | None = None
        self._closed = asyncio.Event()
        self._close_exc: BaseException | None = None

    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    def start(self) -> None:
        if self._reader_task is not None:
            return
        self._reader_task = asyncio.create_task(self._read_loop(), name="frame-router-reader")

    async def wait_closed(self) -> None:
        await self._closed.wait()

    async def close(self, exc: BaseException | None = None) -> None:
        if self._closed.is_set():
            return
        self._close_exc = exc
        self._closed.set()
        if self._reader_task:
            self._reader_task.cancel()
        try:
            await self._ws.close()
        except Exception:
            pass
        await self._fail_all(exc or ConnectionError("router closed"))

    async def subscribe(self, req_id: str, session_key: str) -> _Subscription:
        sub = _Subscription(req_id=req_id, session_key=session_key, queue=asyncio.Queue())
        async with self._lock:
            if self._closed.is_set():
                raise PoolError("router closed")
            if req_id in self._subs_by_id:
                raise PoolError(f"duplicate req_id subscription: {req_id}")
            self._subs_by_id[req_id] = sub
            self._subs_by_session.setdefault(session_key, []).append(sub)
        return sub

    async def unsubscribe(self, sub: _Subscription) -> None:
        async with self._lock:
            self._subs_by_id.pop(sub.req_id, None)
            waiters = self._subs_by_session.get(sub.session_key)
            if waiters and sub in waiters:
                waiters.remove(sub)
                if not waiters:
                    self._subs_by_session.pop(sub.session_key, None)

    async def send_request(self, req_id: str, method: str, params: dict[str, Any]) -> None:
        frame = OpenClawGatewayClient.build_request(req_id, method, params)
        async with self._send_lock:
            if self._closed.is_set():
                raise PoolError("router closed")
            await self._ws.send(frame)

    async def _read_loop(self) -> None:
        try:
            async for raw in self._ws:
                try:
                    frame = json.loads(raw)
                except json.JSONDecodeError:
                    _logger.warning("router got non-json frame: %r", raw[:200])
                    continue
                if not isinstance(frame, dict):
                    continue
                await self._dispatch(frame)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _logger.info("router read loop ended: %r", exc)
            await self.close(exc)
            return
        await self.close(ConnectionError("gateway closed connection"))

    async def _dispatch(self, frame: dict[str, Any]) -> None:
        frame_type = frame.get("type")
        if frame_type == "res":
            req_id = frame.get("id")
            if not isinstance(req_id, str):
                return
            sub = self._subs_by_id.get(req_id)
            if sub is not None:
                sub.queue.put_nowait(frame)
            return
        if frame_type == "event":
            session_key = _extract_session_key(frame)
            if not session_key:
                return
            # Exact or suffix match: gateway often namespaces events with
            # `agent:<agentId>:<sessionKey>`, so a subscription for `chat:foo:bar`
            # should also receive `agent:main:chat:foo:bar` events.
            for sk_sub, waiters in list(self._subs_by_session.items()):
                if session_key == sk_sub or session_key.endswith(":" + sk_sub):
                    for sub in list(waiters):
                        sub.queue.put_nowait(frame)
            return

    async def _fail_all(self, exc: BaseException) -> None:
        async with self._lock:
            subs = list(self._subs_by_id.values())
            self._subs_by_id.clear()
            self._subs_by_session.clear()
        for sub in subs:
            sub.queue.put_nowait({"__router_closed__": True, "error": repr(exc)})


def _extract_session_key(frame: dict[str, Any]) -> str | None:
    payload = frame.get("payload")
    if isinstance(payload, dict):
        sk = payload.get("sessionKey")
        if isinstance(sk, str):
            return sk
    return None


# ---------------------------------------------------------------------------
# 连接管理 + 握手
# ---------------------------------------------------------------------------


class ConnectionSupervisor:
    """持有共享连接 + 握手 + 自动重连。"""

    def __init__(
        self,
        *,
        url: str,
        password: str | None,
        token: str | None,
        identity: DeviceIdentity | None = None,
        operator_token: str | None = None,
        max_payload_bytes: int = 10 * 1024 * 1024,
    ) -> None:
        self._url = url
        self._password = password
        self._token = token
        self._identity = identity
        self._operator_token = operator_token
        self._max_payload_bytes = max_payload_bytes
        self._router: FrameRouter | None = None
        self._router_ready = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._stopping = False

    async def start(self) -> None:
        if self._task is not None:
            return
        self._identity = self._identity or DeviceIdentity.load()
        if self._operator_token is None:
            self._operator_token = load_operator_token()
        self._task = asyncio.create_task(self._supervise(), name="pool-supervisor")

    async def stop(self) -> None:
        self._stopping = True
        if self._router is not None:
            await self._router.close()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    async def get_router(self) -> FrameRouter:
        if self._task is None:
            raise PoolError("supervisor not started; call start() first")
        while True:
            router = self._router
            if router is not None and not router.closed:
                return router
            self._router_ready.clear()
            await self._router_ready.wait()

    async def _supervise(self) -> None:
        backoff = RECONNECT_BACKOFF_MIN
        while not self._stopping:
            try:
                ws = await websockets.connect(self._url, max_size=self._max_payload_bytes)
                try:
                    await self._handshake(ws)
                except Exception:
                    await ws.close()
                    raise
                router = FrameRouter(ws)
                router.start()
                self._router = router
                self._router_ready.set()
                backoff = RECONNECT_BACKOFF_MIN
                _logger.info("gateway connected: %s", self._url)
                await router.wait_closed()
                _logger.info("gateway connection closed")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _logger.warning("gateway supervisor error: %r", exc)
            finally:
                self._router = None
                self._router_ready.clear()
            if self._stopping:
                break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, RECONNECT_BACKOFF_MAX)

    async def _handshake(self, ws: ClientConnection) -> None:
        assert self._identity is not None
        raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
        challenge = json.loads(raw)
        if challenge.get("type") != "event" or challenge.get("event") != "connect.challenge":
            raise PoolError(f"expected connect.challenge, got {challenge!r}")
        nonce = challenge["payload"]["nonce"]
        signed_at = challenge["payload"]["ts"]

        for auth_payload, signing_secret in self._auth_variants():
            signed = build_signed_device(
                self._identity,
                nonce=nonce,
                signed_at=signed_at,
                client_id="cli",
                client_mode="cli",
                platform="linux",
                role="operator",
                scopes=DEFAULT_OPERATOR_SCOPES,
                signing_secret=signing_secret,
            )
            candidates = signed.pop("signatureCandidates")
            req_id = f"req-{uuid.uuid4().hex}"
            base_payload = {
                "minProtocol": 3,
                "maxProtocol": 3,
                "client": {
                    "id": "cli",
                    "displayName": "OpenClaw Session Pool",
                    "version": "0.1.0",
                    "platform": "linux",
                    "mode": "cli",
                    "instanceId": str(uuid.uuid4()),
                },
                "role": "operator",
                "scopes": DEFAULT_OPERATOR_SCOPES,
                "caps": [],
                "commands": [],
                "permissions": {},
                "auth": auth_payload,
                "locale": "zh-CN",
                "userAgent": "openclaw-client-pool/0.1.0",
            }
            for signature in candidates:
                device = dict(signed)
                device["signature"] = signature
                base_payload["device"] = device
                await ws.send(OpenClawGatewayClient.build_request(req_id, "connect", base_payload))
                response = json.loads(await asyncio.wait_for(ws.recv(), timeout=10.0))
                if (
                    response.get("type") == "res"
                    and response.get("id") == req_id
                    and response.get("ok")
                ):
                    return
                if response.get("type") == "res" and response.get("id") == req_id:
                    continue
                raise PoolError(f"unexpected connect response: {response!r}")
        raise PoolError("gateway rejected all auth variants")

    def _auth_variants(self) -> list[tuple[dict[str, Any], str]]:
        variants: list[tuple[dict[str, Any], str]] = []
        if self._operator_token:
            variants.append(({"token": self._operator_token}, self._operator_token))
        if self._token:
            variants.append(({"token": self._token}, self._token))
        if self._password:
            variants.append(({"password": self._password}, self._password))
            variants.append(({"password": self._password}, ""))
        if not variants:
            raise PoolError("no auth credentials configured (password/token/operator_token)")
        return variants


# ---------------------------------------------------------------------------
# 并发调度
# ---------------------------------------------------------------------------


class FairScheduler:
    """全局 slot 的 FIFO 调度器。

    单用户单请求场景下，asyncio.Semaphore 的 FIFO 公平性已经够用；`key` 参数保
    留以便后续做 per-user round-robin。
    """

    def __init__(self, capacity: int) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self._capacity = capacity
        self._sem = asyncio.Semaphore(capacity)

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def available(self) -> int:
        return self._sem._value  # type: ignore[attr-defined]

    @asynccontextmanager
    async def slot(self, key: str):
        await self._sem.acquire()
        try:
            yield
        finally:
            self._sem.release()


# ---------------------------------------------------------------------------
# 会话池顶层
# ---------------------------------------------------------------------------


class OpenClawSessionPool:
    """把 chatbot 的 (user, session_id) 绑定到 openclaw 的 sessionKey。"""

    def __init__(
        self,
        *,
        url: str = DEFAULT_URL,
        password: str | None = DEFAULT_PASSWORD,
        token: str | None = None,
        store_path: Path | str | None = None,
        concurrency: int = DEFAULT_CONCURRENCY,
    ) -> None:
        self._store = SessionStore(Path(store_path) if store_path else DEFAULT_STORE_PATH)
        self._store.load()
        self._scheduler = FairScheduler(concurrency)
        self._supervisor = ConnectionSupervisor(url=url, password=password, token=token)
        self._started = False

    async def __aenter__(self) -> OpenClawSessionPool:
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.stop()

    async def start(self) -> None:
        if self._started:
            return
        await self._supervisor.start()
        self._started = True

    async def stop(self) -> None:
        if not self._started:
            return
        await self._supervisor.stop()
        self._started = False

    # -- 绑定 --

    def bind(
        self,
        user: str,
        chat_session_id: str,
        *,
        session_key: str | None = None,
    ) -> SessionBinding:
        """绑定或切换用户的当前会话，返回对应的 openclaw sessionKey。"""
        if not user or not chat_session_id:
            raise ValueError("user and chat_session_id are required")
        key = session_key or derive_session_key(user, chat_session_id)
        return self._store.set(user, chat_session_id, key)

    def unbind(self, user: str) -> bool:
        return self._store.delete(user)

    def binding(self, user: str) -> SessionBinding | None:
        return self._store.get(user)

    def bindings(self) -> dict[str, SessionBinding]:
        return self._store.all()

    # -- 发送 --

    async def send(
        self,
        user: str,
        message: str,
        *,
        response_timeout: float = DEFAULT_RESPONSE_TIMEOUT,
        settle_timeout: float = DEFAULT_SETTLE_TIMEOUT,
        fallback_history: bool = True,
        on_event: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        subscribe_transcript: bool = True,
    ) -> RequestResult:
        binding = self._store.get(user)
        if binding is None:
            raise PoolError(f"user {user!r} has no bound session; call bind() first")

        async with self._scheduler.slot(user):
            router = await self._supervisor.get_router()
            return await _send_via_router(
                router=router,
                session_key=binding.openclaw_session_key,
                message=message,
                response_timeout=response_timeout,
                settle_timeout=settle_timeout,
                fallback_history=fallback_history,
                on_event=on_event,
                subscribe_transcript=subscribe_transcript,
            )

    async def history(
        self,
        user: str,
        *,
        timeout: float = 30.0,
    ) -> list[dict[str, Any]]:
        """拉取当前绑定 session 的历史。返回 gateway 原始 `messages` 数组。"""
        binding = self._store.get(user)
        if binding is None:
            raise PoolError(f"user {user!r} has no bound session; call bind() first")
        async with self._scheduler.slot(user):
            router = await self._supervisor.get_router()
            frame = await _request_via_router(
                router=router,
                method="chat.history",
                params={"sessionKey": binding.openclaw_session_key},
                session_key=binding.openclaw_session_key,
                timeout=timeout,
            )
        payload = frame.get("payload") or {}
        messages = payload.get("messages")
        return messages if isinstance(messages, list) else []


# ---------------------------------------------------------------------------
# 发送/等待协议（基于帧路由器的 per-request queue）
# ---------------------------------------------------------------------------


async def _send_via_router(
    *,
    router: FrameRouter,
    session_key: str,
    message: str,
    response_timeout: float,
    settle_timeout: float,
    fallback_history: bool,
    on_event: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    subscribe_transcript: bool = True,
) -> RequestResult:
    req_id = f"req-{uuid.uuid4().hex}"
    idem_key = f"req-{uuid.uuid4().hex}"
    sub = await router.subscribe(req_id, session_key)
    transcript_subscribed = False
    try:
        if subscribe_transcript:
            # 开启 gateway 侧的 transcript 事件流；每条新 message（toolCall /
            # toolResult / thinking / assistant 等）会通过 `session.message`
            # 事件推送给我们，让前端真正流式看到每一步。
            try:
                await _request_via_router(
                    router=router,
                    method="sessions.messages.subscribe",
                    params={"key": session_key},
                    session_key=session_key,
                    timeout=5.0,
                )
                transcript_subscribed = True
            except PoolError:
                # 老版本 gateway 没这个 RPC；失败就退回「只有 assistant delta +
                # 结尾靠 chat.history 兜底」的模式。
                transcript_subscribed = False

        params = {
            "sessionKey": session_key,
            "message": message,
            "deliver": True,
            "idempotencyKey": idem_key,
        }
        await router.send_request(req_id, "agent", params)

        events: list[dict[str, Any]] = []
        response: dict[str, Any] | None = None
        deadline = asyncio.get_running_loop().time() + response_timeout

        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                if response is not None:
                    return await _finalize(
                        router=router,
                        response=response,
                        events=events,
                        session_key=session_key,
                        message=message,
                        fallback_history=fallback_history,
                        deadline=deadline,
                    )
                raise PoolError("timed out waiting for agent response")

            recv_timeout = remaining if response is None else min(remaining, settle_timeout)
            try:
                frame = await asyncio.wait_for(sub.queue.get(), timeout=recv_timeout)
            except asyncio.TimeoutError:
                if response is not None:
                    return await _finalize(
                        router=router,
                        response=response,
                        events=events,
                        session_key=session_key,
                        message=message,
                        fallback_history=fallback_history,
                        deadline=deadline,
                    )
                raise PoolError("timed out waiting for agent response") from None

            if frame.get("__router_closed__"):
                raise PoolError(f"connection closed while waiting for response: {frame.get('error')}")

            frame_type = frame.get("type")
            if frame_type == "event":
                events.append(frame)
                if on_event is not None:
                    try:
                        await on_event(frame)
                    except Exception:
                        _logger.exception("on_event callback failed")
                if response is not None and OpenClawGatewayClient._is_terminal_agent_event(frame):
                    return await _finalize(
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
                    raise PoolError(f"agent request failed: {frame}")
                response = frame
                reply = OpenClawGatewayClient.extract_reply_text([frame])
                if reply:
                    return RequestResult(response=frame, events=events, reply_text=reply)
    finally:
        if transcript_subscribed:
            try:
                await _request_via_router(
                    router=router,
                    method="sessions.messages.unsubscribe",
                    params={"key": session_key},
                    session_key=session_key,
                    timeout=3.0,
                )
            except Exception:
                pass
        await router.unsubscribe(sub)


async def _finalize(
    *,
    router: FrameRouter,
    response: dict[str, Any],
    events: list[dict[str, Any]],
    session_key: str,
    message: str,
    fallback_history: bool,
    deadline: float,
) -> RequestResult:
    reply = OpenClawGatewayClient.extract_reply_text(events)
    if not reply and fallback_history:
        accepted_at = _coerce_int((response.get("payload") or {}).get("acceptedAt"))
        reply = await _poll_history(
            router=router,
            session_key=session_key,
            message=message,
            accepted_at=accepted_at,
            deadline=deadline,
        )
    return RequestResult(response=response, events=events, reply_text=reply)


async def _poll_history(
    *,
    router: FrameRouter,
    session_key: str,
    message: str,
    accepted_at: int | None,
    deadline: float,
    interval: float = 0.5,
) -> str:
    loop = asyncio.get_running_loop()
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            return ""
        history = await _request_via_router(
            router=router,
            method="chat.history",
            params={"sessionKey": session_key},
            session_key=session_key,
            timeout=remaining,
        )
        reply = OpenClawGatewayClient._extract_reply_from_history(
            history=history,
            message=message,
            accepted_at=accepted_at,
        )
        if reply:
            return reply
        await asyncio.sleep(min(interval, max(deadline - loop.time(), 0)))


async def _request_via_router(
    *,
    router: FrameRouter,
    method: str,
    params: dict[str, Any],
    session_key: str,
    timeout: float,
) -> dict[str, Any]:
    req_id = f"req-{uuid.uuid4().hex}"
    sub = await router.subscribe(req_id, session_key)
    try:
        await router.send_request(req_id, method, params)
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise PoolError(f"timed out waiting for {method} response")
            frame = await asyncio.wait_for(sub.queue.get(), timeout=remaining)
            if frame.get("__router_closed__"):
                raise PoolError(f"connection closed while waiting for {method}")
            if frame.get("type") != "res":
                continue
            if not frame.get("ok"):
                raise PoolError(f"{method} request failed: {frame}")
            return frame
    finally:
        await router.unsubscribe(sub)


def _coerce_int(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return None


__all__ = [
    "DEFAULT_STORE_PATH",
    "DEFAULT_URL",
    "DEFAULT_PASSWORD",
    "DEFAULT_CONCURRENCY",
    "ConnectionSupervisor",
    "FairScheduler",
    "FrameRouter",
    "OpenClawSessionPool",
    "PoolError",
    "SessionBinding",
    "SessionStore",
    "derive_session_key",
]
