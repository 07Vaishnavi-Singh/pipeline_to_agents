"""
coordinator.py — LangGraph StateGraph coordinator (PLAN §2–§6).

This is the core of the prototype. It demonstrates all three problem solutions:

  Problem B (shared state):
    decompose_node, run_wave_node, synthesize_node all operate on
    CoordinatorState. Agents never touch this state directly — they receive
    an AgentInput slice and return (AgentArtifact, list[MemoryDelta]).

  Problem C (tool bleed):
    Each agent is a plain function (run_recommender, run_creator, etc.).
    The coordinator calls them via AGENT_RUNNERS. Agents don't import each
    other and don't share a tool registry.

  Problem D (parallel vs sequential):
    _build_execution_waves() reads DEPENDENCY_SEQUENTIAL_FROMS and runs
    a topological sort to produce execution waves. Agents in the same wave
    run in parallel via ThreadPoolExecutor. Waves run sequentially.

Graph shape:
  START → decompose → build_dag → run_wave ⟲ → synthesize → END
                                   (loops until all waves done)
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Literal
import re

from langgraph.graph import END, START, StateGraph

from agents.analyst import run_analyst
from agents.creator import run_creator
from agents.outreach import run_outreach
from agents.recommender import run_recommender
from memory import MemoryStore
from state import (
    DEPENDENCY_SEQUENTIAL_FROMS,
    DIRECT_TOOL_TO_INTENT,
    INTENT_TO_AGENT_KIND,
    AgentArtifact,
    AgentInput,
    CoordinatorState,
    IntentKind,
    MemoryDelta,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Stable ordering for intents — used when sorting within a wave so trace output
# is deterministic (important for readable logs during the interview demo).
_INTENT_STAGE_ORDER = [
    IntentKind.RECOMMEND_PLAY.value,
    IntentKind.ANALYZE_ACCOUNT.value,
    IntentKind.CREATE_PLAY.value,
    IntentKind.RECOMMEND_OUTREACH.value,
]
_INTENT_RANK = {k: i for i, k in enumerate(_INTENT_STAGE_ORDER)}

# Reverse map: agent_kind → intent string (used in _should_skip).
_KIND_TO_INTENT = {v: k for k, v in INTENT_TO_AGENT_KIND.items()}

# Global memory store — one instance per process (M1 scope = session).
MEMORY_STORE = MemoryStore()

# Agent runner registry (Problem C: coordinator dispatches, agents don't call each other).
AGENT_RUNNERS: dict[str, Callable[[AgentInput], tuple[AgentArtifact, list[MemoryDelta]]]] = {
    "recommender": run_recommender,
    "creator":     run_creator,
    "outreach":    run_outreach,
    "analyst":     run_analyst,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _trace(state: CoordinatorState) -> str:
    return state["request"]["trace_id"]


def _dedupe_stable(items: list[str]) -> list[str]:
    """Remove duplicates while preserving order."""
    seen: set[str] = set()
    return [x for x in items if not (x in seen or seen.add(x))]  # type: ignore[func-returns-value]


def _extract_entities(query: str) -> dict[str, str]:
    """
    Rule-based entity extractor (no LLM for prototype).
    In production this would be an LLM call or a NER model.
    """
    q = query.lower()
    ent: dict[str, str] = {}
    if "fintech" in q:
        ent["industry"] = "fintech"
    elif "saas" in q:
        ent["industry"] = "saas"
    if m := re.search(r"\b(stripe|notion|linear)\b", q, re.I):
        ent["account_name"] = m.group(1).title()
    return ent


# ---------------------------------------------------------------------------
# Node 1: decompose
# ---------------------------------------------------------------------------

def _decompose_natural_language(query: str) -> tuple[list[str], dict[str, str]]:
    """
    Rule-based NL classifier → (ordered intent list, entities).

    Routing logic mirrors the dependency table: if recommend+create are
    both present, RECOMMEND must precede CREATE (enforced in build_dag via
    DEPENDENCY_SEQUENTIAL_FROMS). Analyst+outreach have no dependency so
    they land in the same wave in build_dag.
    """
    q = query.lower()
    intents: set[str] = set()
    entities = _extract_entities(query)

    outreach_cues = any(w in q for w in ["outreach strategy", "outreach ", "linkedin", "messaging strategy", "email sequence"])
    analyze_cues  = ("analyze " in q and "account" in q) or "account health" in q
    recommend_cues = any(w in q for w in ["recommend a play", "which play", "suggest a play", "play for"])
    create_cues    = any(w in q for w in ["create a play", "create it", "draft a play", "build a play", "then create"])

    if outreach_cues and ("stripe" in q or "analyze" in q or "account" in q):
        # Scenario 3: parallel — analyst + outreach have no dependency
        intents.add(IntentKind.ANALYZE_ACCOUNT.value)
        intents.add(IntentKind.RECOMMEND_OUTREACH.value)
    elif recommend_cues and create_cues:
        # Scenario 2: sequential — recommend must finish before create
        intents.add(IntentKind.RECOMMEND_PLAY.value)
        intents.add(IntentKind.CREATE_PLAY.value)
    elif analyze_cues and create_cues:
        # Scenario 4: sequential — analyst must finish before create
        intents.add(IntentKind.ANALYZE_ACCOUNT.value)
        intents.add(IntentKind.CREATE_PLAY.value)
    elif outreach_cues:
        intents.add(IntentKind.RECOMMEND_OUTREACH.value)
    elif analyze_cues:
        intents.add(IntentKind.ANALYZE_ACCOUNT.value)
    elif recommend_cues:
        intents.add(IntentKind.RECOMMEND_PLAY.value)
        if create_cues:
            intents.add(IntentKind.CREATE_PLAY.value)
    elif create_cues:
        intents.add(IntentKind.CREATE_PLAY.value)
    else:
        # Default: recommend a play
        intents.add(IntentKind.RECOMMEND_PLAY.value)

    ordered = sorted(intents, key=lambda i: _INTENT_RANK.get(i, 99))
    return ordered, entities


def decompose_node(state: CoordinatorState) -> CoordinatorState:
    """
    Entry node for every request (direct tool or ask_recepto).

    Direct tool: intent is already known → skip NL decompose, build single-node DAG.
    ask_recepto: run NL classifier → may produce 1–4 intents.

    Both paths go through the coordinator so every request gets a trace_id
    and produces artifacts in CoordinatorWorkingMemory.
    """
    req = state["request"]
    tool = req["tool_name"]
    print(f"[trace_id={req['trace_id']}] decompose ← tool={tool}")

    entities = _extract_entities(req["user_query"])

    if tool != "ask_recepto":
        # Direct tool: shallow-wrap — intent is pre-known, no NL needed
        intent = DIRECT_TOOL_TO_INTENT.get(tool)
        if intent is None:
            raise ValueError(f"Unknown direct tool: `{tool}`")
        intents_list = _dedupe_stable([intent])
        print(f"[trace_id={req['trace_id']}] decompose → intents (direct): {intents_list}")
        return CoordinatorState(**{**dict(state), "intents": intents_list, "entities": entities})

    intents_list, nl_entities = _decompose_natural_language(req["user_query"])
    merged = {**entities, **nl_entities}
    print(f"[trace_id={req['trace_id']}] decompose → intents (NL): {intents_list} entities={merged}")
    return CoordinatorState(**{**dict(state), "intents": intents_list, "entities": merged})


# ---------------------------------------------------------------------------
# Node 2: build_dag
# ---------------------------------------------------------------------------

def _build_execution_waves(intents: list[str], trace: str) -> tuple[list[list[str]], list[dict]]:
    """
    Topological sort over DEPENDENCY_SEQUENTIAL_FROMS → execution waves.

    Each wave is a list of agent_kinds that can run in parallel.
    Waves themselves run sequentially (wave N+1 starts only after wave N completes).

    Example — intents=[recommend_play, create_play]:
      prereqs: create_play depends on recommend_play
      Wave 1: [recommender]   ← no prerequisites
      Wave 2: [creator]       ← waits for recommender

    Example — intents=[analyze_account, recommend_outreach]:
      prereqs: none (neither depends on the other)
      Wave 1: [analyst, outreach]  ← both independent, run in parallel
    """
    intents_u = _dedupe_stable(intents)
    intent_set = set(intents_u)

    # Build prerequisite map from the dependency table
    prereq: dict[str, set[str]] = {i: set() for i in intents_u}
    for (left, right), is_sequential in DEPENDENCY_SEQUENTIAL_FROMS.items():
        if is_sequential and left in intent_set and right in intent_set:
            prereq.setdefault(right, set()).add(left)

    placed: set[str] = set()
    waves_agents: list[list[str]] = []
    serialized: list[dict] = []
    safety = 0  # guard against infinite loop from a malformed dependency table

    while len(placed) < len(intent_set):
        safety += 1
        if safety > 32:
            raise RuntimeError(f"DAG cycle or unsatisfiable intents: {intents_u}")

        # All intents whose prerequisites are already placed
        layer = [
            x for x in sorted(intent_set, key=lambda k: _INTENT_RANK.get(k, 99))
            if x not in placed and prereq.get(x, set()).issubset(placed)
        ]
        if not layer:
            raise RuntimeError(f"Unsatisfiable dependency — check DEPENDENCY_SEQUENTIAL_FROMS: {intents_u}")

        agent_layer = sorted(
            [INTENT_TO_AGENT_KIND[i] for i in layer],
            key=lambda k: _INTENT_RANK.get(_KIND_TO_INTENT[k], 99),
        )
        waves_agents.append(agent_layer)
        serialized.append({"wave_index": len(waves_agents) - 1, "intents": layer, "agent_kinds": agent_layer})
        placed.update(layer)

    print(f"[trace_id={trace}] build_dag → waves={waves_agents}")
    return waves_agents, serialized


def build_dag_node(state: CoordinatorState) -> CoordinatorState:
    trace = _trace(state)
    waves, plan = _build_execution_waves(state["intents"], trace)
    return CoordinatorState(**{**dict(state), "waves": waves, "wave_index": 0, "plan_dag": plan})


# ---------------------------------------------------------------------------
# Node 3: run_wave  (loops until all waves are done)
# ---------------------------------------------------------------------------

def _skipped_artifact(agent_kind: str, trace_id: str, reason: str) -> AgentArtifact:
    """Create a skipped artifact — used when a prerequisite agent failed."""
    return AgentArtifact(
        agent_kind=agent_kind,
        step_id=f"{trace_id}:{agent_kind}",
        payload={},
        status="skipped",
        error=reason,
    )


def _should_skip(intent: str, intents_set: set[str], artifacts: dict[str, dict]) -> tuple[bool, str | None]:
    """
    Check whether this intent should be skipped because a required upstream
    agent did not produce a successful artifact.

    This is how failure propagates cleanly (Problem B fix):
    instead of an exception crashing the whole pipeline, downstream agents
    are simply marked 'skipped' with a clear reason.
    """
    for left in intents_set:
        if not DEPENDENCY_SEQUENTIAL_FROMS.get((left, intent)):
            continue  # no sequential dependency from `left` to `intent`
        upstream_kind = INTENT_TO_AGENT_KIND[left]
        upstream_artifact = artifacts.get(upstream_kind)
        if upstream_artifact is None or upstream_artifact.get("status") != "ok":
            return True, f"missing ok artifact from prerequisite agent `{upstream_kind}`"
    return False, None


def _run_one_agent(
    agent_kind: str, state: CoordinatorState
) -> tuple[AgentArtifact, list[MemoryDelta]]:
    """
    Run a single agent:
      1. Check if it should be skipped (upstream failed)
      2. Build a coordinator-controlled AgentInput slice
      3. Call the agent runner
    """
    req          = state["request"]
    entities     = dict(state.get("entities") or {})
    artifacts    = dict(state.get("artifacts") or {})
    intent       = _KIND_TO_INTENT[agent_kind]
    intents_set  = set(state.get("intents") or [])

    skip, why = _should_skip(intent, intents_set, artifacts)
    if skip:
        return _skipped_artifact(agent_kind, req["trace_id"], why or "skipped"), []

    agent_input = AgentInput.from_coordinator_slice(req, entities, artifacts, agent_kind)
    return AGENT_RUNNERS[agent_kind](agent_input)


def run_wave_node(state: CoordinatorState) -> CoordinatorState:
    """
    Execute one wave. Agents in the same wave run in parallel (ThreadPoolExecutor).
    Results are merged back into CoordinatorState via the Annotated reducers.
    """
    req    = state["request"]
    trace  = req["trace_id"]
    waves  = state.get("waves") or []
    wi     = state.get("wave_index", 0)
    layer  = waves[wi]

    print(f"[trace_id={trace}] run_wave {wi + 1} → agents={layer}")

    results: list[tuple[AgentArtifact, list[MemoryDelta]]] = []
    with ThreadPoolExecutor(max_workers=min(8, len(layer))) as pool:
        futures = {pool.submit(_run_one_agent, kind, state): kind for kind in layer}
        for fut in as_completed(futures):
            artifact, deltas = fut.result()
            print(f"[trace_id={trace}] run_wave {wi + 1} → {artifact.agent_kind}: {artifact.status}")
            results.append((artifact, deltas))

    new_artifacts    = {a.agent_kind: a.to_dict() for a, _ in results}
    new_deltas       = [d.to_dict() for _, ds in results for d in ds]
    merged_artifacts = {**(state.get("artifacts") or {}), **new_artifacts}
    merged_deltas    = (state.get("pending_memory_deltas") or []) + new_deltas

    return CoordinatorState(**{
        **dict(state),
        "artifacts":             merged_artifacts,
        "wave_index":            wi + 1,
        "pending_memory_deltas": merged_deltas,
    })


def _route_after_wave(state: CoordinatorState) -> Literal["again", "synthesize"]:
    """Conditional edge: loop run_wave until all waves are exhausted."""
    wi    = state.get("wave_index", 0)
    waves = state.get("waves") or []
    return "again" if wi < len(waves) else "synthesize"


# ---------------------------------------------------------------------------
# Node 4: synthesize
# ---------------------------------------------------------------------------

def synthesize_node(state: CoordinatorState) -> CoordinatorState:
    """
    Final node:
      1. Merge all artifact payloads into a human-readable response
      2. Review and commit pending MemoryDeltas to the M1 session store
    """
    req       = state["request"]
    trace     = req["trace_id"]
    artifacts = dict(state.get("artifacts") or {})
    parts: list[str] = []

    for kind in ["recommender", "creator", "outreach", "analyst"]:
        art = artifacts.get(kind)
        if not art:
            continue
        status  = art.get("status")
        payload = art.get("payload") or {}

        if status == "skipped":
            parts.append(f"[{kind}] skipped: {art.get('error', '')}".strip())
        elif status == "failed":
            parts.append(f"[{kind}] failed: {art.get('error', 'unknown')}".strip())
        elif kind == "recommender":
            cands = payload.get("candidates") or []
            title = cands[0]["title"] if cands else "No candidate"
            parts.append(f"Top play: **{title}** — {payload.get('rationale_short', '')}")
        elif kind == "creator":
            parts.append(
                f"Draft play `{payload.get('play_id')}` "
                f"warnings={payload.get('validation_warnings', [])}"
            )
        elif kind == "outreach":
            strats = payload.get("strategies") or []
            name   = strats[0]["name"] if strats else "Strategy"
            parts.append(f"Outreach: **{name}** — {payload.get('rationale', '')}")
        elif kind == "analyst":
            snap    = payload.get("snapshot") or {}
            signals = payload.get("signals") or []
            parts.append(
                f"{snap.get('name', 'Account')} — "
                f"stage={snap.get('stage')} health={snap.get('health')} signals={signals}"
            )

    final_response = "\n".join(parts) if parts else "No outputs produced."

    # Coordinator reviews and commits pending memory deltas (Problem B: gated writes)
    session_id = req.get("session_id")
    raw_deltas = state.get("pending_memory_deltas") or []
    deltas = [
        MemoryDelta(**d) if isinstance(d, dict) else d
        for d in raw_deltas
    ]
    if deltas:
        committed = MEMORY_STORE.commit_deltas(session_id, deltas)
        if committed:
            print(f"[trace_id={trace}] synthesize → committed {len(committed)} memory delta(s)")

    print(f"[trace_id={trace}] synthesize → done")
    preview = final_response.replace("\n", " | ")[:300]
    print(f"[trace_id={trace}] response ← {preview}{'…' if len(final_response) > 300 else ''}\n")

    return CoordinatorState(**{**dict(state), "final_response": final_response})


# ---------------------------------------------------------------------------
# Graph assembly
# ---------------------------------------------------------------------------

def _build_coordinator_graph() -> StateGraph:
    g = StateGraph(CoordinatorState)
    g.add_node("decompose",  decompose_node)
    g.add_node("build_dag",  build_dag_node)
    g.add_node("run_wave",   run_wave_node)
    g.add_node("synthesize", synthesize_node)

    g.add_edge(START, "decompose")
    g.add_edge("decompose", "build_dag")
    g.add_edge("build_dag", "run_wave")
    g.add_conditional_edges("run_wave", _route_after_wave, {"again": "run_wave", "synthesize": "synthesize"})
    g.add_edge("synthesize", END)
    return g


_COORDINATOR = _build_coordinator_graph().compile()


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_request(
    user_query: str,
    *,
    tool_name:  str,
    tenant_id:  str = "demo",
    session_id: str | None = None,
    trace_id:   str | None = None,
) -> CoordinatorState:
    """
    Main entry point. Called by direct MCP tools and by ask_recepto.

    Both paths shallow-wrap through the coordinator so every request has:
      - A trace_id for log correlation
      - Artifacts in CoordinatorWorkingMemory
      - Memory writes gated through synthesize_node
    """
    trace = trace_id or uuid.uuid4().hex[:12]
    initial: CoordinatorState = {
        "request": {
            "trace_id":   trace,
            "user_query": user_query,
            "tool_name":  tool_name,
            "tenant_id":  tenant_id,
            "session_id": session_id,
        },
        "intents":               [],
        "entities":              {},
        "plan_dag":              [],
        "waves":                 [],
        "wave_index":            0,
        "artifacts":             {},
        "pending_memory_deltas": [],
        "final_response":        None,
    }
    return _COORDINATOR.invoke(initial)  # type: ignore[return-value]
