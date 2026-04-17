"""Tools Invoke HTTP 接口演示。

参考文档：https://docs.openclaw.ai/gateway/tools-invoke-http-api

用法：
    # 列出当前会话
    uv run python demo/tools_invoke_demo.py --tool sessions_list

    # 使用自定义参数
    uv run python demo/tools_invoke_demo.py --tool health --args-json '{}'

    # 传入 action 字段
    uv run python demo/tools_invoke_demo.py --tool sessions_list --action json

该端点始终启用（默认路径为 POST /tools/invoke）。
注意共享密钥鉴权等同于完整的 operator 权限，切勿直接暴露到公网。
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from typing import Any


def invoke_tool(
    base: str,
    token: str,
    *,
    tool: str,
    action: str | None,
    args: dict[str, Any] | None,
    session_key: str | None,
    dry_run: bool,
    extra_headers: dict[str, str] | None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"tool": tool}
    if action:
        body["action"] = action
    if args is not None:
        body["args"] = args
    if session_key:
        body["sessionKey"] = session_key
    if dry_run:
        body["dryRun"] = True

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)

    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url=f"{base}/tools/invoke",
        data=data,
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as resp:
        return json.loads(resp.read())


def _parse_headers(values: list[str] | None) -> dict[str, str]:
    headers: dict[str, str] = {}
    for item in values or []:
        if ":" not in item:
            raise SystemExit(f"非法的 header 格式：{item!r}")
        key, value = item.split(":", 1)
        headers[key.strip()] = value.strip()
    return headers


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:18789", help="Gateway base URL（无 /v1 后缀）")
    parser.add_argument("--token", default="zxt2000", help="Gateway bearer token 或密码")
    parser.add_argument("--tool", required=True, help="要调用的工具名，例如 sessions_list、health")
    parser.add_argument("--action", default=None, help="工具的 action 字段")
    parser.add_argument("--args-json", default=None, help="args 字段，JSON 字符串")
    parser.add_argument("--session-key", default=None, help="目标 sessionKey，默认为 main")
    parser.add_argument("--dry-run", action="store_true", help="dryRun 字段，目前被服务端忽略")
    parser.add_argument(
        "-H",
        "--header",
        action="append",
        default=None,
        help="附加请求头（可重复），例如 -H 'x-openclaw-message-channel: slack'",
    )
    args = parser.parse_args()

    tool_args: dict[str, Any] | None = None
    if args.args_json is not None:
        tool_args = json.loads(args.args_json)

    try:
        result = invoke_tool(
            args.base,
            args.token,
            tool=args.tool,
            action=args.action,
            args=tool_args,
            session_key=args.session_key,
            dry_run=args.dry_run,
            extra_headers=_parse_headers(args.header),
        )
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(f"HTTP {exc.code}: {body}", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"连接失败：{exc}", file=sys.stderr)
        return 1

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok", False) else 1


if __name__ == "__main__":
    raise SystemExit(main())
