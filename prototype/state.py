"""
state.py — all shared types for the coordinator + agents (PLAN §6).

Design principle (Problem B fix):
  Old pipelines shared a mutable global dict between steps.
  Here, every piece of data that crosses a boundary is a typed dataclass or TypedDict.
  Agents cannot write to CoordinatorState directly — they return AgentArtifact + MemoryDelta
  and the coordinator decides what to commit.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Annotated, TypedDict


# ---------------------------------------------------------------------------
# LangGraph reducer helpers
# These tell LangGraph how to MERGE state updates when multiple nodes run.
# Without these, the last writer wins — which would lose artifacts from
# parallel branches. _merge_dicts unions them; _extend_list appends.
# ---------------------------------------------------------------------------

def _merge_dicts(a: dict | None, b: dict | None) -> dict:
    x = a if a is not None else {}
    y = b if b is not None else {}
    return {**x, **y}


def _extend_list(a: list | None, b: list | None) -> list:
    return (a or []) + (b or [])


# ---------------------------------------------------------------------------
# Intent classification
# ---------------------------------------------------------------------------

class IntentKind(str, Enum):
    """The four use cases Recepto supports. Each maps to exactly one agent."""
    RECOMMEND_PLAY     = "recommend_play"
    CREATE_PLAY        = "create_play"
    RECOMMEND_OUTREACH = "recommend_outreach"
    ANALYZE_ACCOUNT    = "analyze_account"


# Maps intent string → agent_kind string used as dict keys throughout.
INTENT_TO_AGENT_KIND: dict[str, str] = {
    IntentKind.RECOMMEND_PLAY.value:     "recommender",
    IntentKind.CREATE_PLAY.value:        "creator",
    IntentKind.RECOMMEND_OUTREACH.value: "outreach",
    IntentKind.ANALYZE_ACCOUNT.value:    "analyst",
}

# Direct MCP tool names → their pre-known intent (None = ask_recepto, needs decompose).
# Direct calls skip NL decomposition but still shallow-wrap through the coordinator
# so every request gets a trace_id and artifacts end up in CoordinatorWorkingMemory.
DIRECT_TOOL_TO_INTENT: dict[str, str | None] = {
    "ask_recepto":        None,                               # full decompose
    "recommend_play":     IntentKind.RECOMMEND_PLAY.value,
    "create_play":        IntentKind.CREATE_PLAY.value,
    "recommend_outreach": IntentKind.RECOMMEND_OUTREACH.value,
    "analyze_account":    IntentKind.ANALYZE_ACCOUNT.value,
}


# ---------------------------------------------------------------------------
# Dependency table (Problem D fix)
#
# Old pipelines hardcoded step order per use case — adding a new use case
# meant copy-pasting a new script and re-ordering manually.
#
# Here the coordinator reads this table at runtime and builds a DAG per request.
# True = left intent MUST finish before right (sequential).
# Missing entry = independent (can run in parallel).
# ---------------------------------------------------------------------------

DEPENDENCY_SEQUENTIAL_FROMS: dict[tuple[str, str], bool] = {
    # Creator needs the recommendation artifact as seed input.
    (IntentKind.RECOMMEND_PLAY.value,  IntentKind.CREATE_PLAY.value): True,
    # Creator can optionally enrich the play with account signals.
    (IntentKind.ANALYZE_ACCOUNT.value, IntentKind.CREATE_PLAY.value): True,
    # Account analysis and outreach strategy are independent → parallel.
    # (absence of entry = parallel allowed)
}


# ---------------------------------------------------------------------------
# Which upstream artifacts each agent is allowed to read (Problem C fix)
#
# Old pipelines let any step call any integration.
# Here, AGENT_INPUT_DEPS is the coordinator-enforced allowlist:
# when building AgentInput, only deps listed here are passed to the agent.
# An agent physically cannot read artifacts it isn't supposed to see.
# ---------------------------------------------------------------------------

AGENT_INPUT_DEPS: dict[str, list[str]] = {
    "recommender": [],                        # reads nothing upstream
    "creator":     ["recommender", "analyst"],# seeded by recommendation + account brief
    "outreach":    [],                        # independent
    "analyst":     [],                        # independent
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RequestContext:
    """Immutable per-request envelope. Set once at entry, never mutated."""
    trace_id:   str
    user_query: str
    tool_name:  str            # "ask_recepto" or a direct tool name
    tenant_id:  str = "demo"
    session_id: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AgentArtifact:
    """
    Typed output from one agent invocation.

    status values:
      "ok"      — agent completed successfully
      "failed"  — agent ran but could not produce output (e.g. account not found)
      "skipped" — coordinator skipped this agent because a prerequisite failed
    """
    agent_kind: str
    step_id:    str   # "{trace_id}:{agent_kind}" — unique per invocation
    payload:    dict
    status:     str
    error:      str | None = None

    def to_dict(self) -> dict:
        return {
            "agent_kind": self.agent_kind,
            "step_id":    self.step_id,
            "payload":    self.payload,
            "status":     self.status,
            "error":      self.error,
        }

    @staticmethod
    def from_dict(d: dict) -> "AgentArtifact":
        return AgentArtifact(
            agent_kind=d["agent_kind"],
            step_id=d["step_id"],
            payload=d.get("payload", {}),
            status=d["status"],
            error=d.get("error"),
        )


@dataclass
class MemoryDelta:
    """
    A proposed memory write from an agent.

    Agents never write to memory directly — they return a list of MemoryDeltas.
    The coordinator's synthesize_node reviews each delta (PII check, cap)
    before committing to the MemoryStore. This keeps every memory write
    observable and attributable (Problem B fix).
    """
    tier:   str   # "M1" (session) — M2 durable is out of scope for prototype
    key:    str
    value:  str
    reason: str   # why the agent is proposing this write

    def to_dict(self) -> dict:
        return {"tier": self.tier, "key": self.key, "value": self.value, "reason": self.reason}


@dataclass
class AgentInput:
    """
    The coordinator-controlled slice of state passed to each agent.

    Agents only receive:
      1. The original request (trace_id, query, entities)
      2. Artifacts from upstream agents listed in AGENT_INPUT_DEPS[agent_kind]

    They never see the full CoordinatorState — enforcing the no-silent-globals rule.
    """
    trace_id:   str
    user_query: str
    entities:   dict[str, str]
    artifacts:  dict[str, dict]  # agent_kind → AgentArtifact.to_dict()

    @staticmethod
    def from_coordinator_slice(
        request:        dict,
        entities:       dict[str, str],
        full_artifacts: dict[str, dict],
        agent_kind:     str,
    ) -> "AgentInput":
        """Build an AgentInput by filtering full_artifacts down to allowed deps."""
        allowed = AGENT_INPUT_DEPS.get(agent_kind, [])
        relevant = {k: full_artifacts[k] for k in allowed if k in full_artifacts}
        return AgentInput(
            trace_id=request["trace_id"],
            user_query=request["user_query"],
            entities=entities,
            artifacts=relevant,
        )


# ---------------------------------------------------------------------------
# LangGraph coordinator state
# ---------------------------------------------------------------------------

class CoordinatorState(TypedDict, total=False):
    """
    The single source of truth for a coordinator run.

    Annotated reducers on `artifacts` and `pending_memory_deltas` let LangGraph
    merge updates from parallel branches without the last-writer-wins problem.
    """
    request:                dict                                        # RequestContext.to_dict()
    intents:                list[str]                                   # after decompose
    entities:               dict[str, str]                              # extracted from query
    plan_dag:               list[dict]                                  # serialized DAG nodes
    waves:                  list[list[str]]                             # execution waves
    wave_index:             int                                         # current wave pointer
    artifacts:              Annotated[dict[str, dict], _merge_dicts]    # agent_kind → artifact
    pending_memory_deltas:  Annotated[list[dict], _extend_list]         # proposed M1 writes
    final_response:         str | None
