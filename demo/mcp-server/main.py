"""
MCP Server — Purrfect Bites Cat Bakery
=========================================
Exposes all MCP tools for product discovery and ACP checkout.

KEY CHANGE vs Minnebar version:
  Products are NO LONGER hardcoded. This server fetches the live ACP feed
  from the catalog-sync service (CATALOG_SYNC_URL) with a 5-second TTL cache.
  Edit demo/catalog-sync/data/products.json → POST /sync/trigger → tool calls
  immediately reflect the updated catalog. This is the "live pipeline" demo.

Env vars:
  CATALOG_SYNC_URL  default http://catalog-sync:8002
  PORT              default 8001
"""
import os
import time
import uuid
import random
from typing import Optional

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import uvicorn

app = FastAPI(title="Purrfect Bites MCP Server", version="2.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

CATALOG_SYNC_URL = os.environ.get("CATALOG_SYNC_URL", "http://catalog-sync:8002")
CACHE_TTL = 5  # seconds — short enough for live demo changes

# ── Catalog cache ──────────────────────────────────────────────────────────────
_catalog_cache: dict = {"products": [], "discount_codes": {}, "fetched_at": 0}


def _acp_variant_to_product(prod: dict, variant: dict) -> dict:
    """Convert ACP feed product+variant into the flat dict the tools expect."""
    avail = variant.get("availability", {})
    price_minor = variant.get("price", {}).get("amount", 0)
    categories = variant.get("categories", [])
    category = categories[0]["value"] if categories else "misc"
    return {
        "id": prod["id"],
        "name": prod.get("title", prod["id"]),
        "category": category,
        "price": round(price_minor / 100, 2),
        "stock": 99 if avail.get("available") else 0,  # ACP feed has no stock count; treat available=True as >0
        "description": prod.get("description", {}).get("plain", ""),
        "image": "🐾",
    }


def get_products() -> list[dict]:
    """Return products, refreshing from catalog-sync if cache is stale."""
    now = time.time()
    if now - _catalog_cache["fetched_at"] < CACHE_TTL and _catalog_cache["products"]:
        return _catalog_cache["products"]

    try:
        with httpx.Client(timeout=4.0) as client:
            r = client.get(f"{CATALOG_SYNC_URL}/feed/acp")
            r.raise_for_status()
            feed = r.json()

            dr = client.get(f"{CATALOG_SYNC_URL}/feed/discounts")
            discount_codes = dr.json() if dr.status_code == 200 else {}

        products = []
        for prod in feed.get("products", []):
            for variant in prod.get("variants", []):
                products.append(_acp_variant_to_product(prod, variant))

        _catalog_cache["products"] = products
        _catalog_cache["discount_codes"] = discount_codes
        _catalog_cache["fetched_at"] = now
        print(f"[MCP] Catalog refreshed from catalog-sync: {len(products)} products")
    except Exception as exc:
        print(f"[MCP] WARNING: catalog-sync unavailable ({exc}), using stale cache ({len(_catalog_cache['products'])} products)")

    return _catalog_cache["products"]


def get_discount_codes() -> dict:
    get_products()  # ensure cache is warm
    return _catalog_cache["discount_codes"]


# ── ACP Checkout Session state ─────────────────────────────────────────────────
CHECKOUT_SESSIONS: dict = {}

ACP_FULFILLMENT_OPTIONS = [
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

# ── MCP Tool Definitions ───────────────────────────────────────────────────────
MCP_TOOLS = [
    {
        "name": "product_search",
        "description": "Search the Purrfect Bites cat bakery catalog. Returns baked goods matching the query, optionally filtered by in-stock status and max price.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query":     {"type": "string",  "description": "Search query (product name, category, or description)"},
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
            "properties": {"product_id": {"type": "string", "description": "The product ID to check inventory for"}},
            "required": ["product_id"],
        },
    },
    {
        "name": "apply_discount",
        "description": "Apply a discount code to a subtotal. Returns the discount amount and new total.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "discount_code": {"type": "string", "description": "The discount/coupon code to apply"},
                "subtotal":      {"type": "number", "description": "The cart subtotal in USD"},
            },
            "required": ["discount_code", "subtotal"],
        },
    },
    {
        "name": "get_product_details",
        "description": "Get full details for a specific product: allergens, weight, shelf life, and ingredients note.",
        "inputSchema": {
            "type": "object",
            "properties": {"product_id": {"type": "string", "description": "The product ID (e.g. prod_001)"}},
            "required": ["product_id"],
        },
    },
    {
        "name": "get_recommendations",
        "description": "Get 3 product recommendations, optionally based on a specific product or category.",
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
        "description": "Get store policies: returns, allergens, shipping, and hours.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_bestsellers",
        "description": "Get the top 3 bestselling products at Purrfect Bites.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "create_checkout_session",
        "description": "Create an ACP checkout session. Returns a CheckoutSession with line items, fulfillment options, totals, and AP2 payment handler capabilities.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "meta":    {"type": "object", "properties": {"api_version": {"type": "string"}, "idempotency_key": {"type": "string"}}},
                "payload": {
                    "type": "object",
                    "properties": {
                        "currency":   {"type": "string"},
                        "line_items": {"type": "array", "items": {"type": "object"}},
                        "capabilities": {"type": "object"},
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
            "properties": {
                "meta":    {"type": "object"},
                "id":      {"type": "string"},
                "payload": {"type": "object"},
            },
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
        "description": "Complete an ACP checkout session with buyer info and payment data.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "meta":    {"type": "object"},
                "id":      {"type": "string"},
                "payload": {"type": "object"},
            },
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
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/tools")


@app.get("/health")
def health():
    products = _catalog_cache["products"]
    return {
        "status": "ok",
        "catalog_source": CATALOG_SYNC_URL,
        "products_cached": len(products),
        "cache_age_s": round(time.time() - _catalog_cache["fetched_at"], 1),
        "cache_ttl_s": CACHE_TTL,
    }


@app.get("/feed")
def product_feed():
    """Pass-through — returns the live ACP feed from catalog-sync."""
    products = get_products()
    return {"schema_version": "2.0", "merchant": "purrfect-bites", "source": "catalog-sync", "products": products}


@app.get("/tools")
def list_tools():
    return {"tools": MCP_TOOLS}


@app.post("/tools/call")
async def call_tool(request: Request):
    body = await request.json()
    tool_name = body.get("name")
    tool_input = body.get("input", {})
    dispatch = {
        "product_search":           lambda: _product_search(**tool_input),
        "inventory_check":          lambda: _inventory_check(**tool_input),
        "apply_discount":           lambda: _apply_discount(**tool_input),
        "get_product_details":      lambda: _get_product_details(**tool_input),
        "get_recommendations":      lambda: _get_recommendations(**tool_input),
        "get_store_policy":         lambda: _get_store_policy(),
        "get_bestsellers":          lambda: _get_bestsellers(),
        "create_checkout_session":  lambda: _create_checkout_session(**tool_input),
        "update_checkout_session":  lambda: _update_checkout_session(**tool_input),
        "get_checkout_session":     lambda: _get_checkout_session(**tool_input),
        "complete_checkout_session":lambda: _complete_checkout_session(**tool_input),
        "cancel_checkout_session":  lambda: _cancel_checkout_session(**tool_input),
    }
    handler = dispatch.get(tool_name)
    if not handler:
        return JSONResponse(status_code=400, content={"error": f"Unknown tool: {tool_name}"})
    return handler()


@app.post("/tools/product_search")
async def product_search_ep(request: Request):
    return _product_search(**(await request.json()))


@app.post("/tools/inventory_check")
async def inventory_check_ep(request: Request):
    return _inventory_check(**(await request.json()))


@app.post("/tools/apply_discount")
async def apply_discount_ep(request: Request):
    return _apply_discount(**(await request.json()))


# ── Tool implementations ───────────────────────────────────────────────────────

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
        "tool": "inventory_check", "product_id": product_id,
        "name": product["name"], "stock": product["stock"],
        "in_stock": product["stock"] > 0, "status": status,
    }


def _apply_discount(discount_code: str, subtotal: float):
    codes = get_discount_codes()
    code = discount_code.upper().strip()
    if code not in codes:
        return {"tool": "apply_discount", "valid": False, "discount_code": code,
                "message": f"Discount code '{code}' is not valid."}
    pct = codes[code]
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
    allergens_by_cat = {
        "cookies": ["gluten", "dairy", "eggs"], "macarons": ["gluten", "dairy", "eggs", "nuts"],
        "brownies": ["gluten", "dairy", "eggs"], "pastries": ["gluten", "dairy"],
        "scones": ["gluten", "dairy", "eggs"], "cake": ["gluten", "dairy", "eggs"],
        "cake pops": ["gluten", "dairy", "eggs"],
    }
    return {
        "tool": "get_product_details", **product,
        "in_stock": product["stock"] > 0,
        "allergens": allergens_by_cat.get(product["category"], ["gluten", "dairy"]),
        "weight_g": random.choice([85, 100, 120, 150, 200]),
        "shelf_life": "3-5 days at room temperature, 2 weeks refrigerated",
        "ingredients_note": "Made fresh daily in our cat-themed kitchen. All items baked to order.",
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
    return {
        "tool": "get_store_policy",
        "return_policy": "Returns accepted within 24 hours of delivery for damaged or incorrect items.",
        "allergen_policy": "Baked in a facility using gluten, dairy, eggs, and nuts. Dedicated allergen-free batches available on request.",
        "shipping_policy": "Standard 5-7 days ($4.99). Express 2-3 days ($9.99). Next Day ($19.99). Free shipping over $30.",
        "freshness_guarantee": "All items baked fresh within 24 hours of shipment.",
        "hours": "Online orders 24/7. Baking Mon-Sat 5am-2pm CST.",
        "contact": "meow@purrfectbites.demo",
    }


def _get_bestsellers():
    in_stock = [p for p in get_products() if p["stock"] > 0]
    top3 = sorted(in_stock, key=lambda p: p["stock"], reverse=True)[:3]
    return {"tool": "get_bestsellers", "bestsellers": [
        {"rank": i + 1, "id": p["id"], "name": p["name"],
         "price": p["price"], "stock": p["stock"], "description": p["description"], "image": p["image"]}
        for i, p in enumerate(top3)
    ]}


# ── ACP Checkout ───────────────────────────────────────────────────────────────

def _acp_session_public(session: dict) -> dict:
    return {k: v for k, v in session.items() if not k.startswith("_")}


def _create_checkout_session(payload: dict, meta: dict = None):
    session_id = f"cs_{uuid.uuid4().hex[:12]}"
    line_items_req = payload.get("line_items", [])
    currency = payload.get("currency", "usd")
    products = get_products()

    line_items = []
    subtotal_cents = 0
    for item in line_items_req:
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
        "line_items": line_items, "fulfillment_options": ACP_FULFILLMENT_OPTIONS,
        "selected_fulfillment_options": [],
        "totals": [
            {"type": "items_base_amount", "display_text": "Item(s) total", "amount": subtotal_cents},
            {"type": "subtotal",          "display_text": "Subtotal",      "amount": subtotal_cents},
            {"type": "total",             "display_text": "Total",         "amount": subtotal_cents},
        ],
        "messages": [],
        "links": [
            {"type": "terms_of_use",  "url": "https://purrfectbites.demo/terms"},
            {"type": "return_policy", "url": "https://purrfectbites.demo/returns"},
        ],
        "capabilities": {"payment": {"handlers": [{
            "id": "ap2_token", "name": "dev.acp.ap2.token",
            "display_name": "Agentic Payment Token (AP2)", "version": "2026-04-17",
            "spec": "https://agenticcommerce.dev/handlers/ap2",
            "requires_delegate_payment": False, "requires_pci_compliance": False,
            "psp": "purrfect-bites-demo",
            "config": {"merchant_id": "purrfect-bites-demo", "environment": "demo"},
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
        shipping_opt = next((f for f in ACP_FULFILLMENT_OPTIONS if f["id"] == opt_id), ACP_FULFILLMENT_OPTIONS[0])
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
        "permalink_url": f"https://purrfectbites.demo/orders/{order_id}",
        "status": "confirmed",
        "ap2_token": {
            "token_id": token_id, "sub": "user_demo_001",
            "intent": f"purchase:retail:{','.join(li['name'].lower().replace(' ','_') for li in session['line_items'])}",
            "merchant_scope": "purrfect-bites.demo",
            "max_amount": round(total_cents / 100, 2), "currency": "USD",
            "expires_at": "2026-07-02T23:59:59Z", "single_use": True,
            "revocation_url": f"https://pay.agent/revoke/{token_id}",
            "user_consent_proof": "vc:credential:aiengineer26_demo",
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
