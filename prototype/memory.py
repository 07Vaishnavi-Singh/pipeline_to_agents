"""
memory.py — M1 session memory store (PLAN §8).

Design principle (Problem B fix):
  Agents NEVER write here directly. They return MemoryDelta proposals.
  The coordinator's synthesize_node calls commit_deltas() after reviewing
  each delta for PII and cap limits. This makes every memory write:
    - Observable (you can log exactly what was committed and why)
    - Attributable (you know which agent proposed it)
    - Controlled (coordinator can reject it without the agent knowing)

Memory tiers in this prototype:
  M0 — ephemeral DAG state (LangGraph CoordinatorState, gone after run)
  M1 — session store (this file, in-process dict, capped at 5 entries)
  M2 — durable hints (out of scope for prototype)
"""

from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass, field

from state import MemoryDelta


# PII patterns — prototype-level check. Production would use a proper scrubber.
_PII_PATTERNS = [
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),               # SSN-like
    re.compile(r"\b[\w._%+-]+@[\w.-]+\.[A-Za-z]{2,}\b"), # email
]


@dataclass
class MemoryStore:
    """
    Ordered map: session_id → {key: value}, newest entries last.
    When the cap is exceeded, the oldest entry is evicted (LRU-ish).

    Only the coordinator calls commit_deltas(). Agents are never given
    a reference to this object.
    """

    max_entries_per_session: int = 5
    _sessions: dict[str, OrderedDict[str, str]] = field(default_factory=dict)

    def _bucket(self, session_id: str) -> OrderedDict[str, str]:
        if session_id not in self._sessions:
            self._sessions[session_id] = OrderedDict()
        return self._sessions[session_id]

    def commit_deltas(self, session_id: str | None, deltas: list[MemoryDelta]) -> list[MemoryDelta]:
        """
        Coordinator calls this once per request in synthesize_node.
        Returns the list of deltas that were actually committed.
        """
        if not session_id or not deltas:
            return []
        accepted: list[MemoryDelta] = []
        bucket = self._bucket(session_id)
        for d in deltas:
            if d.tier != "M1":
                # M2 writes are out of scope — silently skip
                continue
            if _looks_like_pii(d.value):
                print(f"  [memory] REJECTED '{d.key}' — PII detected")
                continue
            bucket[d.key] = d.value
            # Evict oldest when over cap (keeps memory bounded)
            while len(bucket) > self.max_entries_per_session:
                bucket.popitem(last=False)
            accepted.append(d)
        return accepted

    def snapshot(self, session_id: str | None) -> dict[str, str]:
        """Read-only view of current session memory."""
        if not session_id:
            return {}
        return dict(self._bucket(session_id))


def _looks_like_pii(text: str) -> bool:
    return any(p.search(text) for p in _PII_PATTERNS)
