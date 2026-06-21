"""
Template MCP Server — Agentic Commerce Stack Starter Kit
=========================================================
Replace the PRODUCTS list with your own catalog,
add your own discount codes, and extend the tools as needed.

Start here: https://github.com/ahnafyy/ai-engineer-2026-agentic-commerce-stack
"""
import os
from typing import Optional

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

app = FastAPI(title="My Store MCP Server", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ── TODO: Replace with your product catalog ────────────────────────────────────
PRODUCTS: list[dict] = [
    # {"id": "prod_001", "name": "Product Name", "category": "category", "price": 9.99, "stock": 10, "description": "..."},
]

DISCOUNT_CODES: dict[str, int] = {
    # "CODE10": 10,  # 10% off
}

# ── MCP Tool Definitions ───────────────────────────────────────────────────────
MCP_TOOLS = [
    {
        "name": "product_search",
        "description": "Search the product catalog by keyword.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query":    {"type": "string", "description": "Search keyword"},
                "in_stock": {"type": "boolean", "description": "Only return in-stock products"},
            },
            "required": ["query"],
        },
    },
    # TODO: Add more tools here
]


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

    if tool_name == "product_search":
        return _product_search(**tool_input)

    return JSONResponse(status_code=400, content={"error": f"Unknown tool: {tool_name}"})


# ── Tool implementations ───────────────────────────────────────────────────────

def _product_search(query: str, in_stock: bool = False):
    q = query.lower()
    results = [
        p for p in PRODUCTS
        if q in p["name"].lower() or q in p.get("description", "").lower()
        if not in_stock or p.get("stock", 0) > 0
    ]
    return {"tool": "product_search", "query": query, "count": len(results), "results": results}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8001)))
