"""chatbot 前端专用的「用户拥有的 session 列表」持久化。

与 `openclaw_client.pool.SessionStore` 的职责分离：
- `SessionStore`：记录每个用户当前绑定到哪个 OpenClaw sessionKey
- `UserSessionsStore`：记录每个用户创建过的所有 chat_session（id + 名称 + 时间）
"""
from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path


DEFAULT_PATH = Path.home() / ".openclaw-cli" / "user-sessions.json"
STORE_VERSION = 1


@dataclass(slots=True)
class ChatSession:
    id: str
    name: str
    created_at: int
    updated_at: int


class UserSessionsStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._data: dict[str, list[ChatSession]] = {}

    def load(self) -> None:
        if not self.path.exists():
            self._data = {}
            return
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        if raw.get("version") != STORE_VERSION:
            raise ValueError(f"unsupported user-sessions version: {raw.get('version')!r}")
        users = raw.get("users") or {}
        out: dict[str, list[ChatSession]] = {}
        for user, items in users.items():
            if not isinstance(items, list):
                continue
            sessions = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                try:
                    sessions.append(
                        ChatSession(
                            id=str(item["id"]),
                            name=str(item.get("name") or item["id"]),
                            created_at=int(item.get("created_at") or 0),
                            updated_at=int(item.get("updated_at") or 0),
                        )
                    )
                except KeyError:
                    continue
            out[user] = sessions
        self._data = out

    def save(self) -> None:
        payload = {
            "version": STORE_VERSION,
            "users": {
                user: [asdict(s) for s in sessions]
                for user, sessions in self._data.items()
            },
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

    def list(self, user: str) -> list[ChatSession]:
        return list(self._data.get(user, []))

    def get(self, user: str, session_id: str) -> ChatSession | None:
        for s in self._data.get(user, []):
            if s.id == session_id:
                return s
        return None

    def create(self, user: str, name: str | None = None) -> ChatSession:
        session_id = uuid.uuid4().hex[:12]
        now = int(time.time())
        display = (name or f"Session {session_id[:6]}").strip() or f"Session {session_id[:6]}"
        session = ChatSession(id=session_id, name=display, created_at=now, updated_at=now)
        self._data.setdefault(user, []).insert(0, session)
        self.save()
        return session

    def rename(self, user: str, session_id: str, name: str) -> ChatSession | None:
        for s in self._data.get(user, []):
            if s.id == session_id:
                s.name = name.strip() or s.name
                s.updated_at = int(time.time())
                self.save()
                return s
        return None

    def touch(self, user: str, session_id: str) -> None:
        for s in self._data.get(user, []):
            if s.id == session_id:
                s.updated_at = int(time.time())
                self.save()
                return

    def delete(self, user: str, session_id: str) -> bool:
        sessions = self._data.get(user)
        if not sessions:
            return False
        for i, s in enumerate(sessions):
            if s.id == session_id:
                del sessions[i]
                if not sessions:
                    self._data.pop(user, None)
                self.save()
                return True
        return False

    def users(self) -> list[str]:
        return sorted(self._data.keys())


__all__ = ["ChatSession", "UserSessionsStore", "DEFAULT_PATH"]
