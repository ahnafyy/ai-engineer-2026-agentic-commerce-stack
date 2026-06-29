"""
LLM Quality Evals — Cerebras model as judge.
=============================================
Sends TEST_CASES prompts to your agent, then uses a Cerebras model to score
each response on 4 dimensions (1–5 scale):

  - helpfulness:         Did the agent address the user's actual need?
  - accuracy:            Were product/policy facts correct?
  - protocol_awareness:  Did the agent call the right tools in the right order?
  - tone:                Was the response on-brand for your store?

TODO: Replace TEST_CASES with prompts and products that match YOUR store.
      Update JUDGE_SYSTEM's "bakery" references to describe YOUR store persona.

Requires: CEREBRAS_API_KEY in the environment.
"""
import os
import re
import sys
import json
import uuid
import time

import httpx
from openai import OpenAI
from rich.console import Console
from rich.table import Table
from rich import box

AGENT_URL         = os.environ.get("AGENT_URL",         "http://localhost:10999")
CEREBRAS_API_KEY  = os.environ.get("CEREBRAS_API_KEY",  "")
CEREBRAS_BASE_URL = os.environ.get("CEREBRAS_BASE_URL", "https://api.cerebras.ai/v1")
CEREBRAS_MODEL    = os.environ.get("CEREBRAS_MODEL",    "gpt-oss-120b")
TIMEOUT           = 60.0
console           = Console(stderr=True)

judge_client = OpenAI(
    base_url=CEREBRAS_BASE_URL,
    api_key=CEREBRAS_API_KEY,
) if CEREBRAS_API_KEY else None

# TODO: Replace these with prompts relevant to YOUR store.
TEST_CASES = [
    {
        "prompt": "What are your bestselling items?",
        "expected_tools": ["get_bestsellers"],
        "note": "Should list top products with prices",
    },
    {
        "prompt": "What's your return policy?",
        "expected_tools": ["get_store_policy"],
        "note": "Should accurately state the return policy",
    },
    {
        "prompt": "Show me what you have in stock",
        "expected_tools": ["product_search"],
        "note": "Should call product_search and list items",
    },
    {
        "prompt": "I want to buy a Classic Tee",
        "expected_tools": ["create_checkout_session"],
        "note": "Should ask about discount then create checkout",
    },
    {
        "prompt": "Apply code SAVE10 to my cart",
        "expected_tools": ["apply_discount"],
        "note": "Should call apply_discount and return savings",
    },
]

# TODO: update this to describe YOUR store persona instead of "bakery"
JUDGE_SYSTEM = """You are an expert evaluator for an agentic commerce system.
You will receive a user prompt sent to a shopping agent, the agent's response, and the MCP tools that were called.
Score the response on these 4 dimensions using integers 1-5:

1. helpfulness (1=useless, 5=fully addressed the need)
2. accuracy (1=wrong facts, 5=all facts correct)
3. protocol_awareness (1=wrong tools, 5=exactly right tools in right order)
4. tone (1=robotic/off-brand, 5=warm, helpful, on-brand)

Return ONLY valid JSON in this exact format:
{"helpfulness": <int>, "accuracy": <int>, "protocol_awareness": <int>, "tone": <int>, "reasoning": "<one sentence>"}"""


def _send(prompt: str) -> dict:
    payload = {
        "jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": "message/send",
        "params": {"contextId": str(uuid.uuid4()),
                   "message": {"parts": [{"kind": "text", "text": prompt}]}},
    }
    r = httpx.post(f"{AGENT_URL}/a2a", json=payload, timeout=TIMEOUT)
    r.raise_for_status()
    body = r.json()
    if body.get("error"):
        msg = body["error"].get("message", body["error"]) if isinstance(body["error"], dict) else body["error"]
        raise RuntimeError(f"agent JSON-RPC error: {msg}")
    result = body.get("result", {})
    text = result.get("artifacts", [{}])[0].get("parts", [{}])[0].get("text", "")
    tools = [e["tool"] for e in result.get("metadata", {}).get("tool_events", [])]
    return {"text": text, "tools": tools}


def _extract_json(text: str) -> dict:
    """Robustly extract JSON from LLM output — handles markdown fences and preamble."""
    text = re.sub(r"```(?:json)?\s*", "", text).strip().rstrip("`")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r'\{.*?\}', text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass
    raise ValueError(f"No valid JSON found in judge response: {text[:300]}")


def _judge(prompt: str, response: str, tools: list[str], expected_tools: list[str]) -> dict:
    if not judge_client:
        return {"helpfulness": 0, "accuracy": 0, "protocol_awareness": 0, "tone": 0,
                "reasoning": "CEREBRAS_API_KEY not set — skipping LLM judge"}
    user_content = (
        f"USER PROMPT: {prompt}\n\n"
        f"AGENT RESPONSE:\n{response}\n\n"
        f"TOOLS CALLED: {tools}\n"
        f"EXPECTED TOOLS: {expected_tools}"
    )
    try:
        resp = judge_client.chat.completions.create(
            model=CEREBRAS_MODEL,
            messages=[{"role": "system", "content": JUDGE_SYSTEM},
                      {"role": "user", "content": user_content}],
            max_tokens=400, temperature=0,
        )
        return _extract_json(resp.choices[0].message.content.strip())
    except Exception as e:
        return {"helpfulness": 0, "accuracy": 0, "protocol_awareness": 0, "tone": 0,
                "reasoning": f"Judge call failed: {e}"}


def _is_rate_limit(text: str) -> bool:
    t = (text or "").lower()
    return "429" in t or "rate limit" in t or "ratelimitreached" in t


if __name__ == "__main__":
    console.rule("[bold cyan]LLM Quality Evals[/bold cyan]")
    if not CEREBRAS_API_KEY:
        console.print("[red]ERROR: CEREBRAS_API_KEY not set — judge cannot run.[/red]")

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
        avg = round((judgment.get("helpfulness", 0) + judgment.get("accuracy", 0) +
                     judgment.get("protocol_awareness", 0) + judgment.get("tone", 0)) / 4, 2)
        is_error = avg == 0
        rate_limited = rate_limited or _is_rate_limit(judgment.get("reasoning", ""))
        scores.append({"prompt": tc["prompt"], **judgment, "avg": avg, "error": is_error})
        time.sleep(0.5)

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
        table.add_row(s["prompt"][:35], str(s.get("helpfulness", "?")), str(s.get("accuracy", "?")),
                      str(s.get("protocol_awareness", "?")), str(s.get("tone", "?")),
                      f"[{color}]{avg}[/{color}]", s.get("reasoning", "")[:40])
    console.print(table)

    failed = [s for s in scores if s.get("error")]
    graded = [s for s in scores if not s.get("error")]
    overall_avg = round(sum(s["avg"] for s in graded) / len(graded), 2) if graded else 0
    console.print(f"\nGraded {len(graded)}/{len(scores)} — overall average: [bold]{overall_avg}[/bold] / 5.0")
    if failed:
        console.print(f"[red]{len(failed)} case(s) could not be graded — FAILED.[/red]")
        if rate_limited:
            console.print("[yellow]Looks like a rate limit — wait and re-run.[/yellow]")

    print(f"RESULTS_JSON:{json.dumps({'scores': scores, 'overall_avg': overall_avg, 'failed': len(failed), 'graded': len(graded), 'rate_limited': rate_limited})}")
    sys.exit(1 if (failed or not scores) else 0)
