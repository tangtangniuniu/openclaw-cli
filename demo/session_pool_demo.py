"""OpenClaw 会话池示例：多用户绑定 + 并发发送。

用法：
    uv run python demo/session_pool_demo.py \
        --users alice:sess-1 bob:sess-9 carol:sess-3 \
        --message "一句话自我介绍"

    # 切换用户 session，仅更新绑定，不发送请求
    uv run python demo/session_pool_demo.py --switch alice:sess-2

    # 列出当前持久化的映射
    uv run python demo/session_pool_demo.py --list

默认 `store` 路径为 `~/.openclaw-cli/session-map.json`，重启后仍能恢复绑定；
默认并发为 4，超过的用户会排队等 slot 释放。
"""
from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict
from pathlib import Path

from openclaw_client import OpenClawSessionPool, SessionBinding
from openclaw_client.pool import DEFAULT_STORE_PATH


def _parse_user_spec(spec: str) -> tuple[str, str]:
    if ":" not in spec:
        raise SystemExit(f"用户格式应为 user:chat_session_id，收到 {spec!r}")
    user, session_id = spec.split(":", 1)
    user, session_id = user.strip(), session_id.strip()
    if not user or not session_id:
        raise SystemExit(f"非法 user:session 对：{spec!r}")
    return user, session_id


def _binding_to_dict(binding: SessionBinding) -> dict:
    return asdict(binding)


async def _run_send(
    pool: OpenClawSessionPool,
    users: list[tuple[str, str]],
    message: str,
    response_timeout: float,
) -> None:
    for user, session_id in users:
        pool.bind(user, session_id)

    await pool.start()
    try:
        tasks = [
            asyncio.create_task(pool.send(user, message, response_timeout=response_timeout), name=f"send-{user}")
            for user, _ in users
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        await pool.stop()

    for (user, _), result in zip(users, results):
        print(f"=== {user} ===")
        if isinstance(result, Exception):
            print(f"  ERROR: {result!r}")
            continue
        print(f"  reply: {result.reply_text or '(empty)'}")
        if result.events:
            print(f"  events: {len(result.events)} 帧")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="ws://127.0.0.1:18789", help="Gateway WebSocket 地址")
    parser.add_argument("--password", default="zxt2000", help="Gateway 密码")
    parser.add_argument("--token", default=None, help="Gateway token")
    parser.add_argument(
        "--store",
        default=None,
        help=f"持久化路径，默认 {DEFAULT_STORE_PATH}",
    )
    parser.add_argument("--concurrency", type=int, default=4, help="OpenClaw 主 agent 并发上限")
    parser.add_argument(
        "--users",
        nargs="+",
        default=None,
        help="要发消息的用户列表，格式 user:chat_session_id",
    )
    parser.add_argument(
        "--switch",
        default=None,
        help="仅切换一个用户的当前 session，格式 user:chat_session_id",
    )
    parser.add_argument("--list", action="store_true", help="仅列出持久化的绑定")
    parser.add_argument("--message", default="你好，请一句话回复。", help="发送的消息")
    parser.add_argument("--response-timeout", type=float, default=60.0, help="等待回复的秒数")
    args = parser.parse_args()

    store_path = Path(args.store) if args.store else None

    pool = OpenClawSessionPool(
        url=args.url,
        password=args.password,
        token=args.token,
        store_path=store_path,
        concurrency=args.concurrency,
    )

    if args.switch:
        user, session_id = _parse_user_spec(args.switch)
        binding = pool.bind(user, session_id)
        print(json.dumps(_binding_to_dict(binding), ensure_ascii=False, indent=2))
        return 0

    if args.list:
        bindings = pool.bindings()
        print(json.dumps({u: _binding_to_dict(b) for u, b in bindings.items()}, ensure_ascii=False, indent=2))
        return 0

    if not args.users:
        parser.error("需要指定 --users / --switch / --list 之一")

    users = [_parse_user_spec(spec) for spec in args.users]
    asyncio.run(_run_send(pool, users, args.message, args.response_timeout))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
