"""Plain HTTP API over browser-mcp, for browser frontends that can't speak MCP.

MCP's stdio/SSE transports are built for LLM clients, not `fetch()` calls
from a webpage. This exposes the same tool functions - called in-process via
`mcp.list_tools()` / `mcp.call_tool()`, no subprocess, no MCP client needed -
plus a `/tasks` endpoint that runs the same tool-use loop as
scripts/bedrock_agent.py, adapted to run as a background job instead of a
blocking CLI script.

Three things here exist so a run is watchable rather than a black box:
`/sessions/{name}/screenshot` (what the browser sees right now),
`/sessions/{name}/live` (screenshot + url + title in one poll), and
`/tasks/{id}/message` (say something to the agent mid-run or after it
finishes, and have it actually act on it). A job keeps its full Bedrock
message history, so a finished run is a conversation you can pick back up,
not a dead record.

No auth. Deliberate for now - this runs locally/privately, not behind a
public page yet. Add auth before anything here is reachable from the public
ax-scraper-homepage.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import datetime
import json
import os
import re
import time
import uuid
from pathlib import Path

import boto3
import httpx
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.staticfiles import StaticFiles
from starlette.routing import Route

from . import agents as agent_store
from .auth import TokenAuthMiddleware, auth_enabled
from .browser import _headless_enabled, manager, set_headless
from . import templates as template_store
from .extract import take_extraction
from .server import mcp

# GLM 4.7 Flash over Nova Pro. Measured in ap-south-1 on the same two tasks,
# both models returning the same records: Nova Pro cost $0.0063 on a 40-product
# Amazon harvest and $0.0067 on a 20-lead Maps run; GLM 4.7 Flash cost $0.0016
# and $0.0011. That is list price ($0.08/$0.48 per M tokens against
# $0.47/$1.88) plus a smaller tokenizer - on an identical prompt Nova billed
# 410 input tokens where other models billed 139-180, and every step of the
# loop pays that again. Tool calling was verified against real Bedrock before
# switching. Override with BEDROCK_MODEL_ID if a site starts defeating it.
DEFAULT_MODEL_ID = "zai.glm-4.7-flash"
MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", DEFAULT_MODEL_ID)
REGION = os.environ.get("AWS_REGION", "ap-south-1")

# A real harvest is many steps: navigate, maybe search, scroll, extract, and
# retry when a page loads slowly. The old value of 15 was set before
# extract_records existed and killed a real 50-product run after two products
# - confirmed in a live run, not theorised. Even with bulk extraction, a
# multi-page harvest legitimately needs dozens of steps.
MAX_STEPS = int(os.environ.get("MAX_STEPS", "60"))

# Ceiling on a single model reply. Tool calls need a few dozen tokens and a
# four-sentence summary needs a few hundred, so this only ever bites when the
# model ignores the "do not retype records" rule - which it does
# inconsistently: the same Maps task produced 437 output tokens on one run and
# 2,400 on another, the difference being a reprint of records already captured
# server-side. Output is the expensive side of the bill, so the waste is
# capped rather than left to the model's mood.
MAX_REPLY_TOKENS = int(os.environ.get("MAX_REPLY_TOKENS", "2048"))

SYSTEM_PROMPT = (
    "You control a real web browser through tools. This run is pinned to one "
    "fixed browser session for its whole duration. "
    "The session may already have a page open from a previous step; if a "
    "tool result already shows a loaded page, work from that instead of "
    "assuming you need to open one.\n\n"
    "GOOGLE MAPS: for any Maps request - local businesses, lead lists, "
    "\"find me dentists in X\" - call `maps_leads` with the search query. It "
    "scrolls the results panel (Maps does not paginate) and opens each listing "
    "to read its website, phone and address. `extract_records` on Maps returns "
    "only the few results visible before scrolling and cannot see the website "
    "field at all, so it is the wrong tool there.\n\n"
    "HARVESTING DATA: when the task asks for a list of items (products, "
    "listings, businesses, posts, comments), navigate to the page that shows "
    "them and call `extract_records` ONCE with the total number you want as "
    "`limit`. It walks the site's pagination for you and de-duplicates across "
    "pages - you do NOT need to visit page 2, 3, 4 yourself. Never call "
    "get_text once per field; that burns the entire step budget on a handful "
    "of items.\n\n"
    "NEVER call the same tool with the same arguments twice in a row hoping "
    "for a different answer - it will return exactly the same thing. If a "
    "page gave you fewer records than you wanted, either call `scroll_page` "
    "first (for feeds that load on scroll, like maps or social sites) or "
    "accept the real count and say so. Reporting 9 real records is correct; "
    "looping until the run dies is not.\n\n"
    "NAVIGATING: state/browser_open return the page as indexed interactive "
    "elements like [3]<button> Sign In. Use that index with click/type_text - "
    "never guess an index that wasn't just shown to you. Indices change when "
    "the page changes, so re-read the returned state after every action.\n\n"
    "If you call request_human_help, the tool result means the human has "
    "ALREADY finished - your next reply must call the next real tool needed "
    "to keep going, not describe what you're about to do.\n\n"
    "HONESTY: only report values you actually saw in a tool result. If a "
    "field wasn't on the page, leave it out - never fill it with a plausible "
    "guess. If you could not complete the task, say exactly how far you got "
    "and why.\n\n"
    "Stop calling tools once the task is complete, then reply with plain "
    "text.\n\n"
    "DO NOT RETYPE RECORDS. When `extract_records` or `maps_leads` returned "
    "the data, it has already been captured and saved in full, and the user "
    "reads it from there - copying it into your reply adds nothing and is "
    "thrown away. Your reply must not contain a table, a numbered list of "
    "items, per-record lines, or a JSON block. Write at most four sentences: "
    "how many records were collected, from where, and anything genuinely "
    "notable (a shortfall against what was asked, a blocked page, a field "
    "the site did not expose). "
    "The one exception is records YOU assembled by hand from page text that "
    "no extraction tool returned - those exist nowhere else, so end with a "
    "fenced ```json array containing them."
)

# job_id -> full job record. Also mirrored into SQLite so history survives a
# restart; JOBS holds the live asyncio bits (task handle, events) that can't
# be serialised.
JOBS: dict[str, dict] = {}

# Models label the fence inconsistently - ```json, bare ```, or no fence at
# all. Keying only on ```json silently dropped a real 50-record harvest that
# had come back under a bare fence, so accept every shape.
_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\[.*\]|\{.*\})\s*```", re.DOTALL)
_BARE_JSON_RE = re.compile(r"(\[\s*\{.*\}\s*\])", re.DOTALL)


# A single tool result can be enormous - a search page's interactive-element
# dump, or 50 extracted records. Left uncapped they accumulate in the message
# history until the model rejects the whole request with "Input Tokens
# Exceeded", which killed a real 50-product run mid-way. Cap what goes into
# the conversation; the full text still reaches the activity log the user
# reads.
MAX_TOOL_RESULT_CHARS = int(os.environ.get("MAX_TOOL_RESULT_CHARS", "6000"))
# Rough chars-per-token; only used to decide when to start compacting.
COMPACT_THRESHOLD_CHARS = int(os.environ.get("COMPACT_THRESHOLD_CHARS", "90000"))
KEEP_FULL_RECENT = 6


def _tool_text(result) -> str:
    return "\n".join(getattr(b, "text", str(b)) for b in result.content)


def _cap(text: str, limit: int = MAX_TOOL_RESULT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return (
        text[:limit]
        + f"\n\n[... truncated {len(text) - limit} characters. If you needed more of "
        "this, narrow the request - e.g. extract_records with a smaller limit, "
        "or get_text on a specific index.]"
    )


def _messages_size(messages: list[dict]) -> int:
    return len(json.dumps(messages))


def _compact_messages(messages: list[dict]) -> bool:
    """Shrink old tool results in place when the conversation gets too big.

    Compacting in place rather than dropping messages is deliberate: Bedrock
    requires every toolUse to keep its matching toolResult, so removing old
    turns outright makes the request invalid. Replacing the *text* inside an
    old toolResult keeps the pairing intact while reclaiming most of the
    space. Recent turns are left alone - those are the ones the model is
    actually reasoning over.
    """
    if _messages_size(messages) < COMPACT_THRESHOLD_CHARS:
        return False
    cutoff = max(1, len(messages) - KEEP_FULL_RECENT)
    changed = False
    for msg in messages[1:cutoff]:
        for block in msg.get("content", []):
            tr = block.get("toolResult") if isinstance(block, dict) else None
            if not tr:
                continue
            for part in tr.get("content", []):
                text = part.get("text")
                if text and len(text) > 400:
                    part["text"] = (
                        text[:400] + "\n[... older tool result compacted to save context]"
                    )
                    changed = True
    return changed


def _extract_structured(text: str) -> tuple[str, str | None]:
    """Pulls a trailing ```json ...``` block out of the model's final reply.

    Real structured export (CSV/JSON download) needs actual structured data,
    not just whatever prose the model returned - this is what makes that
    honest instead of just wrapping free text in a .json file extension.
    Returns (prose_without_block, json_text_or_None); invalid JSON in the
    block is treated as absent rather than raised, since a malformed block
    from the model is not the caller's error to handle.
    """
    match = _JSON_BLOCK_RE.search(text) or _BARE_JSON_RE.search(text)
    if not match:
        # No closing bracket anywhere: a run cut off mid-array. Salvage from
        # the first `[{` rather than losing every record that did come back.
        start = text.find("[{")
        if start == -1:
            start = text.find("[\n")
        if start == -1:
            return text, None
        salvaged = _salvage_json_array(text[start:])
        if salvaged is None:
            return text, None
        return text[:start].strip(), json.dumps(salvaged)
    try:
        parsed = json.loads(match.group(1))
    except json.JSONDecodeError:
        # A run that hit the step budget mid-array leaves the JSON unclosed.
        # Salvaging the complete objects is far better than throwing away a
        # whole harvest because the tail was cut off.
        salvaged = _salvage_json_array(match.group(1))
        if salvaged is None:
            return text, None
        parsed = salvaged
    prose = (text[: match.start()] + text[match.end() :]).strip()
    return prose, json.dumps(parsed)


def _salvage_json_array(raw: str) -> list | None:
    """Recover the complete objects from a truncated JSON array."""
    if not raw.lstrip().startswith("["):
        return None
    objects: list = []
    depth = 0
    start = None
    in_string = False
    escape = False
    for i, ch in enumerate(raw):
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    objects.append(json.loads(raw[start : i + 1]))
                except json.JSONDecodeError:
                    pass
                start = None
    return objects or None


# Arguments the model never needs to see. `session` is forced onto every call
# so a job cannot wander onto another job's browser, and `persistent` comes
# from the agent's own settings - showing them costs schema tokens on every
# step and offers only a way to get them wrong.
#
# `selector` is hidden for a different reason, found by watching a real run:
# with the long docstring trimmed away, the model started guessing CSS
# selectors ("table span.titleline a") that matched nothing, turning a
# two-step Hacker News harvest into five steps of failed extractions. The
# auto-detection is what works and is what every template relies on, so the
# default is left to stand.
HIDDEN_ARGS = ("session", "persistent", "selector")


def _tool_summary(description: str) -> str:
    """The first paragraph of a tool's docstring.

    The full docstrings are written for a person reading the code and for MCP
    clients like Claude Desktop, and they repeat steering that SYSTEM_PROMPT
    already gives this loop - when to prefer `maps_leads`, that
    `extract_records` paginates by itself, not to re-use a stale element
    index. Paying for both on every step buys nothing, so Bedrock gets the
    summary and the system prompt keeps the steering.
    """
    return (description or "").strip().split("\n\n", 1)[0].strip()


async def _mcp_tools_to_bedrock() -> list[dict]:
    """Tool schemas as Bedrock wants them, trimmed to what the model decides.

    Measured: the untrimmed set was 2,571 tokens resent on every step, against
    a system prompt of ~640. Descriptions carry most of it, and the forced
    arguments carry the rest.
    """
    tools = await mcp.list_tools()
    specs = []
    for t in tools:
        schema = dict(t.input_schema or {"type": "object", "properties": {}})
        properties = {
            name: value
            for name, value in (schema.get("properties") or {}).items()
            if name not in HIDDEN_ARGS
        }
        schema["properties"] = properties
        required = [r for r in schema.get("required", []) if r not in HIDDEN_ARGS]
        if required:
            schema["required"] = required
        else:
            schema.pop("required", None)
        specs.append(
            {
                "toolSpec": {
                    "name": t.name,
                    "description": _tool_summary(t.description),
                    "inputSchema": {"json": schema},
                }
            }
        )
    return specs


def _log(job: dict, entry: dict) -> None:
    entry["at"] = time.time()
    job["log"].append(entry)


# How many times the same tool + arguments may return a byte-identical result
# before the loop is treated as stuck. A real lead-gen run called
# extract_records 18 times with identical output and burned all 60 steps, so
# this is a measured limit rather than a guessed one.
REPEAT_LIMIT = 3


def _loop_warning(job: dict, name: str, args: dict, text_out: str) -> str | None:
    """Detect a tool call repeating with no change, and tell the model to stop.

    Returning guidance as part of the tool result (rather than aborting the
    run) keeps the agent in control - it can change approach and still finish,
    which a hard abort would deny it.
    """
    signature = f"{name}|{json.dumps(args, sort_keys=True, default=str)}|{hash(text_out)}"
    counts = job.setdefault("repeat_counts", {})
    counts[signature] = counts.get(signature, 0) + 1
    if counts[signature] < REPEAT_LIMIT:
        return None
    return (
        f"\n\n[STOP REPEATING] You have now called {name} with the same arguments "
        f"{counts[signature]} times and received identical output every time. "
        "Calling it again will return the same thing and waste the run. This page "
        "has no more data to give under this approach. Do something different: "
        "scroll_page to load more, navigate somewhere new, or accept what you "
        "already have and give your final answer with the records collected so "
        "far. Do not call this tool with these arguments again."
    )


async def _deliver_webhook(webhook_url: str, payload: dict) -> None:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(webhook_url, json=payload)
    except Exception:  # noqa: BLE001 - a dead webhook endpoint isn't the run's failure
        pass


def _persist_job(job_id: str, job: dict) -> None:
    agent_store.save_job(
        job_id,
        job["session"],
        job["task"],
        job["status"],
        job.get("result"),
        json.dumps(job["structured_result"]) if job.get("structured_result") else None,
        job.get("error"),
        json.dumps(job["log"][-400:]),
        job["created_at"],
    )


async def _finish_job(
    job: dict,
    *,
    status: str,
    result: str | None,
    error: str | None,
    run_id: str | None,
    webhook_url: str | None,
) -> None:
    job["status"] = status
    structured = None
    if result:
        prose, structured = _extract_structured(result)
        job["result"] = prose
    else:
        job["result"] = result

    model_records = json.loads(structured) if structured else None

    # Prefer what the browser actually read over what the model retyped. A real
    # run had the tool extract 50 records while the model's own JSON block
    # carried only 2 - at any real volume the model is a lossy pipe, so its
    # transcription is only used when no genuine extraction happened (a
    # question-answering run, or records it assembled from page text by hand).
    captured = take_extraction(job["session"])
    captured_records = captured["records"] if captured else None
    if captured_records and (not model_records or len(captured_records) >= len(model_records)):
        job["structured_result"] = captured_records
        job["records_source"] = "extracted"
    else:
        job["structured_result"] = model_records
        job["records_source"] = "model" if model_records else None

    structured = json.dumps(job["structured_result"]) if job["structured_result"] else None
    job["error"] = error
    if result:
        _log(job, {"type": "final", "text": job["result"]})

    if run_id:
        await asyncio.to_thread(
            agent_store.finish_run, run_id, status, job["result"], structured, error
        )
    await asyncio.to_thread(_persist_job, job["id"], job)
    if webhook_url:
        asyncio.create_task(
            _deliver_webhook(
                webhook_url,
                {
                    "job_id": job.get("id"),
                    "status": status,
                    "result": job["result"],
                    "structured_result": job["structured_result"],
                    "error": error,
                },
            )
        )


async def _run_agent_job(
    job_id: str,
    session: str,
    *,
    persistent: bool = False,
    run_id: str | None = None,
    webhook_url: str | None = None,
) -> None:
    """Runs (or continues) the tool-use loop for a job.

    Reads the conversation from `job["messages"]` rather than starting fresh,
    which is what lets a finished job be picked back up when the user sends a
    follow-up - the agent keeps everything it already saw.
    """
    job = JOBS[job_id]
    bedrock = boto3.client("bedrock-runtime", region_name=REGION)
    tool_config = {"tools": await _mcp_tools_to_bedrock()}
    messages = job["messages"]

    try:
        for _step in range(1, MAX_STEPS + 1):
            # A message typed while the run is in flight gets injected here,
            # so "wait, also grab the seller name" actually reaches the agent
            # mid-run instead of being queued until it finishes.
            while job["pending_messages"]:
                text = job["pending_messages"].pop(0)
                messages.append({"role": "user", "content": [{"text": text}]})
                _log(job, {"type": "user", "text": text})

            if _compact_messages(messages):
                _log(job, {"type": "system", "text": "Compacted older context to stay within the model's input limit."})

            resp = await asyncio.to_thread(
                bedrock.converse,
                modelId=MODEL_ID,
                system=[{"text": SYSTEM_PROMPT}],
                messages=messages,
                toolConfig=tool_config,
                inferenceConfig={"maxTokens": MAX_REPLY_TOKENS},
            )
            out_message = resp["output"]["message"]
            messages.append(out_message)

            for block in out_message["content"]:
                if "text" in block and block["text"].strip():
                    _log(job, {"type": "model", "text": block["text"]})

            if resp["stopReason"] != "tool_use":
                final_text = "\n".join(b["text"] for b in out_message["content"] if "text" in b)
                # If the user typed something while the model was composing its
                # final answer, keep going instead of dropping their message.
                if job["pending_messages"]:
                    continue
                await _finish_job(
                    job,
                    status="done",
                    result=final_text,
                    error=None,
                    run_id=run_id,
                    webhook_url=webhook_url,
                )
                return

            tool_uses = [b["toolUse"] for b in out_message["content"] if "toolUse" in b]
            result_content = []
            for tu in tool_uses:
                name, args, use_id = tu["name"], tu.get("input", {}), tu["toolUseId"]
                # Force this job's session onto every tool call - a job targets one
                # session for its whole run, and the model has no reason to know
                # which session name this job was created against (it isn't told),
                # so leaving this to the model silently collapses every job onto
                # the tool's own "default" and defeats session isolation entirely.
                args = {**args, "session": session}
                if name == "browser_open":
                    # persistent is only a browser_open param - other tools'
                    # schemas don't declare it and would reject an extra field.
                    args["persistent"] = persistent
                _log(job, {"type": "tool_call", "name": name, "args": args})
                job["current_action"] = name
                try:
                    result = await mcp.call_tool(name, args)
                    text_out = _tool_text(result)
                    is_error = bool(result.is_error)
                except Exception as exc:  # noqa: BLE001
                    detail = str(exc.__cause__) if exc.__cause__ else str(exc)
                    text_out = f"Tool call failed: {detail}"
                    is_error = True
                warning = _loop_warning(job, name, args, text_out)
                if warning:
                    text_out += warning
                    _log(job, {"type": "system", "text": f"Loop detected on {name} - told the agent to change approach."})
                _log(
                    job,
                    {
                        "type": "tool_result",
                        "name": name,
                        "text": text_out[:4000],
                        "is_error": is_error,
                    },
                )

                if name == "request_human_help":
                    job["status"] = "needs_human"
                    job["human_reason"] = args.get("reason", "")
                    resume_event = asyncio.Event()
                    job["resume_event"] = resume_event
                    await asyncio.to_thread(_persist_job, job_id, job)
                    await resume_event.wait()
                    job["status"] = "running"
                    text_out += (
                        "\n\nThe human has just confirmed this step is complete. "
                        "Call the next real tool right now to continue the original "
                        "task - do not just describe what you plan to do."
                    )

                result_content.append(
                    {"toolResult": {"toolUseId": use_id, "content": [{"text": _cap(text_out)}]}}
                )
            messages.append({"role": "user", "content": result_content})
            job["steps_used"] = job.get("steps_used", 0) + 1
            await asyncio.to_thread(_persist_job, job_id, job)

        await _finish_job(
            job,
            status="error",
            result=None,
            error=(
                f"Stopped after {MAX_STEPS} steps without finishing. "
                "The task may need to be narrower, or the agent got stuck in a loop - "
                "check the activity log to see where."
            ),
            run_id=run_id,
            webhook_url=webhook_url,
        )
    except asyncio.CancelledError:
        job["status"] = "cancelled"
        job["error"] = "Stopped by user."
        await asyncio.to_thread(_persist_job, job_id, job)
        raise
    except Exception as exc:  # noqa: BLE001 - surface any real failure to the caller
        await _finish_job(
            job, status="error", result=None, error=str(exc), run_id=run_id, webhook_url=webhook_url
        )


async def _run_direct_plan(
    job_id: str,
    session: str,
    plan: list[dict],
    *,
    persistent: bool = False,
    run_id: str | None = None,
    webhook_url: str | None = None,
) -> None:
    """Executes a known tool sequence with no model in the loop.

    A filled-in template already states exactly which calls it wants. Handing
    that prompt to a model so it can decide to make those same calls costs
    real money for a decision with one possible answer: a measured 20-lead
    Maps run burned 9,188 input and 1,249 output tokens to arrive at the
    single `maps_leads` call its template had already named. This path runs
    them directly, so a template run costs nothing but the browser.

    Logging, session forcing, record capture and the finished-job shape are
    identical to the model path - the console and the API cannot tell the
    difference, which is the point: this is the same run, minus the guessing.
    If any step fails the job fails honestly rather than falling back to the
    model, because a broken selector or a dead URL is not something a model
    would have fixed either.
    """
    job = JOBS[job_id]
    job["mode"] = "direct"
    _log(job, {"type": "system", "text": f"Running {len(plan)} known step(s) directly - no model needed."})
    try:
        last_text = ""
        for step in plan:
            name = step["tool"]
            args = {**step["args"], "session": session}
            if name == "browser_open":
                args["persistent"] = persistent
            _log(job, {"type": "tool_call", "name": name, "args": args})
            job["current_action"] = name
            result = await mcp.call_tool(name, args)
            text_out = _tool_text(result)
            if result.is_error:
                _log(job, {"type": "tool_result", "name": name, "text": text_out[:4000], "is_error": True})
                await _finish_job(
                    job,
                    status="error",
                    result=None,
                    error=f"{name} failed: {text_out}",
                    run_id=run_id,
                    webhook_url=webhook_url,
                )
                return
            _log(job, {"type": "tool_result", "name": name, "text": text_out[:4000], "is_error": False})
            job["steps_used"] = job.get("steps_used", 0) + 1
            last_text = text_out
            await asyncio.to_thread(_persist_job, job_id, job)

        # The last step's own output is the answer. `_finish_job` prefers the
        # records the browser really captured over anything in this text, so
        # a plan gets the same verified records the model path gets.
        await _finish_job(
            job,
            status="done",
            result=last_text,
            error=None,
            run_id=run_id,
            webhook_url=webhook_url,
        )
    except asyncio.CancelledError:
        job["status"] = "cancelled"
        job["error"] = "Stopped by user."
        await asyncio.to_thread(_persist_job, job_id, job)
        raise
    except Exception as exc:  # noqa: BLE001
        await _finish_job(
            job, status="error", result=None, error=str(exc), run_id=run_id, webhook_url=webhook_url
        )


def _new_job(session: str, task: str) -> tuple[str, dict]:
    job_id = uuid.uuid4().hex[:12]
    job = {
        "id": job_id,
        "status": "running",
        "session": session,
        "task": task,
        "log": [],
        "messages": [{"role": "user", "content": [{"text": task}]}],
        "pending_messages": [],
        "result": None,
        "structured_result": None,
        "error": None,
        "human_reason": None,
        "resume_event": None,
        "task_ref": None,
        "current_action": None,
        "steps_used": 0,
        "mode": "model",
        "created_at": time.time(),
    }
    JOBS[job_id] = job
    _log(job, {"type": "user", "text": task})
    return job_id, job


def _job_public(job: dict) -> dict:
    return {
        "job_id": job["id"],
        "status": job["status"],
        "task": job["task"],
        "session": job["session"],
        "log": job["log"],
        "result": job["result"],
        "structured_result": job["structured_result"],
        "error": job["error"],
        "human_reason": job["human_reason"],
        "current_action": job.get("current_action"),
        "records_source": job.get("records_source"),
        # "direct" means this run cost no model tokens at all.
        "mode": job.get("mode", "model"),
        "steps_used": job.get("steps_used", 0),
        "max_steps": MAX_STEPS,
        "created_at": job["created_at"],
    }


async def create_task(request: Request) -> JSONResponse:
    body = await request.json()
    task = (body.get("task") or "").strip()
    if not task:
        return JSONResponse({"error": "task is required"}, status_code=400)
    session = body.get("session") or "default"
    persistent = bool(body.get("persistent", False))

    job_id, job = _new_job(session, task)
    job["task_ref"] = asyncio.create_task(
        _run_agent_job(job_id, session, persistent=persistent)
    )
    return JSONResponse({"job_id": job_id, "status": "running"})


async def get_task(request: Request) -> JSONResponse:
    job_id = request.path_params["job_id"]
    job = JOBS.get(job_id)
    if job is None:
        stored = await asyncio.to_thread(agent_store.get_job, job_id)
        if stored is None:
            return JSONResponse({"error": "no such job"}, status_code=404)
        return JSONResponse(stored)
    return JSONResponse(_job_public(job))


async def list_tasks(request: Request) -> JSONResponse:
    """Live jobs first, then anything else recovered from disk."""
    live = [_job_public(j) for j in JOBS.values()]
    live_ids = {j["job_id"] for j in live}
    stored = await asyncio.to_thread(agent_store.list_jobs, 50)
    merged = live + [s for s in stored if s["job_id"] not in live_ids]
    merged.sort(key=lambda j: j.get("created_at") or 0, reverse=True)
    return JSONResponse({"tasks": merged[:50]})


async def message_task(request: Request) -> JSONResponse:
    """Say something to the agent - mid-run or after it finished.

    Running: queued and injected at the next step. Finished: the loop restarts
    from the existing conversation, so the agent still remembers the page it
    was on and everything it already extracted.
    """
    job_id = request.path_params["job_id"]
    job = JOBS.get(job_id)
    if job is None:
        return JSONResponse({"error": "no such job (or it predates a restart)"}, status_code=404)
    body = await request.json()
    text = (body.get("text") or "").strip()
    if not text:
        return JSONResponse({"error": "text is required"}, status_code=400)

    job["pending_messages"].append(text)

    if job["status"] in ("done", "error", "cancelled"):
        job["status"] = "running"
        job["error"] = None
        job["result"] = None
        job["task_ref"] = asyncio.create_task(_run_agent_job(job_id, job["session"]))
        return JSONResponse({"status": "running", "resumed": True})

    _log(job, {"type": "user_queued", "text": text})
    return JSONResponse({"status": job["status"], "queued": True})


async def resume_task(request: Request) -> JSONResponse:
    job = JOBS.get(request.path_params["job_id"])
    if job is None:
        return JSONResponse({"error": "no such job"}, status_code=404)
    if job["status"] != "needs_human" or job["resume_event"] is None:
        return JSONResponse({"error": "job is not waiting on a human"}, status_code=409)
    job["resume_event"].set()
    return JSONResponse({"status": "resumed"})


async def cancel_task(request: Request) -> JSONResponse:
    job = JOBS.get(request.path_params["job_id"])
    if job is None:
        return JSONResponse({"error": "no such job"}, status_code=404)
    if job["status"] not in ("running", "needs_human"):
        return JSONResponse({"error": f"job is already {job['status']}"}, status_code=409)
    if job["resume_event"] is not None and not job["resume_event"].is_set():
        job["resume_event"].set()  # unblock a human-help wait so cancel() lands
    if job["task_ref"] is not None:
        job["task_ref"].cancel()
    job["status"] = "cancelled"
    job["error"] = "Stopped by user."
    await asyncio.to_thread(_persist_job, job["id"], job)
    return JSONResponse({"status": "cancelled"})


async def list_sessions(request: Request) -> JSONResponse:
    names = manager.list_sessions()
    sessions = []
    for name in names:
        session = await manager.get(name)
        sessions.append(
            {"name": name, "url": session.current_url, "persistent": session.persistent}
        )
    return JSONResponse({"sessions": sessions})


async def session_screenshot(request: Request) -> Response:
    """Raw PNG of what this session's browser is looking at right now."""
    name = request.path_params["name"]
    if name not in manager.list_sessions():
        return JSONResponse({"error": "no such session"}, status_code=404)
    try:
        session = await manager.get(name)
        page = await session.ensure_page()
        png = await page.screenshot(type="png")
    except Exception as exc:  # noqa: BLE001 - mid-navigation screenshots can fail
        return JSONResponse({"error": str(exc)}, status_code=503)
    return Response(png, media_type="image/png", headers={"Cache-Control": "no-store"})


async def session_live(request: Request) -> JSONResponse:
    """Screenshot + url + title in one poll, for a live view panel.

    One request instead of three keeps the view coherent - a separate
    screenshot and URL fetch can straddle a navigation and show one page's
    picture next to another page's address.
    """
    name = request.path_params["name"]
    if name not in manager.list_sessions():
        return JSONResponse({"error": "no such session"}, status_code=404)
    try:
        session = await manager.get(name)
        page = await session.ensure_page()
        png = await page.screenshot(type="png")
        title = await page.title()
        return JSONResponse(
            {
                "url": page.url,
                "title": title,
                "screenshot": "data:image/png;base64," + base64.b64encode(png).decode(),
            }
        )
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=503)


async def call_tool_route(request: Request) -> JSONResponse:
    """Direct low-level tool call - what the dashboard uses for
    open/click/type/state/network/close instead of the agent loop."""
    name = request.path_params["name"]
    try:
        args = await request.json()
    except Exception:
        args = {}
    try:
        result = await mcp.call_tool(name, args)
    except Exception as exc:  # noqa: BLE001
        detail = str(exc.__cause__) if exc.__cause__ else str(exc)
        return JSONResponse({"error": detail}, status_code=400)
    return JSONResponse({"text": _tool_text(result), "is_error": result.is_error})


async def list_tools_route(request: Request) -> JSONResponse:
    tools = await mcp.list_tools()
    return JSONResponse(
        {
            "tools": [
                {"name": t.name, "description": (t.description or "").strip()} for t in tools
            ]
        }
    )


async def _trigger_agent_run(agent: dict) -> dict:
    """Starts one run of an agent's current version.

    A version saved from a filled-in template carries the tool calls it means,
    so it runs them directly - no model, no tokens. A hand-written prompt, or
    a template prompt the user has since edited, has no plan and goes through
    the model exactly as before.
    """
    version = agent["versions"][0]
    session = agent["session"]
    job_id, job = _new_job(session, version["prompt"])
    run_id = await asyncio.to_thread(agent_store.start_run, agent["id"], version["id"], job_id)
    plan = json.loads(version["plan"]) if version.get("plan") else None
    runner = (
        _run_direct_plan(
            job_id,
            session,
            plan,
            persistent=bool(agent["persistent"]),
            run_id=run_id,
            webhook_url=agent["webhook_url"],
        )
        if plan
        else _run_agent_job(
            job_id,
            session,
            persistent=bool(agent["persistent"]),
            run_id=run_id,
            webhook_url=agent["webhook_url"],
        )
    )
    job["task_ref"] = asyncio.create_task(runner)
    return {"job_id": job_id, "run_id": run_id}


async def create_agent(request: Request) -> JSONResponse:
    body = await request.json()
    name = (body.get("name") or "").strip()
    prompt = (body.get("prompt") or "").strip()
    if not name or not prompt:
        return JSONResponse({"error": "name and prompt are required"}, status_code=400)
    browser_mode = body.get("browser_mode") or "Standard Browser"
    agent = await asyncio.to_thread(agent_store.create_agent, name, prompt, browser_mode)
    if body.get("persistent") is not None or body.get("webhook_url") is not None:
        await asyncio.to_thread(
            agent_store.update_agent_settings,
            agent["id"],
            webhook_url=body.get("webhook_url"),
            persistent=body.get("persistent"),
        )
        agent = await asyncio.to_thread(agent_store.get_agent, agent["id"])
    return JSONResponse(agent, status_code=201)


async def list_agents_route(request: Request) -> JSONResponse:
    agents = await asyncio.to_thread(agent_store.list_agents)
    return JSONResponse({"agents": agents})


async def get_agent_route(request: Request) -> JSONResponse:
    agent = await asyncio.to_thread(agent_store.get_agent, request.path_params["agent_id"])
    if agent is None:
        return JSONResponse({"error": "no such agent"}, status_code=404)
    runs = await asyncio.to_thread(agent_store.list_runs, agent["id"])
    return JSONResponse({**agent, "runs": runs})


async def update_agent_route(request: Request) -> JSONResponse:
    agent_id = request.path_params["agent_id"]
    agent = await asyncio.to_thread(agent_store.get_agent, agent_id)
    if agent is None:
        return JSONResponse({"error": "no such agent"}, status_code=404)
    body = await request.json()
    new_prompt = (body.get("prompt") or "").strip()
    if new_prompt and new_prompt != agent["versions"][0]["prompt"]:
        await asyncio.to_thread(
            agent_store.add_version, agent_id, new_prompt, body.get("note", "Updated prompt")
        )
    if "webhook_url" in body or "persistent" in body:
        await asyncio.to_thread(
            agent_store.update_agent_settings,
            agent_id,
            webhook_url=body.get("webhook_url"),
            persistent=body.get("persistent"),
        )
    agent = await asyncio.to_thread(agent_store.get_agent, agent_id)
    return JSONResponse(agent)


async def delete_agent_route(request: Request) -> JSONResponse:
    ok = await asyncio.to_thread(agent_store.delete_agent, request.path_params["agent_id"])
    if not ok:
        return JSONResponse({"error": "no such agent"}, status_code=404)
    return JSONResponse({"status": "deleted"})


async def run_agent_route(request: Request) -> JSONResponse:
    agent = await asyncio.to_thread(agent_store.get_agent, request.path_params["agent_id"])
    if agent is None:
        return JSONResponse({"error": "no such agent"}, status_code=404)
    if not agent["versions"]:
        return JSONResponse({"error": "agent has no version to run"}, status_code=400)
    return JSONResponse(await _trigger_agent_run(agent))


async def set_schedule_route(request: Request) -> JSONResponse:
    agent_id = request.path_params["agent_id"]
    agent = await asyncio.to_thread(agent_store.get_agent, agent_id)
    if agent is None:
        return JSONResponse({"error": "no such agent"}, status_code=404)
    body = await request.json()
    kind = body.get("kind")
    if kind not in ("daily", "weekly", "monthly"):
        return JSONResponse({"error": "kind must be daily, weekly, or monthly"}, status_code=400)
    await asyncio.to_thread(
        agent_store.set_schedule,
        agent_id,
        kind,
        int(body.get("hour", 9)),
        int(body.get("minute", 0)),
        body.get("weekday"),
        body.get("day_of_month"),
    )
    return JSONResponse({"status": "scheduled"})


async def toggle_schedule_route(request: Request) -> JSONResponse:
    agent_id = request.path_params["agent_id"]
    body = await request.json()
    await asyncio.to_thread(
        agent_store.set_schedule_active, agent_id, bool(body.get("active", True))
    )
    return JSONResponse({"status": "ok"})


async def _scheduler_loop() -> None:
    """Checked once a minute - triggers a real agent run when a schedule is
    due. `last_run_at` prevents double-firing within the same minute; a
    missed minute (process was down) is not backfilled, it just waits for
    the next due time."""
    while True:
        await asyncio.sleep(60)
        try:
            now = datetime.datetime.now(datetime.timezone.utc)
            schedules = await asyncio.to_thread(agent_store.active_schedules)
            for sched in schedules:
                if now.hour != sched["hour"] or now.minute != sched["minute"]:
                    continue
                if (
                    sched["kind"] == "weekly"
                    and sched["weekday"] is not None
                    and now.weekday() != sched["weekday"]
                ):
                    continue
                if (
                    sched["kind"] == "monthly"
                    and sched["day_of_month"] is not None
                    and now.day != sched["day_of_month"]
                ):
                    continue
                last_run = sched["last_run_at"]
                if last_run and now.timestamp() - last_run < 55:
                    continue
                agent = await asyncio.to_thread(agent_store.get_agent, sched["agent_id"])
                if agent is None or not agent["versions"]:
                    continue
                await _trigger_agent_run(agent)
                await asyncio.to_thread(
                    agent_store.mark_schedule_ran, sched["agent_id"], now.timestamp()
                )
        except Exception:  # noqa: BLE001 - one bad tick must not kill the scheduler
            pass


async def health(request: Request) -> JSONResponse:
    return JSONResponse(
        {
            "status": "ok",
            "model": MODEL_ID,
            "region": REGION,
            "max_steps": MAX_STEPS,
            "headless": _headless_enabled(),
            "auth_required": auth_enabled(),
            "live_sessions": len(manager.list_sessions()),
        }
    )


async def list_templates_route(request: Request) -> JSONResponse:
    return JSONResponse(
        {
            "categories": ["All", *template_store.CATEGORIES],
            "templates": template_store.list_templates(
                request.query_params.get("category"),
                request.query_params.get("search"),
            ),
        }
    )


async def use_template(request: Request) -> JSONResponse:
    """Turn a template plus the caller's inputs into a real run, or a real agent.

    `save=true` creates a persisted agent (so it can be versioned and
    scheduled); otherwise it starts a one-off run.

    A filled-in template usually names its tool calls outright, and those run
    directly with no model involved - the same run, minus paying a model to
    re-derive a decision that has one answer. Templates whose path genuinely
    varies (Google, which can answer with a consent wall or a CAPTCHA) have no
    plan and keep the model. `model=true` in the body forces the model path
    for a template that has a plan, which is what you want when a site has
    changed shape and the fixed calls no longer fit it.
    """
    template_id = request.path_params["template_id"]
    body = await request.json() if await request.body() else {}
    values = body.get("values") or {}
    prompt = template_store.render_prompt(template_id, values)
    if prompt is None:
        return JSONResponse({"error": "no such template"}, status_code=404)
    template = template_store.get_template(template_id)
    plan = None if body.get("model") else template_store.render_plan(template_id, values)

    if body.get("save"):
        name = (body.get("name") or template["name"]).strip()
        agent = await asyncio.to_thread(
            agent_store.create_agent, name, prompt, "Standard Browser", plan
        )
        result = await _trigger_agent_run(agent)
        return JSONResponse({**result, "agent_id": agent["id"], "prompt": prompt})

    session = body.get("session") or f"tpl-{uuid.uuid4().hex[:6]}"
    job_id, job = _new_job(session, prompt)
    runner = (
        _run_direct_plan(job_id, session, plan) if plan else _run_agent_job(job_id, session)
    )
    job["task_ref"] = asyncio.create_task(runner)
    return JSONResponse(
        {
            "job_id": job_id,
            "status": "running",
            "prompt": prompt,
            "mode": "direct" if plan else "model",
        }
    )



async def infrastructure_route(request: Request) -> JSONResponse:
    """What the runtime is actually configured with, read from the environment.

    Reports the real state rather than a designed-looking panel: if no proxy is
    set the answer is "direct connection", not a greyed-out control implying
    one is a click away. Credentials are never echoed - only whether they are
    present.
    """
    proxy_server = os.environ.get("PROXY_SERVER")
    return JSONResponse(
        {
            "proxy": {
                "configured": bool(proxy_server),
                "server": proxy_server or None,
                "has_credentials": bool(os.environ.get("PROXY_USERNAME")),
                "rotating": os.environ.get("PROXY_ROTATE") == "1",
                "note": (
                    "Rotating a distinct IP per session."
                    if os.environ.get("PROXY_ROTATE") == "1"
                    else "Every session shares one exit IP."
                )
                if proxy_server
                else "No proxy set. Traffic goes out from this machine's own IP.",
                "country_targeting": False,
            },
            "stealth": {
                "engine": "patchright",
                "block_media": os.environ.get("BLOCK_MEDIA", "0") not in ("0", "false", "False"),
                "block_trackers": os.environ.get("BLOCK_TRACKERS", "1") not in ("0", "false", "False"),
            },
            "captcha": {"configured": False, "note": "No CAPTCHA solver wired in."},
            "delivery": {
                "webhook": True,
                "csv_json": True,
                "integrations": [],
            },
        }
    )

async def set_config(request: Request) -> JSONResponse:
    """Flip headless mode without a restart.

    Only affects browsers launched after this call - an already-running
    Chromium cannot change between windowed and windowless. The caller is told
    how many sessions are still on the old mode so the UI can offer to close
    them rather than leaving the setting looking like it did nothing.
    """
    body = await request.json()
    if "headless" in body:
        set_headless(bool(body["headless"]))
    open_sessions = manager.list_sessions()
    return JSONResponse(
        {
            "headless": _headless_enabled(),
            "sessions_on_old_mode": len(open_sessions),
            "note": (
                "Applies to new browsers. Close existing sessions for it to take effect there."
                if open_sessions
                else "Applies to every new run."
            ),
        }
    )


# The built console, served by this same process when it exists.
#
# One process serving both the API and the UI is what makes this reachable
# from a phone as a single URL, and it removes CORS from the picture entirely
# since the page and its API share an origin. Absent in a dev checkout (the
# console runs on Vite then), so its absence is normal, not an error.
def _console_dist() -> Path | None:
    """Locate the built console.

    Deliberately not derived from `__file__`: this package is installed
    non-editable, so `__file__` lives in site-packages and walking up from it
    lands inside the virtualenv rather than the checkout. Resolve from the
    working directory instead, with an env override for deployments that put
    the bundle elsewhere.
    """
    override = os.environ.get("CONSOLE_DIST")
    candidates = [Path(override)] if override else []
    candidates += [Path.cwd() / "console" / "dist", Path.cwd() / "dist"]
    for candidate in candidates:
        if candidate.is_dir() and (candidate / "index.html").is_file():
            return candidate
    return None


CONSOLE_DIST = _console_dist()


class ConsoleFiles(StaticFiles):
    """Serves the console SPA, falling back to index.html for unknown paths.

    A single-page app owns its own routing, so a deep link must return the
    app shell rather than a 404 - but only for paths the API itself does not
    claim, which Starlette guarantees by matching this mount last.
    """

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        if response.status_code == 404:
            return await super().get_response("index.html", scope)
        return response


@contextlib.asynccontextmanager
async def _lifespan(app: Starlette):
    await asyncio.to_thread(agent_store.init_db)
    scheduler = asyncio.create_task(_scheduler_loop())
    yield
    scheduler.cancel()


app = Starlette(
    routes=[
        Route("/health", health),
        Route("/config", set_config, methods=["POST"]),
        Route("/infrastructure", infrastructure_route),
        Route("/templates", list_templates_route),
        Route("/templates/{template_id}/use", use_template, methods=["POST"]),
        Route("/tools", list_tools_route),
        Route("/tools/{name}", call_tool_route, methods=["POST"]),
        Route("/sessions", list_sessions),
        Route("/sessions/{name}/screenshot", session_screenshot),
        Route("/sessions/{name}/live", session_live),
        Route("/tasks", create_task, methods=["POST"]),
        Route("/tasks", list_tasks, methods=["GET"]),
        Route("/tasks/{job_id}", get_task),
        Route("/tasks/{job_id}/message", message_task, methods=["POST"]),
        Route("/tasks/{job_id}/resume", resume_task, methods=["POST"]),
        Route("/tasks/{job_id}/cancel", cancel_task, methods=["POST"]),
        Route("/agents", create_agent, methods=["POST"]),
        Route("/agents", list_agents_route, methods=["GET"]),
        Route("/agents/{agent_id}", get_agent_route, methods=["GET"]),
        Route("/agents/{agent_id}", update_agent_route, methods=["PATCH"]),
        Route("/agents/{agent_id}", delete_agent_route, methods=["DELETE"]),
        Route("/agents/{agent_id}/run", run_agent_route, methods=["POST"]),
        Route("/agents/{agent_id}/schedule", set_schedule_route, methods=["PUT"]),
        Route("/agents/{agent_id}/schedule", toggle_schedule_route, methods=["PATCH"]),
    ],
    middleware=[
        Middleware(
            CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
        ),
        Middleware(TokenAuthMiddleware),
    ],
    lifespan=_lifespan,
)

if CONSOLE_DIST is not None:
    app.mount("/", ConsoleFiles(directory=str(CONSOLE_DIST), html=True), name="console")


def main() -> None:
    import uvicorn

    uvicorn.run(
        app,
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("API_PORT", "8787")),
    )


if __name__ == "__main__":
    main()
