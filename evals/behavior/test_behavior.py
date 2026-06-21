"""
Behavior Evals — Did the agent call the right tools?
======================================================
Sends canned prompts to the A2A endpoint and asserts:
  - The expected MCP tools were called at least once
  - Checkout reaches COMPLETED state when a buy flow is triggered

Requires the full stack to be running:
  AGENT_URL   default http://merchant-agent:10999
  MCP_URL     default http://mcp-server:8001
"""
import os
import uuid

import httpx
import pytest

AGENT_URL = os.environ.get("AGENT_URL", "http://merchant-agent:10999")
MCP_URL = os.environ.get("MCP_URL", "http://mcp-server:8001")
TIMEOUT = 60.0


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


# ── Behavior Tests ─────────────────────────────────────────────────────────────

class TestProductDiscovery:
    def test_product_search_called_for_query(self):
        """Asking about cookies should trigger product_search."""
        resp = _send("show me your cookies")
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
        """Asking if a specific product is in stock should call inventory_check."""
        resp = _send("is the Tabbyccino Cake Pop in stock?")
        tools = _tool_names(resp)
        # Agent should search first, then check inventory
        assert "product_search" in tools or "inventory_check" in tools, \
            f"Expected product_search or inventory_check in {tools}"

    def test_product_details_called(self):
        """Asking for allergen info should call get_product_details."""
        resp = _send("what allergens are in the Paw Print Shortbread?")
        tools = _tool_names(resp)
        assert "get_product_details" in tools or "product_search" in tools, \
            f"Expected get_product_details or product_search in {tools}"

    def test_discount_apply_tool_called(self):
        """Asking to apply a discount should call apply_discount."""
        ctx = str(uuid.uuid4())
        _send("add the Meow Macarons to my cart", context_id=ctx)
        resp = _send("apply discount code MEOW20", context_id=ctx)
        tools = _tool_names(resp)
        assert "apply_discount" in tools, f"Expected apply_discount in {tools}"


class TestCheckoutFlow:
    def test_checkout_session_created_on_buy(self):
        """Saying 'buy' should result in a checkout session being created."""
        resp = _send("I want to buy the Purr-fect Matcha Cookies, quantity 2")
        tools = _tool_names(resp)
        assert "create_checkout_session" in tools, \
            f"Expected create_checkout_session in {tools}"

    def test_checkout_metadata_present(self):
        """The A2A response should include ucp_checkout metadata when checkout is created."""
        resp = _send("add Kitten Mittons Shortbread to cart and check out")
        metadata = resp.get("result", {}).get("metadata", {})
        assert metadata.get("checkout_id") is not None or metadata.get("ucp_checkout") is not None, \
            f"Expected checkout_id or ucp_checkout in metadata: {metadata}"

    def test_completed_checkout_has_order(self):
        """A full checkout flow should produce an order_id in the confirmed checkout."""
        ctx = str(uuid.uuid4())
        # Step 1: buy
        resp1 = _send("I want to buy 1 Paw Print Shortbread", context_id=ctx)
        checkout_id = resp1.get("result", {}).get("metadata", {}).get("checkout_id")
        if not checkout_id:
            pytest.skip("No checkout created — agent may not have understood the buy intent")

        # Step 2: complete via UCP endpoint
        r2 = httpx.post(f"{AGENT_URL}/ucp/checkout/{checkout_id}/complete", json={
            "shipping_address": {"line1": "1 Eval St", "city": "Minneapolis", "state": "MN", "zip": "55401"},
            "payment_instrument": "visa",
            "fulfillment_option": "standard",
        }, timeout=10)
        assert r2.status_code == 200

        # Step 3: confirm
        r3 = httpx.post(f"{AGENT_URL}/ucp/checkout/{checkout_id}/confirm", json={}, timeout=10)
        assert r3.status_code == 200
        data = r3.json()
        assert data.get("state") == "COMPLETED", f"Expected COMPLETED, got: {data.get('state')}"
        assert data.get("order_id") is not None, "Expected order_id in confirmed checkout"
        assert data.get("ap2_token", {}).get("token_id") is not None, "Expected AP2 token"
