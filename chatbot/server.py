"""chatbot 前端的 HTTP + WebSocket 后端。

- 同一个端口（默认 5173）上通过 `websockets.asyncio.serve` 的 `process_request`
  钩子服务静态文件（`/`、`/static/*`）和 `/ws` 的 WebSocket 升级。
- WS 消息协议见 docstring 的「消息协议」小节。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import mimetypes
from http import HTTPStatus
from pathlib import Path
from typing import Any

from websockets.asyncio.server import ServerConnection, serve
from websockets.datastructures import Headers
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Response

from openclaw_client.client import OpenClawGatewayClient
from openclaw_client.pool import OpenClawSessionPool, PoolError

from .user_sessions import ChatSession, UserSessionsStore, DEFAULT_PATH as SESSIONS_PATH


ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
INDEX_HTML = STATIC_DIR / "index.html"

_logger = logging.getLogger("openclaw_chatbot")


"""消息协议（client ↔ server，JSON over text frame）

client → server:
  {"op": "hello", "user": "alice"}
  {"op": "sessions.list"}
  {"op": "sessions.new", "name": "optional"}
  {"op": "sessions.switch", "id": "..."}
  {"op": "sessions.delete", "id": "..."}
  {"op": "sessions.rename", "id": "...", "name": "..."}
  {"op": "send", "text": "..."}
  {"op": "history.refresh"}

server → client:
  {"op": "hello.ok", "user", "sessions", "active"}
  {"op": "sessions", "sessions", "active"}
  {"op": "history", "messages"}
  {"op": "reply.pending"}
  {"op": "reply.done", "text", "events": [...]}
  {"op": "reply.error", "message"}
  {"op": "error", "message"}
"""


class ChatbotServer:
    def __init__(
        self,
        *,
        pool: OpenClawSessionPool,
        sessions: UserSessionsStore,
        host: str = "127.0.0.1",
        port: int = 5173,
    ) -> None:
        self.pool = pool
        self.sessions = sessions
        self.host = host
        self.port = port

    async def run(self) -> None:
        await self.pool.start()
        try:
            async with serve(
                self._handle_ws,
                self.host,
                self.port,
                process_request=self._process_request,
            ):
                _logger.info("chatbot web ready at http://%s:%d/", self.host, self.port)
                print(f"🦞  chatbot web ready:  http://{self.host}:{self.port}/")
                await asyncio.Future()
        finally:
            await self.pool.stop()

    # ------------------------------------------------------------------
    # 静态文件
    # ------------------------------------------------------------------

    async def _process_request(self, connection: ServerConnection, request) -> Response | None:
        path = request.path.split("?", 1)[0]
        if path == "/ws":
            return None  # 继续走 WebSocket 升级

        if path == "/":
            return self._serve_file(INDEX_HTML, "text/html; charset=utf-8")

        if path.startswith("/static/"):
            rel = path[len("/static/") :].lstrip("/")
            file_path = (STATIC_DIR / rel).resolve()
            try:
                file_path.relative_to(STATIC_DIR.resolve())
            except ValueError:
                return self._not_found()
            if file_path.is_file():
                mime, _ = mimetypes.guess_type(file_path.name)
                return self._serve_file(file_path, mime or "application/octet-stream")

        return self._not_found()

    def _serve_file(self, path: Path, mime: str) -> Response:
        body = path.read_bytes()
        headers = Headers(
            [
                ("Content-Type", mime),
                ("Content-Length", str(len(body))),
                ("Cache-Control", "no-store"),
            ]
        )
        return Response(HTTPStatus.OK.value, "OK", headers, body)

    def _not_found(self) -> Response:
        body = b"not found\n"
        return Response(
            HTTPStatus.NOT_FOUND.value,
            "Not Found",
            Headers([("Content-Type", "text/plain"), ("Content-Length", str(len(body)))]),
            body,
        )

    # ------------------------------------------------------------------
    # WebSocket 会话
    # ------------------------------------------------------------------

    async def _handle_ws(self, connection: ServerConnection) -> None:
        state: dict[str, Any] = {"user": None}
        try:
            async for raw in connection:
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", errors="replace")
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    await self._send(connection, {"op": "error", "message": "invalid json"})
                    continue
                if not isinstance(msg, dict):
                    await self._send(connection, {"op": "error", "message": "expected object"})
                    continue
                try:
                    await self._dispatch(connection, state, msg)
                except PoolError as exc:
                    await self._send(connection, {"op": "error", "message": str(exc)})
                except Exception as exc:
                    _logger.exception("handler error")
                    await self._send(connection, {"op": "error", "message": repr(exc)})
        except ConnectionClosed:
            return

    async def _dispatch(
        self,
        connection: ServerConnection,
        state: dict[str, Any],
        msg: dict[str, Any],
    ) -> None:
        op = msg.get("op")
        if op == "hello":
            user = str(msg.get("user") or "").strip()
            if not user:
                await self._send(connection, {"op": "error", "message": "user required"})
                return
            state["user"] = user
            active = self._active_info(user)
            await self._send(
                connection,
                {
                    "op": "hello.ok",
                    "user": user,
                    "sessions": self._sessions_payload(user),
                    "active": active,
                },
            )
            return

        user = state.get("user")
        if not user:
            await self._send(connection, {"op": "error", "message": "not authenticated"})
            return

        if op == "sessions.list":
            await self._reply_sessions(connection, user)
            return

        if op == "sessions.new":
            name = msg.get("name")
            session = self.sessions.create(user, name if isinstance(name, str) else None)
            self.pool.bind(user, session.id, session_key=self._derive_key(user, session))
            await self._reply_sessions(connection, user)
            await self._reply_history(connection, user)
            return

        if op == "sessions.switch":
            sid = str(msg.get("id") or "")
            session = self.sessions.get(user, sid)
            if session is None:
                await self._send(connection, {"op": "error", "message": "session not found"})
                return
            self.pool.bind(user, session.id, session_key=self._derive_key(user, session))
            self.sessions.touch(user, session.id)
            await self._reply_sessions(connection, user)
            await self._reply_history(connection, user)
            return

        if op == "sessions.delete":
            sid = str(msg.get("id") or "")
            was_active = self._current_binding_session_id(user) == sid
            removed = self.sessions.delete(user, sid)
            if removed and was_active:
                self.pool.unbind(user)
            await self._reply_sessions(connection, user)
            return

        if op == "sessions.rename":
            sid = str(msg.get("id") or "")
            name = str(msg.get("name") or "").strip()
            if not name:
                await self._send(connection, {"op": "error", "message": "name required"})
                return
            self.sessions.rename(user, sid, name)
            await self._reply_sessions(connection, user)
            return

        if op == "history.refresh":
            await self._reply_history(connection, user)
            return

        if op == "send":
            text = str(msg.get("text") or "").strip()
            if not text:
                await self._send(connection, {"op": "error", "message": "text required"})
                return
            binding = self.pool.binding(user)
            if binding is None:
                await self._send(connection, {"op": "error", "message": "no active session"})
                return
            self.sessions.touch(user, binding.chat_session_id)
            await self._reply_sessions(connection, user)
            await self._send(connection, {"op": "reply.pending"})

            seen_keys: set[Any] = set()

            async def on_event(frame: dict[str, Any]) -> None:
                await self._forward_stream_event(connection, frame, seen_keys)

            try:
                result = await self.pool.send(user, text, on_event=on_event)
            except PoolError as exc:
                await self._send(connection, {"op": "reply.error", "message": str(exc)})
                return
            reply_text = result.reply_text or OpenClawGatewayClient.extract_reply_text(result.events)
            await self._send(
                connection,
                {
                    "op": "reply.done",
                    "text": reply_text,
                    "events": _trim_events(result.events),
                },
            )
            # 回复完成后刷新历史，前端可以拿到 gateway 侧视图
            await self._reply_history(connection, user)
            return

        await self._send(connection, {"op": "error", "message": f"unknown op: {op!r}"})

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------

    async def _reply_sessions(self, connection: ServerConnection, user: str) -> None:
        await self._send(
            connection,
            {
                "op": "sessions",
                "sessions": self._sessions_payload(user),
                "active": self._active_info(user),
            },
        )

    async def _reply_history(self, connection: ServerConnection, user: str) -> None:
        binding = self.pool.binding(user)
        if binding is None:
            await self._send(connection, {"op": "history", "messages": []})
            return
        try:
            messages = await self.pool.history(user)
        except PoolError as exc:
            await self._send(connection, {"op": "error", "message": f"history failed: {exc}"})
            return
        await self._send(connection, {"op": "history", "messages": _normalize_messages(messages)})

    def _sessions_payload(self, user: str) -> list[dict[str, Any]]:
        active_id = self._current_binding_session_id(user)
        return [
            {
                "id": s.id,
                "name": s.name,
                "created_at": s.created_at,
                "updated_at": s.updated_at,
                "active": s.id == active_id,
            }
            for s in self.sessions.list(user)
        ]

    def _active_info(self, user: str) -> dict[str, Any] | None:
        binding = self.pool.binding(user)
        if binding is None:
            return None
        session = self.sessions.get(user, binding.chat_session_id)
        return {
            "id": binding.chat_session_id,
            "name": session.name if session else binding.chat_session_id,
            "session_key": binding.openclaw_session_key,
        }

    def _current_binding_session_id(self, user: str) -> str | None:
        binding = self.pool.binding(user)
        return binding.chat_session_id if binding else None

    def _derive_key(self, user: str, session: ChatSession) -> str:
        return f"chat:{user}:{session.id}"

    async def _send(self, connection: ServerConnection, payload: dict[str, Any]) -> None:
        try:
            await connection.send(json.dumps(payload, ensure_ascii=False))
        except ConnectionClosed:
            pass

    async def _forward_stream_event(
        self,
        connection: ServerConnection,
        frame: dict[str, Any],
        seen_keys: set[Any],
    ) -> None:
        """Convert a raw gateway event into zero or more chatbot stream frames."""
        ev = frame.get("event")
        payload = frame.get("payload") or {}
        if not isinstance(payload, dict):
            return

        # 真正的"每完成一步推送一条消息"的事件流：session.message
        if ev == "session.message":
            message = payload.get("message")
            if not isinstance(message, dict):
                return
            oc = message.get("__openclaw") or {}
            msg_id = oc.get("id") or f"{message.get('timestamp')}:{message.get('role')}"
            if msg_id in seen_keys:
                return
            seen_keys.add(msg_id)
            normalized_list = _normalize_messages([message])
            if not normalized_list:
                return
            await self._send(
                connection,
                {
                    "op": "stream.message",
                    "message": normalized_list[0],
                },
            )
            return

        if ev == "chat":
            # chat.final 作为兜底（session.message 订阅失败时也能看到 assistant 终稿）
            state = payload.get("state")
            if state != "final":
                return
            message = payload.get("message")
            if not isinstance(message, dict):
                return
            normalized_list = _normalize_messages([message])
            if not normalized_list:
                return
            normalized = normalized_list[0]
            key = (
                "chat-final",
                normalized.get("role"),
                message.get("timestamp"),
                (normalized.get("text") or "")[:32],
            )
            if key in seen_keys:
                return
            seen_keys.add(key)
            await self._send(
                connection,
                {"op": "stream.message", "message": normalized},
            )
            return

        if ev == "agent":
            stream = payload.get("stream")
            data = payload.get("data") or {}
            if stream == "assistant" and isinstance(data, dict):
                delta = data.get("delta")
                text_full = data.get("text")
                if isinstance(delta, str) and delta:
                    await self._send(
                        connection,
                        {
                            "op": "stream.delta",
                            "delta": delta,
                            "text": text_full if isinstance(text_full, str) else None,
                        },
                    )
                return
            if stream == "lifecycle" and isinstance(data, dict):
                phase = data.get("phase")
                if phase in ("start", "end"):
                    await self._send(
                        connection,
                        {"op": "stream.lifecycle", "phase": phase},
                    )
                return


def _trim_events(events: list[dict[str, Any]], limit: int = 16) -> list[dict[str, Any]]:
    trimmed: list[dict[str, Any]] = []
    for frame in events[:limit]:
        trimmed.append(
            {
                "event": frame.get("event"),
                "payload": frame.get("payload"),
            }
        )
    return trimmed


TOOL_RESULT_ROLES = {"tool", "toolResult", "tool_result", "function", "function_result"}
TOOL_CALL_ROLES = {"toolUse", "tool_use", "tool_call"}


def _normalize_messages(messages: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in messages:
        if not isinstance(item, dict):
            continue
        role = item.get("role") or "system"
        text_bits: list[str] = []
        parts: list[dict[str, Any]] = []
        _walk_content(item.get("content"), text_bits, parts)
        _maybe_emit_tool_part(item, parts)
        text = "".join(text_bits).strip()

        # 若整条消息语义上是 tool 的输入/输出（role 本身就是 toolResult / toolUse），
        # 把"裸文本"的 content 搬进对应 part，避免被当成普通气泡文字。
        if role in TOOL_RESULT_ROLES:
            if text and not any(
                p.get("kind") == "tool_output" and p.get("output") for p in parts
            ):
                parts.append(
                    {
                        "kind": "tool_output",
                        "name": item.get("name")
                        or item.get("tool_name")
                        or item.get("toolName")
                        or item.get("tool"),
                        "output": text,
                        "id": item.get("tool_call_id")
                        or item.get("toolCallId")
                        or item.get("call_id")
                        or item.get("id"),
                    }
                )
                text = ""
        elif role in TOOL_CALL_ROLES:
            if text and not any(p.get("kind") == "tool_call" for p in parts):
                parts.append(
                    {
                        "kind": "tool_call",
                        "name": item.get("name")
                        or item.get("tool_name")
                        or item.get("toolName"),
                        "input": text,
                        "id": item.get("id")
                        or item.get("tool_use_id")
                        or item.get("toolCallId")
                        or item.get("call_id"),
                    }
                )
                text = ""

        message_id = (item.get("__openclaw") or {}).get("id") if isinstance(item.get("__openclaw"), dict) else None

        out.append(
            {
                "role": role,
                "text": text,
                "parts": parts,
                "timestamp": item.get("timestamp"),
                "error_message": item.get("errorMessage"),
                "message_id": message_id,
            }
        )
    return out


def _walk_content(value: Any, text_bits: list[str], parts: list[dict[str, Any]]) -> None:
    if value is None:
        return
    if isinstance(value, str):
        if value.strip():
            text_bits.append(value)
        return
    if isinstance(value, list):
        for item in value:
            _walk_content(item, text_bits, parts)
        return
    if not isinstance(value, dict):
        return

    ctype = value.get("type") or value.get("role") or value.get("kind")

    if ctype in ("tool_use", "tool_call", "function_call", "toolCall", "toolUse"):
        parts.append(_tool_call_part(value))
        return
    if ctype in (
        "tool_result",
        "tool_output",
        "function_output",
        "function_response",
        "toolResult",
        "toolOutput",
    ):
        parts.append(_tool_output_part(value))
        return
    if ctype in ("thinking", "reasoning"):
        thinking_text = value.get("text") or value.get("content") or value.get("thinking")
        if isinstance(thinking_text, str) and thinking_text.strip():
            parts.append({"kind": "thinking", "text": thinking_text.strip()})
        return
    if ctype == "text":
        inner = value.get("text")
        if isinstance(inner, str) and inner.strip():
            text_bits.append(inner)
        return

    # generic recursion for nested shapes we don't yet recognize
    for key in ("text", "content", "message", "parts", "output", "body"):
        nested = value.get(key)
        if nested is not None:
            _walk_content(nested, text_bits, parts)


def _maybe_emit_tool_part(item: dict[str, Any], parts: list[dict[str, Any]]) -> None:
    # 某些实现把 tool call 挂在 assistant message 的 tool_calls 字段
    tool_calls = item.get("tool_calls")
    if isinstance(tool_calls, list):
        for tc in tool_calls:
            if isinstance(tc, dict):
                parts.append(_tool_call_part(tc))


def _tool_call_part(value: dict[str, Any]) -> dict[str, Any]:
    name = value.get("name")
    if not name:
        fn = value.get("function")
        if isinstance(fn, dict):
            name = fn.get("name")
    args = (
        value.get("input")
        or value.get("arguments")
        or value.get("args")
        or value.get("parameters")
    )
    if args is None and isinstance(value.get("function"), dict):
        args = value["function"].get("arguments")
    return {
        "kind": "tool_call",
        "name": name,
        "input": args,
        "id": value.get("id") or value.get("call_id") or value.get("tool_use_id"),
    }


def _tool_output_part(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "tool_output",
        "name": value.get("name") or value.get("tool") or value.get("tool_name"),
        "output": value.get("output") or value.get("content") or value.get("result"),
        "id": value.get("id")
        or value.get("tool_use_id")
        or value.get("tool_call_id")
        or value.get("call_id"),
    }


def _make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="OpenClaw chatbot webui server.")
    parser.add_argument("--host", default="127.0.0.1", help="HTTP bind host")
    parser.add_argument("--port", type=int, default=5173, help="HTTP bind port")
    parser.add_argument("--gateway-url", default="ws://127.0.0.1:18789", help="OpenClaw gateway WS URL")
    parser.add_argument("--gateway-password", default="zxt2000", help="OpenClaw gateway password")
    parser.add_argument("--gateway-token", default=None, help="OpenClaw gateway token (overrides password)")
    parser.add_argument("--concurrency", type=int, default=4, help="OpenClaw 主 agent 车道并发")
    parser.add_argument(
        "--pool-store",
        default=None,
        help="pool 绑定持久化文件，默认 ~/.openclaw-cli/session-map.json",
    )
    parser.add_argument(
        "--sessions-store",
        default=None,
        help=f"user-sessions 持久化文件，默认 {SESSIONS_PATH}",
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    return parser


def main() -> int:
    args = _make_parser().parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    pool = OpenClawSessionPool(
        url=args.gateway_url,
        password=args.gateway_password,
        token=args.gateway_token,
        store_path=Path(args.pool_store) if args.pool_store else None,
        concurrency=args.concurrency,
    )

    sessions = UserSessionsStore(
        Path(args.sessions_store) if args.sessions_store else SESSIONS_PATH
    )
    sessions.load()

    server = ChatbotServer(pool=pool, sessions=sessions, host=args.host, port=args.port)
    try:
        asyncio.run(server.run())
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
