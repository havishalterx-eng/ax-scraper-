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

**Network transport — done, verified real:** the server ran only over stdio
(local subprocess) until now, which meant it could never be reached from
anywhere but the machine running it - a real blocker for any cloud host.
`MCP_TRANSPORT=streamable-http` switches it to a real network server
(`HOST`/`PORT` configurable, defaults `0.0.0.0:8000`); unset, it's still
plain stdio, so the existing local Claude Code registration is untouched.
Verified for real: started the server over HTTP as a background process,
connected a separate MCP client over the network, listed all 11 tools, and
ran a real `browser_open` through it - not just "the process starts."

No auth wired in yet - fine for now since nothing is deployed anywhere
network-reachable, but a real requirement before actually exposing this on
a cloud host.

**Cloud deployment - live.** Running on AWS Lightsail (not Fly.io - Fly's
account got locked behind a payment-method gate, Lightsail already had
billing set up on this AWS account, zero new signup friction). Private-only:
firewall has only SSH open, port 8000 is not publicly reachable - confirmed
via a direct connection attempt from outside (timeout, not even a refusal).
Real access is through an SSH tunnel (`ssh -L 8940:localhost:8000 ...`),
verified end to end with a real MCP client and a real `browser_open` call.

Redeploying: the box holds a real `git clone` of this repo (public, no
auth needed), so updates are `./deploy.sh` - pulls the new commit, rebuilds,
restarts the container. Replaces what used to be a manual tar/scp/build
cycle.

**Phase 3 (HTTP API + real backend for the frontends) — done, verified live:**

- `src/browser_mcp/api.py` - a plain Starlette/uvicorn HTTP API (`browser-mcp-api`
  entrypoint, default port 8787) over the same tools, called in-process via
  `mcp.call_tool()` - no MCP client, no subprocess. Exists because MCP's own
  transports are for LLM/agent clients, not a browser's `fetch()`. No auth -
  deliberate, this is local/private, not behind a public page.
- `/tasks` runs the same tool-use loop as `bedrock_agent.py` as a background
  job instead of a blocking CLI script; `needs_human` status + `/resume`
  replaces the old blocking `input()`. `/tasks/{id}/cancel` really cancels
  the underlying asyncio task, not just marks a flag - verified: cancelled
  mid-run, log stopped growing immediately.
- **Saved, versioned, reusable agents are real now** (`agents.py`, SQLite at
  `data/browser-mcp.db`, gitignored) - `/agents` create/list/get/update/
  delete, `/agents/{id}/run` triggers a real run against the agent's own
  dedicated session, `/agents/{id}` returns real version history and real
  run history. Verified: created an agent, ran it twice, versioned its
  prompt, confirmed both runs and both versions persisted - including
  across a full process restart (SQLite, not memory).
- **Scheduling is real** - `PUT /agents/{id}/schedule` (daily/weekly/monthly),
  a background loop checked every 60s. Verified live: scheduled a run 2
  minutes out, walked away, came back to a real new run in that agent's
  history with no manual trigger.
- **Fresh vs signed-in sessions are real** - `browser_open(..., persistent=True)`
  now uses `launch_persistent_context` with an on-disk profile per session
  name (`data/profiles/<name>`) instead of a throwaway context. Verified
  across two separate Python processes: wrote to `localStorage` in process
  A, closed it, a fresh process B reopening the same session name read it
  back. A `persistent=False` session confirmed NOT to survive, same test.
- **Webhook delivery is real** - an agent with `webhook_url` set gets a real
  POST with its result on every completed run. Verified with a throwaway
  local listener actually receiving the payload.
- **Structured export is real, not just prose wrapped in a file extension** -
  the system prompt asks the model to end with a fenced ```json block when
  a task asks for records; the API parses it into `structured_result`
  separate from the prose. When the model actually returns one, real
  CSV/JSON export works from real data. When it doesn't, the UI says so
  instead of showing anything invented.
- **Model swapped: Mistral Large → Amazon Nova Pro** (`apac.amazon.nova-pro-v1:0`).
  Mistral frequently narrated a tool call as prose instead of actually
  invoking it, especially on longer/vaguer tasks - confirmed by direct
  comparison, same kind of task run twice. Re-checked Bedrock access before
  switching: Anthropic models are still hard-blocked on this account
  (`INVALID_PAYMENT_INSTRUMENT`, re-confirmed live against Claude 3.5
  Sonnet) - a real AWS Marketplace billing issue, not something fixable
  from code. Nova Pro isn't gated by that (Amazon's own model, separate
  from the Anthropic Marketplace subscription) and is dramatically more
  reliable at actually calling tools - verified running the full real
  loop end to end: opened Amazon, searched, extracted 5 real products by
  index, returned real structured JSON. Its own real weak spot, seen in
  that same test: it can fabricate a plausible-looking value for a field
  it never actually extracted (invented a `price` field when it had only
  pulled title+rating) rather than honestly omitting it - so structured
  output still wants a skeptical read, not blind trust. Switch to Claude
  with `BEDROCK_MODEL_ID=apac.anthropic.claude-3-7-sonnet-20250219-v1:0`
  (or newer) once the Marketplace billing is fixed - the loop doesn't
  care which model it's talking to.
- **Known real limit on region routing**: the region selector some UIs show
  has no backend behind it. The one proxy zone actually set up (Bright Data
  ISP, Phase 2) is 5 fixed US IPs with no per-country targeting - that needs
  the Residential product plus KYC, explicitly not set up. Selecting a
  country in a UI does not change the real exit IP's country; don't wire it
  to look like it does.

**Phase 4 (watchable runs + bulk extraction) — done, verified live:**

- **`extract_records` - the change that made this a scraper instead of a demo.**
  A real run of "top 50 wireless headphones with 5 fields each" died at
  `stopped after 15 steps` having collected *two* products, because the only
  way to read data was `get_text(index)` once per field - ~250 calls. The new
  tool finds the page's repeated record structure itself (sibling elements
  sharing a structural signature) and returns every item with its fields
  already parsed, in ONE call. Same Amazon page: 16 records, one call, two
  steps. Field parsing is pattern-based (currency shape, "N out of 5" shape,
  longest anchor as title), not site-specific, so it degrades to text+links
  on an unfamiliar page rather than breaking.
  - Two real parser bugs found and fixed by checking output against the live
    page rather than trusting it: review counts were echoing the price (sites
    split the currency symbol into its own span, leaving a bare "899" that
    looks identical to a review count - now excluded by value and by price
    container), and `seller` was matching the word "by" inside ordinary
    product titles and inventing sellers that were never on the page (now
    requires an explicit "sold by"/"seller:" label).
- **`scroll_page`** for lazy-loaded lists. When a page doesn't grow it says so
  explicitly and tells the agent to paginate instead - added after watching a
  real run scroll an exhausted page three times in a row.
- **Context-overflow handling.** A real run died with `Input Tokens Exceeded`:
  every tool result accumulates in the conversation, and a search page's
  element dump is enormous. Three fixes: tool results are capped before
  entering the conversation (full text still reaches the activity log), old
  tool results are compacted *in place* when the conversation grows past a
  threshold (in place, not dropped - Bedrock requires every toolUse to keep
  its matching toolResult), and the state dump caps at 120 elements with a
  note saying so.
- **`MAX_STEPS` raised 15 -> 60** (env-configurable). 15 predated bulk
  extraction and was not enough for any real multi-page harvest.
- **Fail-fast element handling.** A model guessing `index=0` (indices start at
  1) used to hang for Playwright's full 30s default timeout and then return a
  wall of locator internals. Now an invalid index answers instantly with the
  valid range; click/type failures explain the likely cause (overlay,
  off-screen, not a text field) instead of raising.
- **Page-settle retry.** `domcontentloaded` fires before a script-rendered
  page paints anything clickable, so scanning right after navigation found
  zero elements on amazon.in and sent the agent into a human-help detour.
  One short settle-and-retry removed that failure mode.
- **Live view**: `/sessions/{name}/live` returns screenshot + url + title in a
  single response (one call, so the picture and the address can't straddle a
  navigation). `BLOCK_MEDIA` now defaults OFF - blocking images saved a
  measured 8-11% and made every live frame render with holes in it.
- **Talk to a running agent**: `POST /tasks/{id}/message`. Mid-run it's
  injected at the next step; after a run finishes the loop restarts from the
  existing conversation, so the agent still remembers the page and everything
  it extracted. Verified: asked a finished run "which of these has the best
  rating and what is its price" and got a correct answer read off its own
  real harvest.
- **Jobs persist to SQLite** (`/tasks` list, `/tasks/{id}` falls back to disk),
  so run history survives a restart. Verified in the console UI, which labels
  recovered runs "from disk".
- **Structured-output parsing hardened**: accepts ```json fences, bare ```
  fences, and unfenced arrays, and salvages the complete objects from an
  array truncated by the step budget. A real 50-record harvest was being
  thrown away because the model used a bare fence.

**Phase 5 (pagination + honest data path) — done, verified live:**

- **`extract_records` follows pagination itself.** Asking for 50 items from a
  site that shows 16 per page used to return 16 (or 6, when the model gave up
  early). It now detects the page parameter in the URL (`page=N` number-style
  and `start=N`/`offset=N` record-style, which must be advanced differently -
  guessing wrong silently reruns page 1 forever), walks the pages,
  de-duplicates across them, and stops when it has enough or the site stops
  producing new rows. Verified: 4 real Amazon pages, 64 unique records.
- **The model no longer carries the data.** A run where the tool correctly
  extracted 50 records ended with **2** in the final answer - Nova simply will
  not retype 50 rows x 6 fields into a JSON block. Extractions are now kept
  server-side and attached to the job directly; the model only summarises.
  Jobs report `records_source`: `extracted` (read from the page) or `model`
  (assembled by the model, worth spot-checking), surfaced as a badge in the
  console. This is the difference between a downloadable dataset and a
  paraphrase of one.
- **Loop breaker.** A real lead-gen run called `extract_records` 18 times -
  the last five byte-identical - and burned all 60 steps. Three identical
  results for the same tool+args now append an explicit instruction to change
  approach or finish with what it has.
- **`scroll_page` scrolls inner panels too.** Google Maps renders results in
  its own fixed-height scrolling div, so scrolling the window did nothing and
  no further results ever loaded - the direct cause of that looping run. It
  now finds the largest scrollable element and scrolls that as well.
- **Manually closing a browser window now closes the session.** Playwright
  keeps its browser process alive when the window's own X is used, so the
  session became a zombie: no window, 7-9 Chromium processes still resident,
  still listed in the UI, and the next run silently reopened inside it.
  A page-close handler reaps the session. Verified: 9 processes -> 0, session
  gone from the list, other sessions unaffected.

**Phase 6 (templates + full console IA) — done, verified live:**

- **Templates are real** (`templates.py`, `/templates`, `/templates/{id}/use`).
  12 starting points whose prompts are shaped around what the tools actually
  do well - land on a results URL, name the fields, state a count. A blank
  prompt box was the hardest part of using this: the difference between a run
  that finishes in 3 steps and one that loops until the budget dies is almost
  entirely phrasing. Each has typed inputs (search term, location, count) and
  can either run once or be saved as a versioned, schedulable agent. Verified
  end to end through the UI: Hacker News template returned 30 real records in
  1 step.
- **`/infrastructure`** reports the runtime's real configuration - proxy,
  stealth, CAPTCHA, delivery - read from the environment, with credentials
  never echoed (only whether they're present).
- **`list_agents` now returns `run_count`, `last_run_at`, `schedule_active`**
  so the agents list can filter and sort without one request per card.
- **Console rebuilt around a grouped sidebar** (Home / Templates / Agents,
  then Runtime, then Infrastructure) with the headless toggle in the footer.
  Home is a dashboard: hero composer, live template row, real activity stats.
  Agents is a card grid with search + filters and inline run/delete. Proxies
  and Delivery state plainly what is and isn't configured, naming the exact
  env var that would change it, rather than showing a disabled control that
  implies a feature is one click away.
- Layout and information architecture follow common SaaS conventions
  (sidebar + card grid + template gallery); the visual identity is AlterX's
  own - black, off-white, `#FF4D0A` - with our own copy throughout.

**AX Scraper Console (`../ax-scraper-app`) — the product UI, done:**

A real operating console, not a marketing page: live browser view that polls
the running session (fast while active, slow when idle), colour-coded
activity feed with expandable entries, results table with real CSV/JSON
export, chat box for talking to the agent, saved-agent management with
versions/schedules/run history, session list, and run history including
restored-from-disk runs. Every control is wired to a real endpoint - verified
end to end by driving the UI itself, not just the API.

**Not built yet (later phases):**

- CAPTCHA solving - deliberately deferred to the production-ready phase,
  see project memory for the full vendor shortlist already researched
- Full any-device remote-assist (needs a real remote deployment first)
- Auth on the network transport and on the HTTP API (needed before any
  public/cloud exposure of either)
- Real per-country proxy routing (needs a Residential proxy product + KYC,
  or per-country vendor accounts - not signed up for)
- Delivery integrations beyond a plain webhook (Make/n8n/Zapier/email/cloud
  storage) - each needs its own vendor credentials/OAuth app, none set up

## CAPTCHA solving

`request_human_help` covers a CAPTCHA the honest way: pause the run, wait for
a person. That only works if someone is actually watching, and a real run
showed the failure mode when nobody is - it fired, nobody cleared it, and the
model kept going alone for 13 more steps until it hallucinated its way into a
dead end.

`solve_captcha` is the second option, for when nobody's watching. It detects
a reCAPTCHA v2, hCaptcha or Cloudflare Turnstile widget on the current page,
pays 2Captcha a fraction of a cent to solve it, and writes the solved token
into the page. It is off unless `TWOCAPTCHA_API_KEY` is set - without it, or
if a solve fails, the tool says so and the model is told to fall back to
`request_human_help` rather than retry. This is deliberately the only paid,
per-request step anywhere in this tool; everything else costs Bedrock tokens
or nothing, which is most of why AX Scraper is cheaper to run than a credit-
metered competitor - so it only activates on the pages that genuinely need
it, never speculatively.

Two things worth knowing if you touch this code:

- reCAPTCHA v3 and enterprise variants are not handled. They score a session
  rather than present a checkbox, so there is nothing to detect and no field
  to fill.
- Firing a widget's registered `data-callback` cannot be done through
  `page.evaluate()`. patchright runs `evaluate` in an isolated JS world on
  purpose - keeping automation invisible to a page that inspects its own
  `window` for CDP tells - and a callback registered on the page's real
  `window` is invisible from that isolated world, in both directions: it
  cannot be called from there, and its result cannot be read back from there
  either. Proven directly: a synthetic widget's callback wrote its result
  into the DOM and that DOM write was visible, while the same callback
  writing to a `window` property was not, from either world. The token field
  write happens fine through `evaluate` because the DOM is shared regardless
  of world; the callback fires through `page.add_script_tag(...)` instead,
  which runs as a real script in the page's own world.

2Captcha's cost by widget type is a comment in `src/browser_mcp/captcha.py`,
sourced from their published pricing page at the time this was written, not
from a live pricing endpoint - they do not expose one. Treat it as an
order-of-magnitude log line, not a billing record; check 2captcha.com for
what your account was actually charged.

## Model choice

The default model is `zai.glm-4.7-flash` in `ap-south-1`. Override it for a single run with `BEDROCK_MODEL_ID=<model-id>`, or set it in the environment before starting the API. The loop itself is model-agnostic.

We switched from Nova Pro after measuring the same two tasks end to end. A 40-product Amazon harvest cost $0.0063 on Nova Pro and $0.0011 on GLM; a 20-lead Maps run cost $0.0067 on Nova Pro and $0.0007 on GLM. Both returned the same records. The gap comes from list price and tokenisation: GLM is priced at $0.08/$0.48 per million input/output tokens against Nova Pro's $0.47/$1.88, and on an identical prompt Nova billed 410 input tokens where GLM billed 180. Every step of the loop pays that difference again.

Prompt caching was part of the comparison. GLM on Bedrock rejects `cachePoint` with `invoked an unsupported model or your request did not allow prompt caching`, so caching is unavailable with the current default. Nova models accept it. Caching only discounts input, and Nova Pro with perfect caching still costs several times more than GLM with no caching, so the switch held on price.

What did replace caching was a structural cut in the per-step prompt. Trimming tool descriptions and hiding arguments the model never needs to see brought the fixed per-step cost from 3,480 tokens down to 2,095. That saving applies to every model and depends on no vendor feature.

Template runs use no model at all: a filled-in template renders a fixed plan of tool calls, and the API executes those directly. Editing an agent's prompt drops its plan and sends the new prompt to the model on every run. The console warns before that happens.

The open question is Nova Lite. It is cheaper than GLM on list price ($0.036/$0.284 per million) and supports Bedrock prompt caching, so it could win on both counts if its quality holds. The eval harness in `scripts/eval.py` exists to settle that. Its first GLM-vs-Nova-Lite comparison (see commit `f68d4eb`) gave GLM 5/6 successes at $0.004334 and Nova Lite 3/6 successes at $0.001728. Reddit blocked both; Nova Lite also collected one record on the pure question-answering task and 29 instead of 30 on the LinkedIn hard case. Re-run the harness as models and prices change.

Governance note: GLM is a third-party vendor model hosted on Bedrock. That is fine for first-party lead generation, but if this becomes a product running customer data the model choice should be revisited deliberately rather than inherited.

IAM note: the production user `ax-scraper-bedrock` is scoped to `bedrock:InvokeModel` on Nova models and on `zai.glm-4.7-flash` only. Changing the model in production requires updating that policy first; otherwise every run fails with `AccessDeniedException`.

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

As an MCP server (stdio transport, default) for any local MCP client:

```bash
.venv/bin/browser-mcp
```

Over the network (streamable-http), for a remote host:

```bash
MCP_TRANSPORT=streamable-http PORT=8000 .venv/bin/browser-mcp
```

## Verifying it

```bash
.venv/bin/python scripts/smoke_test_client.py
```

Connects a real MCP client over stdio, lists registered tools, opens
example.com, clicks the first link by index, and prints the resulting
page state — a protocol-level check, not just an import check.
