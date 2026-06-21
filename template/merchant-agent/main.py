"""
Template Merchant Agent — Agentic Commerce Stack Starter Kit
=============================================================
A complete, working merchant agent for the fictional "My Store".

What it does:
  - Speaks the A2A protocol (JSON-RPC 2.0) at POST /a2a
  - Publishes an Agent Card at /.well-known/agent-card.json
  - Publishes a UCP profile at /.well-known/ucp
  - Drives a GPT-4o tool-calling loop that calls your MCP server
  - Implements the UCP checkout lifecycle (REST) used by the chat UI:
        POST /ucp/checkout/{id}/complete   -> READY_FOR_PAYMENT
        POST /ucp/checkout/{id}/confirm    -> COMPLETED + AP2 token
  - Broadcasts a live protocol trace over /ws/trace (WebSocket)

To make it yours:
  1. Edit SYSTEM_PROMPT to give your agent its persona and rules.
  2. Update AGENT_CARD / UCP_PROFILE branding.
  3. Keep OPENAI_TOOLS in sync with the tools your MCP server exposes.

Env vars:
  GITHUB_TOKEN    GitHub PAT with read:packages (calls GPT-4o via GitHub AI Models)
  MCP_SERVER_URL  default http://mcp-server:8001
  AGENT_BASE_URL  default http://localhost:10999

Start here: https://github.com/ahnafyy/ai-engineer-2026-agentic-commerce-stack
"""
import os
import json
import uuid
import time
import asyncio

import httpx
import uvicorn
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from openai import AsyncOpenAI

app = FastAPI(title="My Store Agent", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ── Config ─────────────────────────────────────────────────────────────────────
GITHUB_TOKEN   = os.environ.get("GITHUB_TOKEN", "")
MCP_SERVER     = os.environ.get("MCP_SERVER_URL", "http://mcp-server:8001")
AGENT_BASE_URL = os.environ.get("AGENT_BASE_URL", "http://localhost:10999")
MERCHANT_HOST  = "my-store.demo"

openai_client = AsyncOpenAI(
    base_url="https://models.inference.ai.azure.com",
    api_key=GITHUB_TOKEN,
)

# In-memory task + checkout stores
TASKS: dict     = {}
CHECKOUTS: dict = {}

# ── WebSocket trace broadcast ──────────────────────────────────────────────────
_ws_clients: set[WebSocket] = set()


async def _broadcast(event: dict) -> None:
    """Push a structured trace event to all connected WebSocket clients."""
    if not _ws_clients:
        return
    payload = json.dumps(event)
    dead = set()
    for ws in list(_ws_clients):
        try:
            await ws.send_text(payload)
        except Exception:
            dead.add(ws)
    _ws_clients.difference_update(dead)


# ── TODO: Customize the Agent Card ─────────────────────────────────────────────
AGENT_CARD = {
    "protocolVersion": "1.0",
    "name":            "My Store Agent",
    "description":     "AI shopping assistant for My Store. Supports product discovery, checkout, and fulfillment via ACP/UCP.",
    "url":             AGENT_BASE_URL,
    "version":         "1.0.0",
    "capabilities": {
        "streaming":              True,
        "pushNotifications":      False,
        "stateTransitionHistory": True,
    },
    "skills": [
        {
            "id":          "shopping",
            "name":        "Shopping & Checkout",
            "description": "Search products, build a cart, and complete checkout via UCP",
            "tags":        ["shopping", "checkout", "ucp", "ecommerce"],
        }
    ],
    "extensions": [
        {"uri": "dev.ucp.shopping.checkout",    "required": True,  "description": "UCP-compliant checkout flow"},
        {"uri": "dev.ucp.shopping.fulfillment", "required": False, "description": "Shipping + fulfillment options"},
        {"uri": "dev.ucp.shopping.discount",    "required": False, "description": "Discount / coupon code support"},
    ],
    "securitySchemes": {},
}

# ── TODO: Customize the UCP profile ────────────────────────────────────────────
UCP_PROFILE = {
    "ucp_version": "1.0",
    "merchant": {"name": "My Store (Demo)", "url": AGENT_BASE_URL, "logo": "🛍️", "country": "US"},
    "capabilities": [
        "dev.ucp.shopping.checkout",
        "dev.ucp.shopping.fulfillment",
        "dev.ucp.shopping.discount",
    ],
    "payment_instruments": [
        {"type": "google_pay", "label": "Google Pay"},
        {"type": "apple_pay",  "label": "Apple Pay"},
        {"type": "card",       "label": "Credit / Debit Card"},
    ],
    "fulfillment_options": [
        {"id": "standard", "label": "Standard Shipping", "days": "5-7", "price": 4.99},
        {"id": "express",  "label": "Express Shipping",  "days": "2-3", "price": 9.99},
        {"id": "next_day", "label": "Next Day Delivery", "days": "1",   "price": 19.99},
    ],
    "checkout_endpoint": f"{AGENT_BASE_URL}/ucp/checkout",
}

# ── TODO: Replace with your system prompt ──────────────────────────────────────
SYSTEM_PROMPT = """You are Max, the AI shopping assistant for My Store — a friendly online shop selling apparel, drinkware, and accessories.

You have access to these tools (via MCP):
- product_search: find products by keyword, price, or availability
- inventory_check: verify stock levels for a specific product
- apply_discount: apply a coupon code to a cart subtotal
- get_product_details: get full details for a product (material, weight, care, SKU)
- get_recommendations: suggest products, optionally based on a product or category
- get_store_policy: get store policies — returns, shipping, warranty, and hours
- get_bestsellers: get the top 3 bestselling in-stock products
- create_checkout_session: open an ACP checkout session when the customer is ready to buy

Guidelines:
- Be friendly, helpful, and concise.
- ALWAYS call product_search first before creating a checkout session — you need the product `id` (format: prod_001, etc.) from the search results.
- When a user wants to buy, call product_search to get the product id, then call create_checkout_session with that exact id.
- NEVER guess or make up product IDs — always use the `id` field returned by product_search.
- Always mention price and stock status when showing items.
- Discount codes: SAVE10 (10% off), WELCOME15 (15% off), VIP20 (20% off).

Protocol context (for transparency in the demo):
- You communicate via A2A (Agent2Agent).
- Checkout uses ACP (Agentic Commerce Protocol, spec/2026-04-17) mirrored to UCP.
- Tool calls go through MCP (Model Context Protocol).
- Payments use AP2 (Agent Payments Protocol) with agentic payment tokens.
"""

# ── GPT-4o tool definitions (mirror of MCP tools) ──────────────────────────────
OPENAI_TOOLS = [
    {"type": "function", "function": {
        "name": "product_search",
        "description": "Search the catalog for items matching a query.",
        "parameters": {"type": "object", "properties": {
            "query":     {"type": "string",  "description": "Search term"},
            "in_stock":  {"type": "boolean", "description": "Only return in-stock items"},
            "max_price": {"type": "number",  "description": "Maximum price filter in USD"},
        }, "required": ["query"]},
    }},
    {"type": "function", "function": {
        "name": "inventory_check",
        "description": "Check the inventory level for a specific product.",
        "parameters": {"type": "object", "properties": {
            "product_id": {"type": "string", "description": "The product ID"},
        }, "required": ["product_id"]},
    }},
    {"type": "function", "function": {
        "name": "apply_discount",
        "description": "Apply a discount code to a cart subtotal.",
        "parameters": {"type": "object", "properties": {
            "discount_code": {"type": "string", "description": "Discount/coupon code"},
            "subtotal":      {"type": "number", "description": "Cart subtotal in USD"},
        }, "required": ["discount_code", "subtotal"]},
    }},
    {"type": "function", "function": {
        "name": "get_product_details",
        "description": "Get full details for a product: material, weight, care, SKU.",
        "parameters": {"type": "object", "properties": {
            "product_id": {"type": "string", "description": "Product ID"},
        }, "required": ["product_id"]},
    }},
    {"type": "function", "function": {
        "name": "get_recommendations",
        "description": "Get product recommendations, optionally based on a product or category.",
        "parameters": {"type": "object", "properties": {
            "product_id": {"type": "string", "description": "Product to base recommendations on"},
            "category":   {"type": "string", "description": "Category to recommend from"},
        }},
    }},
    {"type": "function", "function": {
        "name": "get_store_policy",
        "description": "Get store policies: returns, shipping, warranty, hours.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "get_bestsellers",
        "description": "Get the top 3 bestselling in-stock products.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "create_checkout_session",
        "description": "Create an ACP checkout session when the customer wants to purchase. IMPORTANT: call product_search first and use the `id` field (e.g. prod_001) from the results. Never guess product IDs.",
        "parameters": {"type": "object", "properties": {
            "payload": {"type": "object", "properties": {
                "currency":   {"type": "string", "description": "Currency code, e.g. usd"},
                "line_items": {"type": "array", "items": {"type": "object", "properties": {
                    "id":       {"type": "string",  "description": "Product ID"},
                    "quantity": {"type": "integer"},
                }}, "description": "Items to purchase"},
            }, "required": ["currency", "line_items"]},
        }, "required": ["payload"]},
    }},
]


# ── MCP tool helpers ───────────────────────────────────────────────────────────
async def call_mcp_tool(tool_name: str, tool_input: dict) -> dict:
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.post(f"{MCP_SERVER}/tools/call", json={"name": tool_name, "input": tool_input})
        return r.json()


async def handle_tool_call(name: str, args: dict, task_id: str) -> str:
    """Call an MCP tool, broadcast a trace event, and (for checkout) mirror state for UCP REST."""
    t0 = time.time()
    result = await call_mcp_tool(name, args)
    asyncio.create_task(_broadcast({
        "timestamp": time.time(), "type": "mcp", "tool": name,
        "latency_ms": round((time.time() - t0) * 1000, 2), "task_id": task_id,
        "input": args, "output": result,
    }))

    # Mirror an ACP checkout session into CHECKOUTS so the UCP REST endpoints work.
    if name == "create_checkout_session" and "id" in result:
        session_id = result["id"]
        items_for_ucp = [{
            "product_id": li["item"]["id"], "name": li["name"],
            "quantity": li["quantity"], "unit_price": li["unit_amount"] / 100,
        } for li in result.get("line_items", [])]
        subtotal = sum(i["unit_price"] * i["quantity"] for i in items_for_ucp)
        CHECKOUTS[session_id] = {
            "id": session_id, "task_id": task_id, "state": "NOT_READY_FOR_PAYMENT",
            "line_items": items_for_ucp, "subtotal": round(subtotal, 2),
            "shipping": None, "discount": None, "total": round(subtotal, 2),
            "payment_instruments": UCP_PROFILE["payment_instruments"],
            "fulfillment_options": UCP_PROFILE["fulfillment_options"],
            "created_at": time.time(),
        }
        if task_id in TASKS:
            TASKS[task_id]["checkout_id"] = session_id

    return json.dumps(result)


async def run_agent(messages: list, task_id: str) -> tuple[str, list]:
    """Run GPT-4o with a tool-calling loop. Returns (final_text, tool_events)."""
    tool_events = []
    msgs = list(messages)
    for _ in range(8):  # max 8 tool rounds
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
            tool_events.append({"tool": fn_name, "input": fn_args, "call_id": tc.id, "output": json.loads(result_str)})
            msgs.append({"role": "tool", "tool_call_id": tc.id, "content": result_str})
    return "I've processed your request.", tool_events


# ── Discovery endpoints ────────────────────────────────────────────────────────
@app.get("/")
def root():
    return RedirectResponse(url="/.well-known/agent-card.json")


@app.get("/.well-known/agent-card.json")
def get_agent_card():
    return AGENT_CARD


@app.get("/.well-known/ucp")
def get_ucp_profile():
    return UCP_PROFILE


@app.get("/health")
def health():
    return {"status": "ok", "agent": AGENT_CARD["name"]}


# ── WebSocket trace ────────────────────────────────────────────────────────────
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


# ── A2A endpoint ───────────────────────────────────────────────────────────────
@app.post("/a2a")
async def a2a_endpoint(request: Request):
    body   = await request.json()
    rpc_id = body.get("id")
    method = body.get("method", "")
    params = body.get("params", {})

    def rpc_ok(result):  return {"jsonrpc": "2.0", "id": rpc_id, "result": result}
    def rpc_err(code, m): return {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": code, "message": m}}

    if method == "message/send":
        message    = params.get("message", {})
        context_id = params.get("contextId") or str(uuid.uuid4())
        task_id    = str(uuid.uuid4())
        user_text  = "".join(p["text"] for p in message.get("parts", []) if p.get("kind") == "text")

        # Rebuild conversation history from prior tasks in this context
        context_history = []
        for t in TASKS.values():
            if t.get("context_id") == context_id and t.get("agent_response"):
                context_history.append({"role": "user",      "content": t["user_message"]})
                context_history.append({"role": "assistant", "content": t["agent_response"]})

        msgs = [{"role": "system", "content": SYSTEM_PROMPT}] + context_history + [{"role": "user", "content": user_text}]
        TASKS[task_id] = {
            "id": task_id, "context_id": context_id, "state": "working",
            "user_message": user_text, "agent_response": "", "tool_events": [],
            "checkout_id": None, "created_at": time.time(),
        }

        try:
            final_text, tool_events = await run_agent(msgs, task_id)
        except Exception as e:
            TASKS[task_id]["state"] = "failed"
            return rpc_err(-32603, str(e))

        TASKS[task_id].update({"agent_response": final_text, "tool_events": tool_events, "state": "completed"})
        asyncio.create_task(_broadcast({
            "timestamp": time.time(), "type": "a2a", "event": "task_completed",
            "task_id": task_id, "context_id": context_id,
            "tool_count": len(tool_events), "response_length": len(final_text),
        }))

        return rpc_ok({
            "id": task_id, "contextId": context_id,
            "status": {"state": "completed", "timestamp": time.time()},
            "artifacts": [{"artifactId": str(uuid.uuid4()), "parts": [{"kind": "text", "text": final_text}]}],
            "metadata": {
                "tool_events":  tool_events,
                "checkout_id":  TASKS[task_id].get("checkout_id"),
                "ucp_checkout": CHECKOUTS.get(TASKS[task_id].get("checkout_id", ""), None),
            },
        })

    elif method == "tasks/get":
        task_id = params.get("id")
        if task_id not in TASKS:
            return rpc_err(-32001, f"Task {task_id} not found")
        return rpc_ok(TASKS[task_id])

    elif method == "tasks/list":
        return rpc_ok(list(TASKS.values()))

    return rpc_err(-32601, f"Method not found: {method}")


# ── UCP checkout endpoints (driven by the chat UI checkout card) ───────────────
@app.get("/ucp/checkout/{checkout_id}")
def get_checkout(checkout_id: str):
    c = CHECKOUTS.get(checkout_id)
    if not c:
        return JSONResponse(status_code=404, content={"error": "Checkout not found"})
    return c


@app.post("/ucp/checkout/{checkout_id}/complete")
async def complete_checkout(checkout_id: str, request: Request):
    c = CHECKOUTS.get(checkout_id)
    if not c:
        return JSONResponse(status_code=404, content={"error": "Checkout not found"})
    body = await request.json()
    c["state"]             = "READY_FOR_PAYMENT"
    c["shipping_address"]  = body.get("shipping_address", {})
    c["selected_payment"]  = body.get("payment_instrument", "card")
    c["selected_shipping"] = body.get("fulfillment_option", "standard")
    shipping_cost = next((f["price"] for f in c["fulfillment_options"] if f["id"] == c["selected_shipping"]), 4.99)
    c["shipping"] = shipping_cost
    c["total"]    = round(c["subtotal"] + shipping_cost - (c.get("discount") or 0), 2)
    return c


@app.post("/ucp/checkout/{checkout_id}/confirm")
async def confirm_checkout(checkout_id: str, request: Request):
    c = CHECKOUTS.get(checkout_id)
    if not c:
        return JSONResponse(status_code=404, content={"error": "Checkout not found"})

    token_id = f"tok_{uuid.uuid4().hex[:12]}"
    ap2_token = {
        "token_id":           token_id, "sub": "user_demo_001",
        "intent":             f"purchase:retail:{','.join(i['name'].lower().replace(' ', '_') for i in c['line_items'])}",
        "merchant_scope":     MERCHANT_HOST, "max_amount": c["total"], "currency": "USD",
        "expires_at":         "2026-12-31T23:59:59Z", "single_use": True,
        "revocation_url":     f"https://pay.agent/revoke/{token_id}",
        "user_consent_proof": "vc:credential:my_store_demo", "issued_at": time.time(),
    }
    order_id = f"ORD-{uuid.uuid4().hex[:8].upper()}"
    c.update({"state": "COMPLETED", "order_id": order_id, "ap2_token": ap2_token, "confirmed_at": time.time()})

    asyncio.create_task(_broadcast({
        "timestamp": time.time(), "type": "ucp", "event": "checkout_confirmed",
        "checkout_id": checkout_id, "order_id": order_id, "total": c["total"],
        "ap2_token_id": ap2_token["token_id"],
    }))

    return {
        "state": "COMPLETED", "order_id": order_id, "checkout_id": checkout_id,
        "total": c["total"], "line_items": c["line_items"],
        "shipping_address": c.get("shipping_address", {}), "ap2_token": ap2_token,
        "message": f"🎉 Order {order_id} confirmed! Your items will ship in 5-7 business days.",
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=10999)
