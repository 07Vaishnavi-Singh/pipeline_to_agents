#!/usr/bin/env python3
from __future__ import annotations

from coordinator import MEMORY_STORE, run_request


def _divider(title: str) -> None:
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72 + "\n")


def main() -> None:
    session = "demo_session"

    _divider("Scenario 1 — single agent (direct tool: recommend_play)")
    run_request(
        "recommend a play for a fintech company with high intent signals",
        tool_name="recommend_play",
        session_id=session,
    )

    _divider("Scenario 2 — sequential agents (ask_recepto)")
    run_request(
        "recommend a play for a SaaS company and then create it",
        tool_name="ask_recepto",
        session_id=session,
    )

    _divider("Scenario 3 — parallel agents (ask_recepto)")
    run_request(
        "analyze Stripe's account and recommend an outreach strategy",
        tool_name="ask_recepto",
        session_id=session,
    )

    _divider("Scenario 4 — partial DAG failure (ask_recepto)")
    run_request(
        "analyze AcmeCorp's account and then create a play from it",
        tool_name="ask_recepto",
        session_id=session,
    )

    _divider("M1 session memory snapshot (end of session)")
    snap = MEMORY_STORE.snapshot(session)
    if snap:
        for k, v in snap.items():
            print(f"  {k}: {v}")
    else:
        print("  (empty)")


if __name__ == "__main__":
    main()
