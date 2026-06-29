# The Agentic Commerce Stack

> **Talk by Ahnaf Prio · World AI Engineer Fair 2026 · July 2, 2026 · 20 min**

A reference implementation of the full agentic commerce protocol stack — **MCP**, **A2A**, **ACP**, **UCP**, and **AP2** — with engineering-grade tooling: live catalog sync, protocol compliance evals, latency benchmarks, LLM quality scoring, and a real-time WebSocket trace inspector.

Demo merchant: **Purrfect Bites** 🐱 — a fictional cat bakery.

## Quick Start

```bash
git clone https://github.com/ahnafyy/ai-engineer-2026-agentic-commerce-stack
cd ai-engineer-2026-agentic-commerce-stack
cp .env.example .env        # add your CEREBRAS_API_KEY
docker-compose up --build
```

| Service | URL | What it does |
|---|---|---|
| Chat UI + Protocol Inspector | http://localhost:3000 | React UI with 6 inspector tabs including real-time Timeline |
| Merchant Agent (A2A + UCP + WS) | http://localhost:10999 | Merchant agent, A2A JSON-RPC, WebSocket trace |
| MCP Server | http://localhost:8001 | 12 MCP tools, live catalog from catalog-sync |
| Catalog Sync (ETL batch job) | http://localhost:8002 | ACP + UCP + Meta feed publisher, 60s schedule |

**`CEREBRAS_API_KEY`** — a Cerebras API key, used to call the model (default `gpt-oss-120b`) via Cerebras inference. Get one at https://cloud.cerebras.ai. Override the model with `CEREBRAS_MODEL`.

### Run without Docker

On a fresh Mac with only Homebrew and Git, run the setup script first to install Docker, Node, and Python:

```bash
./scripts/setup.sh          # installs Docker Desktop, Node 20, Python 3.13 + 3.11
```

Then use the launcher — it creates a `.venv`, installs deps, and starts catalog-sync, the MCP server, the merchant agent, and the React dev server, all wired to `localhost`:

```bash
cp .env.example .env        # add your CEREBRAS_API_KEY
./scripts/app-start.sh      # Ctrl+C stops everything
```

## Documentation

Deep dives live in [`docs/`](docs/README.md):

- [Architecture](docs/architecture.md) — services, ports, env vars, caching, and the lifecycle of one chat turn
- [Protocol stack](docs/protocols.md) — MCP, A2A, ACP, UCP, AP2 explained, with where each lives and example payloads
- [Evaluation guide](docs/evals.md) — running the four suites, reading the output, and the Cerebras rate-limit caveat

## Demo Flow

1. Open http://localhost:3000
2. **"show me cookies"** → **MCP tab** shows tool calls · **⚡ Timeline tab** shows real-time events with latency
3. **"add Kitten Mittons Shortbread to cart"** → **A2A tab** · **UCP tab** · **ACP tab** light up
4. Complete checkout → **Payment tab** shows the decoded AP2 token
5. Show http://localhost:8002/feed/acp and http://localhost:8002/feed/ucp — live feed diff
6. Edit `demo/catalog-sync/data/products.json`, POST to http://localhost:8002/sync/trigger — watch MCP reflect the change

## Live Catalog Demo

The MCP server has **no hardcoded catalog**. Products come from `catalog-sync` via a live ACP feed with a 5-second TTL cache.

```bash
# Edit a product price or stock level
$EDITOR demo/catalog-sync/data/products.json

# Trigger an immediate sync
curl -X POST http://localhost:8002/sync/trigger

# Verify MCP reflects the change
curl -X POST http://localhost:8001/tools/call \
  -H "Content-Type: application/json" \
  -d '{"name":"product_search","input":{"query":"shortbread"}}'
```

## Running Evals

**With Docker:**
```bash
# All suites
docker-compose run --rm evals python run_evals.py --suite all --report

# Individual suites
docker-compose run --rm evals python run_evals.py --suite behavior
docker-compose run --rm evals python run_evals.py --suite compliance
docker-compose run --rm evals python run_evals.py --suite latency
docker-compose run --rm evals python run_evals.py --suite quality

# Results saved to evals/results/<timestamp>.json
```

**Without Docker** (requires `app-start.sh` already running):
```bash
./scripts/run-evals.sh          # all suites
./scripts/run-evals.sh behavior
./scripts/run-evals.sh compliance
./scripts/run-evals.sh latency
./scripts/run-evals.sh quality
```

| Eval Suite | What it checks |
|---|---|
| **behavior** | Did the agent call the right MCP tools? Did checkout reach COMPLETED? |
| **compliance** | Do MCP /tools, A2A JSON-RPC, UCP checkout, and ACP feed match their specs? |
| **latency** | P50/P95/P99 per MCP tool + A2A round-trip |
| **quality** | Cerebras judge scores: helpfulness, accuracy, protocol_awareness, tone (1–5) |

> **Heads up — rate limits.** The agent and the quality judge call a model via Cerebras inference. Free-tier keys have per-minute and per-day request/token caps; a full `--suite all` run plus live demoing can exhaust them, and the LLM-dependent suites then fail with a `429`. See [docs/evals.md](docs/evals.md#rate-limits) for details and how to run off the cap.

## Repo Structure

```
demo/
  catalog-sync/       ETL batch service — ACP + UCP + Meta feed publisher (port 8002)
    data/products.json  ← your "database"
  mcp-server/         MCP tool server, reads live from catalog-sync (port 8001)
  merchant-agent/     A2A + UCP agent + /ws/trace WebSocket broadcast (port 10999)
  chat-client/        React UI + Protocol Inspector (6 tabs inc. Timeline) (port 3000)
evals/
  behavior/           Agent tool-call assertions
  compliance/         MCP/A2A/UCP/ACP schema validators
  latency/            P50/P95/P99 benchmarks
  quality/            LLM judge scorecard
  run_evals.py        CLI runner (--suite, --report)
template/             Starter kit — fork this to build your own stack
  evals/              Same 4 suites, pre-wired for localhost, ready to customize
  .vscode/            GitHub Copilot instruction files (merchant-agent, customer-agent,
                       product-feed, evals) — applied automatically when editing each area
slides/index.html     Reveal.js engineering deck (20 min)
docker-compose.yml
```

## Protocol Stack

```
┌─────────────────────────────────────────────────────────────┐
│                     Chat Client / User                       │
└───────────────────────┬─────────────────────────────────────┘
                        │ A2A (JSON-RPC 2.0)
┌───────────────────────▼─────────────────────────────────────┐
│              Merchant Agent (Cerebras)                       │
│   /.well-known/agent-card.json  ·  /.well-known/ucp          │
│   /ucp/checkout/{id}            ·  /ws/trace (WebSocket)     │
└──────────┬────────────────────────────────┬─────────────────┘
           │ MCP (tool calls)               │ UCP / ACP (checkout)
┌──────────▼────────────┐     ┌─────────────▼────────────────┐
│    MCP Server          │     │  Catalog Sync (ETL)           │
│  product_search        │◄────│  /feed/acp  (OpenAI spec)     │
│  inventory_check       │     │  /feed/ucp  (Google spec)     │
│  create_checkout_session│    │  /feed/meta (Meta spec)       │
│  + 9 more tools        │     │  POST /sync/trigger           │
└────────────────────────┘     └──────────────────────────────┘
                   AP2 token issued on checkout confirm
```

## Tech Stack

Python 3.13 + FastAPI + APScheduler · React 18 + TypeScript · Cerebras inference · pytest + rich · Reveal.js 5.x · Docker Compose

## Resources

- [ACP (OpenAI)](https://developers.openai.com/commerce) · [ACP Products API](https://developers.openai.com/commerce/specs/api/products)
- [UCP (Google)](https://developers.google.com/merchant/ucp) · [UCP Merchant Center](https://developers.google.com/merchant/ucp/guides/merchant-center)
- [A2A Protocol](https://github.com/a2aproject/A2A)
- [MCP](https://modelcontextprotocol.io)
- [AP2](https://agenticcommerce.dev)

## Template

Want to build your own agentic commerce stack? The `template/` folder contains a minimal 3-service skeleton (MCP server, merchant agent, chat client) with all the boilerplate wired up. Fork the repo (it's a GitHub template) and start from there.

```bash
# Clone and replace the template with your own catalog + system prompt
cp -r template/ my-store/
cd my-store/
# Edit mcp-server/main.py → add your PRODUCTS list
# Edit merchant-agent/main.py → set SYSTEM_PROMPT + OPENAI_TOOLS
docker-compose up --build
```
