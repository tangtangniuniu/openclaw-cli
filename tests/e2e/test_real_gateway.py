import os

import pytest

from openclaw_client.client import OpenClawGatewayClient


pytestmark = pytest.mark.e2e


@pytest.mark.skipif(
    os.getenv("RUN_OPENCLAW_E2E") != "1",
    reason="Set RUN_OPENCLAW_E2E=1 to run against a real OpenClaw Gateway.",
)
@pytest.mark.asyncio
async def test_real_gateway_connect_send_disconnect():
    client = OpenClawGatewayClient(
        url=os.getenv("OPENCLAW_GATEWAY_URL", "ws://127.0.0.1:18789"),
        password=os.getenv("OPENCLAW_GATEWAY_PASSWORD", "zxt2000"),
    )

    connect_response = await client.connect()
    assert connect_response["ok"] is True
    assert client.is_connected is True

    result = await client.send_message(
        os.getenv("OPENCLAW_TEST_MESSAGE", "你好，请回复一条来自 pytest 的测试消息。"),
        session_key=os.getenv("OPENCLAW_SESSION_KEY", "main"),
    )
    assert result.response["ok"] is True

    await client.disconnect()
    assert client.is_connected is False
