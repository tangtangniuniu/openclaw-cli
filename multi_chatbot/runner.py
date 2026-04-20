"""并发车道执行器：把每个 (用户, 会话) 当成一条独立车道。

- 每条车道按顺序串行提问自己的 questions（同一个 gateway session 本来
  就不支持多请求交错）。
- 不同车道之间的请求通过 `OpenClawSessionPool` 共用一条 WS，由
  `FairScheduler` 控制总并发，排队部分正是本应用要暴露给前端的
  「阻塞中」指标。
- 执行过程中通过 `ProgressCallback` 推送状态事件给前端。
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from openclaw_client.client import OpenClawGatewayClient
from openclaw_client.pool import OpenClawSessionPool, PoolError, derive_session_key


_logger = logging.getLogger("openclaw.multi_chatbot.runner")

ProgressCallback = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass(slots=True)
class LaneSpec:
    user: str
    session_id: str
    session_name: str
    questions: list[str]
    session_key: str = ""

    def key(self) -> str:
        return f"{self.user}::{self.session_id}"


@dataclass(slots=True)
class LaneState:
    spec: LaneSpec
    total: int
    done: int = 0
    status: str = "pending"  # pending / running / blocked / finished / error / cancelled
    current_question: str | None = None
    error: str | None = None
    started_at: float = 0.0
    updated_at: float = 0.0
    transcript: list[dict[str, Any]] = field(default_factory=list)


class ConcurrentRunner:
    """并发驱动多条车道，负责推送状态。"""

    def __init__(
        self,
        *,
        pool: OpenClawSessionPool,
        progress: ProgressCallback,
        stats_interval: float = 0.5,
    ) -> None:
        self._pool = pool
        self._progress = progress
        self._stats_interval = stats_interval
        self._lanes: dict[str, LaneState] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._stats_task: asyncio.Task[None] | None = None
        self._running = False
        self._stop_event = asyncio.Event()

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._running

    def lanes_snapshot(self) -> list[dict[str, Any]]:
        return [_lane_to_dict(state) for state in self._lanes.values()]

    async def start(self, specs: list[LaneSpec]) -> None:
        if self._running:
            raise RuntimeError("runner already running; stop() first")
        if not specs:
            raise ValueError("at least one lane is required")
        self._running = True
        self._stop_event.clear()
        self._lanes = {}
        self._tasks = {}
        now = time.time()
        for spec in specs:
            if not spec.session_key:
                spec.session_key = derive_session_key(spec.user, spec.session_id)
            total = len(spec.questions)
            self._lanes[spec.key()] = LaneState(
                spec=spec, total=total, started_at=now, updated_at=now
            )
        await self._progress(
            {
                "op": "run.started",
                "lanes": [_lane_to_dict(state) for state in self._lanes.values()],
            }
        )
        for key, state in self._lanes.items():
            self._tasks[key] = asyncio.create_task(
                self._run_lane(state), name=f"lane-{key}"
            )
        self._stats_task = asyncio.create_task(self._stats_loop(), name="runner-stats")

    async def stop(self) -> None:
        if not self._running:
            return
        self._stop_event.set()
        for task in self._tasks.values():
            task.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        if self._stats_task is not None:
            self._stats_task.cancel()
            try:
                await self._stats_task
            except (asyncio.CancelledError, Exception):
                pass
            self._stats_task = None
        self._running = False
        await self._progress({"op": "run.stopped", "lanes": self.lanes_snapshot()})

    async def wait_done(self) -> None:
        if not self._tasks:
            return
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)

    # ------------------------------------------------------------------
    # 单条车道
    # ------------------------------------------------------------------

    async def _run_lane(self, state: LaneState) -> None:
        key = state.spec.key()
        try:
            for index, question in enumerate(state.spec.questions):
                if self._stop_event.is_set():
                    state.status = "cancelled"
                    state.updated_at = time.time()
                    await self._emit_lane(state)
                    return
                state.status = "blocked"
                state.current_question = question
                state.updated_at = time.time()
                await self._emit_lane(
                    state,
                    extra={
                        "op": "lane.question.pending",
                        "index": index,
                        "question": question,
                    },
                )

                req_id = uuid.uuid4().hex[:8]
                send_started_at = time.time()
                try:
                    async with _slot_watch(self._pool, state, self._emit_lane):
                        send_started_at = time.time()
                        result = await self._pool.send_with_key(
                            state.spec.session_key,
                            question,
                            slot_key=key,
                        )
                except asyncio.CancelledError:
                    raise
                except PoolError as exc:
                    state.status = "error"
                    state.error = f"pool error: {exc}"
                    state.updated_at = time.time()
                    state.transcript.append(
                        {
                            "index": index,
                            "req_id": req_id,
                            "question": question,
                            "error": str(exc),
                            "timestamp": int(state.updated_at * 1000),
                        }
                    )
                    await self._emit_lane(
                        state,
                        extra={
                            "op": "lane.question.error",
                            "index": index,
                            "req_id": req_id,
                            "message": str(exc),
                        },
                    )
                    return
                except Exception as exc:  # noqa: BLE001
                    _logger.exception("lane %s question %d failed", key, index)
                    state.status = "error"
                    state.error = repr(exc)
                    state.updated_at = time.time()
                    await self._emit_lane(
                        state,
                        extra={
                            "op": "lane.question.error",
                            "index": index,
                            "req_id": req_id,
                            "message": repr(exc),
                        },
                    )
                    return

                duration_ms = int((time.time() - send_started_at) * 1000)
                reply = result.reply_text or OpenClawGatewayClient.extract_reply_text(
                    result.events
                )
                state.done = index + 1
                state.current_question = None
                state.updated_at = time.time()
                state.status = (
                    "finished" if state.done >= state.total else "running"
                )
                state.transcript.append(
                    {
                        "index": index,
                        "req_id": req_id,
                        "question": question,
                        "reply": reply,
                        "duration_ms": duration_ms,
                        "timestamp": int(state.updated_at * 1000),
                    }
                )
                await self._emit_lane(
                    state,
                    extra={
                        "op": "lane.question.reply",
                        "index": index,
                        "req_id": req_id,
                        "question": question,
                        "reply": reply,
                        "duration_ms": duration_ms,
                    },
                )
            if state.status != "finished":
                state.status = "finished"
                state.updated_at = time.time()
                await self._emit_lane(state)
        except asyncio.CancelledError:
            if state.status not in {"finished", "error"}:
                state.status = "cancelled"
                state.updated_at = time.time()
                try:
                    await self._emit_lane(state)
                except Exception:
                    pass
            raise
        except Exception as exc:  # noqa: BLE001
            _logger.exception("lane %s crashed", key)
            state.status = "error"
            state.error = repr(exc)
            state.updated_at = time.time()
            await self._emit_lane(state)

    # ------------------------------------------------------------------
    # 统计循环
    # ------------------------------------------------------------------

    async def _stats_loop(self) -> None:
        try:
            while not self._stop_event.is_set():
                await self._emit_stats()
                # 所有 lane 都终结了就退出
                if self._tasks and all(t.done() for t in self._tasks.values()):
                    await self._emit_stats()
                    break
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(), timeout=self._stats_interval
                    )
                except asyncio.TimeoutError:
                    continue
        except asyncio.CancelledError:
            raise
        except Exception:
            _logger.exception("stats loop crashed")

    async def _emit_stats(self) -> None:
        stats = self._pool.scheduler_stats()
        await self._progress(
            {
                "op": "stats",
                "scheduler": stats,
                "lanes": self.lanes_snapshot(),
                "running": self._running,
            }
        )

    async def _emit_lane(
        self,
        state: LaneState,
        *,
        extra: dict[str, Any] | None = None,
    ) -> None:
        lane_payload = _lane_to_dict(state)
        await self._progress({"op": "lane", "lane": lane_payload})
        if extra is not None:
            payload = dict(extra)
            payload["key"] = state.spec.key()
            await self._progress(payload)


class _slot_watch:
    """进入/离开 scheduler.slot 时把 lane 状态在 blocked/running 间切换。

    `OpenClawSessionPool.send_with_key` 内部自己 acquire slot；我们做不到
    拦截，只能把 blocked→running 这一步做成「在 send_with_key 返回前
    很快地设一次 running」，通过 stats 周期再同步真实 active/queued。
    """

    def __init__(
        self,
        pool: OpenClawSessionPool,
        state: LaneState,
        emit: Callable[..., Awaitable[None]],
    ) -> None:
        self._pool = pool
        self._state = state
        self._emit = emit

    async def __aenter__(self) -> "_slot_watch":
        # 真实的 blocked→running 发生在 send_with_key 内部；这里把状态先翻
        # 成 running 对前端是一个乐观呈现，stats 周期会纠偏。
        self._state.status = "running"
        self._state.updated_at = time.time()
        await self._emit(self._state)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


def _lane_to_dict(state: LaneState) -> dict[str, Any]:
    return {
        "key": state.spec.key(),
        "user": state.spec.user,
        "session_id": state.spec.session_id,
        "session_name": state.spec.session_name,
        "session_key": state.spec.session_key,
        "total": state.total,
        "done": state.done,
        "status": state.status,
        "current_question": state.current_question,
        "error": state.error,
        "started_at": int(state.started_at * 1000),
        "updated_at": int(state.updated_at * 1000),
    }


__all__ = ["ConcurrentRunner", "LaneSpec", "LaneState", "ProgressCallback"]
