"""Shared types and coordinator state shape (PLAN §6)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Annotated, TypedDict


def _merge_dicts(a: dict | None, b: dict | None) -> dict:
    x = a if a is not None else {}
    y = b if b is not None else {}
    return {**x, **y}


def _extend_list(a: list | None, b: list | None) -> list:
    return (a or []) + (b or [])


class IntentKind(str, Enum):
    RECOMMEND_PLAY = "recommend_play"
    CREATE_PLAY = "create_play"
    RECOMMEND_OUTREACH = "recommend_outreach"
    ANALYZE_ACCOUNT = "analyze_account"


INTENT_TO_AGENT_KIND = {
    IntentKind.RECOMMEND_PLAY.value: "recommender",
    IntentKind.CREATE_PLAY.value: "creator",
    IntentKind.RECOMMEND_OUTREACH.value: "outreach",
    IntentKind.ANALYZE_ACCOUNT.value: "analyst",
}


DIRECT_TOOL_TO_INTENT = {
    "ask_recepto": None,
    "recommend_play": IntentKind.RECOMMEND_PLAY.value,
    "create_play": IntentKind.CREATE_PLAY.value,
    "recommend_outreach": IntentKind.RECOMMEND_OUTREACH.value,
    "analyze_account": IntentKind.ANALYZE_ACCOUNT.value,
}


# True when left intent must complete before right (sequential dependency).
DEPENDENCY_SEQUENTIAL_FROMS: dict[tuple[str, str], bool] = {
    (IntentKind.RECOMMEND_PLAY.value, IntentKind.CREATE_PLAY.value): True,
    (IntentKind.ANALYZE_ACCOUNT.value, IntentKind.CREATE_PLAY.value): True,
}


@dataclass(frozen=True)
class RequestContext:
    trace_id: str
    user_query: str
    tool_name: str
    tenant_id: str = "demo"
    session_id: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AgentArtifact:
    agent_kind: str
    step_id: str
    payload: dict
    status: str  # ok | failed | skipped
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "agent_kind": self.agent_kind,
            "step_id": self.step_id,
            "payload": self.payload,
            "status": self.status,
            "error": self.error,
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
    tier: str
    key: str
    value: str
    reason: str

    def to_dict(self) -> dict:
        return {"tier": self.tier, "key": self.key, "value": self.value, "reason": self.reason}


@dataclass
class AgentInput:
    trace_id: str
    user_query: str
    entities: dict[str, str]
    artifacts: dict[str, dict]  # agent_kind -> AgentArtifact dict

    @staticmethod
    def from_coordinator_slice(
        request: dict,
        entities: dict[str, str],
        full_artifacts: dict[str, dict],
        agent_kind: str,
    ) -> "AgentInput":
        deps = AGENT_INPUT_DEPS.get(agent_kind, [])
        relevant = {k: full_artifacts[k] for k in deps if k in full_artifacts}
        return AgentInput(
            trace_id=request["trace_id"],
            user_query=request["user_query"],
            entities=entities,
            artifacts=relevant,
        )


# Which upstream agent artifacts this agent may read (coordinator-enforced slice).
AGENT_INPUT_DEPS: dict[str, list[str]] = {
    "recommender": [],
    "creator": ["recommender", "analyst"],
    # Parallel with analyst when needed — still reads analyst artifact if coordinator already merged prior waves.
    "outreach": [],
    "analyst": [],
}


class CoordinatorState(TypedDict, total=False):
    request: dict
    intents: list[str]
    entities: dict[str, str]
    plan_dag: list[dict]
    waves: list[list[str]]
    wave_index: int
    artifacts: Annotated[dict[str, dict], _merge_dicts]
    pending_memory_deltas: Annotated[list[dict], _extend_list]
    final_response: str | None
