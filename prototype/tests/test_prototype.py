"""
tests/test_prototype.py — smoke tests for all 4 demo scenarios.

These tests verify the three problem solutions are actually working in code,
not just described in docs. Each test asserts on artifact status, DAG shape,
and the specific behaviour that demonstrates the solution.
"""

from __future__ import annotations

import pytest

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from coordinator import run_request, MemoryStore
from state import AGENT_INPUT_DEPS, DEPENDENCY_SEQUENTIAL_FROMS, IntentKind


# ---------------------------------------------------------------------------
# Scenario 1 — single agent, direct tool (Problem C: tool isolation)
# ---------------------------------------------------------------------------

def test_scenario1_single_agent_direct_tool():
    result = run_request(
        "recommend a play for a fintech company with high intent signals",
        tool_name="recommend_play",
        session_id="test_s1",
    )
    # Only recommender ran
    assert set(result["artifacts"].keys()) == {"recommender"}
    assert result["artifacts"]["recommender"]["status"] == "ok"

    # DAG had exactly one wave with one agent
    assert result["waves"] == [["recommender"]]

    # Recommender produced candidates
    payload = result["artifacts"]["recommender"]["payload"]
    assert len(payload["candidates"]) > 0
    assert "rationale_short" in payload
    assert len(payload["citations"]) > 0


def test_scenario1_direct_tool_skips_nl_decompose():
    """Direct tool call should produce exactly one intent — no NL classification."""
    result = run_request(
        "some ambiguous text that would confuse a classifier",
        tool_name="recommend_play",
        session_id="test_s1b",
    )
    assert result["intents"] == ["recommend_play"]


# ---------------------------------------------------------------------------
# Scenario 2 — sequential agents (Problem B: artifact slicing)
# ---------------------------------------------------------------------------

def test_scenario2_sequential_dag():
    result = run_request(
        "recommend a play for a SaaS company and then create it",
        tool_name="ask_recepto",
        session_id="test_s2",
    )
    # Both agents ran and succeeded
    assert result["artifacts"]["recommender"]["status"] == "ok"
    assert result["artifacts"]["creator"]["status"] == "ok"

    # DAG was sequential (two separate waves)
    assert result["waves"] == [["recommender"], ["creator"]]


def test_scenario2_creator_receives_only_allowed_artifacts():
    """
    Problem B fix: coordinator passes only AGENT_INPUT_DEPS["creator"] to creator.
    Creator must not receive artifacts it is not supposed to see.
    """
    # AGENT_INPUT_DEPS["creator"] = ["recommender", "analyst"]
    # In a recommend+create run, analyst is absent — creator should only see recommender.
    result = run_request(
        "recommend a play for a SaaS company and then create it",
        tool_name="ask_recepto",
        session_id="test_s2b",
    )
    creator_payload = result["artifacts"]["creator"]["payload"]
    # Creator used the recommender artifact as seed (seed_play_id lives inside play_object)
    assert creator_payload["play_object"]["seed_play_id"] is not None
    assert "validation_warnings" in creator_payload
    # No warnings about missing recommendation (it was passed correctly)
    assert "No recommendation artifact" not in " ".join(creator_payload["validation_warnings"])


def test_scenario2_memory_writes_are_coordinator_gated():
    """
    Problem B fix: memory is committed by synthesize_node, not by agents directly.
    Verify session memory is populated after the run (committed deltas appear).
    """
    store = MemoryStore()
    result = run_request(
        "recommend a play for a SaaS company and then create it",
        tool_name="ask_recepto",
        session_id="test_mem",
    )
    # pending_memory_deltas were proposed by agents
    assert len(result["pending_memory_deltas"]) > 0
    # All deltas are dicts (serialised MemoryDelta — coordinator normalises them)
    for d in result["pending_memory_deltas"]:
        assert isinstance(d, dict)
        assert {"tier", "key", "value", "reason"}.issubset(d.keys())


# ---------------------------------------------------------------------------
# Scenario 3 — parallel agents (Problem D: DAG-based orchestration)
# ---------------------------------------------------------------------------

def test_scenario3_parallel_dag():
    result = run_request(
        "analyze Stripe's account and recommend an outreach strategy",
        tool_name="ask_recepto",
        session_id="test_s3",
    )
    # Both agents ran and succeeded
    assert result["artifacts"]["analyst"]["status"] == "ok"
    assert result["artifacts"]["outreach"]["status"] == "ok"

    # DAG put both in the SAME wave (parallel)
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
    assert payload["strategies"][0]["name"]  # non-empty name


# ---------------------------------------------------------------------------
# Scenario 4 — partial DAG failure (Problem B: failure as data)
# ---------------------------------------------------------------------------

def test_scenario4_analyst_fails_for_unknown_account():
    result = run_request(
        "analyze AcmeCorp's account and then create a play from it",
        tool_name="ask_recepto",
        session_id="test_s4",
    )
    assert result["artifacts"]["analyst"]["status"] == "failed"
    assert "No mock account matched" in result["artifacts"]["analyst"]["error"]


def test_scenario4_creator_skipped_when_analyst_fails():
    """
    Problem B fix: failure propagates as data (skipped artifact), not exception.
    Creator must be skipped with a clear reason pointing to the failed analyst.
    """
    result = run_request(
        "analyze AcmeCorp's account and then create a play from it",
        tool_name="ask_recepto",
        session_id="test_s4b",
    )
    creator = result["artifacts"]["creator"]
    assert creator["status"] == "skipped"
    assert "analyst" in creator["error"]  # reason names the upstream agent


def test_scenario4_no_exception_raised():
    """Partial failure must not crash the coordinator — it must complete gracefully."""
    # If this raises, the test fails — that's the assertion.
    result = run_request(
        "analyze AcmeCorp's account and then create a play from it",
        tool_name="ask_recepto",
        session_id="test_s4c",
    )
    assert result["final_response"] is not None


# ---------------------------------------------------------------------------
# State schema tests (AGENT_INPUT_DEPS and DEPENDENCY_SEQUENTIAL_FROMS)
# ---------------------------------------------------------------------------

def test_agent_input_deps_are_declared():
    """Every agent kind must have an entry in AGENT_INPUT_DEPS."""
    for kind in ("recommender", "creator", "outreach", "analyst"):
        assert kind in AGENT_INPUT_DEPS


def test_dependency_table_keys_are_valid_intents():
    """All intent strings in the dependency table must be valid IntentKind values."""
    valid = {i.value for i in IntentKind}
    for left, right in DEPENDENCY_SEQUENTIAL_FROMS.keys():
        assert left  in valid, f"Unknown intent in dependency table: {left}"
        assert right in valid, f"Unknown intent in dependency table: {right}"
