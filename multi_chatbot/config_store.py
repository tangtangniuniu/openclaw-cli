"""多用户多会话预设的持久化存储。

树形结构：users[] → sessions[] → questions[]

- 每个节点带稳定 `id`（uuid hex），前端/后端可以据此做增量编辑
- 整棵树一次性读写到 `~/.openclaw-cli/multi-chatbot-config.json`
- 原子写（tmp 文件 + rename）
"""
from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


DEFAULT_PATH = Path.home() / ".openclaw-cli" / "multi-chatbot-config.json"
STORE_VERSION = 1


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _now() -> int:
    return int(time.time())


@dataclass(slots=True)
class QuestionNode:
    id: str
    text: str


@dataclass(slots=True)
class SessionNode:
    id: str
    name: str
    questions: list[QuestionNode] = field(default_factory=list)


@dataclass(slots=True)
class UserNode:
    id: str
    name: str
    sessions: list[SessionNode] = field(default_factory=list)


@dataclass(slots=True)
class ConfigTree:
    users: list[UserNode] = field(default_factory=list)
    updated_at: int = 0


class MultiConfigStore:
    """JSON 文件支撑的树形配置。非线程安全（asyncio 单线程使用）。"""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._tree = ConfigTree()

    # ------------------------------------------------------------------
    # 读写
    # ------------------------------------------------------------------

    def load(self) -> ConfigTree:
        if not self.path.exists():
            self._tree = ConfigTree()
            return self._tree
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        if raw.get("version") != STORE_VERSION:
            raise ValueError(f"unsupported multi-chatbot config version: {raw.get('version')!r}")
        users: list[UserNode] = []
        for u in raw.get("users") or []:
            if not isinstance(u, dict):
                continue
            sessions: list[SessionNode] = []
            for s in u.get("sessions") or []:
                if not isinstance(s, dict):
                    continue
                questions: list[QuestionNode] = []
                for q in s.get("questions") or []:
                    if not isinstance(q, dict):
                        continue
                    qid = str(q.get("id") or _new_id())
                    text = str(q.get("text") or "")
                    questions.append(QuestionNode(id=qid, text=text))
                sid = str(s.get("id") or _new_id())
                name = str(s.get("name") or f"Session {sid[:6]}")
                sessions.append(SessionNode(id=sid, name=name, questions=questions))
            uid = str(u.get("id") or _new_id())
            name = str(u.get("name") or f"user-{uid[:6]}")
            users.append(UserNode(id=uid, name=name, sessions=sessions))
        self._tree = ConfigTree(users=users, updated_at=int(raw.get("updated_at") or 0))
        return self._tree

    def save(self) -> None:
        self._tree.updated_at = _now()
        payload = {
            "version": STORE_VERSION,
            "updated_at": self._tree.updated_at,
            "users": [asdict(u) for u in self._tree.users],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_fd, tmp_name = tempfile.mkstemp(
            prefix=self.path.name, suffix=".tmp", dir=str(self.path.parent)
        )
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as fp:
                json.dump(payload, fp, ensure_ascii=False, indent=2)
            os.replace(tmp_name, self.path)
        except Exception:
            Path(tmp_name).unlink(missing_ok=True)
            raise

    # ------------------------------------------------------------------
    # 访问
    # ------------------------------------------------------------------

    @property
    def tree(self) -> ConfigTree:
        return self._tree

    def to_dict(self) -> dict[str, Any]:
        return {
            "updated_at": self._tree.updated_at,
            "users": [asdict(u) for u in self._tree.users],
        }

    def replace(self, payload: dict[str, Any]) -> ConfigTree:
        """用前端传来的整棵树覆盖当前状态并持久化。"""
        users_raw = payload.get("users")
        if not isinstance(users_raw, list):
            raise ValueError("users must be a list")
        users: list[UserNode] = []
        seen_uids: set[str] = set()
        for u in users_raw:
            if not isinstance(u, dict):
                continue
            uid = str(u.get("id") or _new_id())
            while uid in seen_uids:
                uid = _new_id()
            seen_uids.add(uid)
            name = str(u.get("name") or "").strip() or f"user-{uid[:6]}"
            sessions: list[SessionNode] = []
            seen_sids: set[str] = set()
            for s in u.get("sessions") or []:
                if not isinstance(s, dict):
                    continue
                sid = str(s.get("id") or _new_id())
                while sid in seen_sids:
                    sid = _new_id()
                seen_sids.add(sid)
                sname = str(s.get("name") or "").strip() or f"session-{sid[:6]}"
                questions: list[QuestionNode] = []
                seen_qids: set[str] = set()
                for q in s.get("questions") or []:
                    if not isinstance(q, dict):
                        continue
                    qid = str(q.get("id") or _new_id())
                    while qid in seen_qids:
                        qid = _new_id()
                    seen_qids.add(qid)
                    # 保留空占位（用户可能先建卡槽再慢慢填内容）；
                    # run.start 里会再次过滤掉 strip() 后为空的项。
                    text = str(q.get("text") or "")
                    questions.append(QuestionNode(id=qid, text=text))
                sessions.append(SessionNode(id=sid, name=sname, questions=questions))
            users.append(UserNode(id=uid, name=name, sessions=sessions))
        self._tree = ConfigTree(users=users, updated_at=_now())
        self.save()
        return self._tree


__all__ = [
    "ConfigTree",
    "DEFAULT_PATH",
    "MultiConfigStore",
    "QuestionNode",
    "SessionNode",
    "UserNode",
]
