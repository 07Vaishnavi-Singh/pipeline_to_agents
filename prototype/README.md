# Recepto MCP — Multi-Agent Pipeline Prototype

A LangGraph prototype showing how Recepto's linear pipelines can be migrated to a multi-agent DAG architecture. Built to solve three concrete problems with the current system and expose the result as an MCP server.

---

## The Problem

Recepto currently runs **linear pipelines** — hardcoded step sequences:

```
Step 1: fetch account → Step 2: score intent → Step 3: recommend play → Step 4: draft email
```

This breaks in three ways when you try to evolve the system:

### Problem B — Shared Mutable State
All steps share a global dict. Any step can read or write any key at any time. There is no contract between steps, no review of what gets written to memory, and no way to attribute which step caused a bad write. When a step fails mid-pipeline, downstream steps silently receive stale or missing data.

**Fix:** Every piece of data that crosses a boundary is a typed dataclass. Agents never touch `CoordinatorState` directly — they receive a narrow `AgentInput` slice and return `(AgentArtifact, list[MemoryDelta])`. Memory writes are proposed by agents and committed (or rejected) by the coordinator after a PII check and cap review.

### Problem C — Tool Bleed
Any step can call any integration. There is no ownership. When something breaks, you don't know which step called which API. Adding a new use case requires copy-pasting a whole pipeline and re-wiring integrations.

**Fix:** Each agent owns exactly the tools it needs — as private functions. No agent imports another agent's tools. The coordinator dispatches via a registry (`AGENT_RUNNERS`) — agents never call each other directly.

### Problem D — Hardcoded Execution Order
Step order is hardcoded per use case. Adding a use case that needs two things in parallel means writing a new script. Sequential and parallel execution can't coexist in the same pipeline.

**Fix:** A dependency table (`DEPENDENCY_SEQUENTIAL_FROMS`) declares which intents must run before which. The coordinator runs a topological sort at request time and produces execution **waves**. Agents in the same wave run in parallel via `ThreadPoolExecutor`. Waves run sequentially.

---

## Architecture

```
MCP Client (Claude Desktop, etc.)
        │
        ▼
   server.py  ──── 5 FastMCP tools
        │
        ▼
  coordinator.py  ──── LangGraph StateGraph
        │
   ┌────┴────────────────────────┐
   │                             │
decompose_node            synthesize_node
   │                             │
build_dag_node         commit MemoryDeltas
   │                        (M1 + M2)
   │
run_wave_node  ──── ThreadPoolExecutor
   │
   ├── run_recommender()   agents/recommender.py
   ├── run_creator()       agents/creator.py
   ├── run_outreach()      agents/outreach.py
   └── run_analyst()       agents/analyst.py
```

### Request flow

```
1. Tool called (MCP or direct)
2. decompose_node  → LLM intent classification (or rule-based fallback) → 1–4 intents
3. build_dag_node  → topological sort over dependency table → execution waves
4. run_wave_node   → runs one wave at a time, agents in a wave run in parallel
5. synthesize_node → LLM natural language summary (or template fallback)
                   → reviews and commits MemoryDeltas
```

### Direct tool vs ask_recepto

```
Direct tool (e.g. recommend_play)        ask_recepto
─────────────────────────────────        ──────────────────────────────────
intent is pre-known                      NL query → llm_decompose() → 1–4 intents
skip NL decompose                        build DAG → 1 or N waves
single-node DAG                          parallel and/or sequential agents
still goes through coordinator           same coordinator, same memory gating
(trace_id, memory, artifacts all work)
```

---

## File Map

```
prototype/
├── state.py          All shared types — CoordinatorState, AgentArtifact,
│                     AgentInput, MemoryDelta, dependency + allowlist tables
│
├── memory.py         M1 session store (OrderedDict, capped at 5 per session)
│                     M2 durable store (SQLite, persists across restarts)
│                     Agents never write here directly — coordinator gates all writes
│
├── coordinator.py    LangGraph StateGraph — 4 nodes:
│                     decompose → build_dag → run_wave (loop) → synthesize
│
├── llm.py            Claude API wrapper — llm_decompose + llm_synthesize
│                     Returns None when no API key → graceful offline fallback
│
├── server.py         FastMCP server — 5 tools, all route through run_request()
│
├── main.py           Runs 4 demo scenarios end-to-end (fully offline)
│
├── agents/
│   ├── recommender.py  Owns _kb_search() → reads mocks/plays.json
│   ├── creator.py      Owns _icp_from() → reads upstream artifacts via coordinator slice
│   ├── outreach.py     Owns _analytics_fetch() + _notion_lookup() (deterministic mocks)
│   └── analyst.py      Owns _platform_lookup() → reads mocks/accounts.json
│                       Returns status="failed" cleanly when account not found
│
├── mocks/
│   ├── plays.json      4 static sales plays (keyword-scored search)
│   └── accounts.json   3 accounts: Stripe, Notion, Linear
│
└── tests/
    ├── test_prototype.py     11 tests — 4 scenarios + schema validation
    └── test_new_features.py  22 tests — M2 memory, LLM fallback, MCP tools
```

---

## Key Types (state.py)

```python
AgentInput          # what the coordinator passes to each agent
    trace_id        # for log correlation
    user_query      # original query
    entities        # extracted from query (account_name, industry)
    artifacts       # ONLY the upstream artifacts this agent is allowed to read
                    # enforced by AGENT_INPUT_DEPS[agent_kind]

AgentArtifact       # what every agent returns
    agent_kind      # "recommender" | "creator" | "outreach" | "analyst"
    step_id         # "{trace_id}:{agent_kind}" — unique per invocation
    payload         # agent-specific output dict
    status          # "ok" | "failed" | "skipped"
    error           # reason if failed or skipped

MemoryDelta         # a proposed memory write from an agent
    tier            # "M1" (session) or "M2" (durable SQLite)
    key / value     # what to store
    reason          # why the agent is proposing this write

CoordinatorState    # LangGraph TypedDict — the single source of truth
    request         # RequestContext (immutable per request)
    intents         # after decompose
    entities        # extracted from query
    plan_dag        # serialised DAG
    waves           # execution waves [[agent_kind, ...], ...]
    wave_index      # pointer into waves
    artifacts       # merged across all waves (Annotated reducer)
    pending_memory_deltas  # proposed by agents, committed in synthesize
    final_response  # human-readable output
```

---

## Dependency Table (state.py)

```python
DEPENDENCY_SEQUENTIAL_FROMS = {
    (RECOMMEND_PLAY,  CREATE_PLAY): True,   # creator needs recommendation as seed
    (ANALYZE_ACCOUNT, CREATE_PLAY): True,   # creator can enrich from account signals
    # ANALYZE_ACCOUNT + RECOMMEND_OUTREACH: no entry → they run in parallel
}

AGENT_INPUT_DEPS = {
    "recommender": [],                        # reads nothing upstream
    "creator":     ["recommender", "analyst"],# seeded by recommendation + account brief
    "outreach":    [],                        # independent
    "analyst":     [],                        # independent
}
```

When a new use case is added, you update these two tables — the coordinator's topological sort handles the rest automatically.

---

## Memory Tiers

```
M0  CoordinatorState (LangGraph)
    Ephemeral — exists only during a single run_request() call. Gone after.

M1  MemoryStore._sessions (in-process OrderedDict)
    Per-session, capped at 5 entries (LRU eviction).
    Lost when the process restarts.
    Example: preferred_industry, last_draft_goal

M2  DurableMemoryStore (SQLite — memory_m2.db)
    Persists across process restarts.
    Example: last_account_viewed (so "show me that account again" works after a restart)
```

Agents propose writes by returning `MemoryDelta` objects. The coordinator's `synthesize_node` runs a PII check (SSN-like patterns, emails) and enforces the M1 cap before committing. Agents never hold a reference to `MemoryStore`.

---

## The 4 Demo Scenarios

| # | Query | Tool | DAG | What it demonstrates |
|---|-------|------|-----|----------------------|
| 1 | "recommend a play for a fintech company with high intent signals" | `recommend_play` (direct) | `[recommender]` | Problem C — tool isolation. Direct tool shallow-wraps coordinator but skips NL decompose. |
| 2 | "recommend a play for a SaaS company and then create it" | `ask_recepto` | `[recommender] → [creator]` | Problem B — creator receives only the recommendation artifact slice, not full state. Memory writes are gated. |
| 3 | "analyze Stripe's account and recommend an outreach strategy" | `ask_recepto` | `[analyst ∥ outreach]` | Problem D — no dependency between these two, so they land in the same wave and run in parallel. |
| 4 | "analyze AcmeCorp's account and then create a play from it" | `ask_recepto` | `[analyst] → [creator: skipped]` | Problem B — AcmeCorp not in mocks → analyst returns status="failed" → coordinator skips creator with a clear reason. No exception raised. |

---

## MCP Tools (server.py)

5 tools exposed via FastMCP:

| Tool | Route | What it does |
|------|-------|--------------|
| `recommend_play` | direct | Play Recommender only — keyword search over plays.json |
| `create_play` | direct | Play Creator only — drafts a play from ICP mock |
| `recommend_outreach` | direct | Outreach Strategist only — analytics + Notion mock |
| `analyze_account` | direct | Account Analyst only — looks up account, returns health/signals |
| `ask_recepto` | NL decompose | Full multi-agent DAG — classifies intent, builds and runs the DAG |

All 5 tools call `run_request()` — every call gets a `trace_id`, coordinator-gated memory, and artifacts in `CoordinatorState`.

---

## LLM Integration (llm.py)

```
ANTHROPIC_API_KEY set?
  YES → llm_decompose()   calls claude-opus-4-6
                          returns {"intents": [...], "entities": {...}}
        llm_synthesize()  calls claude-opus-4-6 with adaptive thinking
                          returns a natural language summary
  NO  → both return None
        coordinator falls back to rule-based decompose + template synthesize
        all 33 tests pass with zero API calls
```

The LLM is an optional enhancement — the prototype runs fully offline.

---

## How to Run

```bash
cd prototype
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Run the 4 demo scenarios (fully offline)
.venv/bin/python main.py

# Run the test suite (33 tests)
.venv/bin/python -m pytest tests/ -v

# Run as an MCP server
.venv/bin/python server.py
```

### Enable real LLM calls

```bash
export ANTHROPIC_API_KEY=sk-ant-...
.venv/bin/python main.py
# decompose + synthesize now use claude-opus-4-6
# all 33 tests still pass
```

### Enable LangSmith tracing

```bash
export LANGCHAIN_TRACING_V2=true
export LANGCHAIN_API_KEY=ls__...
export LANGCHAIN_PROJECT=recepto-mcp

.venv/bin/python main.py
# traces appear at smith.langchain.com
# every LangGraph node, agent call, and LLM call is linked under one run ID
```

---

## What Is Mocked vs What Would Be Real

| Component | Mock (prototype) | Real (production) |
|-----------|-----------------|-------------------|
| Play KB search | Keyword scoring over `mocks/plays.json` (4 plays) | Vector search over Recepto KB API |
| Account lookup | JSON file `mocks/accounts.json` (3 accounts) | Recepto platform APIs |
| ICP builder | Inline `_icp_from()` in creator.py | Recepto ICP service |
| Notion playbooks | Static list in `_notion_lookup()` | Notion MCP integration |
| Analytics | Hash-based deterministic numbers in `_analytics_fetch()` | Recepto analytics API |

The coordinator, agent interfaces, memory system, and DAG execution are **not** mocked — they work exactly as they would in production. Replacing a mock means replacing one private function inside one agent file, with no changes to any other file.

---

## Test Suite

33 tests across 2 files:

**test_prototype.py** — the 4 scenarios
- Scenario 1: direct tool runs only recommender, single-wave DAG
- Scenario 2: sequential DAG, creator receives only allowed artifact slice
- Scenario 2: memory deltas are proposed by agents and committed by coordinator
- Scenario 3: parallel DAG, both agents in same wave
- Scenario 4: analyst fails cleanly, creator is skipped (not crashed)
- Schema tests: AGENT_INPUT_DEPS and DEPENDENCY_SEQUENTIAL_FROMS are valid

**test_new_features.py** — M2 memory, LLM fallback, MCP tools
- M2: write/read, persistence across new instance, session isolation, PII rejection
- LLM fallback: both functions return None without API key, coordinator still produces response
- MCP: all 5 tools return non-empty strings, all 5 tools are registered with FastMCP

---

## Design Decisions Worth Discussing

**Why LangGraph?**
The `StateGraph` gives us a persistent, typed state object across all nodes. The `Annotated` reducers on `artifacts` and `pending_memory_deltas` let parallel branches merge cleanly without a last-writer-wins race. The conditional edge on `run_wave_node` lets us loop through waves without writing a manual while loop outside the graph.

**Why not just use async instead of ThreadPoolExecutor?**
The agents are I/O bound in production (API calls). `ThreadPoolExecutor` works with synchronous LangGraph nodes and doesn't require rewriting agents as coroutines. Easy to swap to `asyncio.gather` later.

**Why is the dependency table keyed on intent strings instead of agent kinds?**
Intents are the user-facing concept. An intent maps to exactly one agent, but the routing logic (what depends on what) belongs to the intent layer, not the agent layer. This makes it easy to add a new intent that maps to an existing agent with different dependencies.

**Why does `synthesize_node` handle memory commits instead of doing it inline in each agent?**
Every memory write becomes observable in one place. You can add rate limiting, tenant-level caps, or audit logging to `commit_deltas()` without touching any agent. Agents can't accidentally commit memory if synthesize crashes.
