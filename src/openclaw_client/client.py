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

        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise OpenClawGatewayError("Timed out waiting for agent response.")

            frame = await self._recv_json(timeout=remaining)
            frame_type = frame.get("type")
            if frame_type == "event":
                events.append(frame)
                continue

            self._ensure_response(frame, request_id, "agent")
            return RequestResult(response=frame, events=events)

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
