"""多会话并发测试 chatbot 的 HTTP + WebSocket 后端。

消息协议（client ↔ server，text frame JSON）：

client → server:
    {"op": "hello"}
    {"op": "config.get"}
    {"op": "config.save", "config": {...}}
    {"op": "run.start", "plan": [{"user": "...", "session_id": "...", "session_name": "...", "questions": [...]}, ...]}
    {"op": "run.stop"}

server → client:
    {"op": "hello.ok", "config": {...}, "scheduler": {...}}
    {"op": "config", "config": {...}}
    {"op": "run.started", "lanes": [...]}
    {"op": "run.stopped", "lanes": [...]}
    {"op": "lane", "lane": {...}}                      # 任一 lane 状态变化
    {"op": "lane.question.pending", "key", "index", "question"}
    {"op": "lane.question.reply", "key", "index", "question", "reply", "duration_ms"}
    {"op": "lane.question.error", "key", "index", "message"}
    {"op": "stats", "scheduler": {...}, "lanes": [...], "running": bool}
    {"op": "error", "message"}
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import mimetypes
from http import HTTPStatus
from pathlib import Path
from typing import Any

from websockets.asyncio.server import ServerConnection, serve
from websockets.datastructures import Headers
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Response

from openclaw_client.pool import OpenClawSessionPool, PoolError

from .config_store import DEFAULT_PATH as CONFIG_PATH, MultiConfigStore
from .runner import ConcurrentRunner, LaneSpec


ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
INDEX_HTML = STATIC_DIR / "index.html"

_logger = logging.getLogger("openclaw.multi_chatbot")


class MultiChatbotServer:
    def __init__(
        self,
        *,
        pool: OpenClawSessionPool,
        config: MultiConfigStore,
        host: str = "127.0.0.1",
        port: int = 5273,
    ) -> None:
        self.pool = pool
        self.config = config
        self.host = host
        self.port = port
        # 每条 WS 连接对应一个 runner（多人同时打开页面时互不干扰）
        self._connections: set[ServerConnection] = set()

    async def run(self) -> None:
        await self.pool.start()
        try:
            async with serve(
                self._handle_ws,
                self.host,
                self.port,
                process_request=self._process_request,
            ):
                _logger.info(
                    "multi-chatbot ready at http://%s:%d/", self.host, self.port
                )
                print(
                    f"🦞  multi-chatbot ready:  http://{self.host}:{self.port}/"
                )
                await asyncio.Future()
        finally:
            await self.pool.stop()

    # ------------------------------------------------------------------
    # 静态文件
    # ------------------------------------------------------------------

    async def _process_request(
        self, connection: ServerConnection, request
    ) -> Response | None:
        path = request.path.split("?", 1)[0]
        if path == "/ws":
            return None
        if path == "/":
            return self._serve_file(INDEX_HTML, "text/html; charset=utf-8")
        if path.startswith("/static/"):
            rel = path[len("/static/"):].lstrip("/")
            file_path = (STATIC_DIR / rel).resolve()
            try:
                file_path.relative_to(STATIC_DIR.resolve())
            except ValueError:
                return self._not_found()
            if file_path.is_file():
                mime, _ = mimetypes.guess_type(file_path.name)
                return self._serve_file(file_path, mime or "application/octet-stream")
        return self._not_found()

    def _serve_file(self, path: Path, mime: str) -> Response:
        body = path.read_bytes()
        headers = Headers(
            [
                ("Content-Type", mime),
                ("Content-Length", str(len(body))),
                ("Cache-Control", "no-store"),
            ]
        )
        return Response(HTTPStatus.OK.value, "OK", headers, body)

    def _not_found(self) -> Response:
        body = b"not found\n"
        return Response(
            HTTPStatus.NOT_FOUND.value,
            "Not Found",
            Headers(
                [("Content-Type", "text/plain"), ("Content-Length", str(len(body)))]
            ),
            body,
        )

    # ------------------------------------------------------------------
    # WebSocket 会话
    # ------------------------------------------------------------------

    async def _handle_ws(self, connection: ServerConnection) -> None:
        self._connections.add(connection)
        runner: ConcurrentRunner | None = None

        async def progress(payload: dict[str, Any]) -> None:
            await self._send(connection, payload)

        try:
            async for raw in connection:
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", errors="replace")
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    await self._send(connection, {"op": "error", "message": "invalid json"})
                    continue
                if not isinstance(msg, dict):
                    await self._send(
                        connection, {"op": "error", "message": "expected object"}
                    )
                    continue
                try:
                    runner = await self._dispatch(connection, msg, runner, progress)
                except PoolError as exc:
                    await self._send(
                        connection, {"op": "error", "message": str(exc)}
                    )
                except Exception as exc:  # noqa: BLE001
                    _logger.exception("handler error")
                    await self._send(
                        connection, {"op": "error", "message": repr(exc)}
                    )
        except ConnectionClosed:
            pass
        finally:
            self._connections.discard(connection)
            if runner is not None and runner.running:
                await runner.stop()

    async def _dispatch(
        self,
        connection: ServerConnection,
        msg: dict[str, Any],
        runner: ConcurrentRunner | None,
        progress,
    ) -> ConcurrentRunner | None:
        op = msg.get("op")

        if op == "hello":
            await self._send(
                connection,
                {
                    "op": "hello.ok",
                    "config": self.config.to_dict(),
                    "scheduler": self.pool.scheduler_stats(),
                },
            )
            return runner

        if op == "config.get":
            await self._send(
                connection, {"op": "config", "config": self.config.to_dict()}
            )
            return runner

        if op == "config.save":
            cfg = msg.get("config")
            if not isinstance(cfg, dict):
                await self._send(
                    connection, {"op": "error", "message": "config object required"}
                )
                return runner
            self.config.replace(cfg)
            await self._send(
                connection,
                {"op": "config", "config": self.config.to_dict(), "saved": True},
            )
            return runner

        if op == "run.start":
            if runner is not None and runner.running:
                await self._send(
                    connection,
                    {"op": "error", "message": "a run is already in progress"},
                )
                return runner
            plan_raw = msg.get("plan")
            if not isinstance(plan_raw, list) or not plan_raw:
                await self._send(
                    connection,
                    {"op": "error", "message": "plan must be a non-empty list"},
                )
                return runner
            specs: list[LaneSpec] = []
            for entry in plan_raw:
                if not isinstance(entry, dict):
                    continue
                user = str(entry.get("user") or "").strip()
                sid = str(entry.get("session_id") or "").strip()
                sname = str(entry.get("session_name") or sid).strip() or sid
                questions_raw = entry.get("questions")
                if not user or not sid or not isinstance(questions_raw, list):
                    continue
                questions = [
                    str(q).strip()
                    for q in questions_raw
                    if isinstance(q, str) and q.strip()
                ]
                if not questions:
                    continue
                specs.append(
                    LaneSpec(
                        user=user,
                        session_id=sid,
                        session_name=sname,
                        questions=questions,
                    )
                )
            if not specs:
                await self._send(
                    connection,
                    {"op": "error", "message": "no runnable lanes in plan"},
                )
                return runner
            runner = ConcurrentRunner(pool=self.pool, progress=progress)
            await runner.start(specs)
            # 在后台 await 所有 lane 完成后推一条 summary
            asyncio.create_task(self._finalize(runner, progress))
            return runner

        if op == "run.stop":
            if runner is not None and runner.running:
                await runner.stop()
                return None
            await self._send(
                connection, {"op": "error", "message": "no active run"}
            )
            return runner

        await self._send(
            connection, {"op": "error", "message": f"unknown op: {op!r}"}
        )
        return runner

    async def _finalize(self, runner: ConcurrentRunner, progress) -> None:
        try:
            await runner.wait_done()
        except Exception:
            _logger.exception("runner wait_done failed")
            return
        if runner.running:
            # 所有 lane 结束，标记总 run 完毕
            runner._running = False  # type: ignore[attr-defined]
        await progress(
            {
                "op": "run.stopped",
                "lanes": runner.lanes_snapshot(),
                "scheduler": self.pool.scheduler_stats(),
            }
        )

    async def _send(
        self, connection: ServerConnection, payload: dict[str, Any]
    ) -> None:
        try:
            await connection.send(json.dumps(payload, ensure_ascii=False))
        except ConnectionClosed:
            pass


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="OpenClaw 多用户多会话并发测试 chatbot。"
    )
    parser.add_argument("--host", default="127.0.0.1", help="HTTP 绑定地址")
    parser.add_argument("--port", type=int, default=5273, help="HTTP 绑定端口")
    parser.add_argument(
        "--gateway-url", default="ws://127.0.0.1:18789", help="OpenClaw gateway WS URL"
    )
    parser.add_argument(
        "--gateway-password", default="zxt2000", help="OpenClaw gateway 密码"
    )
    parser.add_argument(
        "--gateway-token",
        default=None,
        help="OpenClaw gateway token（若设置，优先于 password）",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=4,
        help="OpenClaw 主 agent 车道并发（FairScheduler capacity）",
    )
    parser.add_argument(
        "--pool-store",
        default=None,
        help="pool 绑定持久化文件，默认 ~/.openclaw-cli/session-map.json",
    )
    parser.add_argument(
        "--config-path",
        default=None,
        help=f"multi-chatbot 树形配置持久化，默认 {CONFIG_PATH}",
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    return parser


def main() -> int:
    args = _make_parser().parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    pool = OpenClawSessionPool(
        url=args.gateway_url,
        password=args.gateway_password,
        token=args.gateway_token,
        store_path=Path(args.pool_store) if args.pool_store else None,
        concurrency=args.concurrency,
    )

    config = MultiConfigStore(
        Path(args.config_path) if args.config_path else CONFIG_PATH
    )
    config.load()

    server = MultiChatbotServer(
        pool=pool, config=config, host=args.host, port=args.port
    )
    try:
        asyncio.run(server.run())
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
