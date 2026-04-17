"""OpenResponses HTTP 接口演示。

参考文档：https://docs.openclaw.ai/gateway/openresponses-http-api

用法：
    uv run python demo/openresponses_http_demo.py
    uv run python demo/openresponses_http_demo.py --stream
    uv run python demo/openresponses_http_demo.py --input-json demo/example_responses_input.json

前置条件：
    Gateway 已启用该端点（gateway.http.endpoints.responses.enabled=true）。
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


STREAM_TEXT_EVENTS = ("response.output_text.delta",)


def _build_request(
    url: str,
    *,
    token: str,
    body: dict[str, Any],
    extra_headers: dict[str, str] | None,
) -> urllib.request.Request:
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)

    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    return urllib.request.Request(url=url, data=data, headers=headers, method="POST")


def create_response(
    base: str,
    token: str,
    *,
    body: dict[str, Any],
    stream: bool,
    extra_headers: dict[str, str] | None,
) -> None:
    body = dict(body)
    body["stream"] = stream
    request = _build_request(f"{base}/responses", token=token, body=body, extra_headers=extra_headers)

    if not stream:
        with urllib.request.urlopen(request, timeout=120) as resp:
            payload = json.loads(resp.read())
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    current_event: str | None = None
    with urllib.request.urlopen(request, timeout=120) as resp:
        for raw in resp:
            line = raw.decode("utf-8", errors="replace").rstrip("\n")
            if not line:
                current_event = None
                continue
            if line.startswith(":"):
                continue
            if line.startswith("event: "):
                current_event = line[len("event: ") :].strip()
                continue
            if line.startswith("data: "):
                payload = line[len("data: ") :]
                if payload == "[DONE]":
                    print("\n[stream done]")
                    return
                event = json.loads(payload)
                if current_event in STREAM_TEXT_EVENTS:
                    delta = event.get("delta")
                    if isinstance(delta, str) and delta:
                        sys.stdout.write(delta)
                        sys.stdout.flush()
                        continue
                print(f"\n[{current_event}] {json.dumps(event, ensure_ascii=False)}")


def _load_input(value: str | None, default_prompt: str) -> str | list[dict[str, Any]]:
    if not value:
        return default_prompt
    path = Path(value)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return value


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
    parser.add_argument("--base", default="http://127.0.0.1:18789/v1", help="Gateway base URL")
    parser.add_argument("--token", default="zxt2000", help="Gateway bearer token 或密码")
    parser.add_argument("--model", default="openclaw", help="agent target，例如 openclaw / openclaw/default / openclaw/<agentId>")
    parser.add_argument(
        "--input",
        default=None,
        help="input 内容：字符串或指向 JSON 文件的路径（支持 item-based input 数组）",
    )
    parser.add_argument(
        "--prompt",
        default="你好，请用一句话总结 OpenClaw 的定位。",
        help="当 --input 未提供时使用的默认提示词",
    )
    parser.add_argument("--instructions", default=None, help="附加的系统指令")
    parser.add_argument("--user", default=None, help="OpenResponses user 字段，用于会话路由")
    parser.add_argument("--stream", action="store_true", help="启用 SSE 流式输出")
    parser.add_argument(
        "-H",
        "--header",
        action="append",
        default=None,
        help="附加请求头（可重复），例如 -H 'x-openclaw-agent-id: main'",
    )
    args = parser.parse_args()

    body: dict[str, Any] = {
        "model": args.model,
        "input": _load_input(args.input, args.prompt),
    }
    if args.instructions:
        body["instructions"] = args.instructions
    if args.user:
        body["user"] = args.user

    try:
        create_response(
            args.base,
            args.token,
            body=body,
            stream=args.stream,
            extra_headers=_parse_headers(args.header),
        )
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode("utf-8", errors="replace")
        print(f"HTTP {exc.code}: {body_text}", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"连接失败：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
