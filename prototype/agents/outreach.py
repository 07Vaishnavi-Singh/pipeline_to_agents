"""
agents/outreach.py — Outreach Strategist agent (PLAN §7).

Problem C fix — tool ownership:
  _analytics_fetch() and _notion_lookup() are private to this agent.
  No other agent calls these functions.

Note: analytics uses a hash of the query for deterministic-but-varied
mock numbers — useful for demos where you run the same query twice
and expect consistent output.
"""

from __future__ import annotations

import hashlib

from state import AgentArtifact, AgentInput, MemoryDelta


def _analytics_fetch(query: str) -> dict:
    """
    Mock analytics fetch.
    In production: calls Recepto analytics API for outreach performance data.
    Uses a stable hash so the same query always returns the same numbers.
    """
    h = int(hashlib.sha256(query.encode()).hexdigest(), 16)
    return {
        "open_rate":          round(0.35 + (h % 40) / 100, 2),
        "reply_rate":         round(0.09 + (h % 20) / 100, 2),
        "meetings_booked_30d": int((h % 5) + 1),
    }


def _notion_lookup(_playbook_key: str) -> list[str]:
    """
    Mock Notion playbook lookup.
    In production: reads from the Recepto Notion workspace via Notion MCP.
    """
    return [
        "Founder-to-champion double touch (48h cadence)",
        "Intent-led hook + social proof block",
        "Compliance angle only if verified signal",
    ]


def run_outreach(inp: AgentInput) -> tuple[AgentArtifact, list[MemoryDelta]]:
    perf      = _analytics_fetch(inp.user_query)
    playbooks = _notion_lookup("gtm_core")

    artifact = AgentArtifact(
        agent_kind="outreach",
        step_id=f"{inp.trace_id}:outreach",
        payload={
            "strategies": [
                {
                    "name":           playbooks[0],
                    "channels":       ["Email", "LinkedIn"],
                    "predicted_lift": perf["reply_rate"],
                },
                {
                    "name":           playbooks[1],
                    "channels":       ["Email"],
                    "predicted_lift": perf["open_rate"],
                },
            ],
            "rationale":          "Mock analytics + static Notion playbook lines (prototype offline).",
            "risks":              ["Low sample size — numbers are deterministic placeholders."],
            "analytics_snapshot": perf,
        },
        status="ok",
    )

    deltas = [MemoryDelta(
        tier="M1",
        key="outreach_motion_bias",
        value="dual_touch",
        reason="Preferred motion when outreach strategy requested.",
    )]
    return artifact, deltas
