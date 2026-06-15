from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class SessionRecord:
    id: str
    created_at: float
    updated_at: float
    messages: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class SessionStore:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def derive_session_id(system_prompt: str | None, first_user_message: str | None) -> str:
        seed = f"{system_prompt or ''}\n{first_user_message or ''}"
        digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
        return f"claude-{digest}"

    def _path(self, session_id: str) -> Path:
        safe = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in session_id)[:256]
        return self.root / f"{safe}.json"

    def get(self, session_id: str) -> SessionRecord | None:
        path = self._path(session_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return SessionRecord(
            id=str(data.get("id") or session_id),
            created_at=float(data.get("created_at") or time.time()),
            updated_at=float(data.get("updated_at") or time.time()),
            messages=list(data.get("messages") or []),
            metadata=dict(data.get("metadata") or {}),
        )

    def history(self, session_id: str, *, limit: int = 40) -> list[dict[str, str]]:
        record = self.get(session_id)
        if record is None:
            return []
        history: list[dict[str, str]] = []
        for msg in record.messages:
            if not isinstance(msg, dict):
                continue
            role = str(msg.get("role") or "")
            content = msg.get("content")
            if role in {"user", "assistant"} and isinstance(content, str) and content:
                history.append({"role": role, "content": content})
        return history[-limit:]

    def delete(self, session_id: str) -> bool:
        path = self._path(session_id)
        try:
            path.unlink()
            return True
        except FileNotFoundError:
            return False

    def upsert(
        self,
        session_id: str,
        *,
        append_messages: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SessionRecord:
        now = time.time()
        existing = self.get(session_id)
        if existing is None:
            record = SessionRecord(id=session_id, created_at=now, updated_at=now)
        else:
            record = existing
            record.updated_at = now
        if append_messages:
            record.messages.extend(append_messages)
            record.messages = record.messages[-40:]
        if metadata:
            record.metadata.update(metadata)
        payload = {
            "id": record.id,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
            "messages": record.messages,
            "metadata": record.metadata,
        }
        path = self._path(session_id)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:
            pass
        return record
