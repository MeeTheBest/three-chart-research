"""Small local audit log for user-authorized birthplace processing.

The log is deliberately separate from session memory: it makes a failed
location lookup reproducible after a local-server restart, while retaining
records for only the same one-day window as frozen reports.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Any


class LocalAuditLog:
    """Append-only JSONL audit records with bounded local retention."""

    def __init__(self, path: Path, *, retention: timedelta = timedelta(days=1)) -> None:
        if retention <= timedelta(0):
            raise ValueError("retention must be positive")
        self.path = path
        self.retention = retention
        self._lock = Lock()

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)

    @staticmethod
    def _parse_timestamp(value: object) -> datetime | None:
        if not isinstance(value, str):
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None else None

    def record(self, event: str, payload: dict[str, Any], *, now: datetime | None = None) -> None:
        if not event:
            raise ValueError("event is required")
        current = now or self._now()
        item = {"timestamp": current.isoformat().replace("+00:00", "Z"), "event": event, "payload": payload}
        encoded = json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self._lock:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(encoded)
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass

    def purge_expired(self, *, now: datetime | None = None) -> int:
        """Drop malformed and expired records; return how many records were removed."""
        current = now or self._now()
        with self._lock:
            records = self._read_unlocked()
            kept = [item for item in records if (timestamp := self._parse_timestamp(item.get("timestamp"))) and current - timestamp < self.retention]
            removed = len(records) - len(kept)
            if removed:
                self._rewrite_unlocked(kept)
            return removed

    def remove_session(self, session_id: str) -> int:
        """Remove durable birth/location records linked to a user-deleted session."""
        with self._lock:
            records = self._read_unlocked()
            kept = [item for item in records if item.get("payload", {}).get("sessionId") != session_id]
            removed = len(records) - len(kept)
            if removed:
                self._rewrite_unlocked(kept)
            return removed

    def _read_unlocked(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        records: list[dict[str, Any]] = []
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        for line in lines:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                records.append(value)
        return records

    def _rewrite_unlocked(self, records: list[dict[str, Any]]) -> None:
        if not records:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
            return
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text("".join(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n" for item in records), encoding="utf-8")
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        temporary.replace(self.path)
