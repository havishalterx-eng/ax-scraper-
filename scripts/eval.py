#!/usr/bin/env python3
"""Eval harness for browser-mcp agent models.

Runs a fixed task set against one or more Bedrock models and prints a
comparison of records captured, cost, and wall-clock time. Raw results are
written to a JSON file next to this script.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

# Import the installed package, not the checkout, so monkey-patching reaches
# the same module _run_agent_job uses.
import browser_mcp.agents as agent_store
import browser_mcp.api as api

# The API runtime expects the SQLite schema to exist; initialize it before the
# first job is persisted.
agent_store.init_db()

# Prices read from the AWS Pricing API for ap-south-1 on 2026-08-28.
# Nova models need the "apac." inference-profile prefix to invoke on Bedrock;
# GLM does not. Dict keys omit the prefix so cost lookup works either way.
PRICES_PER_MILLION: dict[str, dict[str, float]] = {
    "zai.glm-4.7-flash": {"input": 0.08, "output": 0.48},
    "amazon.nova-pro-v1:0": {"input": 0.47, "output": 1.88},
    "amazon.nova-lite-v1:0": {"input": 0.036, "output": 0.284},
    "amazon.nova-micro-v1:0": {"input": 0.021, "output": 0.082},
    "qwen.qwen3-235b-a22b-2507-v1:0": {"input": 0.26, "output": 1.04},
}

TASKS: list[dict[str, Any]] = [
    {
        "name": "20-lead Maps",
        "prompt": (
            "Find beauty salons in Kondapur Hyderabad and give me 20 of them "
            "with which ones have no website."
        ),
        "expected_records": 20,
    },
    {
        "name": "40-product Amazon",
        "prompt": (
            "Go to https://www.amazon.in/s?k=wireless+headphones and collect 40 "
            "products with price, rating, review count and URL."
        ),
        "expected_records": 40,
    },
    {
        "name": "Hacker News newest",
        "prompt": "Go to https://news.ycombinator.com/newest and collect 15 titles with links.",
        "expected_records": 15,
    },
    # Was a Reddit listing task. Both routes are dead: old.reddit.com redirects
    # to a login wall, and www.reddit.com raises "Execution context was
    # destroyed" out of browser_open. Measuring a model against a site that
    # cannot answer measures nothing, so the slot now measures the thing that
    # failure taught us instead - whether a run gives up quickly on a wall.
    # Success here is a cheap, fast finish, not records: the original Reddit
    # failure cost 300 seconds of a model looping on a login page.
    {
        "name": "Login wall fast-fail",
        "prompt": "Go to https://old.reddit.com/r/programming/hot/ and collect 25 posts with title and URL.",
        "expected_records": 0,
    },
    {
        "name": "HN top story QA",
        "prompt": "Tell me the title of the top story on Hacker News right now.",
        "expected_records": 0,
    },
    # Deliberately hard case: LinkedIn listings require a signed-in session and
    # rate-limit aggressively, so this usually fails or returns far fewer than
    # asked. Included to surface model behaviour on blocked sites.
    {
        "name": "LinkedIn hard case",
        "prompt": (
            "Go to https://www.linkedin.com/jobs/search/?keywords=prompt%20engineer "
            "and collect 30 job listings with company name and location."
        ),
        "expected_records": 30,
    },
]

DEFAULT_MODELS = [
    "zai.glm-4.7-flash",
    "apac.amazon.nova-lite-v1:0",
]


class UsageTracker:
    """Wraps boto3's bedrock-runtime client and accumulates usage blocks."""

    def __init__(self) -> None:
        self.input_tokens = 0
        self.output_tokens = 0
        self._original_client_factory = api.boto3.client

    def install(self) -> None:
        api.boto3.client = self._patched_client  # type: ignore[method-assign]

    def uninstall(self) -> None:
        api.boto3.client = self._original_client_factory  # type: ignore[method-assign]

    def _patched_client(self, service_name: str, **kwargs: Any) -> Any:
        client = self._original_client_factory(service_name, **kwargs)
        if service_name != "bedrock-runtime":
            return client
        original_converse = client.converse

        def wrapped_converse(*args: Any, **kw: Any) -> Any:
            resp = original_converse(*args, **kw)
            usage = resp.get("usage") or {}
            self.input_tokens += usage.get("inputTokens", 0)
            self.output_tokens += usage.get("outputTokens", 0)
            return resp

        client.converse = wrapped_converse  # type: ignore[method-assign]
        return client


def price_key(model_id: str) -> str:
    return model_id.removeprefix("apac.")


def compute_cost(model_id: str, input_tokens: int, output_tokens: int) -> float:
    prices = PRICES_PER_MILLION[price_key(model_id)]
    return (
        input_tokens * prices["input"] + output_tokens * prices["output"]
    ) / 1_000_000


def safe_session_name(model_id: str, task_name: str) -> str:
    return f"eval-{uuid.uuid4().hex[:8]}"


async def run_task(model_id: str, task: dict[str, Any]) -> dict[str, Any]:
    """Run one task in its own browser session and return metrics."""
    session = safe_session_name(model_id, task["name"])
    tracker = UsageTracker()
    started_at = time.perf_counter()
    error: str | None = None
    status = "unknown"
    records: list[Any] = []
    records_source: str | None = None
    result_text = ""
    steps_used = 0

    try:
        api.MODEL_ID = model_id
        tracker.install()
        job_id, job = api._new_job(session, task["prompt"])
        await api._run_agent_job(job_id, session)
        status = job.get("status", "unknown")
        records = job.get("structured_result") or []
        records_source = job.get("records_source")
        result_text = job.get("result") or ""
        steps_used = job.get("steps_used", 0)
        if status != "done":
            error = job.get("error") or "finished with non-done status"
    except Exception as exc:  # noqa: BLE001
        status = "error"
        error = f"{type(exc).__name__}: {exc}"
    finally:
        tracker.uninstall()
        await api.manager.close(session)

    elapsed = time.perf_counter() - started_at
    expected = task["expected_records"]
    if expected == 0:
        success = len(records) == 0 and bool(result_text.strip())
    else:
        success = len(records) >= expected and status == "done"

    cost = compute_cost(model_id, tracker.input_tokens, tracker.output_tokens)

    return {
        "task": task["name"],
        "prompt": task["prompt"],
        "expected_records": expected,
        "status": status,
        "error": error,
        "records": len(records),
        "records_source": records_source,
        "steps_used": steps_used,
        "input_tokens": tracker.input_tokens,
        "output_tokens": tracker.output_tokens,
        "cost_usd": round(cost, 6),
        "wall_seconds": round(elapsed, 2),
        "success": success,
        "result_preview": result_text[:500],
    }


TASK_TIMEOUT_SECONDS = 300

async def run_model(model_id: str) -> dict[str, Any]:
    print(f"\n=== model: {model_id} ===", flush=True)
    results: list[dict[str, Any]] = []
    for task in TASKS:
        print(f"  task: {task['name']} ...", flush=True)
        try:
            row = await asyncio.wait_for(run_task(model_id, task), timeout=TASK_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            row = {
                "task": task["name"],
                "prompt": task["prompt"],
                "expected_records": task["expected_records"],
                "status": "error",
                "error": f"timed out after {TASK_TIMEOUT_SECONDS}s",
                "records": 0,
                "records_source": None,
                "steps_used": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cost_usd": 0.0,
                "wall_seconds": TASK_TIMEOUT_SECONDS,
                "success": False,
                "result_preview": "",
            }
        results.append(row)
        mark = "OK" if row["success"] else "FAIL"
        print(
            f"    {mark} records={row['records']} steps={row['steps_used']} "
            f"tokens={row['input_tokens']}/{row['output_tokens']} "
            f"cost=${row['cost_usd']:.6f} time={row['wall_seconds']}s",
            flush=True,
        )
        if row["error"]:
            print(f"    error: {row['error'][:200]}", flush=True)
    total_cost = sum(r["cost_usd"] for r in results)
    successes = sum(1 for r in results if r["success"])
    return {
        "model": model_id,
        "results": results,
        "total_cost_usd": round(total_cost, 6),
        "successes": successes,
        "tasks_run": len(results),
    }


def format_table(model_results: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    task_names = [t["name"] for t in TASKS]
    header = ["task".ljust(26)] + [r["model"][:18].ljust(20) for r in model_results]
    lines.append("  ".join(header))
    lines.append("-" * len(lines[0]))

    for name in task_names:
        row = [name.ljust(26)]
        for mr in model_results:
            tr = next((x for x in mr["results"] if x["task"] == name), {})
            cell = f"{tr.get('records', 0)} rec | ${tr.get('cost_usd', 0):.6f}"
            row.append(cell.ljust(20))
        lines.append("  ".join(row))

    lines.append("-" * len(lines[0]))
    total_row = ["TOTAL cost / successes".ljust(26)]
    for mr in model_results:
        total_row.append(f"${mr['total_cost_usd']:.6f} / {mr['successes']}/{mr['tasks_run']}".ljust(20))
    lines.append("  ".join(total_row))
    return "\n".join(lines)


async def main() -> int:
    parser = argparse.ArgumentParser(description="Run the browser-mcp eval harness.")
    parser.add_argument(
        "--models",
        default=",".join(DEFAULT_MODELS),
        help="Comma-separated Bedrock model IDs to evaluate.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Path for the JSON results file (default: timestamped file next to script).",
    )
    args = parser.parse_args()

    # Force headless so no Chrome windows open on the desktop.
    api.set_headless(True)

    model_ids = [m.strip() for m in args.models.split(",") if m.strip()]
    unsupported = [m for m in model_ids if price_key(m) not in PRICES_PER_MILLION]
    if unsupported:
        print(f"No price data for: {', '.join(unsupported)}", file=sys.stderr)
        return 1

    run_date = datetime.datetime.now(datetime.timezone.utc).isoformat()
    model_results: list[dict[str, Any]] = []
    for model_id in model_ids:
        model_results.append(await run_model(model_id))

    await api.manager.close_all()

    report = {
        "date": run_date,
        "models": model_ids,
        "price_table": PRICES_PER_MILLION,
        "model_results": model_results,
    }

    out_path = Path(args.output) if args.output else None
    if out_path is None:
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out_path = Path(__file__).parent / f"eval_results_{stamp}.json"
    out_path.write_text(json.dumps(report, indent=2, default=str))

    print(f"\n{format_table(model_results)}")
    print(f"\nRaw results written to: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
