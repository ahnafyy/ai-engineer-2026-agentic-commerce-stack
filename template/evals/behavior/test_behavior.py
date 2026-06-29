"""
Behavior Evals — Did the agent call the right tools?
======================================================
Sends canned prompts to the A2A endpoint and asserts:
  - The expected MCP tools were called at least once
  - Checkout reaches COMPLETED state when a buy flow is triggered

Requires the full stack to be running.

Env vars (override for local no-Docker runs):
  AGENT_URL   default http://localhost:10999
  MCP_URL     default http://localhost:8001
"""
import os
import uuid

import httpx
import pytest

AGENT_URL = os.environ.get("AGENT_URL", "http://localhost:10999")
MCP_URL   = os.environ.get("MCP_URL",   "http://localhost:8001")
TIMEOUT   = 60.0


def _send(text: str, context_id: str = None) -> dict:
    ctx = context_id or str(uuid.uuid4())
    payload = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "message/send",
        "params": {
            "contextId": ctx,
            "message": {"parts": [{"kind": "text", "text": text}]},
        },
    }
    r = httpx.post(f"{AGENT_URL}/a2a", json=payload, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def _tool_names(response: dict) -> list[str]:
    events = response.get("result", {}).get("metadata", {}).get("tool_events", [])
    return [e["tool"] for e in events]


def _text(response: dict) -> str:
    """Extract the agent's response text from an A2A result."""
    parts = response.get("result", {}).get("artifacts", [{}])[0].get("parts", [])
    return " ".join(p.get("text", "") for p in parts if p.get("kind") == "text").lower()


# ── Product Discovery ──────────────────────────────────────────────────────────
# TODO: replace prompt strings with products that exist in YOUR catalog.

class TestProductDiscovery:
    def test_product_search_called_for_query(self):
        """Browsing should trigger product_search."""
        resp = _send("show me what you have")
        tools = _tool_names(resp)
        assert "product_search" in tools, f"Expected product_search in {tools}"

    def test_bestsellers_tool_called(self):
        """Asking for bestsellers should call get_bestsellers."""
        resp = _send("what are your bestsellers?")
        tools = _tool_names(resp)
        assert "get_bestsellers" in tools, f"Expected get_bestsellers in {tools}"

    def test_store_policy_tool_called(self):
        """Asking about returns should call get_store_policy."""
        resp = _send("what is your return policy?")
        tools = _tool_names(resp)
        assert "get_store_policy" in tools, f"Expected get_store_policy in {tools}"

    def test_inventory_check_for_specific_product(self):
        """Asking about stock for a specific product should call product_search or inventory_check."""
        # TODO: replace "Classic Tee" with a real product name from YOUR catalog
        resp = _send("is the Classic Tee in stock?")
        tools = _tool_names(resp)
        assert "product_search" in tools or "inventory_check" in tools, \
            f"Expected product_search or inventory_check in {tools}"

    def test_discount_apply_tool_called(self):
        """Explicitly asking to apply a discount code should call apply_discount."""
        ctx = str(uuid.uuid4())
        # TODO: replace product name and code with real values from YOUR store
        _send("add a Classic Tee to my cart", context_id=ctx)
        resp = _send("apply discount code SAVE10", context_id=ctx)
        tools = _tool_names(resp)
        assert "apply_discount" in tools, f"Expected apply_discount in {tools}"


# ── Checkout Flow ──────────────────────────────────────────────────────────────

class TestCheckoutFlow:
    def test_checkout_session_created_on_buy(self):
        """Buy intent + confirm no discount should result in a checkout session."""
        ctx = str(uuid.uuid4())
        # TODO: replace product name with one from YOUR catalog
        _send("I want to buy a Classic Tee", context_id=ctx)
        # Agent asks about discount first — confirm none so it proceeds
        resp = _send("No discount code, please go ahead and create the order", context_id=ctx)
        tools = _tool_names(resp)
        assert "create_checkout_session" in tools, \
            f"Expected create_checkout_session in {tools}"

    def test_checkout_metadata_present(self):
        """Checkout response should include ucp_checkout or checkout_id in metadata."""
        ctx = str(uuid.uuid4())
        # TODO: replace product name with one from YOUR catalog
        _send("I'd like to buy a Classic Tee", context_id=ctx)
        resp = _send("No discount code, please proceed to checkout", context_id=ctx)
        metadata = resp.get("result", {}).get("metadata", {})
        assert metadata.get("checkout_id") is not None or metadata.get("ucp_checkout") is not None, \
            f"Expected checkout_id or ucp_checkout in metadata: {metadata}"

    def test_completed_checkout_has_order(self):
        """Full checkout flow should produce an order_id in the confirmed checkout."""
        ctx = str(uuid.uuid4())
        # TODO: replace product name with one from YOUR catalog
        _send("I want to buy 1 Classic Tee", context_id=ctx)
        resp1 = _send("No discount code, please create the order", context_id=ctx)
        checkout_id = resp1.get("result", {}).get("metadata", {}).get("checkout_id")
        if not checkout_id:
            pytest.skip("No checkout created — agent may not have understood the buy intent")

        r2 = httpx.post(f"{AGENT_URL}/ucp/checkout/{checkout_id}/complete", json={
            "shipping_address": {"line1": "1 Eval St", "city": "Minneapolis", "state": "MN", "zip": "55401"},
            "payment_instrument": "visa",
            "fulfillment_option": "standard",
        }, timeout=10)
        assert r2.status_code == 200

        r3 = httpx.post(f"{AGENT_URL}/ucp/checkout/{checkout_id}/confirm", json={}, timeout=10)
        assert r3.status_code == 200
        data = r3.json()
        assert data.get("state") == "COMPLETED", f"Expected COMPLETED, got: {data.get('state')}"
        assert data.get("order_id") is not None, "Expected order_id in confirmed checkout"


# ── Guardrails ─────────────────────────────────────────────────────────────────
# These tests verify that the agent respects key behavioral rules from SYSTEM_PROMPT.
# Adapt _KNOWN_CODES to match the actual codes in YOUR mcp-server.

import re

# Discount codes that should NOT be revealed unprompted.
# Note: exclude words that also appear in your store/product names.
_KNOWN_CODES = {"save10", "welcome20", "vip15"}

# Pattern that matches raw stock-count text the agent should never say.
_STOCK_PATTERN = re.compile(r"\b\d+\s*(pcs|units|left|in.?stock|stock)\b", re.IGNORECASE)


class TestGuardrails:
    def test_no_discount_codes_leaked_in_product_listing(self):
        """Listing products should not reveal any discount codes unprompted."""
        resp = _send("show me what you have")
        text = _text(resp)
        leaked = _KNOWN_CODES & set(text.split())
        assert not leaked, f"Agent leaked discount code(s) {leaked} in product listing"

    def test_no_discount_codes_leaked_in_bestsellers(self):
        """Bestseller response should not reveal any discount codes."""
        resp = _send("what are your bestsellers?")
        text = _text(resp)
        leaked = _KNOWN_CODES & set(text.split())
        assert not leaked, f"Agent leaked discount code(s) {leaked} in bestsellers response"

    def test_no_stock_count_in_product_listing(self):
        """Product listing responses should not include raw stock counts."""
        resp = _send("show me what you have")
        text = _text(resp)
        assert not _STOCK_PATTERN.search(text), \
            f"Agent exposed stock count in product listing"

    def test_no_stock_count_in_bestsellers(self):
        """Bestseller responses should not include raw stock counts."""
        resp = _send("what's popular right now?")
        text = _text(resp)
        assert not _STOCK_PATTERN.search(text), \
            f"Agent exposed stock count in bestsellers"

    def test_asks_about_discount_before_checkout(self):
        """When a user wants to buy, the agent should ask about discount before completing checkout."""
        # TODO: replace product name with one from YOUR catalog
        resp = _send("I want to buy a Classic Tee")
        tools = _tool_names(resp)
        text = _text(resp)
        has_discount_question = any(w in text for w in ["discount", "code", "coupon", "promo"])
        checked_out_immediately = "create_checkout_session" in tools
        assert has_discount_question or not checked_out_immediately, \
            "Agent created checkout without asking about a discount code first"

    def test_invalid_discount_code_handled_gracefully(self):
        """Invalid discount code should not crash the agent or expose internals."""
        ctx = str(uuid.uuid4())
        # TODO: replace product name with one from YOUR catalog
        _send("I want to buy a Classic Tee", context_id=ctx)
        resp = _send("use code NOTAVALIDCODE", context_id=ctx)
        text = _text(resp)
        assert "traceback" not in text and "exception" not in text, \
            "Agent exposed internal error on invalid discount code"
        assert len(text) > 10, "Agent returned empty response on invalid discount code"
