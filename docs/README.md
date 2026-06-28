# Documentation

Deep-dive docs for **The Agentic Commerce Stack**. For setup and the demo script, see the [root README](../README.md).

| Doc | What's inside |
|---|---|
| [architecture.md](architecture.md) | Services, ports, env vars, caching, and the full lifecycle of a single chat turn through every protocol |
| [protocols.md](protocols.md) | What MCP, A2A, ACP, UCP, and AP2 are — and exactly where each lives in this repo, with example payloads |
| [evals.md](evals.md) | The four eval suites, how to run them locally and in Docker, how to read the output, and the Cerebras rate-limit caveat |

## At a glance

```
demo/
  catalog-sync/    ETL batch service — publishes ACP + UCP + Meta feeds        (:8002)
  mcp-server/      MCP tool server — reads the live ACP feed (5s TTL cache)    (:8001)
  merchant-agent/  Merchant agent — A2A + UCP + AP2 + /ws/trace WebSocket        (:10999)
  chat-client/     React UI + 6-tab Protocol Inspector                         (:3000)
evals/             behavior · compliance · latency · quality  + run_evals.py
template/          Fork-me starter kit (generic "My Store")
```
