# Evaluation Guide

The `evals/` harness checks that the stack actually behaves — not just that it boots. There are four suites driven by one runner, `evals/run_evals.py`.

## The four suites

| Suite | Type | Needs LLM? | Checks |
|---|---|---|---|
| **behavior** | pytest | ✅ yes (A2A) | Did the agent call the *right* MCP tools for each prompt, and does a buy reach `COMPLETED` with an order + AP2 token? |
| **compliance** | pytest | partial | Do MCP `/tools`, the A2A agent card + JSON-RPC, the UCP profile + checkout, and the ACP feed match their specs? |
| **latency** | subprocess | no¹ | P50/P95/P99 for each MCP tool and the catalog feed (and A2A round-trip if quota allows). |
| **quality** | subprocess | ✅ yes (judge + agent) | A Cerebras model judges each response 1–5 on helpfulness, accuracy, protocol_awareness, and tone. |

¹ The MCP/feed latency rows need no LLM; the A2A latency rows do.

## Running

### Docker (matches CI / the README)

```bash
docker-compose up -d                                   # start the stack
docker-compose run --rm evals python run_evals.py --suite all --report
docker-compose run --rm evals python run_evals.py --suite compliance
```

Inside the compose network the suites reach services by hostname (`mcp-server:8001`, `merchant-agent:10999`, `catalog-sync:8002`) — those are the built-in defaults.

### Local (no Docker)

The suites default to **Docker hostnames**, so when the stack runs on your host you must override the URLs:

```bash
set -a; source .env; set +a            # CEREBRAS_API_KEY for the quality judge
cd evals
AGENT_URL=http://localhost:10999 \
MCP_URL=http://localhost:8001 \
CATALOG_SYNC_URL=http://localhost:8002 \
  python run_evals.py --suite all
```

| Var | Default (Docker) | Local |
|---|---|---|
| `AGENT_URL` | `http://merchant-agent:10999` | `http://localhost:10999` |
| `MCP_URL` | `http://mcp-server:8001` | `http://localhost:8001` |
| `CATALOG_SYNC_URL` | `http://catalog-sync:8002` | `http://localhost:8002` |
| `CEREBRAS_API_KEY` | — | required for `quality` |

`--report` writes a timestamped JSON to `evals/results/`.

## Reading the output

Each suite prints a panel; the run ends with a summary table:

```
╭────────────┬────────┬──────────╮
│ Suite      │ Status │ Duration │
├────────────┼────────┼──────────┤
│ behavior   │  PASS  │  ...     │
│ compliance │  PASS  │  ...     │
│ latency    │  PASS  │  ...     │
│ quality    │  PASS  │  ...     │
╰────────────┴────────┴──────────╯
```

The runner exits **0** only if every suite passes; non-zero otherwise — so it's CI-friendly.

- **behavior / compliance** are pytest: status is driven by pass/fail counts.
- **latency** passes if the benchmark subprocess completes and its JSON parses.
- **quality** passes only if **every** test case was graded. A case that couldn't be graded (agent error, missing token, judge error) is a **FAIL**, not a 0 — see below.

## Rate limits

The agent and the quality judge both call a model via **Cerebras inference** (`https://api.cerebras.ai/v1`, authed with `CEREBRAS_API_KEY`; model set by `CEREBRAS_MODEL`, default `gpt-oss-120b`). Free-tier keys have per-minute request/token limits and a daily token ceiling.

A single `--suite all` run spends ~18 model calls (behavior ~10, quality ~10, compliance ~3); the latency A2A rows and any live demo clicking share the same budget. When a limit is hit you'll see a `429`:

```
429 - Too Many Requests: rate limit exceeded
```

When that happens:

- The agent returns a JSON-RPC **error** (not a result), so `behavior` tool lists come back empty and the A2A `compliance` test fails.
- `quality` reports **FAIL** with a rate-limit hint (it no longer mislabels an all-zero run as PASS).
- `latency`'s MCP/feed rows still pass; its A2A rows may not.

**To get a clean run:** wait for the per-minute window to reset (or pace the suites), then run `--suite all` once. To raise the ceiling, upgrade the Cerebras plan or use a higher-tier key, or set `CEREBRAS_MODEL` to a model with more headroom.

## Adding a case

- **behavior:** add a method to a `Test*` class in `behavior/test_behavior.py`; assert on `_tool_names(resp)` or the UCP checkout result.
- **quality:** append a dict to `TEST_CASES` in `quality/judge_quality.py` (`prompt`, `expected_tools`, `note`).
- **compliance:** add a test asserting on a response schema in `compliance/test_compliance.py`.
- **latency:** add a scenario to the benchmark list in `latency/bench_latency.py`.
