"""OpenClaw 回调池示例：用 `OpenClawCallbackPool` 串起 4 种回调返回值。

用法：
    # 默认：用 "pass" handler 做最普通的一轮问答
    uv run python demo/callback_pool_demo.py --user alice --session s1 \
        --message "你好"

    # 演示 "used"：把 agent.delta 事件全吃掉，只保留最终事件
    uv run python demo/callback_pool_demo.py --user alice --session s1 \
        --message "给我讲个故事" --mode used

    # 演示 "modify"：handler 把第一个事件改写成一个固定字符串返回
    uv run python demo/callback_pool_demo.py --user alice --session s1 \
        --message "..." --mode modify

    # 演示 "async"：handler 直接返回一个 jobid，send 立即结束
    uv run python demo/callback_pool_demo.py --user alice --session s1 \
        --message "..." --mode async --jobid job-42

    # 演示 "fail"：handler 返回错误信息
    uv run python demo/callback_pool_demo.py --user alice --session s1 \
        --message "..." --mode fail

依赖真实 Gateway（默认 `ws://127.0.0.1:18789`）。鉴权默认密码 `zxt2000`，
可通过 `--password` / `--token` 覆盖。
"""
from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from openclaw_client import OpenClawCallbackPool


def _build_handler(mode: str, jobid: str | None):
    """按 CLI 选项构造一个示例 handler，所有分支都会把事件打印到 stderr。"""

    import sys

    async def handler(user: str, session: str, event: dict[str, Any]):
        name = event.get("event", "?")
        print(f"[handler] user={user} session={session} event={name}", file=sys.stderr)

        if mode == "pass":
            return None  # 走默认流程

        if mode == "used":
            # 把 agent.delta 流式增量全吃掉，其他事件走默认
            if name == "agent.delta":
                return ("used", None)
            return None

        if mode == "modify":
            # 第一个事件就改写成结构化回包
            return ("modify", {"replaced_by": "handler", "first_event": name})

        if mode == "async":
            return ("async", jobid or "job-auto")

        if mode == "fail":
            return ("fail", "demo: handler 主动返回错误")

        return None

    return handler


async def _run(args: argparse.Namespace) -> int:
    handler = _build_handler(args.mode, args.jobid)
    async with OpenClawCallbackPool(
        url=args.url,
        password=args.password,
        token=args.token,
    ) as pool:
        pool.bind(args.user, args.session)
        outcome = await pool.send(
            args.user,
            args.session,
            args.message,
            callback_handler=handler,
            response_timeout=args.response_timeout,
            settle_timeout=args.settle_timeout,
        )

    print(json.dumps(outcome.to_dict(), ensure_ascii=False, indent=2))
    return 0 if outcome.ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="ws://127.0.0.1:18789")
    parser.add_argument("--password", default="zxt2000")
    parser.add_argument("--token", default=None)
    parser.add_argument("--user", required=True, help="第三方程序侧的用户标识")
    parser.add_argument("--session", required=True, help="第三方程序侧的会话 id")
    parser.add_argument("--message", required=True)
    parser.add_argument(
        "--mode",
        choices=["pass", "used", "modify", "async", "fail"],
        default="pass",
        help="示例 handler 的返回语义",
    )
    parser.add_argument("--jobid", default=None, help="mode=async 时的 jobid")
    parser.add_argument("--response-timeout", type=float, default=60.0)
    parser.add_argument("--settle-timeout", type=float, default=2.0)
    args = parser.parse_args()

    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
