"""
Purrfect Bites Cat Bakery — Merchant Agent
- Speaks A2A Protocol (JSON-RPC 2.0)
- Implements UCP extension (checkout, fulfillment, discount)
- Calls MCP server internally for tool execution
- Uses Cerebras inference (OpenAI-compatible API)
"""
import os, json, uuid, time, asyncio, httpx
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from openai import AsyncOpenAI
from typing import Optional

app = FastAPI(title="Purrfect Bites Cat Bakery Agent", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Config ────────────────────────────────────────────────────────────────────
CEREBRAS_API_KEY  = os.environ.get("CEREBRAS_API_KEY", "")
CEREBRAS_BASE_URL = os.environ.get("CEREBRAS_BASE_URL", "https://api.cerebras.ai/v1")
CEREBRAS_MODEL    = os.environ.get("CEREBRAS_MODEL", "gpt-oss-120b")
MCP_SERVER    = os.environ.get("MCP_SERVER_URL", "http://mcp-server:8001")
AGENT_BASE_URL = os.environ.get("AGENT_BASE_URL", "http://localhost:10999")

openai_client = AsyncOpenAI(
    base_url=CEREBRAS_BASE_URL,
    api_key=CEREBRAS_API_KEY,
)

# In-memory task + checkout store
TASKS: dict     = {}
CHECKOUTS: dict = {}

# ── WebSocket trace broadcast ─────────────────────────────────────────────────
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

# ── A2A Agent Card ────────────────────────────────────────────────────────────
AGENT_CARD = {
    "protocolVersion": "1.0",
    "name":            "Purrfect Bites Agent",
    "description":     "AI-powered bakery agent for Purrfect Bites 🐱🎂 — a cozy virtual cat bakery. Meet Ginny, our resident calico and chief taste-tester 🐾 Supports baked goods discovery, checkout, and fulfillment via ACP/UCP.",
    "url":             AGENT_BASE_URL,
    "version":         "1.0.0",
    "capabilities": {
        "streaming":           True,
        "pushNotifications":   False,
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
        {
            "uri":         "dev.ucp.shopping.checkout",
            "required":    True,
            "description": "UCP-compliant checkout flow"
        },
        {
            "uri":         "dev.ucp.shopping.fulfillment",
            "required":    False,
            "description": "Shipping + fulfillment options"
        },
        {
            "uri":         "dev.ucp.shopping.discount",
            "required":    False,
            "description": "Discount / coupon code support"
        }
    ],
    "securitySchemes": {},
}

# ── UCP Profile ───────────────────────────────────────────────────────────────
UCP_PROFILE = {
    "ucp_version": "1.0",
    "merchant": {
        "name":    "Purrfect Bites Cat Bakery (Demo)",
        "url":     AGENT_BASE_URL,
        "logo":    "🐱🎂",
        "country": "US",
    },
    "capabilities": [
        "dev.ucp.shopping.checkout",
        "dev.ucp.shopping.fulfillment",
        "dev.ucp.shopping.discount",
    ],
    "payment_instruments": [
        # UCP (Google) uses Google Pay as the payment handler.
        # The wallet credential is passed as a payment token — not tied to any specific PSP.
        {"type": "google_pay", "label": "Google Pay"},
        {"type": "apple_pay",  "label": "Apple Pay"},
        {"type": "card",       "label": "Credit / Debit Card"},
    ],
    "fulfillment_options": [
        {"id": "standard", "label": "Standard Shipping",  "days": "5-7",  "price": 4.99},
        {"id": "express",  "label": "Express Shipping",   "days": "2-3",  "price": 9.99},
        {"id": "next_day", "label": "Next Day Delivery",  "days": "1",    "price": 19.99},
    ],
    "checkout_endpoint": f"{AGENT_BASE_URL}/ucp/checkout",
}

# ── MCP tool helpers ──────────────────────────────────────────────────────────
async def call_mcp_tool(tool_name: str, tool_input: dict) -> dict:
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.post(f"{MCP_SERVER}/tools/call",
                              json={"name": tool_name, "input": tool_input})
        return r.json()

# Cerebras tool definitions (mirror of MCP tools)
OPENAI_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "product_search",
            "description": "Search the Purrfect Bites catalog for items matching a query. NOTE: In production ACP/UCP, products are discovered via a pre-submitted product feed — this tool is used internally by the agent after catalog ingestion.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query":     {"type": "string",  "description": "Search term"},
                    "in_stock":  {"type": "boolean", "description": "Only return in-stock items"},
                    "max_price": {"type": "number",  "description": "Maximum price filter in USD"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "inventory_check",
            "description": "Check the inventory level for a specific product.",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_id": {"type": "string", "description": "The product ID"}
                },
                "required": ["product_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "apply_discount",
            "description": "Apply a discount code to a cart subtotal.",
            "parameters": {
                "type": "object",
                "properties": {
                    "discount_code": {"type": "string", "description": "Discount/coupon code"},
                    "subtotal":      {"type": "number", "description": "Cart subtotal in USD"}
                },
                "required": ["discount_code", "subtotal"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_product_details",
            "description": "Get full details for a product: allergens, weight, shelf life, ingredients.",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_id": {"type": "string", "description": "Product ID"}
                },
                "required": ["product_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_recommendations",
            "description": "Get product recommendations, optionally based on a product or category.",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_id": {"type": "string", "description": "Product to base recommendations on"},
                    "category":   {"type": "string", "description": "Category to recommend from"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_store_policy",
            "description": "Get store policies: returns, allergens, shipping, hours.",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_bestsellers",
            "description": "Get the top 3 bestselling products at Purrfect Bites.",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_checkout_session",
            "description": "Create an ACP checkout session when the customer wants to purchase. IMPORTANT: You MUST call product_search first and use the `id` field (e.g. prod_012) from the search results. Never guess product IDs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "meta": {
                        "type": "object",
                        "properties": {
                            "api_version":     {"type": "string"},
                            "idempotency_key": {"type": "string"},
                        }
                    },
                    "payload": {
                        "type": "object",
                        "properties": {
                            "currency":   {"type": "string", "description": "Currency code, e.g. usd"},
                            "line_items": {
                                "type": "array",
                                "items": {"type": "object", "properties": {
                                    "id":       {"type": "string", "description": "Product ID"},
                                    "quantity": {"type": "integer"}
                                }},
                                "description": "Items to purchase"
                            },
                            "capabilities": {"type": "object"}
                        },
                        "required": ["currency", "line_items"]
                    }
                },
                "required": ["payload"]
            }
        }
    }
]

SYSTEM_PROMPT = """You are Ginny, the AI shopping agent for Purrfect Bites — a cozy virtual cat bakery 🐱🎂. You help customers discover and order delightful cat-themed baked goods.

You have access to these tools (via MCP):
- product_search: find baked goods in our catalog by keyword, price, or availability
- inventory_check: verify stock levels for a specific product
- apply_discount: apply a coupon code to a cart subtotal
- get_product_details: get full details for a product (allergens, weight, shelf life, ingredients)
- get_recommendations: get personalized product recommendations, optionally based on a product or category
- get_store_policy: get store policies — returns, allergens, shipping options, and hours
- get_bestsellers: get the top 3 bestselling in-stock products
- create_checkout_session: open an ACP checkout session when the customer is ready to order

Guidelines:
- Be warm, playful, and a little cat-obsessed 🐾
- Use cat puns naturally but don't overdo it
- ALWAYS call product_search first before creating a checkout session — you need the product `id` (format: prod_001, prod_012, etc.) from the search results
- When a user wants to buy, call product_search to get the product id, then call create_checkout_session with that exact id
- NEVER guess or make up product IDs — always use the `id` field returned by product_search
- Do NOT use markdown tables in your responses — write in plain conversational prose with emoji bullet points
- Format product listings like this (one per line): 🐾 **Name** — $X.XX — short description
- Do NOT include stock counts or product IDs in your response text — even if the customer directly asks "how many do you have?" or "what's the stock?", just say the item is available and redirect to ordering. Never reveal a number.
- If a product was already found in this conversation, use its id directly from the previous tool result — do NOT call product_search again just to get the id
- BEFORE creating a checkout session, always ask the customer if they have a discount code and apply it first if they do — do NOT list or reveal discount codes unprompted
- Whenever a customer mentions, shares, or implies a discount or coupon code — ANY phrasing such as "I have a code", "use code X", "my promo is X", "apply MEOW20", "I have a discount code: X" — you MUST call apply_discount with the code and the product subtotal immediately. NEVER just say "I'll apply that" in text without calling the tool first.
- The bakery is called "Purrfect Bites" — this is a demo for AI Engineer 2026

Protocol context (for transparency in the demo):
- You communicate via A2A protocol (Agent2Agent)
- Checkout uses ACP (Agentic Commerce Protocol, spec/2026-04-17) mirrored to UCP (Universal Commerce Protocol)
- Tool calls go through MCP (Model Context Protocol)
- Payments use AP2 (Agent Payments Protocol) with agentic payment tokens
"""

# ── Tool dispatch ──────────────────────────────────────────────────────────────
async def handle_tool_call(name: str, args: dict, task_id: str) -> str:
    t0 = time.time()
    if name == "create_checkout_session":
        result = await call_mcp_tool("create_checkout_session", args)
        latency_ms = round((time.time() - t0) * 1000, 2)
        asyncio.create_task(_broadcast({
            "timestamp": time.time(), "type": "mcp", "tool": name,
            "latency_ms": latency_ms, "task_id": task_id,
            "input": args, "output": result,
        }))
        # Mirror into CHECKOUTS so UCP REST endpoints continue to work
        if "id" in result:
            session_id = result["id"]
            items_for_ucp = []
            for li in result.get("line_items", []):
                items_for_ucp.append({
                    "product_id": li["item"]["id"],
                    "name":       li["name"],
                    "quantity":   li["quantity"],
                    "unit_price": li["unit_amount"] / 100,
                })
            subtotal = sum(i["unit_price"] * i["quantity"] for i in items_for_ucp)
            CHECKOUTS[session_id] = {
                "id":          session_id,
                "task_id":     task_id,
                "state":       "NOT_READY_FOR_PAYMENT",
                "line_items":  items_for_ucp,
                "subtotal":    round(subtotal, 2),
                "shipping":    None,
                "discount":    None,
                "total":       round(subtotal, 2),
                "payment_instruments": UCP_PROFILE["payment_instruments"],
                "fulfillment_options": UCP_PROFILE["fulfillment_options"],
                "created_at":  time.time(),
            }
            if task_id in TASKS:
                TASKS[task_id]["checkout_id"] = session_id
        return json.dumps(result)
    else:
        result = await call_mcp_tool(name, args)
        latency_ms = round((time.time() - t0) * 1000, 2)
        asyncio.create_task(_broadcast({
            "timestamp": time.time(), "type": "mcp", "tool": name,
            "latency_ms": latency_ms, "task_id": task_id,
            "input": args, "output": result,
        }))
        return json.dumps(result)

# ── Run agent conversation ────────────────────────────────────────────────────
async def run_agent(messages: list, task_id: str) -> tuple[str, list]:
    """Run the model with a tool calling loop. Returns (final_text, tool_events)."""
    import traceback
    tool_events = []
    msgs        = list(messages)

    print(f"\n{'='*60}")
    print(f"[RUN_AGENT START] task_id={task_id} CEREBRAS_API_KEY={'SET' if CEREBRAS_API_KEY else 'MISSING'}")
    print(f"  openai base_url={openai_client.base_url}")
    print(f"{'='*60}")

    for round_num in range(8):  # max 8 tool rounds
        print(f"\n[AI REQUEST] round={round_num} task_id={task_id}")
        print(f"  messages ({len(msgs)} total):")
        for m in msgs:
            role = m.get("role", "?")
            content = m.get("content") or ""
            print(f"    [{role}] {content}")

        try:
            response = await openai_client.chat.completions.create(
                model=CEREBRAS_MODEL,
                messages=msgs,
                tools=OPENAI_TOOLS,
                tool_choice="auto",
                max_tokens=1024,
            )
        except Exception as e:
            print(f"[AI ERROR] OpenAI call failed: {type(e).__name__}: {e}")
            traceback.print_exc()
            raise

        print(f"[AI RESPONSE RAW] {response}")
        msg = response.choices[0].message

        print(f"[AI RESPONSE] finish_reason={response.choices[0].finish_reason}")
        print(f"  content: {msg.content}")
        if msg.tool_calls:
            for tc in msg.tool_calls:
                print(f"  tool_call: {tc.function.name}({tc.function.arguments})")

        if not msg.tool_calls:
            return msg.content or "", tool_events

        # Process tool calls
        msgs.append({"role": "assistant", "content": msg.content, "tool_calls": [
            {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
            for tc in msg.tool_calls
        ]})

        for tc in msg.tool_calls:
            fn_name = tc.function.name
            fn_args = json.loads(tc.function.arguments)

            event = {"tool": fn_name, "input": fn_args, "call_id": tc.id}
            print(f"[TOOL CALL] {fn_name} args={json.dumps(fn_args)}")
            result_str = await handle_tool_call(fn_name, fn_args, task_id)
            result_obj = json.loads(result_str)
            snippet = (result_str[:300] + "...") if len(result_str) > 300 else result_str
            print(f"[TOOL RESULT] {fn_name} -> {snippet}")
            event["output"] = result_obj
            tool_events.append(event)

            msgs.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result_str
            })

    return "I've processed your request.", tool_events

# ── A2A endpoints ──────────────────────────────────────────────────────────────

@app.get("/.well-known/agent-card.json")
def get_agent_card():
    return AGENT_CARD

@app.get("/.well-known/ucp")
def get_ucp_profile():
    return UCP_PROFILE

@app.get("/")
def root():
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/.well-known/agent-card.json")

# A2A: send message (main entry point)
@app.post("/a2a")
async def a2a_endpoint(request: Request):
    body = await request.json()

    # JSON-RPC 2.0
    rpc_id     = body.get("id")
    method     = body.get("method", "")
    params     = body.get("params", {})

    def rpc_ok(result):
        return {"jsonrpc": "2.0", "id": rpc_id, "result": result}

    def rpc_err(code, msg):
        return {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": code, "message": msg}}

    # ── message/send ──
    if method == "message/send":
        message    = params.get("message", {})
        context_id = params.get("contextId") or str(uuid.uuid4())
        task_id    = str(uuid.uuid4())
        user_text  = ""

        for part in message.get("parts", []):
            if part.get("kind") == "text":
                user_text += part["text"]

        # Build conversation history from context
        context_history = []
        for tid, t in TASKS.items():
            if t.get("context_id") == context_id:
                context_history.append({"role": "user",      "content": t["user_message"]})
                context_history.append({"role": "assistant", "content": t["agent_response"]})

        msgs = [{"role": "system", "content": SYSTEM_PROMPT}] + context_history + [{"role": "user", "content": user_text}]

        TASKS[task_id] = {
            "id":             task_id,
            "context_id":     context_id,
            "state":          "working",
            "user_message":   user_text,
            "agent_response": "",
            "tool_events":    [],
            "checkout_id":    None,
            "created_at":     time.time(),
        }

        try:
            final_text, tool_events = await run_agent(msgs, task_id)
        except Exception as e:
            import traceback
            print(f"[A2A ERROR] run_agent raised {type(e).__name__}: {e}")
            traceback.print_exc()
            TASKS[task_id]["state"] = "failed"
            return rpc_err(-32603, str(e))

        TASKS[task_id]["agent_response"] = final_text
        TASKS[task_id]["tool_events"]    = tool_events
        TASKS[task_id]["state"]          = "completed"

        # Broadcast A2A task completion trace event
        asyncio.create_task(_broadcast({
            "timestamp": time.time(), "type": "a2a",
            "event": "task_completed", "task_id": task_id,
            "context_id": context_id,
            "tool_count": len(tool_events),
            "response_length": len(final_text),
        }))

        # Build A2A response
        result = {
            "id":        task_id,
            "contextId": context_id,
            "status": {"state": "completed", "timestamp": time.time()},
            "artifacts": [{
                "artifactId": str(uuid.uuid4()),
                "parts":      [{"kind": "text", "text": final_text}],
            }],
            "metadata": {
                "tool_events":   tool_events,
                "checkout_id":   TASKS[task_id].get("checkout_id"),
                "ucp_checkout":  CHECKOUTS.get(TASKS[task_id].get("checkout_id", ""), None),
            }
        }
        return rpc_ok(result)

    # ── tasks/get ──
    elif method == "tasks/get":
        task_id = params.get("id")
        if task_id not in TASKS:
            return rpc_err(-32001, f"Task {task_id} not found")
        return rpc_ok(TASKS[task_id])

    # ── tasks/list ──
    elif method == "tasks/list":
        return rpc_ok(list(TASKS.values()))

    else:
        return rpc_err(-32601, f"Method not found: {method}")

# ── UCP Checkout endpoints ─────────────────────────────────────────────────────

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
    c["state"]            = "READY_FOR_PAYMENT"
    c["shipping_address"] = body.get("shipping_address", {})
    c["selected_payment"] = body.get("payment_instrument", "visa")
    c["selected_shipping"]= body.get("fulfillment_option", "standard")

    shipping_cost = next(
        (f["price"] for f in c["fulfillment_options"] if f["id"] == c["selected_shipping"]), 4.99
    )
    c["shipping"] = shipping_cost
    c["total"]    = round(c["subtotal"] + shipping_cost - (c.get("discount") or 0), 2)
    return c

@app.post("/ucp/checkout/{checkout_id}/confirm")
async def confirm_checkout(checkout_id: str, request: Request):
    c = CHECKOUTS.get(checkout_id)
    if not c:
        return JSONResponse(status_code=404, content={"error": "Checkout not found"})

    body = await request.json()

    # Generate mock AP2 agentic payment token
    token_id = f"tok_{uuid.uuid4().hex[:12]}"
    ap2_token = {
        "token_id":          token_id,
        "sub":               "user_demo_001",
        "intent":            f"purchase:retail:{','.join(i['name'].lower().replace(' ', '_') for i in c['line_items'])}",
        "merchant_scope":    "purrfect-bites.demo",
        "max_amount":        c["total"],
        "currency":          "USD",
        "expires_at":        "2026-07-02T23:59:59Z",
        "single_use":        True,
        "revocation_url":    f"https://pay.agent/revoke/{token_id}",
        "user_consent_proof":"vc:credential:aiengineer26_demo",
        "issued_at":         time.time(),
    }

    order_id = f"ORD-{uuid.uuid4().hex[:8].upper()}"
    c["state"]        = "COMPLETED"
    c["order_id"]     = order_id
    c["ap2_token"]    = ap2_token
    c["confirmed_at"] = time.time()

    asyncio.create_task(_broadcast({
        "timestamp": time.time(), "type": "ucp",
        "event": "checkout_confirmed", "checkout_id": checkout_id,
        "order_id": order_id, "total": c["total"],
        "ap2_token_id": ap2_token["token_id"],
    }))

    return {
        "state":            "COMPLETED",
        "order_id":         order_id,
        "checkout_id":      checkout_id,
        "total":            c["total"],
        "line_items":       c["line_items"],
        "shipping_address": c.get("shipping_address", {}),
        "ap2_token":        ap2_token,
        "message":          f"🎉 Order {order_id} confirmed! Your items will ship in 5-7 business days.",
    }

# ── REST convenience endpoints (for the REST tab in the UI) ───────────────────

PRODUCT_CATALOG = [
    {"id": "prod_001", "name": "Paw Print Shortbread",      "price": 4.99, "stock": 42},
    {"id": "prod_002", "name": "Meow Macarons (Box of 6)",  "price": 8.49, "stock": 28},
    {"id": "prod_003", "name": "Whisker Brownies",           "price": 5.99, "stock": 0},
    {"id": "prod_004", "name": "Kitty Ear Croissants",       "price": 3.49, "stock": 20},
    {"id": "prod_005", "name": "Catnip & Lavender Scones",  "price": 4.29, "stock": 33},
    {"id": "prod_006", "name": "Tabby Cinnamon Rolls",       "price": 5.99, "stock": 15},
    {"id": "prod_007", "name": "Calico Cake Slice",          "price": 6.49, "stock": 10},
    {"id": "prod_008", "name": "Purr-fect Matcha Cookies",     "price": 5.49, "stock": 55},
    {"id": "prod_009", "name": "Tabbyccino Cake Pop",          "price": 3.99, "stock": 25},
    {"id": "prod_010", "name": "Meow-garita Cookies",          "price": 4.79, "stock": 18},
    {"id": "prod_011", "name": "Purrfecto Petit Fours",        "price": 7.99, "stock": 8},
    {"id": "prod_012", "name": "Kitten Mittons Shortbread",   "price": 5.49, "stock": 30},
]

@app.get("/products")
def list_products():
    return {"products": [{"id": p["id"], "name": p["name"], "price": p["price"], "in_stock": p["stock"] > 0} for p in PRODUCT_CATALOG]}

@app.websocket("/ws/trace")
async def ws_trace(websocket: WebSocket):
    """Real-time protocol trace stream.
    Broadcasts every MCP tool call, A2A state transition, and UCP checkout
    event as a JSON message to all connected clients.
    Connect with: ws://localhost:10999/ws/trace
    """
    await websocket.accept()
    _ws_clients.add(websocket)
    try:
        # Send a hello event so the client knows the connection is live
        await websocket.send_text(json.dumps({
            "timestamp": time.time(), "type": "system",
            "event": "connected", "message": "Trace stream connected",
        }))
        while True:
            # Keep the connection alive; client can send pings if needed
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        _ws_clients.discard(websocket)


@app.get("/health")
def health():
    return {"status": "ok", "agent": AGENT_CARD["name"], "ws_clients": len(_ws_clients)}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=10999, reload=True)
