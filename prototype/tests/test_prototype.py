from __future__ import annotations

import pytest

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from coordinator import run_request, MemoryStore
from state import AGENT_INPUT_DEPS, DEPENDENCY_SEQUENTIAL_FROMS, IntentKind


def test_scenario1_single_agent_direct_tool():
    result = run_request(
        "recommend a play for a fintech company with high intent signals",
        tool_name="recommend_play",
        session_id="test_s1",
    )
    assert set(result["artifacts"].keys()) == {"recommender"}
    assert result["artifacts"]["recommender"]["status"] == "ok"

    assert result["waves"] == [["recommender"]]

    payload = result["artifacts"]["recommender"]["payload"]
    assert len(payload["candidates"]) > 0
    assert "rationale_short" in payload
    assert len(payload["citations"]) > 0


def test_scenario1_direct_tool_skips_nl_decompose():
    result = run_request(
        "some ambiguous text that would confuse a classifier",
        tool_name="recommend_play",
        session_id="test_s1b",
    )
    assert result["intents"] == ["recommend_play"]


def test_scenario2_sequential_dag():
    result = run_request(
        "recommend a play for a SaaS company and then create it",
        tool_name="ask_recepto",
        session_id="test_s2",
    )
    assert result["artifacts"]["recommender"]["status"] == "ok"
    assert result["artifacts"]["creator"]["status"] == "ok"

    assert result["waves"] == [["recommender"], ["creator"]]


def test_scenario2_creator_receives_only_allowed_artifacts():
    result = run_request(
        "recommend a play for a SaaS company and then create it",
        tool_name="ask_recepto",
        session_id="test_s2b",
    )
    creator_payload = result["artifacts"]["creator"]["payload"]
    assert creator_payload["play_object"]["seed_play_id"] is not None
    assert "validation_warnings" in creator_payload
    assert "No recommendation artifact" not in " ".join(creator_payload["validation_warnings"])


def test_scenario2_memory_writes_are_coordinator_gated():
    store = MemoryStore()
    result = run_request(
        "recommend a play for a SaaS company and then create it",
        tool_name="ask_recepto",
        session_id="test_mem",
    )
    assert len(result["pending_memory_deltas"]) > 0
    for d in result["pending_memory_deltas"]:
        assert isinstance(d, dict)
        assert {"tier", "key", "value", "reason"}.issubset(d.keys())


def test_scenario3_parallel_dag():
    result = run_request(
        "analyze Stripe's account and recommend an outreach strategy",
        tool_name="ask_recepto",
        session_id="test_s3",
    )
    assert result["artifacts"]["analyst"]["status"] == "ok"
    assert result["artifacts"]["outreach"]["status"] == "ok"

    assert len(result["waves"]) == 1
    assert set(result["waves"][0]) == {"analyst", "outreach"}


def test_scenario3_analyst_returned_stripe_data():
    result = run_request(
        "analyze Stripe's account and recommend an outreach strategy",
        tool_name="ask_recepto",
        session_id="test_s3b",
    )
    snapshot = result["artifacts"]["analyst"]["payload"]["snapshot"]
    assert snapshot["name"] == "Stripe"
    assert snapshot["stage"] == "Evaluation"


def test_scenario3_outreach_returned_strategies():
    result = run_request(
        "analyze Stripe's account and recommend an outreach strategy",
        tool_name="ask_recepto",
        session_id="test_s3c",
    )
    payload = result["artifacts"]["outreach"]["payload"]
    assert len(payload["strategies"]) >= 1
    assert payload["strategies"][0]["name"]


def test_scenario4_analyst_fails_for_unknown_account():
    result = run_request(
        "analyze AcmeCorp's account and then create a play from it",
        tool_name="ask_recepto",
        session_id="test_s4",
    )
    assert result["artifacts"]["analyst"]["status"] == "failed"
    assert "No mock account matched" in result["artifacts"]["analyst"]["error"]


def test_scenario4_creator_skipped_when_analyst_fails():
    result = run_request(
        "analyze AcmeCorp's account and then create a play from it",
        tool_name="ask_recepto",
        session_id="test_s4b",
    )
    creator = result["artifacts"]["creator"]
    assert creator["status"] == "skipped"
    assert "analyst" in creator["error"]


def test_scenario4_no_exception_raised():
    result = run_request(
        "analyze AcmeCorp's account and then create a play from it",
        tool_name="ask_recepto",
        session_id="test_s4c",
    )
    assert result["final_response"] is not None


def test_agent_input_deps_are_declared():
    for kind in ("recommender", "creator", "outreach", "analyst"):
        assert kind in AGENT_INPUT_DEPS


def test_dependency_table_keys_are_valid_intents():
    valid = {i.value for i in IntentKind}
    for left, right in DEPENDENCY_SEQUENTIAL_FROMS.keys():
        assert left  in valid, f"Unknown intent in dependency table: {left}"
        assert right in valid, f"Unknown intent in dependency table: {right}"
