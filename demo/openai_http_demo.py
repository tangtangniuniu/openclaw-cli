"""OpenAI 兼容 Chat Completions HTTP 接口演示。

参考文档：https://docs.openclaw.ai/gateway/openai-http-api

用法：
    uv run python demo/openai_http_demo.py --mode chat
    uv run python demo/openai_http_demo.py --mode chat --stream
    uv run python demo/openai_http_demo.py --mode models
    uv run python demo/openai_http_demo.py --mode embeddings

前置条件：
    Gateway 已启用 OpenAI 兼容端点
    （gateway.http.endpoints.chatCompletions.enabled=true）。
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from typing import Any


def _build_request(
    url: str,
    *,
    method: str,
    token: str,
    body: dict[str, Any] | None = None,
    extra_headers: dict[str, str] | None = None,
) -> urllib.request.Request:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)

    data: bytes | None = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"

    return urllib.request.Request(url=url, data=data, headers=headers, method=method)


def list_models(base: str, token: str) -> None:
    request = _build_request(f"{base}/models", method="GET", token=token)
    with urllib.request.urlopen(request, timeout=30) as resp:
        print(json.dumps(json.loads(resp.read()), ensure_ascii=False, indent=2))


def chat_completion(
    base: str,
    token: str,
    *,
    model: str,
    prompt: str,
    stream: bool,
    extra_headers: dict[str, str] | None,
) -> None:
    body: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": stream,
    }
    request = _build_request(
        f"{base}/chat/completions",
        method="POST",
        token=token,
        body=body,
        extra_headers=extra_headers,
    )

    if not stream:
        with urllib.request.urlopen(request, timeout=120) as resp:
            payload = json.loads(resp.read())
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    with urllib.request.urlopen(request, timeout=120) as resp:
        for raw in resp:
            line = raw.decode("utf-8", errors="replace").rstrip("\n")
            if not line:
                continue
            if line.startswith("data: "):
                payload = line[len("data: ") :]
                if payload == "[DONE]":
                    print("\n[stream done]")
                    return
                event = json.loads(payload)
                delta = (
                    event.get("choices", [{}])[0]
                    .get("delta", {})
                    .get("content", "")
                )
                if delta:
                    sys.stdout.write(delta)
                    sys.stdout.flush()


def create_embeddings(
    base: str,
    token: str,
    *,
    model: str,
    inputs: list[str],
    extra_headers: dict[str, str] | None,
) -> None:
    body = {"model": model, "input": inputs}
    request = _build_request(
        f"{base}/embeddings",
        method="POST",
        token=token,
        body=body,
        extra_headers=extra_headers,
    )
    with urllib.request.urlopen(request, timeout=120) as resp:
        print(json.dumps(json.loads(resp.read()), ensure_ascii=False, indent=2))


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
    parser.add_argument("--base", default="http://127.0.0.1:18789/v1", help="Gateway 兼容接口 base URL")
    parser.add_argument("--token", default="zxt2000", help="Gateway bearer token 或密码")
    parser.add_argument("--mode", choices=("models", "chat", "embeddings"), default="chat", help="要演示的接口")
    parser.add_argument("--model", default="openclaw/default", help="model 字段（agent target）")
    parser.add_argument("--prompt", default="你好，请用一句话自我介绍。", help="chat 模式下的用户消息")
    parser.add_argument("--stream", action="store_true", help="chat 模式下启用 SSE 流式输出")
    parser.add_argument(
        "--embedding-input",
        action="append",
        default=None,
        help="embeddings 模式下的输入，可重复",
    )
    parser.add_argument(
        "-H",
        "--header",
        action="append",
        default=None,
        help="附加请求头（可重复），例如 -H 'x-openclaw-model: openai/gpt-5.4'",
    )
    args = parser.parse_args()

    extra_headers = _parse_headers(args.header)

    try:
        if args.mode == "models":
            list_models(args.base, args.token)
        elif args.mode == "chat":
            chat_completion(
                args.base,
                args.token,
                model=args.model,
                prompt=args.prompt,
                stream=args.stream,
                extra_headers=extra_headers,
            )
        else:
            inputs = args.embedding_input or ["alpha", "beta"]
            create_embeddings(
                args.base,
                args.token,
                model=args.model,
                inputs=inputs,
                extra_headers=extra_headers,
            )
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(f"HTTP {exc.code}: {body}", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"连接失败：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
