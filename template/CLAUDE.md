# Agentic Commerce Stack — Starter Template

A complete, working starter kit for building an agentic commerce experience on the full protocol stack: **MCP**, **A2A**, **UCP**, **ACP**, and **AP2**.

## Services

| Service | Port | What it does |
|---|---|---|
| `chat-client` (`@chat-client/src/App.tsx`) | 3000 | React UI + 6-tab Protocol Inspector |
| `merchant-agent` (`@merchant-agent/main.py`) | 10999 | Merchant agent. A2A JSON-RPC, UCP checkout, AP2 token, `/ws/trace` WebSocket |
| `mcp-server` (`@mcp-server/main.py`) | 8001 | 12 MCP tools over an inline sample catalog |

## Run locally

```bash
cp .env.example .env        # add your CEREBRAS_API_KEY
docker-compose up --build
```

Or run manually:

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

## Imports

@merchant-agent/CLAUDE.md
@mcp-server/CLAUDE.md
@chat-client/CLAUDE.md
@evals/CLAUDE.md
