"""
agents/analyst.py — Account Analyst agent (PLAN §7).

Problem C fix — tool ownership:
  _platform_lookup() is private to this agent.
  Only this agent reads mocks/accounts.json.

Failure behaviour (Problem B fix):
  When no account is found, the agent returns status="failed" with a
  clear error message — it does NOT raise an exception.
  The coordinator's _should_skip() detects the failed artifact and marks
  downstream dependent agents (e.g. creator) as "skipped" with a reason.
  This is how partial DAG failure works: explicit, observable, no crash.
"""

from __future__ import annotations

import json
from pathlib import Path

from state import AgentArtifact, AgentInput, MemoryDelta

# Private tool — owned exclusively by this agent.
_ACCOUNTS_PATH = Path(__file__).resolve().parent.parent / "mocks" / "accounts.json"


def _infer_account_from_query(query: str) -> str | None:
    """Heuristic: extract a known account name from free text."""
    q = query.lower()
    for name in ("stripe", "notion", "linear"):
        if name in q:
            return name
    return None


def _platform_lookup(query: str, entities: dict[str, str]) -> dict | None:
    """
    Look up an account in mocks/accounts.json.
    In production: calls Recepto's platform APIs.

    Lookup priority:
      1. entities["account_name"] (extracted by coordinator's decompose node)
      2. _infer_account_from_query (local heuristic for known names)
      3. Substring search (when no explicit account name was detected at all)
      4. None — fail cleanly if nothing matched
    """
    needle = (
        entities.get("account_name")
        or entities.get("account")
        or _infer_account_from_query(query)
        or ""
    ).lower()

    with _ACCOUNTS_PATH.open(encoding="utf-8") as f:
        accounts: list[dict] = json.load(f)

    # Direct match on name or domain
    for acct in accounts:
        if needle and needle in (acct.get("name") or "").lower():
            return acct
        if needle and needle in (acct.get("domain") or "").lower():
            return acct

    # If no explicit needle was found, try substring search against the raw query.
    # This covers edge cases like "what's going on at stripe.com?" where the
    # coordinator didn't extract an entity but the query mentions a known account.
    if not needle:
        q = query.lower()
        for acct in accounts:
            name = (acct.get("name") or "").lower()
            if name and name in q:
                return acct

    # No match — return None so run_analyst sets status="failed"
    return None


def run_analyst(inp: AgentInput) -> tuple[AgentArtifact, list[MemoryDelta]]:
    row = _platform_lookup(inp.user_query, inp.entities)

    if not row:
        # Clean failure — no exception, just a failed artifact with a reason.
        # Downstream agents that depend on this artifact will be skipped
        # (see _should_skip in coordinator.py).
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

    deltas = [MemoryDelta(
        tier="M1",
        key="last_account_viewed",
        value=row["name"],
        reason="Captured last analyzed account for session hints.",
    )]
    return artifact, deltas
