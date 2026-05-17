from __future__ import annotations

try:
    from langsmith import traceable as _traceable
except ImportError:
    def _traceable(**_kw):  # type: ignore[misc]
        def _wrap(fn): return fn
        return _wrap

from state import AgentArtifact, AgentInput, MemoryDelta


def _icp_from(goal: str) -> dict:
    g = goal.lower()
    verticals = (
        ["fintech"]   if "fintech" in g else
        ["saas"]      if "saas"    in g else
        ["b2b saas"]
    )
    intent_signals = (
        ["Funding event", "Hiring spike"] if ("intent" in g or "signal" in g)
        else ["Product launch noise", "Champion churn risk"]
    )
    return {
        "icp": {
            "personas":        ["RevOps Leader", "Head of Growth"],
            "company_size":    "200–2000 employees",
            "regions":         ["NA", "EU"],
            "verticals":       verticals,
            "pain_hypothesis": "Pipeline conversion and outbound efficiency",
        },
        "intent_signals": intent_signals,
    }


@_traceable(name="run_creator", run_type="tool")
def run_creator(inp: AgentInput) -> tuple[AgentArtifact, list[MemoryDelta]]:
    warnings: list[str] = []

    rec_artifact  = inp.artifacts.get("recommender")
    acct_artifact = inp.artifacts.get("analyst")

    seed_play_id: str | None = None
    if rec_artifact and rec_artifact.get("status") == "ok":
        candidates   = rec_artifact.get("payload", {}).get("candidates") or []
        seed_play_id = candidates[0]["id"] if candidates else None
    else:
        warnings.append("No recommendation artifact — drafting play from query text only.")

    icp_bundle    = _icp_from(inp.user_query)
    account_signals: list[str] = []
    if acct_artifact and acct_artifact.get("status") == "ok":
        account_signals = acct_artifact.get("payload", {}).get("signals") or []

    play_object = {
        "title":                   f"Draft play seeded from `{seed_play_id}`" if seed_play_id else "Draft play from query",
        "icp":                     icp_bundle["icp"],
        "intent_signals":          icp_bundle["intent_signals"],
        "account_context_signals": account_signals,
        "seed_play_id":            seed_play_id,
    }

    artifact = AgentArtifact(
        agent_kind="creator",
        step_id=f"{inp.trace_id}:creator",
        payload={
            "play_id":            "mock_play_" + inp.trace_id[:8],
            "play_object":        play_object,
            "validation_warnings": warnings,
        },
        status="ok",
    )

    deltas = [MemoryDelta(
        tier="M1",
        key="last_draft_goal",
        value=inp.user_query[:180],
        reason="Captured last play drafting goal for session continuity.",
    )]
    return artifact, deltas
