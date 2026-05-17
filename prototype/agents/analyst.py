from __future__ import annotations

import json
from pathlib import Path

try:
    from langsmith import traceable as _traceable
except ImportError:
    def _traceable(**_kw):  # type: ignore[misc]
        def _wrap(fn): return fn
        return _wrap

from state import AgentArtifact, AgentInput, MemoryDelta

_ACCOUNTS_PATH = Path(__file__).resolve().parent.parent / "mocks" / "accounts.json"


def _infer_account_from_query(query: str) -> str | None:
    q = query.lower()
    for name in ("stripe", "notion", "linear"):
        if name in q:
            return name
    return None


def _platform_lookup(query: str, entities: dict[str, str]) -> dict | None:
    needle = (
        entities.get("account_name")
        or entities.get("account")
        or _infer_account_from_query(query)
        or ""
    ).lower()

    with _ACCOUNTS_PATH.open(encoding="utf-8") as f:
        accounts: list[dict] = json.load(f)

    for acct in accounts:
        if needle and needle in (acct.get("name") or "").lower():
            return acct
        if needle and needle in (acct.get("domain") or "").lower():
            return acct

    if not needle:
        q = query.lower()
        for acct in accounts:
            name = (acct.get("name") or "").lower()
            if name and name in q:
                return acct

    return None


@_traceable(name="run_analyst", run_type="tool")
def run_analyst(inp: AgentInput) -> tuple[AgentArtifact, list[MemoryDelta]]:
    row = _platform_lookup(inp.user_query, inp.entities)

    if not row:
        return AgentArtifact(
            agent_kind="analyst",
            step_id=f"{inp.trace_id}:analyst",
            payload={
                "snapshot":              None,
                "signals":               [],
                "suggested_next_question": "Which named account should we drill into?",
            },
            status="failed",
            error="No mock account matched query.",
        ), []

    artifact = AgentArtifact(
        agent_kind="analyst",
        step_id=f"{inp.trace_id}:analyst",
        payload={
            "snapshot": {
                "name":   row["name"],
                "domain": row["domain"],
                "stage":  row["stage"],
                "health": row["health"],
            },
            "signals":               row.get("signals", []),
            "suggested_next_question": row.get("suggested_next_question"),
        },
        status="ok",
    )

    deltas = [
        MemoryDelta(
            tier="M1",
            key="last_account_viewed",
            value=row["name"],
            reason="Captured last analyzed account for session hints.",
        ),
        MemoryDelta(
            tier="M2",
            key="last_account_viewed",
            value=row["name"],
            reason="Durable hint for cross-session account context.",
        ),
    ]
    return artifact, deltas
