"""
Protocol Compliance Evals — Do responses match their specs?
============================================================
Validates schema conformance for:
  - MCP /tools endpoint
  - A2A JSON-RPC 2.0 message/send
  - UCP checkout object
  - ACP product feed from catalog-sync

Requires the full stack running.
"""
import os
import uuid

import httpx
import pytest

AGENT_URL       = os.environ.get("AGENT_URL",        "http://merchant-agent:10999")
MCP_URL         = os.environ.get("MCP_URL",           "http://mcp-server:8001")
CATALOG_SYNC_URL = os.environ.get("CATALOG_SYNC_URL", "http://catalog-sync:8002")
TIMEOUT = 15.0


# ── MCP Compliance ─────────────────────────────────────────────────────────────

class TestMCPCompliance:
    @pytest.fixture(scope="class")
    def tools_response(self):
        r = httpx.get(f"{MCP_URL}/tools", timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()

    def test_tools_returns_tools_array(self, tools_response):
        assert "tools" in tools_response, "MCP /tools must return {'tools': [...]}"
        assert isinstance(tools_response["tools"], list)

    def test_tools_not_empty(self, tools_response):
        assert len(tools_response["tools"]) > 0, "Tools list should not be empty"

    def test_each_tool_has_name(self, tools_response):
        for t in tools_response["tools"]:
            assert "name" in t and isinstance(t["name"], str), f"Tool missing 'name': {t}"

    def test_each_tool_has_description(self, tools_response):
        for t in tools_response["tools"]:
            assert "description" in t and isinstance(t["description"], str), f"Tool missing 'description': {t}"

    def test_each_tool_has_input_schema(self, tools_response):
        for t in tools_response["tools"]:
            assert "inputSchema" in t, f"Tool '{t.get('name')}' missing inputSchema"
            assert t["inputSchema"].get("type") == "object", f"inputSchema.type must be 'object' for '{t.get('name')}'"

    def test_required_tools_present(self, tools_response):
        names = {t["name"] for t in tools_response["tools"]}
        required = {"product_search", "inventory_check", "apply_discount", "create_checkout_session"}
        missing = required - names
        assert not missing, f"Missing required MCP tools: {missing}"

    def test_product_search_has_required_query_param(self, tools_response):
        ps = next((t for t in tools_response["tools"] if t["name"] == "product_search"), None)
        assert ps is not None
        required = ps["inputSchema"].get("required", [])
        assert "query" in required, "product_search must require 'query' parameter"

    def test_tool_call_returns_valid_json(self):
        r = httpx.post(f"{MCP_URL}/tools/call", json={"name": "get_bestsellers", "input": {}}, timeout=TIMEOUT)
        assert r.status_code == 200
        data = r.json()
        assert "tool" in data or "bestsellers" in data, f"Unexpected tool response: {data}"

    def test_unknown_tool_returns_400(self):
        r = httpx.post(f"{MCP_URL}/tools/call", json={"name": "nonexistent_tool", "input": {}}, timeout=TIMEOUT)
        assert r.status_code == 400


# ── A2A Compliance ─────────────────────────────────────────────────────────────

class TestA2ACompliance:
    @pytest.fixture(scope="class")
    def agent_card(self):
        r = httpx.get(f"{AGENT_URL}/.well-known/agent-card.json", timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()

    def test_agent_card_has_required_fields(self, agent_card):
        for field in ("protocolVersion", "name", "url", "capabilities", "skills"):
            assert field in agent_card, f"Agent card missing field: {field}"

    def test_agent_card_has_ucp_extension(self, agent_card):
        exts = {e["uri"] for e in agent_card.get("extensions", [])}
        assert "dev.ucp.shopping.checkout" in exts, \
            f"Agent card must advertise UCP checkout extension. Found: {exts}"

    def test_a2a_message_send_is_valid_jsonrpc(self):
        payload = {
            "jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": "message/send",
            "params": {"contextId": str(uuid.uuid4()), "message": {"parts": [{"kind": "text", "text": "hello"}]}},
        }
        r = httpx.post(f"{AGENT_URL}/a2a", json=payload, timeout=TIMEOUT)
        assert r.status_code == 200
        data = r.json()
        assert data.get("jsonrpc") == "2.0", "Response must be JSON-RPC 2.0"
        assert "id" in data, "Response must include id"
        assert "result" in data or "error" in data, "Response must include result or error"

    def test_a2a_result_has_artifacts(self):
        payload = {
            "jsonrpc": "2.0", "id": "1", "method": "message/send",
            "params": {"contextId": str(uuid.uuid4()), "message": {"parts": [{"kind": "text", "text": "what are your bestsellers?"}]}},
        }
        r = httpx.post(f"{AGENT_URL}/a2a", json=payload, timeout=60)
        assert r.status_code == 200
        result = r.json().get("result", {})
        assert "artifacts" in result, "A2A result must contain artifacts"
        assert len(result["artifacts"]) > 0
        parts = result["artifacts"][0].get("parts", [])
        assert any(p.get("kind") == "text" for p in parts), "First artifact must have a text part"

    def test_a2a_result_has_metadata(self):
        payload = {
            "jsonrpc": "2.0", "id": "2", "method": "message/send",
            "params": {"contextId": str(uuid.uuid4()), "message": {"parts": [{"kind": "text", "text": "show me cookies"}]}},
        }
        r = httpx.post(f"{AGENT_URL}/a2a", json=payload, timeout=60)
        result = r.json().get("result", {})
        assert "metadata" in result, "A2A result must include metadata"
        assert "tool_events" in result["metadata"], "metadata must include tool_events"

    def test_a2a_invalid_method_returns_error(self):
        payload = {"jsonrpc": "2.0", "id": "3", "method": "invalid/method", "params": {}}
        r = httpx.post(f"{AGENT_URL}/a2a", json=payload, timeout=TIMEOUT)
        assert r.status_code == 200  # JSON-RPC errors are 200 with error field
        data = r.json()
        assert "error" in data, "Invalid method should return JSON-RPC error"


# ── UCP Compliance ─────────────────────────────────────────────────────────────

class TestUCPCompliance:
    @pytest.fixture(scope="class")
    def ucp_profile(self):
        r = httpx.get(f"{AGENT_URL}/.well-known/ucp", timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()

    def test_ucp_profile_fields(self, ucp_profile):
        assert "merchant" in ucp_profile
        assert "payment_instruments" in ucp_profile
        assert "fulfillment_options" in ucp_profile

    def test_ucp_checkout_object_schema(self):
        """Create a checkout and validate the UCP object schema."""
        # First get a product ID
        r = httpx.post(f"{MCP_URL}/tools/call", json={"name": "product_search", "input": {"query": "shortbread", "in_stock": True}}, timeout=TIMEOUT)
        products = r.json().get("results", [])
        if not products:
            pytest.skip("No in-stock products found for checkout test")
        pid = products[0]["id"]

        # Create checkout via A2A
        payload = {
            "jsonrpc": "2.0", "id": "ucp1", "method": "message/send",
            "params": {"contextId": str(uuid.uuid4()), "message": {"parts": [{"kind": "text", "text": f"I want to buy 1 {products[0]['name']}"}]}},
        }
        r2 = httpx.post(f"{AGENT_URL}/a2a", json=payload, timeout=60)
        checkout_id = r2.json().get("result", {}).get("metadata", {}).get("checkout_id")
        if not checkout_id:
            pytest.skip("No checkout created in this test run")

        # Validate UCP checkout object
        r3 = httpx.get(f"{AGENT_URL}/ucp/checkout/{checkout_id}", timeout=TIMEOUT)
        assert r3.status_code == 200
        co = r3.json()
        for field in ("id", "state", "line_items", "subtotal", "total", "payment_instruments", "fulfillment_options"):
            assert field in co, f"UCP checkout object missing field: {field}"
        assert co["state"] in ("NOT_READY_FOR_PAYMENT", "READY_FOR_PAYMENT", "COMPLETED"), \
            f"Invalid UCP state: {co['state']}"


# ── ACP Feed Compliance ────────────────────────────────────────────────────────

class TestACPFeedCompliance:
    @pytest.fixture(scope="class")
    def acp_feed(self):
        r = httpx.get(f"{CATALOG_SYNC_URL}/feed/acp", timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()

    def test_acp_feed_has_products(self, acp_feed):
        assert "products" in acp_feed, "ACP feed must have 'products' key"
        assert len(acp_feed["products"]) > 0

    def test_acp_product_has_required_fields(self, acp_feed):
        for p in acp_feed["products"]:
            assert "id" in p, f"Product missing 'id': {p}"
            assert "variants" in p, f"Product '{p.get('id')}' missing 'variants'"
            assert len(p["variants"]) > 0

    def test_acp_variant_has_price_and_availability(self, acp_feed):
        for p in acp_feed["products"]:
            for v in p["variants"]:
                price = v.get("price", {})
                assert "amount" in price, f"Variant missing price.amount in product {p.get('id')}"
                assert "currency" in price, f"Variant missing price.currency in product {p.get('id')}"
                avail = v.get("availability", {})
                assert "available" in avail, f"Variant missing availability.available in product {p.get('id')}"

    def test_ucp_feed_has_native_commerce(self):
        r = httpx.get(f"{CATALOG_SYNC_URL}/feed/ucp", timeout=TIMEOUT)
        r.raise_for_status()
        feed = r.json()
        for p in feed.get("products", []):
            assert "native_commerce" in p, f"UCP product '{p.get('id')}' missing native_commerce"
            assert "checkout_eligibility" in p["native_commerce"], \
                f"UCP product '{p.get('id')}' missing native_commerce.checkout_eligibility"

    def test_catalog_sync_status(self):
        r = httpx.get(f"{CATALOG_SYNC_URL}/status", timeout=TIMEOUT)
        assert r.status_code == 200
        data = r.json()
        assert data.get("status") == "ok"
        assert data.get("last_sync_at") is not None
