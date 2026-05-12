"""
agents/recommender.py — Play Recommender agent (PLAN §7).

Problem C fix — tool ownership:
  _kb_search() is defined here and used only here.
  No other agent imports this function or reads plays.json directly.
  If the coordinator needs this agent's output, it gets it through the
  AgentArtifact returned to CoordinatorState — never via a direct import.
"""

from __future__ import annotations

import json
from pathlib import Path

from state import AgentArtifact, AgentInput, MemoryDelta

# Private tool — owned exclusively by this agent.
_PLAYS_PATH = Path(__file__).resolve().parent.parent / "mocks" / "plays.json"


def _kb_search(query: str) -> list[dict]:
    """
    Keyword-scored search over the mock Recepto KB.
    In production: vector search or Recepto's real KB API.
    """
    text = query.lower()
    with _PLAYS_PATH.open(encoding="utf-8") as f:
        plays: list[dict] = json.load(f)

    scored: list[tuple[int, dict]] = []
    for play in plays:
        blob = " ".join([
            play.get("title", ""),
            " ".join(play.get("tags", [])),
            " ".join(play.get("signals", [])),
            play.get("summary", ""),
        ]).lower()
        score = sum(1 for tok in set(text.split()) if len(tok) > 2 and tok in blob)
        # Tag exact-match bonus — tags are the strongest signal
        if any(t in text for t in play.get("tags", [])):
            score += 2
        scored.append((score, play))

    scored.sort(key=lambda x: x[0], reverse=True)
    # Return plays with a positive score; fall back to top 2 if nothing matched
    return [p for s, p in scored if s > 0] or [p for _, p in scored[:2]]


def run_recommender(inp: AgentInput) -> tuple[AgentArtifact, list[MemoryDelta]]:
    candidates = _kb_search(inp.user_query)
    top = candidates[:3]

    artifact = AgentArtifact(
        agent_kind="recommender",
        step_id=f"{inp.trace_id}:recommender",
        payload={
            "candidates":     top,
            "rationale_short": "Ranked mock KB plays by keyword overlap with the query and tag hints.",
            "citations":       [f"mock:plays.json:{p.get('id')}" for p in top],
        },
        status="ok",
    )

    # Propose M1 memory delta — coordinator will decide whether to commit
    deltas: list[MemoryDelta] = []
    industry = inp.entities.get("industry")
    if industry:
        deltas.append(MemoryDelta(
            tier="M1",
            key="preferred_industry",
            value=industry,
            reason="User mentioned an industry while requesting play recommendations.",
        ))

    return artifact, deltas
