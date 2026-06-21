"""
Latency Benchmarks — P50/P95/P99 per tool and full checkout flow.
===================================================================
Runs each scenario N times, computes percentiles, outputs a Rich table
and prints RESULTS_JSON:<json> to stdout for the runner to parse.

Requires the full stack running.
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

MCP_URL    = os.environ.get("MCP_URL",    "http://mcp-server:8001")
AGENT_URL  = os.environ.get("AGENT_URL",  "http://merchant-agent:10999")
N          = int(os.environ.get("BENCH_N", "10"))  # runs per scenario
TIMEOUT    = 30.0
console    = Console(stderr=True)


def _p(samples: list[float], pct: int) -> float:
    if not samples:
        return 0.0
    sorted_s = sorted(samples)
    idx = max(0, int(len(sorted_s) * pct / 100) - 1)
    return round(sorted_s[idx], 1)


def bench_mcp_tool(name: str, input_: dict, n: int = N) -> dict:
    """Benchmark a single MCP tool call."""
    samples = []
    for _ in range(n):
        t0 = time.time()
        try:
            r = httpx.post(f"{MCP_URL}/tools/call", json={"name": name, "input": input_}, timeout=TIMEOUT)
            r.raise_for_status()
        except Exception:
            pass
        samples.append((time.time() - t0) * 1000)
    return {"label": f"mcp:{name}", "n": n, "p50": _p(samples, 50), "p95": _p(samples, 95), "p99": _p(samples, 99), "mean": round(statistics.mean(samples), 1)}


def bench_a2a_roundtrip(prompt: str, label: str, n: int = N) -> dict:
    """Benchmark a full A2A message/send round-trip."""
    samples = []
    for _ in range(n):
        payload = {
            "jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": "message/send",
            "params": {"contextId": str(uuid.uuid4()), "message": {"parts": [{"kind": "text", "text": prompt}]}},
        }
        t0 = time.time()
        try:
            r = httpx.post(f"{AGENT_URL}/a2a", json=payload, timeout=60)
            r.raise_for_status()
        except Exception:
            pass
        samples.append((time.time() - t0) * 1000)
    return {"label": label, "n": n, "p50": _p(samples, 50), "p95": _p(samples, 95), "p99": _p(samples, 99), "mean": round(statistics.mean(samples), 1)}


def bench_catalog_sync(n: int = N) -> dict:
    """Benchmark catalog-sync /feed/acp response time."""
    from catalog_sync_url import CATALOG_SYNC_URL  # noqa - defined below
    samples = []
    for _ in range(n):
        t0 = time.time()
        try:
            httpx.get(f"{CATALOG_SYNC_URL}/feed/acp", timeout=TIMEOUT)
        except Exception:
            pass
        samples.append((time.time() - t0) * 1000)
    return {"label": "catalog-sync:/feed/acp", "n": n, "p50": _p(samples, 50), "p95": _p(samples, 95), "p99": _p(samples, 99), "mean": round(statistics.mean(samples), 1)}


if __name__ == "__main__":
    CATALOG_SYNC_URL = os.environ.get("CATALOG_SYNC_URL", "http://catalog-sync:8002")

    console.rule("[bold cyan]Latency Benchmarks[/bold cyan]")
    console.print(f"N={N} runs per scenario  •  MCP={MCP_URL}  •  Agent={AGENT_URL}\n")

    rows = []

    # MCP tool benchmarks
    mcp_scenarios = [
        ("product_search",    {"query": "cookie", "in_stock": True}),
        ("get_bestsellers",   {}),
        ("inventory_check",   {"product_id": "prod_001"}),
        ("get_store_policy",  {}),
        ("get_product_details", {"product_id": "prod_009"}),
    ]
    for name, inp in mcp_scenarios:
        console.print(f"Benchmarking mcp:{name}...")
        rows.append(bench_mcp_tool(name, inp))

    # Catalog sync feed
    console.print("Benchmarking catalog-sync:/feed/acp...")
    samples = []
    for _ in range(N):
        t0 = time.time()
        try:
            httpx.get(f"{CATALOG_SYNC_URL}/feed/acp", timeout=TIMEOUT)
        except Exception:
            pass
        samples.append((time.time() - t0) * 1000)
    rows.append({"label": "catalog-sync:/feed/acp", "n": N,
                 "p50": _p(samples, 50), "p95": _p(samples, 95), "p99": _p(samples, 99),
                 "mean": round(statistics.mean(samples), 1)})

    # A2A round-trips (fewer runs — LLM calls are slow)
    a2a_n = max(3, N // 3)
    a2a_scenarios = [
        ("show me your bestsellers",          "a2a:get_bestsellers"),
        ("search for cookies under $6",       "a2a:product_search"),
    ]
    for prompt, label in a2a_scenarios:
        console.print(f"Benchmarking {label} (n={a2a_n})...")
        r = bench_a2a_roundtrip(prompt, label, n=a2a_n)
        rows.append(r)

    # Print table to stderr (for visual output when running directly)
    table = Table(title="Latency Results", box=box.ROUNDED)
    table.add_column("Tool / Scenario", style="cyan")
    table.add_column("P50 (ms)", justify="right")
    table.add_column("P95 (ms)", justify="right")
    table.add_column("P99 (ms)", justify="right")
    table.add_column("Mean (ms)", justify="right")
    table.add_column("N", justify="right", style="dim")
    for row in rows:
        p50_color = "green" if row["p50"] < 300 else "yellow" if row["p50"] < 1000 else "red"
        table.add_row(
            row["label"],
            f"[{p50_color}]{row['p50']}[/{p50_color}]",
            str(row["p95"]), str(row["p99"]), str(row["mean"]), str(row["n"]),
        )
    console.print(table)

    # Print machine-readable results to stdout for the runner to parse
    print(f"RESULTS_JSON:{json.dumps({'rows': rows})}")
