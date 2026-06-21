# Evaluation Guide

The `evals/` harness checks that the stack actually behaves — not just that it boots. There are four suites driven by one runner, `evals/run_evals.py`.

## The four suites

| Suite | Type | Needs GPT-4o? | Checks |
|---|---|---|---|
| **behavior** | pytest | ✅ yes (A2A) | Did the agent call the *right* MCP tools for each prompt, and does a buy reach `COMPLETED` with an order + AP2 token? |
| **compliance** | pytest | partial | Do MCP `/tools`, the A2A agent card + JSON-RPC, the UCP profile + checkout, and the ACP feed match their specs? |
| **latency** | subprocess | no¹ | P50/P95/P99 for each MCP tool and the catalog feed (and A2A round-trip if quota allows). |
| **quality** | subprocess | ✅ yes (judge + agent) | A GPT-4o judge scores each response 1–5 on helpfulness, accuracy, protocol_awareness, and tone. |

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
set -a; source .env; set +a            # GITHUB_TOKEN for the quality judge
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
| `GITHUB_TOKEN` | — | required for `quality` |

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

The agent and the quality judge both call **GPT-4o via GitHub Models** (`https://models.inference.ai.azure.com`, authed with `GITHUB_TOKEN`). The free tier allows roughly **50 GPT-4o requests/day per account** on a **rolling 24-hour** window (the `UserByModelByDay` counter).

A single `--suite all` run spends ~18 of those (behavior ~10, quality ~10, compliance ~3); the latency A2A rows and any live demo clicking share the same budget. When it's exhausted you'll see:

```
429 - RateLimitReached: Rate limit of 50 per 86400s exceeded for UserByModelByDay
```

When that happens:

- The agent returns a JSON-RPC **error** (not a result), so `behavior` tool lists come back empty and the A2A `compliance` test fails.
- `quality` reports **FAIL** with a rate-limit hint (it no longer mislabels an all-zero run as PASS).
- `latency`'s MCP/feed rows still pass; its A2A rows may not.

**To get a clean run:** stop making GPT-4o calls for ~24h so the quota refills, then run `--suite all` once. To remove the cap entirely, point the agent/judge at a paid OpenAI or Azure OpenAI key (per-token billing, no daily ceiling) instead of GitHub Models.

## Adding a case

- **behavior:** add a method to a `Test*` class in `behavior/test_behavior.py`; assert on `_tool_names(resp)` or the UCP checkout result.
- **quality:** append a dict to `TEST_CASES` in `quality/judge_quality.py` (`prompt`, `expected_tools`, `note`).
- **compliance:** add a test asserting on a response schema in `compliance/test_compliance.py`.
- **latency:** add a scenario to the benchmark list in `latency/bench_latency.py`.
