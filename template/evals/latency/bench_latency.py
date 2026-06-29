"""
Latency Benchmarks — P50/P95/P99 per MCP tool and A2A round-trip.
===================================================================
Runs each scenario N times, computes percentiles, outputs a Rich table
and prints RESULTS_JSON:<json> to stdout for the runner to parse.

Requires the full stack running.

Env vars:
  MCP_URL     default http://localhost:8001
  AGENT_URL   default http://localhost:10999
  BENCH_N     number of runs per scenario (default 10)

TODO: update the product IDs in mcp_scenarios to match YOUR catalog.
"""
import os
import json
import time
import uuid
import statistics

import httpx
from rich.console import Console
from rich.table import Table
from rich import box

MCP_URL   = os.environ.get("MCP_URL",   "http://localhost:8001")
AGENT_URL = os.environ.get("AGENT_URL", "http://localhost:10999")
N         = int(os.environ.get("BENCH_N", "10"))
TIMEOUT   = 30.0
console   = Console(stderr=True)


def _p(samples: list[float], pct: int) -> float:
    if not samples:
        return 0.0
    sorted_s = sorted(samples)
    idx = max(0, int(len(sorted_s) * pct / 100) - 1)
    return round(sorted_s[idx], 1)


def bench_mcp_tool(name: str, input_: dict, n: int = N) -> dict:
    samples = []
    for _ in range(n):
        t0 = time.time()
        try:
            r = httpx.post(f"{MCP_URL}/tools/call", json={"name": name, "input": input_}, timeout=TIMEOUT)
            r.raise_for_status()
        except Exception:
            pass
        samples.append((time.time() - t0) * 1000)
    return {"label": f"mcp:{name}", "n": n,
            "p50": _p(samples, 50), "p95": _p(samples, 95), "p99": _p(samples, 99),
            "mean": round(statistics.mean(samples), 1)}


def bench_a2a_roundtrip(prompt: str, label: str, n: int = N) -> dict:
    samples = []
    for _ in range(min(n, 3)):   # A2A calls are slower — cap at 3 by default
        payload = {
            "jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": "message/send",
            "params": {"contextId": str(uuid.uuid4()),
                       "message": {"parts": [{"kind": "text", "text": prompt}]}},
        }
        t0 = time.time()
        try:
            r = httpx.post(f"{AGENT_URL}/a2a", json=payload, timeout=60)
            r.raise_for_status()
        except Exception:
            pass
        samples.append((time.time() - t0) * 1000)
    return {"label": label, "n": len(samples),
            "p50": _p(samples, 50), "p95": _p(samples, 95), "p99": _p(samples, 99),
            "mean": round(statistics.mean(samples), 1)}


if __name__ == "__main__":
    console.rule("[bold cyan]Latency Benchmarks[/bold cyan]")
    console.print(f"N={N} runs per scenario  •  MCP={MCP_URL}  •  Agent={AGENT_URL}\n")

    rows = []

    # ── MCP tool benchmarks ────────────────────────────────────────────────────
    # TODO: update product_id values to match IDs in YOUR mcp-server catalog
    mcp_scenarios = [
        ("product_search",      {"query": "tee", "in_stock": True}),
        ("get_bestsellers",     {}),
        ("inventory_check",     {"product_id": "prod_001"}),
        ("get_store_policy",    {}),
        ("get_product_details", {"product_id": "prod_001"}),
    ]
    for name, inp in mcp_scenarios:
        console.print(f"  Benchmarking mcp:{name}...")
        rows.append(bench_mcp_tool(name, inp))

    # ── A2A round-trip benchmarks ──────────────────────────────────────────────
    a2a_scenarios = [
        ("what are your bestsellers?", "a2a:get_bestsellers"),
        # TODO: replace product name with one from YOUR catalog
        ("show me your products",      "a2a:product_search"),
    ]
    for prompt, label in a2a_scenarios:
        console.print(f"  Benchmarking {label}...")
        rows.append(bench_a2a_roundtrip(prompt, label))

    # ── Output ─────────────────────────────────────────────────────────────────
    table = Table(box=box.SIMPLE, show_header=True)
    table.add_column("Tool/Scenario", style="cyan")
    table.add_column("P50 (ms)", justify="right")
    table.add_column("P95 (ms)", justify="right")
    table.add_column("P99 (ms)", justify="right")
    table.add_column("Runs",     justify="right")
    for row in rows:
        table.add_row(row["label"], str(row["p50"]), str(row["p95"]), str(row["p99"]), str(row["n"]))
    console.print(table)

    print(f"RESULTS_JSON:{json.dumps({'rows': rows})}")
