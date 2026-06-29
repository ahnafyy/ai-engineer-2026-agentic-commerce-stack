# Agentic Commerce Stack — Starter Template

A complete, **working** starter kit for building an agentic commerce experience on the
full protocol stack: **MCP**, **A2A**, **UCP**, **ACP**, and **AP2**.

Fork this folder, swap in your own catalog and persona, and you have a running agent
that an AI surface (and the included chat UI) can shop and check out against.

Demo store: **My Store** 🛍️ — a generic shop you replace with your own.

## What's inside

| Service | Port | What it does |
|---|---|---|
| `chat-client` | 3000 | React UI + 6-tab Protocol Inspector (A2A · MCP · UCP · ACP · Payment · ⚡ Timeline) |
| `merchant-agent` | 10999 | Merchant agent. A2A JSON-RPC, UCP checkout, AP2 token, `/ws/trace` WebSocket |
| `mcp-server` | 8001 | 12 MCP tools over an inline sample catalog (no external DB needed) |

Unlike the full demo, this template has **no `catalog-sync` service** — the catalog
lives inline in `mcp-server/main.py` so the stack runs with as few moving parts as possible.

## Quick start

```bash
cp .env.example .env        # add your CEREBRAS_API_KEY
docker-compose up --build
```

Then open <http://localhost:3000> and try:

1. **"show me what you have in stock"** → watch the **MCP** and **⚡ Timeline** tabs.
2. **"add a Classic Tee to my cart and start checkout"** → **A2A**, **UCP**, and **ACP** tabs light up.
3. Pick a payment method → **Proceed to Payment** → **Confirm & Issue AP2 Token**.
4. Open the **💳 Payment** tab to see the decoded AP2 mandate.

## Run without Docker

```bash
# 1. MCP server
cd mcp-server && pip install -r requirements.txt
uvicorn main:app --port 8001

# 2. Merchant agent (new terminal)
cd merchant-agent && pip install -r requirements.txt
CEREBRAS_API_KEY=... MCP_SERVER_URL=http://localhost:8001 uvicorn main:app --port 10999

# 3. Chat client (new terminal)
cd chat-client && npm install
REACT_APP_AGENT_URL=http://localhost:10999 npm start
```

## Make it yours

| To change… | Edit… |
|---|---|
| Products & prices | `PRODUCTS` in `mcp-server/main.py` |
| Discount codes | `DISCOUNT_CODES` in `mcp-server/main.py` |
| Store policies | `STORE_POLICY` in `mcp-server/main.py` |
| Agent persona & rules | `SYSTEM_PROMPT` in `merchant-agent/main.py` |
| Agent identity / UCP profile | `AGENT_CARD` / `UCP_PROFILE` in `merchant-agent/main.py` |
| Add a tool | Add it to `MCP_TOOLS` + the dispatch table (`mcp-server`) **and** `OPENAI_TOOLS` (`merchant-agent`) |
| UI branding | `chat-client/src/App.tsx` (`header-logo`, `quickPrompts`, welcome message) |

> Keep the MCP tool list and the agent's `OPENAI_TOOLS` in sync — the agent can only
> call tools it knows about, and the MCP server can only run tools it has implemented.

## Protocol map

```
Chat UI ──A2A (JSON-RPC 2.0)──▶ Merchant Agent (Cerebras)
                                  │
                                  ├──MCP tool calls──▶ MCP Server (inline catalog)
                                  │
                                  └──UCP checkout / AP2 token──▶ Chat UI checkout card
```

## Running evals

The `evals/` directory contains four test suites that run against a live stack.

```bash
# Install eval dependencies
pip install -r evals/requirements.txt

# Run all suites (stack must be running)
python evals/run_evals.py --suite all

# Run a single suite
python evals/run_evals.py --suite behavior
python evals/run_evals.py --suite compliance
python evals/run_evals.py --suite latency
python evals/run_evals.py --suite quality
```

| Suite | What it checks |
|---|---|
| `behavior` | Agent calls the correct MCP tools for each user intent |
| `compliance` | A2A + MCP endpoints return valid schemas |
| `latency` | p50 / p95 / p99 response times |
| `quality` | LLM judge scores responses 1–5 on helpfulness, accuracy, protocol, tone |

> `quality` requires `CEREBRAS_API_KEY` — it uses a second Cerebras call as the judge.

Customize `evals/behavior/test_behavior.py` (replace `"Classic Tee"` with a real product)
and `evals/quality/judge_quality.py` (`TEST_CASES` and `JUDGE_SYSTEM`) for your store.

## Copilot instruction files

The `.vscode/` directory contains GitHub Copilot instruction files that guide AI-assisted
development for each part of the stack. They are automatically applied when you work on
the relevant files.

| File | When it applies |
|---|---|
| `.vscode/merchant-agent.instructions.md` | Editing `merchant-agent/` — system prompt patterns, adding tools end-to-end |
| `.vscode/customer-agent.instructions.md` | Editing `chat-client/` — A2A protocol, multi-turn, reading tool events |
| `.vscode/product-feed.instructions.md` | Editing `mcp-server/` — product format, ACP/UCP/Meta feed formats |
| `.vscode/evals.instructions.md` | Editing `evals/` — writing tests, multi-turn patterns, debugging failures |

Built from the [Agentic Commerce Stack](https://github.com/ahnafyy/ai-engineer-2026-agentic-commerce-stack).
