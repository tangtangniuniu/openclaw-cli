import json
from types import SimpleNamespace

import pytest

from openclaw_client.client import OpenClawGatewayClient, OpenClawGatewayError


class FakeWebSocket:
    def __init__(self, frames: list[dict], *, delay: float = 0.0) -> None:
        self._frames = [json.dumps(frame, ensure_ascii=False) for frame in frames]
        self.delay = delay
        self.sent_messages: list[dict] = []
        self.closed = False

    async def send(self, message: str) -> None:
        self.sent_messages.append(json.loads(message))

    async def recv(self) -> str:
        if not self._frames:
            raise RuntimeError("No more frames available.")
        if self.delay:
            import asyncio

            await asyncio.sleep(self.delay)
        return self._frames.pop(0)

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_connect_sends_password_and_marks_connected(monkeypatch):
    fake_ws = FakeWebSocket(
        [
            {"type": "event", "event": "connect.challenge", "payload": {"nonce": "n1", "ts": 123}},
            {"type": "res", "id": "req-connect", "ok": True, "payload": {"protocol": 3}},
        ]
    )

    async def fake_connect(*args, **kwargs):
        return fake_ws

    monkeypatch.setattr("openclaw_client.client.websockets.connect", fake_connect)
    monkeypatch.setattr(
        "openclaw_client.client.DeviceIdentity.load",
        classmethod(lambda cls: SimpleNamespace(device_id="d1", public_key="pk", private_key_pem="pem")),
    )
    monkeypatch.setattr("openclaw_client.client.load_operator_token", lambda: None)
    monkeypatch.setattr(
        "openclaw_client.client.build_signed_device",
        lambda *args, **kwargs: {
            "id": "d1",
            "publicKey": "pk",
            "signatureCandidates": ["sig-v3", "sig-v2"],
            "signedAt": 123,
            "nonce": "n1",
        },
    )
    monkeypatch.setattr(
        OpenClawGatewayClient,
        "_next_request_id",
        staticmethod(lambda: "req-connect"),
    )

    client = OpenClawGatewayClient(password="zxt2000")
    response = await client.connect()

    assert client.is_connected is True
    assert response["ok"] is True
    assert fake_ws.sent_messages[0]["method"] == "connect"
    assert fake_ws.sent_messages[0]["params"]["auth"]["password"] == "zxt2000"
    assert fake_ws.sent_messages[0]["params"]["device"]["signature"] == "sig-v3"


@pytest.mark.asyncio
async def test_send_message_collects_events_and_returns_response():
    fake_ws = FakeWebSocket(
        [
            {"type": "event", "event": "agent.delta", "payload": {"text": "hello"}},
            {"type": "res", "id": "req-agent", "ok": True, "payload": {"accepted": True}},
        ]
    )
    client = OpenClawGatewayClient()
    client._ws = fake_ws

    client._next_request_id = lambda: "req-agent"  # type: ignore[method-assign]
    result = await client.send_message("hi", session_key="main")

    assert result.response["payload"]["accepted"] is True
    assert result.events[0]["event"] == "agent.delta"
    assert fake_ws.sent_messages[0]["method"] == "agent"
    assert fake_ws.sent_messages[0]["params"]["message"] == "hi"


@pytest.mark.asyncio
async def test_disconnect_closes_socket():
    fake_ws = FakeWebSocket([])
    client = OpenClawGatewayClient()
    client._ws = fake_ws

    await client.disconnect()

    assert client.is_connected is False
    assert fake_ws.closed is True


@pytest.mark.asyncio
async def test_send_message_requires_connection():
    client = OpenClawGatewayClient()

    with pytest.raises(OpenClawGatewayError, match="not open"):
        await client.send_message("hi")
