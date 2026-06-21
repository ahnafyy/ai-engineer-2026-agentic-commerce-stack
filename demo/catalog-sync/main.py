"""
Catalog Sync Service — Purrfect Bites
======================================
Simulates a merchant ETL/batch pipeline. Reads from data/products.json
(pretending to be a DB) and publishes to three feed formats on a 60s schedule:

  GET /feed/acp  →  OpenAI Agentic Commerce Protocol spec
  GET /feed/ucp  →  Google Universal Commerce Protocol (Merchant Center format)
  GET /feed/meta →  Meta (Facebook/Instagram) catalog format

Exposes:
  GET  /status          – health + last sync time + record counts
  GET  /feed/acp        – current ACP product feed (OpenAI spec)
  GET  /feed/ucp        – current UCP product feed (Google spec)
  GET  /feed/meta       – current Meta catalog feed
  POST /sync/trigger    – manually trigger a sync (useful for live demo)
  GET  /sync/history    – last 10 sync run records

Engineering note: In production this would be a scheduled job (cron, Spring Batch,
Airflow, etc.) reading from a real PIM/ERP. Three competing feed specs means three
transformation pipelines — this service shows why that matters.
"""
import json
import time
import uuid
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

app = FastAPI(title="Purrfect Bites Catalog Sync", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

DATA_FILE = Path(__file__).parent / "data" / "products.json"

scheduler = AsyncIOScheduler()

# ── In-memory state ────────────────────────────────────────────────────────────
_feeds: dict = {"acp": None, "ucp": None, "meta": None, "last_sync": None}
_history: list[dict] = []


# ── Loaders ────────────────────────────────────────────────────────────────────
def _load_source() -> dict:
    with open(DATA_FILE) as f:
        return json.load(f)


# ── Feed transformers ──────────────────────────────────────────────────────────

def _to_acp(products: list[dict]) -> dict:
    """
    OpenAI Agentic Commerce Protocol product feed format.
    Ref: https://developers.openai.com/commerce/specs/api/products
    Each product has one variant (simple catalog). Price in minor units (cents).
    """
    return {
        "schema": "acp/2026-04-17",
        "merchant": "purrfect-bites",
        "products": [
            {
                "id": p["id"],
                "title": p["name"],
                "description": {"plain": p["description"]},
                "url": f"https://purrfectbites.demo/products/{p['id']}",
                "variants": [
                    {
                        "id": f"{p['id']}_v1",
                        "title": p["name"],
                        "price": {
                            "amount": int(round(p["price"] * 100)),
                            "currency": "USD",
                        },
                        "availability": {
                            "available": p["stock"] > 0,
                            "status": "in_stock" if p["stock"] > 0 else "out_of_stock",
                            # Non-standard extension — used internally by MCP server
                            "stock_count": p["stock"],
                        },
                        "categories": [
                            {"value": p["category"], "taxonomy": "merchant"}
                        ],
                        "media": [
                            {
                                "type": "image",
                                "url": f"https://purrfectbites.demo/images/{p['id']}.jpg",
                                "alt_text": p["name"],
                            }
                        ],
                    }
                ],
            }
            for p in products
            if p.get("active", True)
        ],
    }


def _to_ucp(products: list[dict]) -> dict:
    """
    Google Universal Commerce Protocol / Merchant Center format.
    Ref: https://developers.google.com/merchant/ucp/guides/merchant-center
    Key fields: native_commerce.checkout_eligibility opts product into agentic checkout.
    """
    return {
        "schema": "ucp/merchant-center",
        "merchant": "purrfect-bites",
        "products": [
            {
                "id": p["id"],
                "merchant_item_id": p["id"],  # must match checkout API product ID
                "title": p["name"],
                "description": p["description"],
                "price": {"value": p["price"], "currency": "USD"},
                "availability": "in_stock" if p["stock"] > 0 else "out_of_stock",
                "condition": "new",
                "link": f"https://purrfectbites.demo/products/{p['id']}",
                "image_link": f"https://purrfectbites.demo/images/{p['id']}.jpg",
                "brand": "Purrfect Bites",
                "google_product_category": "422",  # Food, Beverages & Tobacco > Food Items > Baked Goods
                # UCP-specific: opts product into agentic checkout on Google AI surfaces
                "native_commerce": {
                    "checkout_eligibility": p["stock"] > 0
                },
            }
            for p in products
            if p.get("active", True)
        ],
    }


def _to_meta(products: list[dict]) -> dict:
    """
    Meta (Facebook / Instagram) catalog format.
    Ref: https://www.facebook.com/business/help/120325381656392
    Used for Meta AI shopping surfaces and Instagram Shopping.
    """
    return {
        "schema": "meta-catalog/2024",
        "merchant": "purrfect-bites",
        "data": [
            {
                "id": p["id"],
                "title": p["name"],
                "description": p["description"],
                "availability": "in stock" if p["stock"] > 0 else "out of stock",
                "condition": "new",
                "price": f"{p['price']:.2f} USD",
                "link": f"https://purrfectbites.demo/products/{p['id']}",
                "image_link": f"https://purrfectbites.demo/images/{p['id']}.jpg",
                "brand": "Purrfect Bites",
                "category": p["category"],
                "retailer_id": p["id"],
            }
            for p in products
            if p.get("active", True)
        ],
    }


# ── Sync runner ────────────────────────────────────────────────────────────────

def run_sync() -> dict:
    """Load products from the source JSON and publish all three feeds."""
    started_at = time.time()
    source = _load_source()
    products = source.get("products", [])
    active = [p for p in products if p.get("active", True)]

    _feeds["acp"] = _to_acp(active)
    _feeds["ucp"] = _to_ucp(active)
    _feeds["meta"] = _to_meta(active)
    _feeds["last_sync"] = started_at
    _feeds["discount_codes"] = source.get("discount_codes", {})

    entry = {
        "sync_id": f"sync_{uuid.uuid4().hex[:8]}",
        "started_at": started_at,
        "duration_ms": round((time.time() - started_at) * 1000, 2),
        "record_counts": {
            "source_total": len(products),
            "active": len(active),
            "acp_products": len(_feeds["acp"]["products"]),
            "ucp_products": len(_feeds["ucp"]["products"]),
            "meta_items": len(_feeds["meta"]["data"]),
        },
        "status": "success",
    }
    _history.append(entry)
    if len(_history) > 10:
        _history.pop(0)

    print(
        f"[SYNC] {entry['sync_id']} completed in {entry['duration_ms']}ms — "
        f"{len(active)} active products published to ACP + UCP + Meta"
    )
    return entry


# ── Lifecycle ──────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    run_sync()  # immediate sync on startup so MCP server can boot
    scheduler.add_job(run_sync, "interval", seconds=60, id="catalog_sync")
    scheduler.start()
    print("[CATALOG-SYNC] Scheduler started — syncing every 60s")


@app.on_event("shutdown")
async def shutdown():
    scheduler.shutdown(wait=False)


# ── Endpoints ──────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {
        "status": "ok",
        "last_sync": _feeds["last_sync"],
        "products_count": len(_feeds["acp"]["products"]) if _feeds["acp"] else 0,
    }


@app.get("/status")
def status():
    last = _history[-1] if _history else None
    return {
        "status": "ok",
        "last_sync_at": _feeds["last_sync"],
        "last_sync": last,
        "feeds": {
            "acp": {"url": "/feed/acp", "count": len(_feeds["acp"]["products"]) if _feeds["acp"] else 0},
            "ucp": {"url": "/feed/ucp", "count": len(_feeds["ucp"]["products"]) if _feeds["ucp"] else 0},
            "meta": {"url": "/feed/meta", "count": len(_feeds["meta"]["data"]) if _feeds["meta"] else 0},
        },
    }


@app.post("/sync/trigger")
def trigger_sync():
    entry = run_sync()
    return {"status": "ok", "message": "Sync triggered manually", "result": entry}


@app.get("/sync/history")
def sync_history():
    return {"history": list(reversed(_history))}


@app.get("/feed/acp")
def acp_feed():
    if not _feeds["acp"]:
        return JSONResponse(status_code=503, content={"error": "Feed not ready — sync in progress"})
    return _feeds["acp"]


@app.get("/feed/ucp")
def ucp_feed():
    if not _feeds["ucp"]:
        return JSONResponse(status_code=503, content={"error": "Feed not ready — sync in progress"})
    return _feeds["ucp"]


@app.get("/feed/meta")
def meta_feed():
    if not _feeds["meta"]:
        return JSONResponse(status_code=503, content={"error": "Feed not ready — sync in progress"})
    return _feeds["meta"]


@app.get("/feed/discounts")
def discounts_feed():
    """Internal endpoint — used by MCP server to load discount codes."""
    return _feeds.get("discount_codes", {})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8002, reload=False)
