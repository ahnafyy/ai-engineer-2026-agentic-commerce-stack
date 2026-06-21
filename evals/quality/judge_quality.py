"""
LLM Quality Evals — GPT-4o as judge.
======================================
Sends predefined prompts to the agent, then uses GPT-4o to score each
response on 4 dimensions (1–5 scale):

  - helpfulness:         Did the agent address the user's actual need?
  - accuracy:            Was factual information about products/policies correct?
  - protocol_awareness:  Did the agent correctly invoke the right tools/protocols?
  - tone:                Was the response warm, on-brand, and appropriately playful?

Prints a scorecard table and outputs RESULTS_JSON:<json> to stdout.

Requires: GITHUB_TOKEN env var (used for GPT-4o via GitHub AI Models)
"""
import os
import sys
import json
import uuid
import time

import httpx
from openai import OpenAI
from rich.console import Console
from rich.table import Table
from rich import box

AGENT_URL      = os.environ.get("AGENT_URL",    "http://merchant-agent:10999")
GITHUB_TOKEN   = os.environ.get("GITHUB_TOKEN", "")
TIMEOUT        = 60.0
console        = Console(stderr=True)

judge_client = OpenAI(
    base_url="https://models.inference.ai.azure.com",
    api_key=GITHUB_TOKEN,
) if GITHUB_TOKEN else None

TEST_CASES = [
    {
        "prompt": "What are your bestselling items?",
        "expected_tools": ["get_bestsellers"],
        "note": "Should list top products with prices",
    },
    {
        "prompt": "Do you have anything gluten-free?",
        "expected_tools": ["product_search", "get_product_details"],
        "note": "Should search and note allergen info",
    },
    {
        "prompt": "What's your return policy if my order is damaged?",
        "expected_tools": ["get_store_policy"],
        "note": "Should accurately state the 24hr return policy",
    },
    {
        "prompt": "I want to buy 2 Purr-fect Matcha Cookies",
        "expected_tools": ["create_checkout_session"],
        "note": "Should create a checkout session with correct quantity",
    },
    {
        "prompt": "Apply code MEOW20 to my cart",
        "expected_tools": ["apply_discount"],
        "note": "Should call apply_discount with the code and return savings",
    },
]

JUDGE_SYSTEM = """You are an expert evaluator for an agentic commerce system.
You will receive a user prompt sent to a bakery shopping agent, the agent's response, and the MCP tools that were called.
Score the response on these 4 dimensions using integers 1-5:

1. helpfulness (1=useless, 5=fully addressed the need)
2. accuracy (1=wrong facts, 5=all facts correct)
3. protocol_awareness (1=wrong tools, 5=exactly right tools in right order)
4. tone (1=robotic/off-brand, 5=warm, playful, on-brand for a cat bakery)

Return ONLY valid JSON in this exact format:
{"helpfulness": <int>, "accuracy": <int>, "protocol_awareness": <int>, "tone": <int>, "reasoning": "<one sentence>"}"""


def _send(prompt: str) -> dict:
    payload = {
        "jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": "message/send",
        "params": {"contextId": str(uuid.uuid4()), "message": {"parts": [{"kind": "text", "text": prompt}]}},
    }
    r = httpx.post(f"{AGENT_URL}/a2a", json=payload, timeout=TIMEOUT)
    r.raise_for_status()
    body = r.json()
    # The agent returns HTTP 200 with a JSON-RPC error body when the LLM call fails
    # (e.g. GitHub Models rate limit). Surface that as a real failure, not an empty score.
    if body.get("error"):
        msg = body["error"].get("message", body["error"]) if isinstance(body["error"], dict) else body["error"]
        raise RuntimeError(f"agent JSON-RPC error: {msg}")
    result = body.get("result", {})
    text = result.get("artifacts", [{}])[0].get("parts", [{}])[0].get("text", "")
    tools = [e["tool"] for e in result.get("metadata", {}).get("tool_events", [])]
    return {"text": text, "tools": tools}


def _judge(prompt: str, response: str, tools: list[str], expected_tools: list[str]) -> dict:
    if not judge_client:
        return {"helpfulness": 0, "accuracy": 0, "protocol_awareness": 0, "tone": 0,
                "reasoning": "GITHUB_TOKEN not set — skipping LLM judge"}
    user_content = (
        f"USER PROMPT: {prompt}\n\n"
        f"AGENT RESPONSE:\n{response}\n\n"
        f"TOOLS CALLED: {tools}\n"
        f"EXPECTED TOOLS: {expected_tools}"
    )
    try:
        resp = judge_client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": JUDGE_SYSTEM},
                {"role": "user", "content": user_content},
            ],
            max_tokens=200, temperature=0,
        )
        return json.loads(resp.choices[0].message.content.strip())
    except Exception as e:
        return {"helpfulness": 0, "accuracy": 0, "protocol_awareness": 0, "tone": 0,
                "reasoning": f"Judge call failed: {e}"}


def _is_rate_limit(text: str) -> bool:
    t = (text or "").lower()
    return "429" in t or "rate limit" in t or "ratelimitreached" in t


if __name__ == "__main__":
    console.rule("[bold cyan]LLM Quality Evals[/bold cyan]")
    if not GITHUB_TOKEN:
        console.print("[red]ERROR: GITHUB_TOKEN not set — the judge cannot run. This is a FAILURE, not a pass.[/red]")

    scores = []
    rate_limited = False
    for tc in TEST_CASES:
        console.print(f"  → {tc['prompt'][:50]}...")
        try:
            agent_out = _send(tc["prompt"])
        except Exception as e:
            rate_limited = rate_limited or _is_rate_limit(str(e))
            console.print(f"[red]Agent call failed: {e}[/red]")
            scores.append({"prompt": tc["prompt"], "helpfulness": 0, "accuracy": 0,
                           "protocol_awareness": 0, "tone": 0, "avg": 0,
                           "error": True, "reasoning": str(e)})
            continue

        judgment = _judge(tc["prompt"], agent_out["text"], agent_out["tools"], tc["expected_tools"])
        avg = round(
            (judgment.get("helpfulness", 0) + judgment.get("accuracy", 0) +
             judgment.get("protocol_awareness", 0) + judgment.get("tone", 0)) / 4, 2
        )
        # A real judgment is on a 1-5 scale, so avg==0 only happens on an error path
        # (judge call failed / token missing). Treat that as a failed case, not a 0 score.
        is_error = avg == 0
        rate_limited = rate_limited or _is_rate_limit(judgment.get("reasoning", ""))
        scores.append({"prompt": tc["prompt"], **judgment, "avg": avg, "error": is_error})
        time.sleep(0.5)  # avoid rate limiting

    # Table to stderr
    table = Table(title="Quality Scorecard", box=box.ROUNDED)
    table.add_column("Prompt", max_width=35, style="cyan")
    table.add_column("Help", justify="center")
    table.add_column("Accur.", justify="center")
    table.add_column("Protocol", justify="center")
    table.add_column("Tone", justify="center")
    table.add_column("Avg", justify="center")
    table.add_column("Reasoning", max_width=40, style="dim")
    for s in scores:
        avg = s.get("avg", 0)
        color = "green" if avg >= 4 else "yellow" if avg >= 3 else "red"
        table.add_row(
            s["prompt"][:35], str(s.get("helpfulness", "?")), str(s.get("accuracy", "?")),
            str(s.get("protocol_awareness", "?")), str(s.get("tone", "?")),
            f"[{color}]{avg}[/{color}]", s.get("reasoning", "")[:40],
        )
    console.print(table)

    failed = [s for s in scores if s.get("error")]
    graded = [s for s in scores if not s.get("error")]
    overall_avg = round(sum(s["avg"] for s in graded) / len(graded), 2) if graded else 0
    console.print(f"\nGraded {len(graded)}/{len(scores)} cases — overall average: [bold]{overall_avg}[/bold] / 5.0")

    if failed:
        console.print(f"[red]{len(failed)} case(s) could not be graded — quality eval FAILED.[/red]")
        if rate_limited:
            console.print(
                "[yellow]Cause looks like a GitHub Models rate limit (free tier: ~50 GPT-4o "
                "requests/day, rolling 24h). Wait for the quota to reset, or point the agent/judge "
                "at an OpenAI/Azure key, then re-run.[/yellow]"
            )

    print(f"RESULTS_JSON:{json.dumps({'scores': scores, 'overall_avg': overall_avg, 'failed': len(failed), 'graded': len(graded), 'rate_limited': rate_limited})}")

    # Exit non-zero if any case failed so the runner reports FAIL honestly.
    sys.exit(1 if (failed or not scores) else 0)
