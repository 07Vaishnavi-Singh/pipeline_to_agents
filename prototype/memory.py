from __future__ import annotations

import re
import sqlite3
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

from state import MemoryDelta


_PII_PATTERNS = [
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    re.compile(r"\b[\w._%+-]+@[\w.-]+\.[A-Za-z]{2,}\b"),
]


class DurableMemoryStore:
    def __init__(self, db_path: str | Path = "memory_m2.db") -> None:
        self._path = Path(db_path)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self._path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS memory (
                    session_id TEXT NOT NULL,
                    key        TEXT NOT NULL,
                    value      TEXT NOT NULL,
                    reason     TEXT,
                    updated_at REAL DEFAULT (unixepoch()),
                    PRIMARY KEY (session_id, key)
                )
            """)

    def write(self, session_id: str, key: str, value: str, reason: str = "") -> None:
        with sqlite3.connect(self._path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO memory "
                "(session_id, key, value, reason, updated_at) "
                "VALUES (?, ?, ?, ?, unixepoch())",
                (session_id, key, value, reason),
            )

    def read(self, session_id: str, key: str) -> str | None:
        with sqlite3.connect(self._path) as conn:
            row = conn.execute(
                "SELECT value FROM memory WHERE session_id = ? AND key = ?",
                (session_id, key),
            ).fetchone()
        return row[0] if row else None

    def snapshot(self, session_id: str) -> dict[str, str]:
        with sqlite3.connect(self._path) as conn:
            rows = conn.execute(
                "SELECT key, value FROM memory WHERE session_id = ? ORDER BY updated_at",
                (session_id,),
            ).fetchall()
        return {k: v for k, v in rows}

    def delete(self, session_id: str, key: str) -> None:
        with sqlite3.connect(self._path) as conn:
            conn.execute(
                "DELETE FROM memory WHERE session_id = ? AND key = ?",
                (session_id, key),
            )


@dataclass
class MemoryStore:
    max_entries_per_session: int = 5
    m2_db_path: str | Path | None = None
    _sessions: dict[str, OrderedDict[str, str]] = field(default_factory=dict)
    _durable:  DurableMemoryStore | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.m2_db_path is not None:
            self._durable = DurableMemoryStore(self.m2_db_path)

    def _bucket(self, session_id: str) -> OrderedDict[str, str]:
        if session_id not in self._sessions:
            self._sessions[session_id] = OrderedDict()
        return self._sessions[session_id]

    def commit_deltas(self, session_id: str | None, deltas: list[MemoryDelta]) -> list[MemoryDelta]:
        if not session_id or not deltas:
            return []
        accepted: list[MemoryDelta] = []
        bucket = self._bucket(session_id)

        for d in deltas:
            if _looks_like_pii(d.value):
                print(f"  [memory] REJECTED '{d.key}' — PII detected")
                continue

            if d.tier == "M1":
                bucket[d.key] = d.value
                while len(bucket) > self.max_entries_per_session:
                    bucket.popitem(last=False)
                accepted.append(d)

            elif d.tier == "M2":
                if self._durable is not None:
                    self._durable.write(session_id, d.key, d.value, d.reason)
                    accepted.append(d)
                    print(f"  [memory] M2 persisted '{d.key}' for session={session_id}")

        return accepted

    def snapshot(self, session_id: str | None) -> dict[str, str]:
        if not session_id:
            return {}
        return dict(self._bucket(session_id))

    def snapshot_m2(self, session_id: str | None) -> dict[str, str]:
        if not session_id or self._durable is None:
            return {}
        return self._durable.snapshot(session_id)


def _looks_like_pii(text: str) -> bool:
    return any(p.search(text) for p in _PII_PATTERNS)
