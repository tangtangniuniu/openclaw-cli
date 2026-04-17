"""WebSocket 协议接口演示。

参考文档：https://docs.openclaw.ai/gateway/protocol

脚本复用仓库内已实现的 `openclaw_client.OpenClawGatewayClient`，用来演示
Gateway WebSocket 协议中的完整握手与消息收发流程：

    1. 建立 WebSocket 连接并读取 `connect.challenge` 事件
    2. 基于本机设备身份和 operator token/密码完成 `connect` 请求签名
    3. 调用 `agent` 或 `chat.history` 等 RPC
    4. 处理服务端返回的 `res` 和 `event` 帧

用法：
    uv run python demo/websocket_protocol_demo.py --mode send
    uv run python demo/websocket_protocol_demo.py --mode history
    uv run python demo/websocket_protocol_demo.py --mode raw --method health --params '{}'
"""
from __future__ import annotations

import argparse
import asyncio
import json

from openclaw_client.client import OpenClawGatewayClient


async def _run_send(client: OpenClawGatewayClient, *, session_key: str, message: str) -> None:
    result = await client.send_message(message, session_key=session_key)
    print("--- res (agent) ---")
    print(json.dumps(result.response, ensure_ascii=False, indent=2))
    if result.reply_text:
        print("--- reply ---")
        print(result.reply_text)
    if result.events:
        print("--- events ---")
        for event in result.events:
            print(json.dumps(event, ensure_ascii=False, indent=2))


async def _run_history(client: OpenClawGatewayClient, *, session_key: str) -> None:
    frame = await client._request(  # noqa: SLF001 - 演示协议帧时直接复用内部 helper
        "chat.history",
        {"sessionKey": session_key},
        timeout=client.timeout,
    )
    print("--- res (chat.history) ---")
    print(json.dumps(frame, ensure_ascii=False, indent=2))


async def _run_raw(
    client: OpenClawGatewayClient,
    *,
    method: str,
    params: dict[str, object],
) -> None:
    frame = await client._request(method, params, timeout=client.timeout)  # noqa: SLF001
    print(f"--- res ({method}) ---")
    print(json.dumps(frame, ensure_ascii=False, indent=2))


async def _main(args: argparse.Namespace) -> int:
    async with OpenClawGatewayClient(
        url=args.url,
        password=args.password,
        token=args.token,
        timeout=args.timeout,
    ) as client:
        print("connect.challenge + connect 握手已完成")
        if args.mode == "send":
            await _run_send(client, session_key=args.session_key, message=args.message)
        elif args.mode == "history":
            await _run_history(client, session_key=args.session_key)
        else:
            params = json.loads(args.params) if args.params else {}
            if not isinstance(params, dict):
                print("--params 必须是 JSON 对象")
                return 2
            await _run_raw(client, method=args.method, params=params)
    print("disconnect: ok")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="ws://127.0.0.1:18789", help="Gateway WebSocket 地址")
    parser.add_argument("--password", default="zxt2000", help="Gateway 密码")
    parser.add_argument("--token", default=None, help="Gateway token（可选）")
    parser.add_argument("--timeout", type=float, default=10.0, help="单次 RPC 超时秒数")
    parser.add_argument(
        "--mode",
        choices=("send", "history", "raw"),
        default="send",
        help="演示类型：send 发消息；history 拉取会话；raw 直调任意 RPC",
    )
    parser.add_argument("--session-key", default="main", help="目标 sessionKey")
    parser.add_argument("--message", default="你好，请回复一条测试消息。", help="send 模式下的消息内容")
    parser.add_argument("--method", default=None, help="raw 模式下的 RPC 方法名，例如 health、models.list")
    parser.add_argument("--params", default=None, help="raw 模式下的 params JSON 字符串")
    args = parser.parse_args()

    if args.mode == "raw" and not args.method:
        parser.error("raw 模式需要指定 --method")

    return asyncio.run(_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
