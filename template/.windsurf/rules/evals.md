---
trigger: glob
glob: evals/**
---

# Writing and Running Evals

## Plan your test cases before customizing

Before editing any eval file, write down the key behaviors your store agent must have. Each behavior maps to one of the four suites.

| Question about your store | Suite to add it to |
|---|---|
| What queries should trigger `product_search`? | behavior |
| What phrases should create a checkout session? | behavior (multi-turn) |
| Do you have discount codes? What phrasings trigger `apply_discount`? | behavior |
| Are there things the agent must NEVER say? (stock counts, hidden info) | behavior (guardrail tests) |
| Do you have custom tools? What inputs trigger each one? | behavior |
| Do your A2A and MCP endpoints return the right shape? | compliance |
| What is your latency target before a demo? | latency |
| What tone/persona should the LLM judge score against? | quality |

Once you have this list, replace placeholder product names and prompts in `behavior/test_behavior.py` and `quality/judge_quality.py` with real examples from your catalog. A test that references `"Classic Tee"` when your store sells software licenses will never fail — or pass — meaningfully.

---

The template includes four eval suites. Run them all at once:

```bash
# Make sure your stack is running first (docker-compose up or manual)
pip install -r evals/requirements.txt
python evals/run_evals.py --suite all
```

Or a single suite:
```bash
python evals/run_evals.py --suite behavior
python evals/run_evals.py --suite compliance
python evals/run_evals.py --suite latency
python evals/run_evals.py --suite quality
```

The runner expects services at `http://localhost:10999` (agent) and `http://localhost:8001` (MCP server). Override with env vars:
```bash
AGENT_URL=http://my-agent:10999 MCP_URL=http://my-mcp:8001 python evals/run_evals.py --suite all
```

---

## The four suites

### 1. Behavior (`evals/behavior/test_behavior.py`)

**What**: pytest tests that send messages to the agent via A2A and assert the correct MCP tools were called.

**When to use**: Every time you add a tool or change the system prompt. This is your regression suite.

**How to read tool events**:
```python
def _chat(prompt: str, ctx: str = None) -> dict:
    # returns {"text": "...", "tools": ["product_search", "get_product_details"]}
```
`result["tools"]` is the list of MCP tool names the agent called in order.

**Customizing**: Replace `"Classic Tee"` with an actual product in your `PRODUCTS` list. If your agent asks for a discount code before checkout (it should), use a multi-turn test:

```python
def test_checkout_created():
    ctx = str(uuid.uuid4())
    _chat("I want to buy a Classic Tee", ctx=ctx)     # first turn: agent asks about discount
    result = _chat("No discount, please proceed", ctx=ctx)  # second turn: agent creates checkout
    assert "create_checkout_session" in result["tools"]
```

**Key lessons from production testing**:

- **Checkout tests must be multi-turn** if the system prompt tells the agent to ask about discount codes first. A single-message test will fail because the agent pauses to ask.
- **Guardrail tests (stock count, discount leak) can false-positive** if product names or descriptions contain the word you're checking for. Use exact substrings that can't appear naturally.
- **Remove flaky tests** rather than loosening assertions. If the agent calls a tool reliably on direct commands ("apply code X") but not passive phrasing ("I have a code: X"), test the direct form.

### 2. Compliance (`evals/compliance/test_compliance.py`)

**What**: pytest tests that check the A2A and MCP endpoints return correct schemas.

**When to use**: After adding or renaming endpoints, changing the A2A response format, or adding MCP tools.

**What it checks**:
- `/a2a` returns valid JSON-RPC 2.0 with `result.artifacts`
- `result.metadata.tool_events` is a list
- `/tools` lists available MCP tools with names and descriptions
- Agent card at `/.well-known/agent.json` has required fields

**Customizing**: Add assertions for any new fields you add to the agent card or metadata.

### 3. Latency (`evals/latency/bench_latency.py`)

**What**: Fires N concurrent requests and reports p50/p95/p99 latency + throughput.

**When to use**: After infrastructure changes, when switching models, or before a demo.

**Running**:
```bash
BENCH_N=20 python evals/latency/bench_latency.py
```

**What the output means**:
- `p50` — half your requests are faster than this. Target < 3s with Cerebras.
- `p95` — 95% of requests are faster than this. Spikes here = rate limiting.
- If p95 >> p50, you're hitting Cerebras queue time on the free tier (can be 32s). Upgrade to paid.

### 4. Quality (`evals/quality/judge_quality.py`)

**What**: Sends test prompts to the agent, then uses a second Cerebras LLM call to score each response 1–5 on helpfulness, accuracy, protocol_awareness, and tone.

**When to use**: After changing the system prompt persona, adding new products, or changing response format rules.

**Requires**: `CEREBRAS_API_KEY` in the environment (the judge uses the same Cerebras inference endpoint as the agent).

**Customizing**: Replace `TEST_CASES` with prompts relevant to your store. Update `JUDGE_SYSTEM` to describe your store's persona instead of "bakery".

**Interpreting scores**:
- 4.0+ average = good
- 3.0–3.9 = acceptable, consider tweaking system prompt
- < 3.0 = something is wrong with the agent's responses

**If the judge returns all zeros**: Usually means `CEREBRAS_API_KEY` is missing or the judge is hitting a rate limit. The `_extract_json()` helper handles markdown-fenced JSON and truncated responses — if you see parse errors, raise `max_tokens` from 400 to 600.

---

## Adding a new eval

1. Add a test function to the appropriate file
2. Use the `_chat(prompt, ctx)` helper to send messages — it returns `{"text": ..., "tools": [...]}`
3. Assert on `result["tools"]` for tool-call behavior, or `result["text"]` for content checks
4. Run `python evals/run_evals.py --suite behavior` to verify

---

## Debugging a failing eval

```bash
# Run a single test with verbose output
cd evals && pytest behavior/test_behavior.py::test_product_search -v

# Check if the agent is reachable
curl http://localhost:10999/health

# Check MCP tools endpoint
curl http://localhost:8001/tools | python3 -m json.tool
```

Common failure causes:

| Symptom | Likely cause |
|---|---|
| `Connection refused` | Stack not running |
| `JSONDecodeError` | Agent returned empty/malformed response (check agent logs) |
| Tool not in `result["tools"]` | System prompt rule not strong enough — add explicit instruction |
| All quality scores are 0 | `CEREBRAS_API_KEY` not set or rate limited |
| Checkout test fails on single message | Agent is asking about discount codes — make the test multi-turn |
