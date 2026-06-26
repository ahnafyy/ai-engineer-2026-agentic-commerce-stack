"""
Template MCP Server — Agentic Commerce Stack Starter Kit
=========================================================
A complete, working MCP tool server for a fictional "My Store".

This is a fully functional starting point — it runs out of the box with a small
sample catalog and every tool the merchant agent and chat UI expect:

  Discovery : product_search · inventory_check · get_product_details
              get_recommendations · get_bestsellers · get_store_policy
  Pricing   : apply_discount
  Checkout  : create_checkout_session · update_checkout_session
              get_checkout_session · complete_checkout_session
              cancel_checkout_session   (ACP spec/2026-04-17, issues an AP2 token)

To make it yours:
  1. Replace PRODUCTS with your own catalog.
  2. Replace DISCOUNT_CODES with your own coupons.
  3. Tweak STORE_POLICY and the merchant identity constants.
  4. Add or remove tools as needed (keep MCP_TOOLS and the dispatch table in sync).

Start here: https://github.com/ahnafyy/ai-engineer-2026-agentic-commerce-stack
"""
import os
import time
import uuid
import random
from typing import Optional

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse

app = FastAPI(title="My Store MCP Server", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ── Merchant identity (TODO: make this yours) ──────────────────────────────────
MERCHANT_ID   = "my-store-demo"
MERCHANT_HOST = "my-store.demo"

# ── TODO: Replace with your product catalog ────────────────────────────────────
# `stock` is the authoritative inventory count. Set one item to 0 to demo
# out-of-stock handling.
PRODUCTS: list[dict] = [
    {"id": "prod_001", "name": "Classic Tee",            "category": "apparel",     "price": 19.99, "stock": 40,  "description": "Soft 100% cotton t-shirt in a relaxed unisex fit", "image": "👕"},
    {"id": "prod_002", "name": "Canvas Tote",            "category": "bags",        "price": 14.99, "stock": 25,  "description": "Sturdy cotton canvas tote with reinforced handles", "image": "👜"},
    {"id": "prod_003", "name": "Enamel Mug",             "category": "drinkware",   "price": 12.99, "stock": 60,  "description": "12oz enamel camping mug with a glossy finish",      "image": "☕"},
    {"id": "prod_004", "name": "Sticker Pack",           "category": "accessories", "price": 5.99,  "stock": 100, "description": "Set of 6 weatherproof die-cut vinyl stickers",      "image": "🌟"},
    {"id": "prod_005", "name": "Knit Beanie",            "category": "apparel",     "price": 24.99, "stock": 0,   "description": "Warm ribbed-knit beanie, one size fits most",       "image": "🧢"},
    {"id": "prod_006", "name": "Insulated Water Bottle", "category": "drinkware",   "price": 18.99, "stock": 15,  "description": "20oz double-walled stainless steel bottle",         "image": "🍶"},
]

# ── TODO: Replace with your discount codes (code -> percent off) ────────────────
DISCOUNT_CODES: dict[str, int] = {
    "SAVE10":    10,
    "WELCOME15": 15,
    "VIP20":     20,
}

# ── TODO: Replace with your store policy ───────────────────────────────────────
STORE_POLICY = {
    "return_policy":   "Free returns within 30 days of delivery for unused items.",
    "shipping_policy": "Standard 5-7 days ($4.99). Express 2-3 days ($9.99). Next Day ($19.99). Free shipping over $50.",
    "warranty":        "All items covered by a 1-year limited warranty against manufacturing defects.",
    "hours":           "Online orders 24/7. Support Mon-Fri 9am-5pm.",
    "contact":         f"support@{MERCHANT_HOST}",
}

# Shared shipping options (used by ACP checkout sessions). Amounts in cents.
FULFILLMENT_OPTIONS = [
    {"type": "shipping", "id": "standard", "title": "Standard Shipping",
     "description": "Arrives in 5-7 days", "carrier": "USPS",
     "totals": [{"type": "total", "display_text": "Shipping", "amount": 499}]},
    {"type": "shipping", "id": "express", "title": "Express Shipping",
     "description": "Arrives in 2-3 days", "carrier": "USPS",
     "totals": [{"type": "total", "display_text": "Shipping", "amount": 999}]},
    {"type": "shipping", "id": "next_day", "title": "Next Day Delivery",
     "description": "Arrives tomorrow", "carrier": "FedEx",
     "totals": [{"type": "total", "display_text": "Shipping", "amount": 1999}]},
]

# In-memory ACP checkout sessions
CHECKOUT_SESSIONS: dict = {}


def get_products() -> list[dict]:
    return PRODUCTS


# ── MCP Tool Definitions (advertised at GET /tools) ────────────────────────────
MCP_TOOLS = [
    {
        "name": "product_search",
        "description": "Search the catalog. Returns products matching the query, optionally filtered by in-stock status and max price.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query":     {"type": "string",  "description": "Search query (name, category, or description)"},
                "in_stock":  {"type": "boolean", "description": "If true, only return in-stock products"},
                "max_price": {"type": "number",  "description": "Maximum price filter (USD)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "inventory_check",
        "description": "Check the current inventory level for a specific product by ID.",
        "inputSchema": {
            "type": "object",
            "properties": {"product_id": {"type": "string", "description": "The product ID to check"}},
            "required": ["product_id"],
        },
    },
    {
        "name": "apply_discount",
        "description": "Apply a discount code to a subtotal. Returns the discount amount and new total.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "discount_code": {"type": "string", "description": "The discount/coupon code"},
                "subtotal":      {"type": "number", "description": "The cart subtotal in USD"},
            },
            "required": ["discount_code", "subtotal"],
        },
    },
    {
        "name": "get_product_details",
        "description": "Get full details for a specific product: material, weight, care, and SKU.",
        "inputSchema": {
            "type": "object",
            "properties": {"product_id": {"type": "string", "description": "The product ID (e.g. prod_001)"}},
            "required": ["product_id"],
        },
    },
    {
        "name": "get_recommendations",
        "description": "Get up to 3 product recommendations, optionally based on a product or category.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "product_id": {"type": "string", "description": "Product ID to base recommendations on"},
                "category":   {"type": "string", "description": "Category to recommend from"},
            },
        },
    },
    {
        "name": "get_store_policy",
        "description": "Get store policies: returns, shipping, warranty, and hours.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_bestsellers",
        "description": "Get the top 3 bestselling in-stock products.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "create_checkout_session",
        "description": "Create an ACP checkout session. Returns a CheckoutSession with line items, fulfillment options, totals, and AP2 payment handler capabilities.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "meta":    {"type": "object"},
                "payload": {
                    "type": "object",
                    "properties": {
                        "currency":   {"type": "string"},
                        "line_items": {"type": "array", "items": {"type": "object"}},
                    },
                    "required": ["currency", "line_items"],
                },
            },
            "required": ["payload"],
        },
    },
    {
        "name": "update_checkout_session",
        "description": "Update an ACP checkout session — e.g. select a fulfillment option.",
        "inputSchema": {
            "type": "object",
            "properties": {"meta": {"type": "object"}, "id": {"type": "string"}, "payload": {"type": "object"}},
            "required": ["id", "payload"],
        },
    },
    {
        "name": "get_checkout_session",
        "description": "Retrieve the current state of an ACP checkout session.",
        "inputSchema": {
            "type": "object",
            "properties": {"meta": {"type": "object"}, "id": {"type": "string"}},
            "required": ["id"],
        },
    },
    {
        "name": "complete_checkout_session",
        "description": "Complete an ACP checkout session with buyer info and payment data. Issues an AP2 token.",
        "inputSchema": {
            "type": "object",
            "properties": {"meta": {"type": "object"}, "id": {"type": "string"}, "payload": {"type": "object"}},
            "required": ["id", "payload"],
        },
    },
    {
        "name": "cancel_checkout_session",
        "description": "Cancel an ACP checkout session.",
        "inputSchema": {
            "type": "object",
            "properties": {"meta": {"type": "object"}, "id": {"type": "string"}, "payload": {"type": "object"}},
            "required": ["id"],
        },
    },
]


# ── Endpoints ──────────────────────────────────────────────────────────────────
@app.get("/")
def root():
    return RedirectResponse(url="/tools")


@app.get("/health")
def health():
    return {"status": "ok", "products": len(PRODUCTS)}


@app.get("/tools")
def list_tools():
    return {"tools": MCP_TOOLS}


@app.post("/tools/call")
async def call_tool(request: Request):
    body = await request.json()
    tool_name  = body.get("name")
    tool_input = body.get("input", {})
    dispatch = {
        "product_search":            lambda: _product_search(**tool_input),
        "inventory_check":           lambda: _inventory_check(**tool_input),
        "apply_discount":            lambda: _apply_discount(**tool_input),
        "get_product_details":       lambda: _get_product_details(**tool_input),
        "get_recommendations":       lambda: _get_recommendations(**tool_input),
        "get_store_policy":          lambda: _get_store_policy(),
        "get_bestsellers":           lambda: _get_bestsellers(),
        "create_checkout_session":   lambda: _create_checkout_session(**tool_input),
        "update_checkout_session":   lambda: _update_checkout_session(**tool_input),
        "get_checkout_session":      lambda: _get_checkout_session(**tool_input),
        "complete_checkout_session": lambda: _complete_checkout_session(**tool_input),
        "cancel_checkout_session":   lambda: _cancel_checkout_session(**tool_input),
    }
    handler = dispatch.get(tool_name)
    if not handler:
        return JSONResponse(status_code=400, content={"error": f"Unknown tool: {tool_name}"})
    if not isinstance(tool_input, dict):
        return JSONResponse(status_code=400, content={"error": "'input' must be an object"})
    try:
        return handler()
    except TypeError as exc:
        # Bad/missing arguments for the tool (e.g. product_search with no query).
        return JSONResponse(
            status_code=400,
            content={"error": f"Invalid arguments for tool '{tool_name}': {exc}"},
        )


# ── Discovery tool implementations ─────────────────────────────────────────────
def _product_search(query: str, in_stock: bool = False, max_price: Optional[float] = None):
    q = query.lower()
    results = []
    for p in get_products():
        if q in p["name"].lower() or q in p["category"].lower() or q in p["description"].lower():
            if in_stock and p["stock"] == 0:
                continue
            if max_price is not None and p["price"] > max_price:
                continue
            results.append({
                "id": p["id"], "name": p["name"], "category": p["category"],
                "price": p["price"], "in_stock": p["stock"] > 0,
                "stock_count": p["stock"], "description": p["description"], "image": p["image"],
            })
    return {"tool": "product_search", "query": query, "count": len(results), "results": results}


def _inventory_check(product_id: str):
    product = next((p for p in get_products() if p["id"] == product_id), None)
    if not product:
        return {"tool": "inventory_check", "error": f"Product {product_id} not found"}
    status = "available" if product["stock"] > 10 else ("low_stock" if product["stock"] > 0 else "out_of_stock")
    return {
        "tool": "inventory_check", "product_id": product_id, "name": product["name"],
        "stock": product["stock"], "in_stock": product["stock"] > 0, "status": status,
    }


def _apply_discount(discount_code: str, subtotal: float):
    code = discount_code.upper().strip()
    if code not in DISCOUNT_CODES:
        return {"tool": "apply_discount", "valid": False, "discount_code": code,
                "message": f"Discount code '{code}' is not valid."}
    pct = DISCOUNT_CODES[code]
    discount_amt = round(subtotal * pct / 100, 2)
    return {
        "tool": "apply_discount", "valid": True, "discount_code": code,
        "discount_percent": pct, "discount_amount": discount_amt,
        "original_subtotal": subtotal, "new_total": round(subtotal - discount_amt, 2),
        "message": f"Applied {pct}% discount: saved ${discount_amt:.2f}",
    }


def _get_product_details(product_id: str):
    product = next((p for p in get_products() if p["id"] == product_id), None)
    if not product:
        return {"tool": "get_product_details", "error": f"Product {product_id} not found"}
    materials_by_cat = {
        "apparel": "100% cotton", "bags": "Cotton canvas",
        "drinkware": "Stainless steel / enamel", "accessories": "Vinyl",
    }
    return {
        "tool": "get_product_details", **product,
        "in_stock": product["stock"] > 0,
        "material": materials_by_cat.get(product["category"], "Mixed materials"),
        "weight_g": random.choice([85, 120, 200, 320, 450]),
        "care": "Wipe clean or machine wash cold. Do not tumble dry.",
        "sku": f"SKU-{product['id'].split('_')[-1]}",
    }


def _get_recommendations(product_id: str = None, category: str = None):
    products = get_products()
    if product_id:
        base = next((p for p in products if p["id"] == product_id), None)
        if base:
            category = base["category"]
    candidates = [p for p in products if p["stock"] > 0 and (not product_id or p["id"] != product_id)]
    random.shuffle(candidates)
    recs = candidates[:3]
    return {"tool": "get_recommendations", "recommendations": [
        {"id": p["id"], "name": p["name"], "price": p["price"],
         "description": p["description"], "image": p["image"]} for p in recs
    ]}


def _get_store_policy():
    return {"tool": "get_store_policy", **STORE_POLICY}


def _get_bestsellers():
    in_stock = [p for p in get_products() if p["stock"] > 0]
    top3 = sorted(in_stock, key=lambda p: p["stock"], reverse=True)[:3]
    return {"tool": "get_bestsellers", "bestsellers": [
        {"rank": i + 1, "id": p["id"], "name": p["name"],
         "price": p["price"], "stock": p["stock"], "description": p["description"], "image": p["image"]}
        for i, p in enumerate(top3)
    ]}


# ── ACP Checkout implementations ───────────────────────────────────────────────
def _acp_session_public(session: dict) -> dict:
    return {k: v for k, v in session.items() if not k.startswith("_")}


def _create_checkout_session(payload: dict, meta: dict = None):
    session_id = f"cs_{uuid.uuid4().hex[:12]}"
    currency = payload.get("currency", "usd")
    products = get_products()

    line_items = []
    subtotal_cents = 0
    for item in payload.get("line_items", []):
        pid = item.get("id") or item.get("product_id")
        product = next((p for p in products if p["id"] == pid), None)
        if not product:
            continue
        qty = item.get("quantity", 1)
        unit_cents = int(round(product["price"] * 100))
        item_total = unit_cents * qty
        subtotal_cents += item_total
        line_items.append({
            "id": f"li_{uuid.uuid4().hex[:8]}", "item": {"id": product["id"]},
            "quantity": qty, "name": product["name"], "description": product["description"],
            "unit_amount": unit_cents,
            "totals": [
                {"type": "items_base_amount", "display_text": "Base Amount", "amount": item_total},
                {"type": "subtotal",          "display_text": "Subtotal",    "amount": item_total},
                {"type": "total",             "display_text": "Total",       "amount": item_total},
            ],
        })

    session = {
        "id": session_id, "protocol": {"version": "2026-04-17"},
        "status": "NOT_READY_FOR_PAYMENT", "currency": currency,
        "line_items": line_items, "fulfillment_options": FULFILLMENT_OPTIONS,
        "selected_fulfillment_options": [],
        "totals": [
            {"type": "items_base_amount", "display_text": "Item(s) total", "amount": subtotal_cents},
            {"type": "subtotal",          "display_text": "Subtotal",      "amount": subtotal_cents},
            {"type": "total",             "display_text": "Total",         "amount": subtotal_cents},
        ],
        "messages": [],
        "links": [
            {"type": "terms_of_use",  "url": f"https://{MERCHANT_HOST}/terms"},
            {"type": "return_policy", "url": f"https://{MERCHANT_HOST}/returns"},
        ],
        "capabilities": {"payment": {"handlers": [{
            "id": "ap2_token", "name": "dev.acp.ap2.token",
            "display_name": "Agentic Payment Token (AP2)", "version": "2026-04-17",
            "spec": "https://agenticcommerce.dev/handlers/ap2",
            "requires_delegate_payment": False, "requires_pci_compliance": False,
            "psp": MERCHANT_ID,
            "config": {"merchant_id": MERCHANT_ID, "environment": "demo"},
        }]}},
        "created_at": time.time(), "_subtotal_cents": subtotal_cents,
    }
    CHECKOUT_SESSIONS[session_id] = session
    return _acp_session_public(session)


def _update_checkout_session(id: str, payload: dict, meta: dict = None):
    session = CHECKOUT_SESSIONS.get(id)
    if not session:
        return {"error": f"Session {id} not found"}
    selected = payload.get("selected_fulfillment_options", [])
    if selected:
        session["selected_fulfillment_options"] = selected
        opt_id = selected[0].get("option_id", "standard")
        shipping_opt = next((f for f in FULFILLMENT_OPTIONS if f["id"] == opt_id), FULFILLMENT_OPTIONS[0])
        shipping_cents = shipping_opt["totals"][0]["amount"]
        subtotal = session["_subtotal_cents"]
        total = subtotal + shipping_cents
        session["totals"] = [
            {"type": "items_base_amount", "display_text": "Item(s) total", "amount": subtotal},
            {"type": "subtotal",          "display_text": "Subtotal",      "amount": subtotal},
            {"type": "fulfillment",       "display_text": "Shipping",      "amount": shipping_cents},
            {"type": "total",             "display_text": "Total",         "amount": total},
        ]
        session["status"] = "READY_FOR_PAYMENT"
    return _acp_session_public(session)


def _get_checkout_session(id: str, meta: dict = None):
    session = CHECKOUT_SESSIONS.get(id)
    if not session:
        return {"error": f"Session {id} not found"}
    return _acp_session_public(session)


def _complete_checkout_session(id: str, payload: dict, meta: dict = None):
    session = CHECKOUT_SESSIONS.get(id)
    if not session:
        return {"error": f"Session {id} not found"}
    buyer = payload.get("buyer", {})
    order_id = f"ORD-{uuid.uuid4().hex[:8].upper()}"
    token_id = f"tok_{uuid.uuid4().hex[:12]}"
    total_entry = next((t for t in session["totals"] if t["type"] == "total"), None)
    total_cents = total_entry["amount"] if total_entry else session["_subtotal_cents"]
    session["status"] = "COMPLETED"
    session["buyer"] = buyer
    session["order"] = {
        "id": order_id, "checkout_session_id": id,
        "order_number": f"#{random.randint(1000, 9999)}",
        "permalink_url": f"https://{MERCHANT_HOST}/orders/{order_id}",
        "status": "confirmed",
        "ap2_token": {
            "token_id": token_id, "sub": "user_demo_001",
            "intent": f"purchase:retail:{','.join(li['name'].lower().replace(' ', '_') for li in session['line_items'])}",
            "merchant_scope": MERCHANT_HOST,
            "max_amount": round(total_cents / 100, 2), "currency": "USD",
            "expires_at": "2026-12-31T23:59:59Z", "single_use": True,
            "revocation_url": f"https://pay.agent/revoke/{token_id}",
            "user_consent_proof": "vc:credential:my_store_demo",
            "issued_at": time.time(),
        },
    }
    return _acp_session_public(session)


def _cancel_checkout_session(id: str, payload: dict = None, meta: dict = None):
    session = CHECKOUT_SESSIONS.get(id)
    if not session:
        return {"error": f"Session {id} not found"}
    session["status"] = "CANCELED"
    return _acp_session_public(session)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8001)))
