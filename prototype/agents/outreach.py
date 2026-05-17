from __future__ import annotations

import hashlib

try:
    from langsmith import traceable as _traceable
except ImportError:
    def _traceable(**_kw):  # type: ignore[misc]
        def _wrap(fn): return fn
        return _wrap

from state import AgentArtifact, AgentInput, MemoryDelta


def _analytics_fetch(query: str) -> dict:
    h = int(hashlib.sha256(query.encode()).hexdigest(), 16)
    return {
        "open_rate":          round(0.35 + (h % 40) / 100, 2),
        "reply_rate":         round(0.09 + (h % 20) / 100, 2),
        "meetings_booked_30d": int((h % 5) + 1),
    }


def _notion_lookup(_playbook_key: str) -> list[str]:
    return [
        "Founder-to-champion double touch (48h cadence)",
        "Intent-led hook + social proof block",
        "Compliance angle only if verified signal",
    ]


@_traceable(name="run_outreach", run_type="tool")
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
