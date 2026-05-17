from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from state import MemoryDelta


class TestM2DurableMemory:
    def _tmp_db(self, tmp_path: Path) -> Path:
        return tmp_path / "test_m2.db"

    def test_write_and_read_same_instance(self, tmp_path):
        from memory import DurableMemoryStore
        db = self._tmp_db(tmp_path)
        store = DurableMemoryStore(db)
        store.write("sess1", "key1", "val1", "test")
        assert store.read("sess1", "key1") == "val1"

    def test_persists_across_new_instance(self, tmp_path):
        from memory import DurableMemoryStore
        db = self._tmp_db(tmp_path)
        DurableMemoryStore(db).write("sess1", "account", "Stripe", "test")
        fresh = DurableMemoryStore(db)
        assert fresh.read("sess1", "account") == "Stripe"

    def test_snapshot_returns_all_keys(self, tmp_path):
        from memory import DurableMemoryStore
        db = self._tmp_db(tmp_path)
        store = DurableMemoryStore(db)
        store.write("sess1", "a", "1", "")
        store.write("sess1", "b", "2", "")
        snap = store.snapshot("sess1")
        assert snap == {"a": "1", "b": "2"}

    def test_sessions_are_isolated(self, tmp_path):
        from memory import DurableMemoryStore
        db = self._tmp_db(tmp_path)
        store = DurableMemoryStore(db)
        store.write("sess1", "key", "s1_val", "")
        store.write("sess2", "key", "s2_val", "")
        assert store.read("sess1", "key") == "s1_val"
        assert store.read("sess2", "key") == "s2_val"

    def test_overwrite_key(self, tmp_path):
        from memory import DurableMemoryStore
        db = self._tmp_db(tmp_path)
        store = DurableMemoryStore(db)
        store.write("sess1", "key", "old", "")
        store.write("sess1", "key", "new", "")
        assert store.read("sess1", "key") == "new"

    def test_m2_delta_committed_via_memory_store(self, tmp_path):
        from memory import MemoryStore
        db = self._tmp_db(tmp_path)
        ms = MemoryStore(m2_db_path=db)
        delta = MemoryDelta(tier="M2", key="last_acct", value="Notion", reason="test")
        committed = ms.commit_deltas("sess1", [delta])
        assert len(committed) == 1
        assert ms.snapshot_m2("sess1")["last_acct"] == "Notion"

    def test_m2_disabled_when_no_db_path(self):
        from memory import MemoryStore
        ms = MemoryStore()
        delta = MemoryDelta(tier="M2", key="key", value="val", reason="test")
        committed = ms.commit_deltas("sess1", [delta])
        assert committed == []
        assert ms.snapshot_m2("sess1") == {}

    def test_m2_skips_pii(self, tmp_path):
        from memory import MemoryStore
        db = self._tmp_db(tmp_path)
        ms = MemoryStore(m2_db_path=db)
        delta = MemoryDelta(tier="M2", key="secret", value="123-45-6789", reason="test")
        committed = ms.commit_deltas("sess1", [delta])
        assert committed == []
        assert ms.snapshot_m2("sess1") == {}

    def test_coordinator_writes_m2_for_analyst(self, tmp_path):
        import importlib
        import coordinator as coord_module

        db = self._tmp_db(tmp_path)
        original_store = coord_module.MEMORY_STORE
        try:
            from memory import MemoryStore
            coord_module.MEMORY_STORE = MemoryStore(m2_db_path=db)
            coord_module.run_request(
                "analyze Stripe's account",
                tool_name="analyze_account",
                session_id="m2_test",
            )
            snap = coord_module.MEMORY_STORE.snapshot_m2("m2_test")
            assert snap.get("last_account_viewed") == "Stripe"
        finally:
            coord_module.MEMORY_STORE = original_store


class TestLLMFallback:
    def test_decompose_returns_none_without_api_key(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        import llm
        llm._CLIENT = None
        result = llm.llm_decompose("recommend a play for fintech")
        assert result is None

    def test_synthesize_returns_none_without_api_key(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        import llm
        llm._CLIENT = None
        result = llm.llm_synthesize("some query", {"recommender": {"status": "ok", "payload": {}}})
        assert result is None

    def test_coordinator_still_produces_response_without_api_key(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        import llm
        llm._CLIENT = None
        from coordinator import run_request
        result = run_request(
            "recommend a play for fintech",
            tool_name="recommend_play",
            session_id="fallback_test",
        )
        assert result["final_response"] is not None
        assert len(result["final_response"]) > 0


class TestMCPServerTools:
    def test_recommend_play_returns_string(self):
        from server import recommend_play
        result = recommend_play("recommend a play for fintech", session_id="srv_test")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_create_play_returns_string(self):
        from server import create_play
        result = create_play("create a play for SaaS", session_id="srv_test")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_recommend_outreach_returns_string(self):
        from server import recommend_outreach
        result = recommend_outreach("outreach for enterprise", session_id="srv_test")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_analyze_account_stripe_returns_string(self):
        from server import analyze_account
        result = analyze_account("analyze Stripe's account", session_id="srv_test")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_analyze_account_unknown_returns_failure_string(self):
        from server import analyze_account
        result = analyze_account("analyze AcmeCorp", session_id="srv_test")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_ask_recepto_sequential_returns_string(self):
        from server import ask_recepto
        result = ask_recepto(
            "recommend a play for SaaS and then create it",
            session_id="srv_test",
        )
        assert isinstance(result, str)
        assert len(result) > 0

    def test_ask_recepto_parallel_returns_string(self):
        from server import ask_recepto
        result = ask_recepto(
            "analyze Stripe's account and recommend an outreach strategy",
            session_id="srv_test",
        )
        assert isinstance(result, str)
        assert len(result) > 0

    def test_all_five_tools_registered(self):
        import asyncio
        from server import mcp
        tools = asyncio.run(mcp.list_tools())
        names = {t.name for t in tools}
        assert names == {
            "recommend_play",
            "create_play",
            "recommend_outreach",
            "analyze_account",
            "ask_recepto",
        }
