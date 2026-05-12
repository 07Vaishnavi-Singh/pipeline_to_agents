"""M1 session memory — prototype: in-process dict, max 5 entries per session (PLAN §8)."""

from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass, field

from state import MemoryDelta


PII_PATTERNS = [
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),  # SSN-like
    re.compile(r"\b[\w._%+-]+@[\w.-]+\.[A-Za-z]{2,}\b"),  # email
]


@dataclass
class MemoryStore:
    """Ordered map session_id -> list of {key, value, reason} newest last, capped per session."""

    max_entries_per_session: int = 5
    _sessions: dict[str, OrderedDict[str, str]] = field(default_factory=dict)

    def _session_bucket(self, session_id: str) -> OrderedDict[str, str]:
        if session_id not in self._sessions:
            self._sessions[session_id] = OrderedDict()
        return self._sessions[session_id]

    def commit_deltas(self, session_id: str | None, deltas: list[MemoryDelta]) -> list[MemoryDelta]:
        if not session_id or not deltas:
            return []
        accepted: list[MemoryDelta] = []
        bucket = self._session_bucket(session_id)
        for d in deltas:
            if d.tier != "M1":
                continue
            if _looks_like_pii(d.value):
                continue
            bucket[d.key] = d.value
            while len(bucket) > self.max_entries_per_session:
                bucket.popitem(last=False)
            accepted.append(d)
        return accepted

    def snapshot(self, session_id: str | None) -> dict[str, str]:
        if not session_id:
            return {}
        return dict(self._session_bucket(session_id))


def _looks_like_pii(text: str) -> bool:
    return any(p.search(text) for p in PII_PATTERNS)
