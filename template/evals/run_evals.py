"""
Eval Runner — Agentic Commerce Stack
=====================================
Usage:
  python run_evals.py --suite all
  python run_evals.py --suite behavior
  python run_evals.py --suite compliance
  python run_evals.py --suite latency
  python run_evals.py --suite quality
  python run_evals.py --suite all --report

Flags:
  --suite    Which suite to run (default: all)
  --report   Persist results to evals/results/<timestamp>.json

Services must be running before executing evals.
  docker-compose up -d  (then)  docker-compose run evals python run_evals.py
"""
import sys
import json
import time
import subprocess
from pathlib import Path
from datetime import datetime, timezone

import typer
from rich.console import Console
from rich.table import Table
from rich import box

app = typer.Typer(add_completion=False)
console = Console()

RESULTS_DIR = Path(__file__).parent / "results"
RESULTS_DIR.mkdir(exist_ok=True)

SUITE_MODULES = {
    "behavior":   "behavior.test_behavior",
    "compliance": "compliance.test_compliance",
    "latency":    "latency.bench_latency",
    "quality":    "quality.judge_quality",
}


def _run_pytest_suite(module: str) -> dict:
    """Run a pytest module and return {passed, failed, errors, duration_s}."""
    t0 = time.time()
    result = subprocess.run(
        [sys.executable, "-m", "pytest", f"{module.replace('.', '/')}.py", "-v", "--tb=short", "--no-header"],
        capture_output=True, text=True,
        cwd=Path(__file__).parent,
    )
    duration = round(time.time() - t0, 2)
    output = result.stdout + result.stderr

    passed = output.count(" PASSED")
    failed = output.count(" FAILED")
    errors = output.count(" ERROR")

    return {
        "module": module,
        "passed": passed,
        "failed": failed,
        "errors": errors,
        "duration_s": duration,
        "exit_code": result.returncode,
        "output": output,
    }


def _run_latency_suite() -> dict:
    """Run latency benchmarks as a subprocess and capture structured output."""
    t0 = time.time()
    result = subprocess.run(
        [sys.executable, "latency/bench_latency.py"],
        capture_output=True, text=True,
        cwd=Path(__file__).parent,
    )
    duration = round(time.time() - t0, 2)
    try:
        data = json.loads(result.stdout.split("RESULTS_JSON:")[-1].strip())
    except Exception:
        data = {"error": "Could not parse latency output", "raw": result.stdout[-500:]}
    return {"module": "latency.bench_latency", "duration_s": duration, "results": data, "exit_code": result.returncode}


def _run_quality_suite() -> dict:
    """Run LLM quality eval as a subprocess."""
    t0 = time.time()
    result = subprocess.run(
        [sys.executable, "quality/judge_quality.py"],
        capture_output=True, text=True,
        cwd=Path(__file__).parent,
    )
    duration = round(time.time() - t0, 2)
    try:
        data = json.loads(result.stdout.split("RESULTS_JSON:")[-1].strip())
    except Exception:
        data = {"error": "Could not parse quality output", "raw": result.stdout[-500:]}
    exit_code = result.returncode
    # Belt-and-suspenders: a parse error or any ungraded case is a failure even if the
    # subprocess somehow exited 0. A quality run that scored 0 across the board is NOT a pass.
    if exit_code == 0 and ("error" in data or data.get("failed", 0) or not data.get("graded", 0)):
        exit_code = 1
    return {"module": "quality.judge_quality", "duration_s": duration, "results": data, "exit_code": exit_code}


@app.command()
def main(
    suite: str = typer.Option("all", help="Suite to run: all | behavior | compliance | latency | quality"),
    report: bool = typer.Option(False, help="Persist results to evals/results/<timestamp>.json"),
):
    console.rule("[bold cyan]Agentic Commerce Eval Runner[/bold cyan]")
    console.print(f"Suite: [bold yellow]{suite}[/bold yellow]  |  Report: {report}\n")

    suites_to_run = list(SUITE_MODULES.keys()) if suite == "all" else [suite]
    if suite != "all" and suite not in SUITE_MODULES:
        console.print(f"[red]Unknown suite '{suite}'. Choose from: {', '.join(SUITE_MODULES.keys())}[/red]")
        raise typer.Exit(1)

    all_results: dict = {"run_at": datetime.now(timezone.utc).isoformat(), "suites": {}}
    overall_pass = True

    for s in suites_to_run:
        console.rule(f"[bold]{s.upper()}[/bold]")

        if s == "latency":
            r = _run_latency_suite()
            all_results["suites"][s] = r
            if r["exit_code"] != 0:
                overall_pass = False
            _print_latency_results(r)
        elif s == "quality":
            r = _run_quality_suite()
            all_results["suites"][s] = r
            if r["exit_code"] != 0:
                overall_pass = False
            _print_quality_results(r)
        else:
            r = _run_pytest_suite(SUITE_MODULES[s])
            all_results["suites"][s] = r
            if r["failed"] > 0 or r["errors"] > 0:
                overall_pass = False
            _print_pytest_results(r)

    # Summary table
    console.rule("[bold]Summary[/bold]")
    table = Table(box=box.ROUNDED, show_header=True)
    table.add_column("Suite", style="cyan")
    table.add_column("Status", justify="center")
    table.add_column("Duration")
    for s, r in all_results["suites"].items():
        ok = r.get("exit_code", 0) == 0
        status = "[green]PASS[/green]" if ok else "[red]FAIL[/red]"
        table.add_row(s, status, f"{r.get('duration_s', '?')}s")
    console.print(table)

    if report:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        out_path = RESULTS_DIR / f"eval_{ts}.json"
        out_path.write_text(json.dumps(all_results, indent=2))
        console.print(f"\n[dim]Results saved → {out_path}[/dim]")

    raise typer.Exit(0 if overall_pass else 1)


def _print_pytest_results(r: dict):
    color = "green" if r["failed"] == 0 and r["errors"] == 0 else "red"
    console.print(f"[{color}]Passed: {r['passed']}  Failed: {r['failed']}  Errors: {r['errors']}  ({r['duration_s']}s)[/{color}]")
    if r["failed"] > 0 or r["errors"] > 0:
        console.print("[dim]" + r["output"][-1500:] + "[/dim]")


def _print_latency_results(r: dict):
    results = r.get("results", {})
    if "error" in results:
        console.print(f"[red]{results['error']}[/red]")
        return
    table = Table(box=box.SIMPLE, show_header=True)
    table.add_column("Tool/Scenario", style="cyan")
    table.add_column("P50 (ms)", justify="right")
    table.add_column("P95 (ms)", justify="right")
    table.add_column("P99 (ms)", justify="right")
    table.add_column("Runs", justify="right")
    for row in results.get("rows", []):
        table.add_row(row["label"], str(row["p50"]), str(row["p95"]), str(row["p99"]), str(row["n"]))
    console.print(table)


def _print_quality_results(r: dict):
    results = r.get("results", {})
    if "error" in results:
        console.print(f"[red]{results['error']}[/red]")
        return
    table = Table(box=box.SIMPLE, show_header=True)
    table.add_column("Prompt", style="cyan", max_width=40)
    table.add_column("Helpfulness", justify="center")
    table.add_column("Accuracy", justify="center")
    table.add_column("Protocol", justify="center")
    table.add_column("Tone", justify="center")
    table.add_column("Avg", justify="center")
    for row in results.get("scores", []):
        avg = row.get("avg", "?")
        color = "green" if isinstance(avg, (int, float)) and avg >= 4 else "yellow" if isinstance(avg, (int, float)) and avg >= 3 else "red"
        table.add_row(
            row["prompt"][:40],
            str(row.get("helpfulness", "?")),
            str(row.get("accuracy", "?")),
            str(row.get("protocol_awareness", "?")),
            str(row.get("tone", "?")),
            f"[{color}]{avg}[/{color}]",
        )
    console.print(table)
    if results.get("failed"):
        console.print(f"[red]{results['failed']} case(s) could not be graded — FAIL.[/red]")
    if results.get("rate_limited"):
        console.print(
            "[yellow]Looks rate-limited by Cerebras. Wait for the quota to reset or use a "
            "different CEREBRAS_API_KEY / model, then re-run.[/yellow]"
        )


if __name__ == "__main__":
    app()
