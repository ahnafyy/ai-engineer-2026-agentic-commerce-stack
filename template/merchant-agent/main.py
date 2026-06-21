"""
Template Merchant Agent — Agentic Commerce Stack Starter Kit
=============================================================
Replace SYSTEM_PROMPT with your persona,
add your own tools to OPENAI_TOOLS,
and customize handle_tool_call to call your own MCP server.

Start here: https://github.com/ahnafyy/ai-engineer-2026-agentic-commerce-stack
"""
import os
import json
import uuid
import time

import httpx
import uvicorn
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from openai import AsyncOpenAI

app = FastAPI(title="My Store Agent", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

GITHUB_TOKEN   = os.environ.get("GITHUB_TOKEN", "")
MCP_SERVER     = os.environ.get("MCP_SERVER_URL", "http://mcp-server:8001")
AGENT_BASE_URL = os.environ.get("AGENT_BASE_URL", "http://localhost:10999")

openai_client = AsyncOpenAI(
    base_url="https://models.inference.ai.azure.com",
    api_key=GITHUB_TOKEN,
)

TASKS: dict = {}
_ws_clients: set[WebSocket] = set()

# ── TODO: Customize these ──────────────────────────────────────────────────────
AGENT_CARD = {
    "protocolVersion": "1.0",
    "name":            "My Store Agent",
    "description":     "TODO: describe your agent",
    "url":             AGENT_BASE_URL,
    "version":         "1.0.0",
    "capabilities":    {"streaming": False, "stateTransitionHistory": True},
    "skills":          [{"id": "shopping", "name": "Shopping", "description": "Search and buy", "tags": ["shopping"]}],
    "extensions":      [{"uri": "dev.ucp.shopping.checkout", "required": True, "description": "UCP checkout"}],
    "securitySchemes": {},
}

# TODO: Replace with your system prompt
SYSTEM_PROMPT = """You are a helpful shopping agent for My Store.
You have access to the product_search tool to find products.
Be helpful and concise."""

# TODO: Mirror your MCP tool definitions here for the LLM
OPENAI_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "product_search",
            "description": "Search the product catalog.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query":    {"type": "string"},
                    "in_stock": {"type": "boolean"},
                },
                "required": ["query"],
            },
        },
    },
    # TODO: Add more tools here
]


# ── Core helpers (no changes needed here) ─────────────────────────────────────
async def _broadcast(event: dict) -> None:
    import asyncio
    payload = json.dumps(event)
    dead = set()
    for ws in list(_ws_clients):
        try:
            await ws.send_text(payload)
        except Exception:
            dead.add(ws)
    _ws_clients.difference_update(dead)


async def call_mcp_tool(name: str, args: dict) -> dict:
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.post(f"{MCP_SERVER}/tools/call", json={"name": name, "input": args})
        return r.json()


async def handle_tool_call(name: str, args: dict, task_id: str) -> str:
    import asyncio
    t0 = time.time()
    result = await call_mcp_tool(name, args)
    asyncio.create_task(_broadcast({
        "timestamp": time.time(), "type": "mcp", "tool": name,
        "latency_ms": round((time.time() - t0) * 1000, 2), "task_id": task_id,
        "input": args, "output": result,
    }))
    return json.dumps(result)


async def run_agent(messages: list, task_id: str) -> tuple[str, list]:
    tool_events = []
    msgs = list(messages)
    for _ in range(8):
        response = await openai_client.chat.completions.create(
            model="gpt-4o", messages=msgs, tools=OPENAI_TOOLS, tool_choice="auto", max_tokens=1024,
        )
        msg = response.choices[0].message
        if not msg.tool_calls:
            return msg.content or "", tool_events
        msgs.append({"role": "assistant", "content": msg.content, "tool_calls": [
            {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
            for tc in msg.tool_calls
        ]})
        for tc in msg.tool_calls:
            fn_name = tc.function.name
            fn_args = json.loads(tc.function.arguments)
            result_str = await handle_tool_call(fn_name, fn_args, task_id)
            tool_events.append({"tool": fn_name, "input": fn_args, "output": json.loads(result_str)})
            msgs.append({"role": "tool", "tool_call_id": tc.id, "content": result_str})
    return "Request processed.", tool_events


# ── Endpoints ──────────────────────────────────────────────────────────────────
@app.get("/.well-known/agent-card.json")
def agent_card():
    return AGENT_CARD


@app.websocket("/ws/trace")
async def ws_trace(websocket: WebSocket):
    await websocket.accept()
    _ws_clients.add(websocket)
    try:
        await websocket.send_text(json.dumps({"timestamp": time.time(), "type": "system", "event": "connected"}))
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        _ws_clients.discard(websocket)


@app.post("/a2a")
async def a2a(request: Request):
    body = await request.json()
    rpc_id = body.get("id")
    method = body.get("method", "")
    params = body.get("params", {})

    def ok(r): return {"jsonrpc": "2.0", "id": rpc_id, "result": r}
    def err(c, m): return {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": c, "message": m}}

    if method == "message/send":
        task_id = str(uuid.uuid4())
        context_id = params.get("contextId") or task_id
        user_text = "".join(p["text"] for p in params.get("message", {}).get("parts", []) if p.get("kind") == "text")
        msgs = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user_text}]
        TASKS[task_id] = {"id": task_id, "state": "working", "context_id": context_id}
        try:
            final_text, tool_events = await run_agent(msgs, task_id)
        except Exception as e:
            return err(-32603, str(e))
        TASKS[task_id].update({"state": "completed", "agent_response": final_text, "tool_events": tool_events})
        return ok({
            "id": task_id, "contextId": context_id,
            "status": {"state": "completed", "timestamp": time.time()},
            "artifacts": [{"artifactId": str(uuid.uuid4()), "parts": [{"kind": "text", "text": final_text}]}],
            "metadata": {"tool_events": tool_events},
        })
    return err(-32601, f"Method not found: {method}")


@app.get("/health")
def health():
    return {"status": "ok", "agent": AGENT_CARD["name"]}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=10999)
