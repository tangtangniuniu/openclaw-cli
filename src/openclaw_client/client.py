from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass
from typing import Any

import websockets
from websockets.asyncio.client import ClientConnection

from openclaw_client.device_auth import (
    DeviceIdentity,
    build_signed_device,
    load_operator_token,
)


DEFAULT_OPERATOR_SCOPES = [
    "operator.read",
    "operator.write",
    "operator.admin",
    "operator.approvals",
    "operator.pairing",
]


class OpenClawGatewayError(RuntimeError):
    """Raised when the gateway returns an error or an unexpected frame."""


@dataclass(slots=True)
class RequestResult:
    response: dict[str, Any]
    events: list[dict[str, Any]]
    reply_text: str


class OpenClawGatewayClient:
    def __init__(
        self,
        url: str = "ws://127.0.0.1:18789",
        password: str = "zxt2000",
        *,
        token: str | None = None,
        timeout: float = 10.0,
    ) -> None:
        self.url = url
        self.password = password
        self.token = token
        self.timeout = timeout
        self._ws: ClientConnection | None = None
        self.device_identity = DeviceIdentity.load()
        self.operator_token = load_operator_token()

    @property
    def is_connected(self) -> bool:
        return self._ws is not None

    @staticmethod
    def build_request(message_id: str, method: str, params: dict[str, Any]) -> str:
        return json.dumps(
            {
                "type": "req",
                "id": message_id,
                "method": method,
                "params": params,
            },
            ensure_ascii=False,
        )

    async def connect(self) -> dict[str, Any]:
        if self._ws is not None:
            raise OpenClawGatewayError("Gateway connection is already open.")

        auth_variants = self._auth_variants()
        last_error: OpenClawGatewayError | None = None

        for auth_payload, signing_secret in auth_variants:
            try:
                return await self._connect_once(auth_payload=auth_payload, signing_secret=signing_secret)
            except OpenClawGatewayError as exc:
                last_error = exc
                await self.disconnect()

        raise last_error or OpenClawGatewayError("Gateway connect failed for all auth variants.")

    async def send_message(
        self,
        message: str,
        *,
        session_key: str = "main",
        deliver: bool = True,
        response_timeout: float = 15.0,
        settle_timeout: float = 1.0,
    ) -> RequestResult:
        ws = self._require_connection()
        request_id = self._next_request_id()
        payload = {
            "sessionKey": session_key,
            "message": message,
            "deliver": deliver,
            "idempotencyKey": self._next_request_id(),
        }

        await ws.send(self.build_request(request_id, "agent", payload))

        events: list[dict[str, Any]] = []
        deadline = asyncio.get_running_loop().time() + response_timeout
        response: dict[str, Any] | None = None

        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                if response is not None:
                    reply_text = self.extract_reply_text(events)
                    if not reply_text:
                        reply_text = await self._poll_history_for_reply(
                            session_key=session_key,
                            message=message,
                            accepted_at=self._extract_accepted_at(response),
                            deadline=deadline,
                        )
                    return RequestResult(
                        response=response,
                        events=events,
                        reply_text=reply_text,
                    )
                raise OpenClawGatewayError("Timed out waiting for agent response.")

            recv_timeout = remaining
            if response is not None:
                recv_timeout = min(remaining, settle_timeout)

            try:
                frame = await self._recv_json(timeout=recv_timeout)
            except asyncio.TimeoutError:
                if response is not None:
                    reply_text = self.extract_reply_text(events)
                    if not reply_text:
                        reply_text = await self._poll_history_for_reply(
                            session_key=session_key,
                            message=message,
                            accepted_at=self._extract_accepted_at(response),
                            deadline=deadline,
                        )
                    return RequestResult(
                        response=response,
                        events=events,
                        reply_text=reply_text,
                    )
                raise OpenClawGatewayError("Timed out waiting for agent response.") from None

            frame_type = frame.get("type")
            if frame_type == "event":
                events.append(frame)
                if response is not None and self._is_terminal_agent_event(frame):
                    reply_text = self.extract_reply_text(events)
                    if not reply_text:
                        reply_text = await self._poll_history_for_reply(
                            session_key=session_key,
                            message=message,
                            accepted_at=self._extract_accepted_at(response),
                            deadline=deadline,
                        )
                    return RequestResult(
                        response=response,
                        events=events,
                        reply_text=reply_text,
                    )
                continue
            if response is not None and frame_type == "res" and frame.get("id") != request_id:
                continue

            self._ensure_response(frame, request_id, "agent")
            response = frame

            if self.extract_reply_text([frame]):
                return RequestResult(response=response, events=events, reply_text=self.extract_reply_text([frame]))

    async def disconnect(self) -> None:
        if self._ws is None:
            return

        ws = self._ws
        self._ws = None
        await ws.close()

    async def __aenter__(self) -> OpenClawGatewayClient:
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.disconnect()

    def _require_connection(self) -> ClientConnection:
        if self._ws is None:
            raise OpenClawGatewayError("Gateway connection is not open.")
        return self._ws

    async def _recv_json(self, *, timeout: float | None = None) -> dict[str, Any]:
        ws = self._require_connection()
        raw = await asyncio.wait_for(ws.recv(), timeout=timeout or self.timeout)
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise OpenClawGatewayError("Gateway returned a non-object frame.")
        return data

    async def _connect_once(self, *, auth_payload: dict[str, Any], signing_secret: str) -> dict[str, Any]:
        self._ws = await websockets.connect(self.url, max_size=10 * 1024 * 1024)

        challenge = await self._recv_json()
        if challenge.get("type") != "event" or challenge.get("event") != "connect.challenge":
            raise OpenClawGatewayError(f"Expected connect.challenge event, got: {challenge}")

        request_id = self._next_request_id()
        signed_device = build_signed_device(
            self.device_identity,
            nonce=challenge["payload"]["nonce"],
            signed_at=challenge["payload"]["ts"],
            client_id="cli",
            client_mode="cli",
            platform="linux",
            role="operator",
            scopes=DEFAULT_OPERATOR_SCOPES,
            signing_secret=signing_secret,
        )

        payload = {
            "minProtocol": 3,
            "maxProtocol": 3,
            "client": {
                "id": "cli",
                "displayName": "Python OpenClaw Client",
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
            "userAgent": "openclaw-client/0.1.0",
        }

        for signature in signed_device.pop("signatureCandidates"):
            device = dict(signed_device)
            device["signature"] = signature
            payload["device"] = device
            await self._ws.send(self.build_request(request_id, "connect", payload))
            response = await self._recv_json()
            if response.get("type") == "res" and response.get("ok") and response.get("id") == request_id:
                return response
            if response.get("type") == "res" and response.get("id") == request_id:
                continue
            raise OpenClawGatewayError(f"Unexpected connect response: {response}")

        raise OpenClawGatewayError("Gateway rejected all device signatures.")

    def _auth_variants(self) -> list[tuple[dict[str, Any], str]]:
        variants: list[tuple[dict[str, Any], str]] = []
        if self.password:
            variants.append(({"password": self.password}, self.password))
            variants.append(({"password": self.password}, ""))
        if self.token:
            variants.append(({"token": self.token}, self.token))
        if self.operator_token:
            variants.insert(0, ({"token": self.operator_token}, self.operator_token))
            variants.append(({"password": self.password}, self.operator_token))
        return variants

    @staticmethod
    def _ensure_response(frame: dict[str, Any], request_id: str, method: str) -> None:
        if frame.get("type") != "res":
            raise OpenClawGatewayError(f"Expected response frame for {method}, got: {frame}")
        if frame.get("id") != request_id:
            raise OpenClawGatewayError(
                f"Response id mismatch for {method}: expected {request_id}, got {frame.get('id')}"
            )
        if not frame.get("ok"):
            raise OpenClawGatewayError(f"{method} request failed: {frame}")

    @staticmethod
    def _next_request_id() -> str:
        return f"req-{uuid.uuid4().hex}"

    async def _request(self, method: str, params: dict[str, Any], *, timeout: float) -> dict[str, Any]:
        ws = self._require_connection()
        request_id = self._next_request_id()
        await ws.send(self.build_request(request_id, method, params))

        while True:
            frame = await self._recv_json(timeout=timeout)
            if frame.get("type") == "event":
                continue
            self._ensure_response(frame, request_id, method)
            return frame

    async def _poll_history_for_reply(
        self,
        *,
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

            history = await self._request("chat.history", {"sessionKey": session_key}, timeout=remaining)
            reply = self._extract_reply_from_history(
                history=history,
                message=message,
                accepted_at=accepted_at,
            )
            if reply:
                return reply

            await asyncio.sleep(min(interval, max(deadline - loop.time(), 0)))

    @classmethod
    def _extract_reply_from_history(
        cls,
        *,
        history: dict[str, Any],
        message: str,
        accepted_at: int | None,
    ) -> str:
        payload = history.get("payload")
        if not isinstance(payload, dict):
            return ""

        messages = payload.get("messages")
        if not isinstance(messages, list):
            return ""

        target_user_index: int | None = None
        for index, item in enumerate(messages):
            if not isinstance(item, dict):
                continue
            if item.get("role") != "user":
                continue
            if accepted_at is not None and cls._coerce_int(item.get("timestamp")) is not None:
                if cls._coerce_int(item.get("timestamp")) < accepted_at - 2000:
                    continue
            if cls.extract_reply_text([{"payload": item.get("content")}]) == message:
                target_user_index = index

        search_start = 0 if target_user_index is None else target_user_index + 1
        for item in messages[search_start:]:
            if not isinstance(item, dict):
                continue
            if item.get("role") != "assistant":
                continue
            timestamp = cls._coerce_int(item.get("timestamp"))
            if accepted_at is not None and timestamp is not None and timestamp < accepted_at:
                continue
            reply = cls.extract_reply_text([{"payload": item.get("content")}])
            if reply:
                return reply
            error_message = item.get("errorMessage")
            if isinstance(error_message, str) and error_message.strip():
                return error_message
        return ""

    @staticmethod
    def _extract_accepted_at(response: dict[str, Any]) -> int | None:
        payload = response.get("payload")
        if not isinstance(payload, dict):
            return None
        return OpenClawGatewayClient._coerce_int(payload.get("acceptedAt"))

    @staticmethod
    def _coerce_int(value: Any) -> int | None:
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        return None

    @staticmethod
    def _is_terminal_agent_event(frame: dict[str, Any]) -> bool:
        event = str(frame.get("event", "")).lower()
        payload = frame.get("payload")
        if event.endswith((".done", ".completed", ".complete", ".finished", ".finish")):
            return True
        if isinstance(payload, dict):
            if payload.get("done") is True or payload.get("completed") is True or payload.get("finished") is True:
                return True
        return False

    @classmethod
    def extract_reply_text(cls, frames: list[dict[str, Any]]) -> str:
        fragments: list[str] = []
        for frame in frames:
            cls._collect_text_fragments(frame.get("payload"), fragments)
        return "".join(fragments).strip()

    @classmethod
    def _collect_text_fragments(cls, value: Any, fragments: list[str]) -> None:
        if value is None:
            return
        if isinstance(value, str):
            if value.strip():
                fragments.append(value)
            return
        if isinstance(value, list):
            for item in value:
                cls._collect_text_fragments(item, fragments)
            return
        if not isinstance(value, dict):
            return

        preferred_keys = ("text", "delta", "content", "message", "reply", "response", "output")
        for key in preferred_keys:
            nested = value.get(key)
            if nested is not None:
                cls._collect_text_fragments(nested, fragments)
