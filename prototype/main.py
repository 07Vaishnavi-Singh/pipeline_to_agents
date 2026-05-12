#!/usr/bin/env python3
"""
main.py — runs the four PLAN §11 demo scenarios end-to-end (fully offline).

Each scenario demonstrates one of the three migration problem solutions:

  Scenario 1 — single agent, direct tool
    Shows: Problem C (tool isolation)
    recommend_play bypasses NL decompose, shallow-wraps into coordinator,
    runs Play Recommender only. Recommender uses only its own KB tool.

  Scenario 2 — sequential agents, ask_recepto
    Shows: Problem B (artifact slicing)
    Coordinator decomposes "recommend + create" → two-wave DAG.
    Creator receives only the recommendation artifact slice — not the
    full CoordinatorState. Memory writes are gated through synthesize_node.

  Scenario 3 — parallel agents, ask_recepto
    Shows: Problem D (DAG-based orchestration)
    "analyze account + recommend outreach" → no dependency between them →
    coordinator puts both in wave 1 → ThreadPoolExecutor runs them in parallel.

  Scenario 4 — partial DAG failure, ask_recepto
    Shows: Problem B (failure as data, not exception)
    "analyze AcmeCorp + create play" → AcmeCorp not in mocks → analyst fails →
    creator is skipped with a clear reason. No exception crashes the pipeline.
"""

from __future__ import annotations

from coordinator import MEMORY_STORE, run_request


def _divider(title: str) -> None:
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72 + "\n")


def main() -> None:
    session = "demo_session"

    # ------------------------------------------------------------------
    _divider("Scenario 1 — single agent (direct tool: recommend_play)")
    # ------------------------------------------------------------------
    run_request(
        "recommend a play for a fintech company with high intent signals",
        tool_name="recommend_play",
        session_id=session,
    )

    # ------------------------------------------------------------------
    _divider("Scenario 2 — sequential agents (ask_recepto)")
    # ------------------------------------------------------------------
    run_request(
        "recommend a play for a SaaS company and then create it",
        tool_name="ask_recepto",
        session_id=session,
    )

    # ------------------------------------------------------------------
    _divider("Scenario 3 — parallel agents (ask_recepto)")
    # ------------------------------------------------------------------
    run_request(
        "analyze Stripe's account and recommend an outreach strategy",
        tool_name="ask_recepto",
        session_id=session,
    )

    # ------------------------------------------------------------------
    _divider("Scenario 4 — partial DAG failure (ask_recepto)")
    # ------------------------------------------------------------------
    # AcmeCorp is not in mocks/accounts.json → analyst fails →
    # creator is skipped (depends on analyst via DEPENDENCY_SEQUENTIAL_FROMS)
    run_request(
        "analyze AcmeCorp's account and then create a play from it",
        tool_name="ask_recepto",
        session_id=session,
    )

    # ------------------------------------------------------------------
    _divider("M1 session memory snapshot (end of session)")
    # ------------------------------------------------------------------
    snap = MEMORY_STORE.snapshot(session)
    if snap:
        for k, v in snap.items():
            print(f"  {k}: {v}")
    else:
        print("  (empty)")


if __name__ == "__main__":
    main()
