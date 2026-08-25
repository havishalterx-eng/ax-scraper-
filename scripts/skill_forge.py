"""Skill Forge: explore a site once, generate reusable fetch code from what it actually found.

Split deliberately into two halves that don't trust each other's job:

1. Deterministic exploration (this script, no LLM) - navigate, wait, read
   network_requests. This is the part that must be correct, so it isn't
   left to a model's judgment about whether to call a tool.
2. LLM code generation, given the REAL captured request as ground truth in
   the prompt, told explicitly not to invent a different URL. This is the
   part that actually needs reasoning (turning a raw JSON response into a
   clean function) - the one thing worth spending model calls on.

Caught during development: asking a model (Mistral Large) to do steps 1
and 2 together in one freeform prompt produced a fully hallucinated
answer - no real tool call happened, and it invented a plausible-looking
but WRONG endpoint from training data instead of using the browser. This
script exists because that failure mode is real, not hypothetical.

Usage:
    .venv/bin/python scripts/skill_forge.py https://unsplash.com/s/photos/mountains
"""

import asyncio
import json
import sys

import boto3

from browser_mcp.browser import manager

MODEL_ID = "mistral.mistral-large-2402-v1:0"
REGION = "ap-south-1"

CODEGEN_PROMPT = """A browser captured this REAL network request while loading a webpage.
Use EXACTLY this URL and these exact query parameters - do not invent or
guess a different endpoint, even if you recognize this site from training
data and remember a different API shape. If the JSON body is truncated,
infer the rest of its structure only from what's actually shown.

Method: {method}
URL: {url}
Response body (truncated): {body}

IMPORTANT: a bare `requests.get()` to a URL like this commonly gets a 403
even with realistic headers - confirmed on a real site, likely a
cookie/session or TLS-fingerprint check a standalone HTTP call can't pass.
Do NOT write standalone Python `requests` code. Instead, output a URL
TEMPLATE with one placeholder for the variable part of the query (e.g.
replacing the real search phrase with {{QUERY}}), plus a one-line note
that it must be called via the browser-mcp `replay_request` tool (which
fetches from inside an already-loaded page, inheriting real cookies), not
via a standalone HTTP client.
"""


async def explore(url: str) -> list[dict]:
    session = await manager.get("skill-forge")
    page = await session.ensure_page()
    await page.goto(url, wait_until="load", timeout=25000)
    await asyncio.sleep(2)
    log = [e for e in session.network_log if e["body"]]
    await manager.close("skill-forge")
    return log


def generate_code(entry: dict) -> str:
    bedrock = boto3.client("bedrock-runtime", region_name=REGION)
    prompt = CODEGEN_PROMPT.format(
        method=entry["method"], url=entry["url"], body=entry["body"][:600]
    )
    resp = bedrock.converse(
        modelId=MODEL_ID, messages=[{"role": "user", "content": [{"text": prompt}]}]
    )
    return resp["output"]["message"]["content"][0]["text"]


async def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: skill_forge.py <url>")
        sys.exit(1)
    url = sys.argv[1]

    print(f"[forge] exploring {url} ...")
    log = await explore(url)
    if not log:
        print("[forge] no JSON XHR/fetch found - nothing to generate code from.")
        return

    print(f"[forge] found {len(log)} JSON API call(s).")
    # Prefer GET (read/search APIs) over POST (often ad/analytics beacons
    # in practice - caught this the hard way: a POST ad-decision endpoint
    # briefly won on raw body size alone). GET first, then largest body.
    gets = [e for e in log if e["method"] == "GET"]
    pool = gets if gets else log
    best = max(pool, key=lambda e: len(e["body"]))
    print(f"[forge] target: {best['method']} {best['url'][:100]}")

    print("[forge] generating code from the REAL captured request...\n")
    code = generate_code(best)
    print(code)


if __name__ == "__main__":
    asyncio.run(main())
