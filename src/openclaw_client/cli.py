from __future__ import annotations

import argparse
import asyncio
import json

from openclaw_client import OpenClawGatewayClient


async def _run(args: argparse.Namespace) -> int:
    async with OpenClawGatewayClient(
        url=args.url,
        password=args.password,
        token=args.token,
        timeout=args.timeout,
    ) as client:
        result = await client.send_message(
            args.message,
            session_key=args.session_key,
            deliver=not args.no_deliver,
            response_timeout=args.response_timeout,
        )

    print("connect: ok")
    print(json.dumps(result.response, ensure_ascii=False, indent=2))
    if result.events:
        print("events:")
        for event in result.events:
            print(json.dumps(event, ensure_ascii=False, indent=2))
    print("disconnect: ok")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="连接 OpenClaw Gateway 并发送测试消息。")
    parser.add_argument("--url", default="ws://127.0.0.1:18789", help="Gateway WebSocket 地址")
    parser.add_argument("--password", default="zxt2000", help="Gateway 密码")
    parser.add_argument("--token", default=None, help="Gateway token，可选")
    parser.add_argument("--session-key", default="main", help="会话 key")
    parser.add_argument("--message", default="你好，请回复一条测试消息。", help="发送给 OpenClaw 的消息")
    parser.add_argument("--timeout", type=float, default=10.0, help="基础等待超时秒数")
    parser.add_argument("--response-timeout", type=float, default=15.0, help="等待 agent 返回的超时秒数")
    parser.add_argument("--no-deliver", action="store_true", help="发送 agent 请求时禁用 deliver")
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
