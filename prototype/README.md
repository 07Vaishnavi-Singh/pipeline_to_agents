# Recepto MCP — Prototype

Fully offline LangGraph prototype demonstrating the 3 solutions to pipeline→agent migration problems.

## Run it

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Run the 4 demo scenarios
.venv/bin/python main.py

# Run the test suite (13 tests)
.venv/bin/python -m pytest tests/ -v
```

## What each file solves

| File | Solves | Key concept |
|------|--------|-------------|
| `state.py` | Problem B | `CoordinatorState`, `AgentArtifact`, `AgentInput` — all shared types in one place. `AGENT_INPUT_DEPS` enforces which agent can read which upstream artifact. |
| `memory.py` | Problem B | `MemoryStore` — agents never write here directly. They return `MemoryDelta` proposals; coordinator commits them in `synthesize_node`. |
| `coordinator.py` | Problem B + D | `decompose_node` classifies intent. `_build_execution_waves` runs topological sort on `DEPENDENCY_SEQUENTIAL_FROMS` to produce waves. `run_wave_node` uses `ThreadPoolExecutor` for real parallelism within a wave. |
| `agents/recommender.py` | Problem C | Owns `_kb_search` — reads `mocks/plays.json`. No other agent imports or calls this function. |
| `agents/creator.py` | Problem C | Owns `_icp_from` mock. Reads upstream `recommender` / `analyst` artifacts only via the coordinator-passed slice, never directly. |
| `agents/outreach.py` | Problem C | Owns `_analytics_fetch` + `_notion_lookup`. Deterministic mock (hash-based) so output is stable across runs. |
| `agents/analyst.py` | Problem C | Owns `_platform_lookup` — reads `mocks/accounts.json`. Returns `status: failed` cleanly when account not found. |

## The 4 demo scenarios

| # | Query | Entry | DAG | Demonstrates |
|---|-------|-------|-----|--------------|
| 1 | "recommend a play for fintech…" | `recommend_play` (direct) | `[recommender]` | Problem C — tool isolation; direct tool shallow-wraps coordinator |
| 2 | "recommend a play for SaaS and then create it" | `ask_recepto` | `[recommender] → [creator]` | Problem B — creator receives only the recommendation artifact slice |
| 3 | "analyze Stripe's account and recommend outreach" | `ask_recepto` | `[analyst ∥ outreach]` | Problem D — both agents run in the same wave (parallel) |
| 4 | "analyze AcmeCorp and create a play from it" | `ask_recepto` | `[analyst] → [creator: skipped]` | Problem B — analyst fails cleanly, creator is skipped via artifact status, no exception |

## How the DAG is built (key code path)

```
run_request(query, tool_name=...)
  └─ decompose_node          # classifies intents, extracts entities
  └─ build_dag_node          # topological sort → execution waves
  └─ run_wave_node (×N)      # ThreadPoolExecutor per wave
       └─ _run_one_agent     # checks _should_skip, slices state, calls agent
       └─ AgentArtifact      # returned by every agent (status: ok|failed|skipped)
  └─ synthesize_node         # merges artifacts, commits MemoryDeltas
```

## Routing: direct tool vs ask_recepto

```
Direct tool call (e.g. recommend_play)
  → intent already known → skip NL decompose → single-node DAG
  → still goes through coordinator (trace_id, memory, artifacts)

ask_recepto
  → decompose_natural_language() → 1–4 intents
  → build_dag → may be 1 wave (parallel) or N waves (sequential)
```

## What is mocked

- `mocks/plays.json` — 4 static plays; `kb_search` does keyword scoring
- `mocks/accounts.json` — 3 accounts (Stripe, Notion, Linear)
- ICP builder, Notion lookup, analytics — inline mocks in each agent file
- Decompose + synthesize — rule-based classifier + template strings (no LLM calls)

Nothing calls an external API. Runs fully offline.
