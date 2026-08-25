# browser-mcp

An MCP server that gives an AI agent real browser control: navigate, read
the page as an indexed list of interactive elements, click/type by index.
Personal tool, built as a BrowserAct-style browser layer that a custom
orchestrator (or any MCP client) can call — see the design notes in the
project's memory for the full context.

## Status

**Phase 1 (core loop) — done:**

- `browser_open(url)` / `navigate(url)` — real Chromium
- `get_state()` — scans the visible DOM, tags every interactive element
  with `data-bmcp-idx`, returns a compact indexed text block (same idea as
  BrowserAct's `state` command / Playwright's own accessibility snapshot)
- `click(index)` / `type_text(index, text, submit=False)` — act by index,
  no selectors
- `get_text(index?)`, `screenshot(path)`, `close_browser()`
- A Bedrock-model agent loop (`scripts/bedrock_agent.py`) proving the server
  works with a real LLM as the caller, not just the test client

**Phase 2 (stealth + proxies) — in progress:**

- Stealth: done. Runs on `patchright`, not plain Playwright — same API,
  but patches the CDP-level tells anti-bot scripts check first. Verified:
  vanilla Playwright reports `navigator.webdriver = True`; patchright
  reports `False`.
- Proxies: done and verified against a real vendor (Webshare free tier).
  `browser.py` reads `PROXY_SERVER` / `PROXY_USERNAME` / `PROXY_PASSWORD`
  from the environment and passes them straight to the browser launch. No
  vendor is baked in — point it at any provider's endpoint. Confirmed the
  target site sees the proxy's IP, not ours, through our own code (not just
  curl).

  Gotcha: Webshare's shared gateway hostname (`p.webshare.io`) rejects
  username/password auth with a 407 - each free-tier proxy has its own
  dedicated IP:port (Dashboard -> Free -> Proxy List), and only those work
  with direct username/password auth. Cost real debugging time; use the
  per-proxy endpoint, not the shared one.
- CAPTCHA solving: not started.
- Bandwidth reduction (`BLOCK_MEDIA`, `BLOCK_TRACKERS`): live, but measured
  savings are modest (8-11%) on modern sites - JS bundles dominate page
  weight, not images, and first-party-proxied trackers dodge static
  blocklists. Real bandwidth numbers should come from the proxy vendor's
  own usage dashboard, not from guessing locally.

**Multi-session isolation — done, verified:**

- Every tool now takes an optional `session` name (default `"default"`).
  Different names get fully separate Chromium processes - own cookies, own
  fingerprint, own lifecycle. Verified: a cookie set in one session doesn't
  appear in another; closing one session leaves others running; 3 sessions
  navigated concurrently via `asyncio.gather` with no artificial
  serialization in the code (wall-clock overhead you'll see is real cost of
  spawning multiple browser processes at once, not a bug).

**Human handoff (`request_human_help`) — done, local-machine version:**

- Agent calls it when stuck (2FA, a wall it can't pass); tool brings that
  session's tab to front and returns a message telling the caller to stop
  and wait for a human. This is the local case: browser + agent + human are
  all on one machine right now, so "handoff" is a screen you can already
  see, not a network problem. BrowserAct's full remote-assist (any-device
  live URL) is a separate, bigger piece for once this runs somewhere the
  human can't physically reach - deliberately not built yet, since that
  deployment doesn't exist.
- Verified end-to-end through `scripts/bedrock_agent.py`: real blocking
  `input()` pause, real resume on Enter, model correctly continued the
  original task afterward (navigated, reported the real page title) - but
  only after the tool result explicitly told it to continue instead of just
  narrating. Mistral Large needed that nudge; a stronger model might not.

**Skill Forge (`scripts/skill_forge.py`) — done, verified end-to-end:**

- `network_requests` tool (new): records every XHR/fetch response per
  session, JSON bodies included. This is the primitive Skill Forge needs -
  find the real API behind a page instead of scraping rendered HTML.
- `replay_request` tool (new): calls a URL via `fetch()` from inside the
  session's already-loaded page, not a bare HTTP request.
- Deliberately NOT one LLM call that explores and writes code together.
  Caught during development: asking Mistral Large to do both in one
  freeform prompt produced a fully hallucinated answer - no real tool call
  happened at all, and it invented a plausible-but-wrong endpoint from
  training data (`unsplash.com/napi/search/photos`) instead of using the
  browser. `skill_forge.py` splits it: our own code does the exploration
  deterministically (navigate + read network_requests, no LLM judgment
  call about whether to use a tool), the LLM only generates code from the
  real captured request handed to it as ground truth.
- Second catch: the LLM's first fix generated standalone `requests`-based
  Python code. Testing it for real against the live API returned 403 -
  Unsplash's endpoint rejects a bare HTTP call, even with a realistic
  Referer and User-Agent added. Confirmed it needs real session
  cookies/TLS fingerprint, which only an actual browser has. Fixed by
  changing what gets generated: a URL template (variable part replaced
  with a placeholder) meant to be called through the new `replay_request`
  tool, not standalone code. Verified for real: replayed the template
  through the actual MCP protocol with a brand-new query ("desert") never
  part of the original exploration - status 200, correct real data.

**Residential/ISP proxy + rotation — done, tested against Amazon specifically:**

- Bright Data ISP zone set up (5 IPs, Shared unlimited, $10/month - real
  charge, confirmed before spending it). Residential proper needs a
  business account + KYC through Bright Data's compliance team - that's
  the user's own identity verification, not something built around.
- `PROXY_ROTATE=1` env var: each named session gets a distinct IP for its
  lifetime (`-session-<random>` suffix, a Bright Data convention). Verified
  real: 3 different real locations (Virginia, LA, Chicago) across a few
  draws from the 5-IP pool. A 1-IP zone can't rotate at all - confirmed
  that too, before spending money to fix it.
- Honest result against Amazon specifically: single static IP succeeded
  ~1 in 5 attempts (mostly HTTP 503, Amazon's soft anti-bot signal); with
  rotation, 2 in 5. Real improvement, still far from reliable - Amazon is
  one of the hardest targets that exists, and BrowserAct's own "99.9%"
  claim implies a much bigger pool plus request pacing, not just "have a
  residential IP." Not a bug in this tool; a scale/budget question.

**Not built yet (later phases):**

- CAPTCHA solving (needs a 2Captcha/CapSolver account)
- Full any-device remote-assist (needs a real remote deployment first)

## Setup

```bash
uv sync --no-editable
.venv/bin/python -m patchright install chromium
```

Optional, for proxy use:

```bash
export PROXY_SERVER="http://host:port"
export PROXY_USERNAME="..."
export PROXY_PASSWORD="..."
```

`--no-editable` matters here: this uv/Python combo doesn't process the
editable-install `.pth` file correctly, so `uv run` alone can flip between
a working and a broken environment. Non-editable install copies the real
package into `.venv/site-packages`, which is stable. Re-run
`uv sync --no-editable` after any source change under `src/` - if uv logs
"Checked" instead of "Built" and your change doesn't show up, force it:
`uv sync --no-editable --reinstall-package browser-mcp`.

## Running it

As an MCP server (stdio transport) for any MCP client:

```bash
.venv/bin/browser-mcp
```

## Verifying it

```bash
.venv/bin/python scripts/smoke_test_client.py
```

Connects a real MCP client over stdio, lists registered tools, opens
example.com, clicks the first link by index, and prints the resulting
page state — a protocol-level check, not just an import check.
