"""
Protocol Compliance Evals — Do responses match the specs?
==========================================================
Validates schema conformance for:
  - MCP /tools endpoint shape
  - A2A JSON-RPC 2.0 message/send contract
  - UCP checkout object schema
  - Agent card required fields

Template note: the ACP feed tests are omitted here because the template has no
catalog-sync service. If you add catalog-sync, copy the TestACPFeedCompliance
class from the full demo's evals/compliance/test_compliance.py.

Env vars:
  AGENT_URL   default http://localhost:10999
  MCP_URL     default http://localhost:8001
"""
import os
import uuid

import httpx
import pytest

AGENT_URL = os.environ.get("AGENT_URL", "http://localhost:10999")
MCP_URL   = os.environ.get("MCP_URL",   "http://localhost:8001")
TIMEOUT   = 15.0


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
        assert len(tools_response["tools"]) > 0

    def test_each_tool_has_name(self, tools_response):
        for t in tools_response["tools"]:
            assert "name" in t and isinstance(t["name"], str)

    def test_each_tool_has_description(self, tools_response):
        for t in tools_response["tools"]:
            assert "description" in t and isinstance(t["description"], str)

    def test_each_tool_has_input_schema(self, tools_response):
        for t in tools_response["tools"]:
            assert "inputSchema" in t, f"Tool '{t.get('name')}' missing inputSchema"
            assert t["inputSchema"].get("type") == "object"

    def test_required_tools_present(self, tools_response):
        """These 4 tools are required by the stack. If you rename them, update this list."""
        names = {t["name"] for t in tools_response["tools"]}
        required = {"product_search", "inventory_check", "apply_discount", "create_checkout_session"}
        missing = required - names
        assert not missing, f"Missing required MCP tools: {missing}"

    def test_product_search_has_required_query_param(self, tools_response):
        ps = next((t for t in tools_response["tools"] if t["name"] == "product_search"), None)
        assert ps is not None
        assert "query" in ps["inputSchema"].get("required", [])

    def test_tool_call_returns_valid_json(self):
        r = httpx.post(f"{MCP_URL}/tools/call", json={"name": "get_bestsellers", "input": {}}, timeout=TIMEOUT)
        assert r.status_code == 200
        data = r.json()
        assert "tool" in data or "bestsellers" in data

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
        assert data.get("jsonrpc") == "2.0"
        assert "id" in data
        assert "result" in data or "error" in data

    def test_a2a_result_has_artifacts(self):
        payload = {
            "jsonrpc": "2.0", "id": "1", "method": "message/send",
            "params": {"contextId": str(uuid.uuid4()), "message": {"parts": [{"kind": "text", "text": "what are your bestsellers?"}]}},
        }
        r = httpx.post(f"{AGENT_URL}/a2a", json=payload, timeout=60)
        assert r.status_code == 200
        result = r.json().get("result", {})
        assert "artifacts" in result
        parts = result["artifacts"][0].get("parts", [])
        assert any(p.get("kind") == "text" for p in parts)

    def test_a2a_result_has_metadata(self):
        payload = {
            "jsonrpc": "2.0", "id": "2", "method": "message/send",
            "params": {"contextId": str(uuid.uuid4()), "message": {"parts": [{"kind": "text", "text": "show me your products"}]}},
        }
        r = httpx.post(f"{AGENT_URL}/a2a", json=payload, timeout=60)
        result = r.json().get("result", {})
        assert "metadata" in result
        assert "tool_events" in result["metadata"]

    def test_a2a_invalid_method_returns_error(self):
        payload = {"jsonrpc": "2.0", "id": "3", "method": "invalid/method", "params": {}}
        r = httpx.post(f"{AGENT_URL}/a2a", json=payload, timeout=TIMEOUT)
        assert r.status_code == 200
        assert "error" in r.json()


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
        r = httpx.post(f"{MCP_URL}/tools/call",
                       json={"name": "product_search", "input": {"query": "tee", "in_stock": True}},
                       timeout=TIMEOUT)
        products = r.json().get("results", [])
        if not products:
            pytest.skip("No in-stock products found for checkout test")

        payload = {
            "jsonrpc": "2.0", "id": "ucp1", "method": "message/send",
            "params": {"contextId": str(uuid.uuid4()), "message": {
                "parts": [{"kind": "text", "text": f"I want to buy 1 {products[0]['name']}"}]}},
        }
        r2 = httpx.post(f"{AGENT_URL}/a2a", json=payload, timeout=60)
        checkout_id = r2.json().get("result", {}).get("metadata", {}).get("checkout_id")
        if not checkout_id:
            pytest.skip("No checkout created in this test run")

        r3 = httpx.get(f"{AGENT_URL}/ucp/checkout/{checkout_id}", timeout=TIMEOUT)
        assert r3.status_code == 200
        co = r3.json()
        for field in ("id", "state", "line_items", "subtotal", "total",
                      "payment_instruments", "fulfillment_options"):
            assert field in co, f"UCP checkout missing field: {field}"
        assert co["state"] in ("NOT_READY_FOR_PAYMENT", "READY_FOR_PAYMENT", "COMPLETED")
