from __future__ import annotations

import os

from mcp.server import MCPServer

from .browser import manager
from .maps import COLLECT_PLACES_JS, PLACE_DETAIL_JS, SCROLL_FEED_JS, format_leads
from .verify import verify_websites
from .extract import (
    EXTRACT_JS,
    looks_like_wall,
    dedupe_records,
    format_records,
    next_page_url,
    remember_extraction,
)
from .state import STATE_JS, format_state

mcp = MCPServer("browser-mcp")

DEFAULT_SESSION = "default"


def _normalize_url(url: str) -> str:
    """Playwright's page.goto() requires a scheme - a bare "example.com"
    fails outright instead of resolving like a browser address bar would.
    Agents (and humans) routinely omit it, so default to https:// here
    rather than surfacing that as a tool error every time.
    """
    if "://" not in url:
        return f"https://{url}"
    return url


async def _current_state(session: str) -> str:
    browser_session = await manager.get(session)
    page = await browser_session.ensure_page()
    data = await page.evaluate(STATE_JS)
    # `domcontentloaded` fires before a script-rendered page has painted
    # anything clickable, so a scan right after navigation can legitimately
    # find zero elements on a site that is full of them - seen for real on
    # amazon.in, where it sent the agent down a human-help detour. One short
    # settle-and-retry costs a second and removes that whole failure mode.
    if not data.get("items"):
        try:
            await page.wait_for_load_state("networkidle", timeout=4000)
        except Exception:  # noqa: BLE001 - busy pages may never go idle; retry anyway
            await page.wait_for_timeout(1200)
        data = await page.evaluate(STATE_JS)
    return format_state(data)


async def _get_page(session: str, persistent: bool = False):
    browser_session = await manager.get(session, persistent=persistent)
    return await browser_session.ensure_page()


@mcp.tool()
async def browser_open(url: str, session: str = DEFAULT_SESSION, persistent: bool = False) -> str:
    """Open a browser and navigate to a URL.

    `session` names an isolated browser - its own cookies, its own
    fingerprint. Different session names never share state, so parallel
    tasks or separate accounts stay isolated. Omit it for the single
    default session. Returns the page's indexed list of interactive
    elements, e.g. [3]<button> Sign In. Use the index with click/type_text.

    `persistent=True` saves this session's cookies/login state to disk under
    this session name, so closing and reopening the same session name later
    resumes signed in - only meaningful the first time a session name is
    created; ignored on a session that already exists (already-open browser
    keeps whatever mode it started in). `persistent=False` (default) is a
    throwaway session - nothing survives closing it.
    """
    page = await _get_page(session, persistent=persistent)
    await page.goto(_normalize_url(url), wait_until="domcontentloaded")
    return await _current_state(session)


@mcp.tool()
async def navigate(url: str, session: str = DEFAULT_SESSION) -> str:
    """Navigate a session's page to a new URL. Returns the updated state."""
    page = await _get_page(session)
    await page.goto(_normalize_url(url), wait_until="domcontentloaded")
    return await _current_state(session)


@mcp.tool()
async def get_state(session: str = DEFAULT_SESSION) -> str:
    """Return a session's current indexed list of interactive elements."""
    return await _current_state(session)


ELEMENT_TIMEOUT_MS = 8000


async def _resolve_index(page, index: int):
    """Return a locator for an indexed element, or a useful error string.

    Playwright's default is to wait 30s for a selector that will never appear,
    so a model that guesses an index (index=0 is a real, observed guess -
    indices start at 1) stalls the whole run for half a minute and then
    reports a wall of locator internals. Checking up front turns that into an
    instant, actionable answer that tells the model what it can actually
    click.
    """
    count = await page.locator(f'[data-bmcp-idx="{index}"]').count()
    if count > 0:
        return page.locator(f'[data-bmcp-idx="{index}"]').first, None
    total = await page.locator("[data-bmcp-idx]").count()
    if total == 0:
        return None, (
            f"No element has index {index} - this page has not been scanned yet. "
            "Call get_state first to index the page, then use an index it shows."
        )
    return None, (
        f"No element has index {index}. Valid indices on this page are 1-{total}. "
        "Indices start at 1, not 0. Call get_state to see the current list - "
        "indices change whenever the page changes."
    )


@mcp.tool()
async def click(index: int, session: str = DEFAULT_SESSION) -> str:
    """Click the element at the given index (from get_state/browser_open).

    Returns the updated state after the click.
    """
    page = await _get_page(session)
    locator, error = await _resolve_index(page, index)
    if error:
        return error
    try:
        await locator.click(timeout=ELEMENT_TIMEOUT_MS)
    except Exception as exc:  # noqa: BLE001
        return (
            f"Could not click index {index}: {type(exc).__name__}. The element may be "
            "covered by an overlay/cookie banner, or off-screen. Call get_state to "
            "re-read the page, or scroll_page first."
        )
    await page.wait_for_load_state("domcontentloaded")
    return await _current_state(session)


@mcp.tool()
async def type_text(index: int, text: str, submit: bool = False, session: str = DEFAULT_SESSION) -> str:
    """Type text into the element at the given index.

    If submit is true, presses Enter afterward. Returns the updated state.
    """
    page = await _get_page(session)
    locator, error = await _resolve_index(page, index)
    if error:
        return error
    try:
        await locator.fill(text, timeout=ELEMENT_TIMEOUT_MS)
    except Exception as exc:  # noqa: BLE001
        return (
            f"Could not type into index {index}: {type(exc).__name__}. That element may "
            "not be a text field. Call get_state and pick an <input> or <textarea>."
        )
    if submit:
        await locator.press("Enter")
        await page.wait_for_load_state("domcontentloaded")
    return await _current_state(session)


@mcp.tool()
async def extract_records(
    session: str = DEFAULT_SESSION,
    limit: int = 50,
    selector: str = "",
    max_pages: int = 5,
) -> str:
    """Extract records from a listing page in ONE call, following pagination automatically.

    This is the right tool whenever the page shows a repeated set of similar
    items - search results, a product grid, a business listing, a comment
    feed. It finds the repeating structure itself and pulls the common fields
    out of each item (title, url, price, list_price, rating, reviews, image,
    availability, seller).

    PAGINATION IS AUTOMATIC. Most sites show only ~16-24 items per page, so
    asking for 50 means walking several pages. This tool does that walk for
    you: it keeps following the next page until it has `limit` records or runs
    out of pages, and de-duplicates across them. Do NOT navigate page by page
    yourself and call this repeatedly - one call with the limit you want is
    the whole job.

    ALWAYS prefer this over calling get_text once per field.

    `limit` is how many records you want in total across all pages.
    `max_pages` caps how many pages it will walk (default 5).
    `selector` is optional - pass a CSS selector only if the auto-detected
    group is the wrong one, otherwise leave it empty.
    """
    page = await _get_page(session)
    all_records: list[dict] = []
    pages_walked = 0
    notes: list[str] = []
    first_payload: dict | None = None

    for page_index in range(1, max(1, min(max_pages, 10)) + 1):
        data = await page.evaluate(
            EXTRACT_JS, {"limit": limit, "selector": selector or None}
        )
        if first_payload is None:
            first_payload = data
        batch = data.get("records", [])
        pages_walked = page_index
        before = len(all_records)
        all_records = dedupe_records(all_records + batch)
        gained = len(all_records) - before

        if len(all_records) >= limit:
            break
        if not batch:
            if page_index == 1:
                body_text = await page.inner_text("body")
                if looks_like_wall(page.url, body_text[:2000]):
                    return (
                        f"url={page.url}\n\n"
                        "This page is a sign-in or security wall, not a listing page. "
                        "The site requires a logged-in session or human verification before it will show results. "
                        "Repeating extraction, scrolling, or navigating elsewhere on this page will not help. "
                        "Use a signed-in session (browser_open with persistent=True) or call request_human_help."
                    )
            notes.append(f"Page {page_index} had no records; stopped.")
            break
        # A site that ignores an out-of-range page number re-serves the page
        # you were already on. Without this the walk keeps "succeeding" while
        # collecting nothing new.
        if page_index > 1 and gained == 0:
            notes.append(f"Page {page_index} added no new records; stopped (end of results).")
            break
        if page_index == max_pages:
            notes.append(f"Reached max_pages={max_pages}.")
            break

        target = next_page_url(page.url, page_index, len(batch))
        if not target:
            notes.append("This page has no page-number parameter to follow; stopped after one page.")
            break
        try:
            await page.goto(target, wait_until="domcontentloaded")
            await page.wait_for_timeout(1200)
        except Exception as exc:  # noqa: BLE001
            notes.append(f"Could not load page {page_index + 1}: {type(exc).__name__}. Stopped.")
            break

    final_records = all_records[:limit]
    payload = dict(first_payload or {})
    payload["records"] = final_records
    payload["totalInGroup"] = len(all_records)

    # Keep the real rows server-side. The caller downloads these; the model
    # never has to retype them (and demonstrably won't, at this volume).
    remember_extraction(session, final_records, page.url)

    header = format_records(payload, limit)
    summary = f"\n\nWalked {pages_walked} page(s); {len(all_records)} unique records after de-duplication."
    if notes:
        summary += " " + " ".join(notes)
    if len(final_records) < limit:
        summary += (
            f" This is fewer than the {limit} requested - that is all the site returned. "
            "Report the real count; do not invent extra rows."
        )
    summary += (
        f"\n\nAll {len(final_records)} records have been saved automatically and are already "
        "available to the user for download. You do NOT need to repeat them in your final "
        "answer - just summarise what you found (how many, from where, anything notable). "
        "Do not retype the rows."
    )
    return header + summary


@mcp.tool()
async def maps_leads(
    query: str,
    session: str = DEFAULT_SESSION,
    limit: int = 20,
    visit_details: bool = True,
    verify_sites: bool = True,
) -> str:
    """Collect Google Maps business listings, including whether each has a website.

    Use this for ANY Google Maps request - local businesses, lead lists,
    "find me dentists in X". Do not use browser_open + extract_records on
    Maps; that returns only the handful of results visible before scrolling
    and cannot see the website field at all.

    `query` is what you would type into Maps, e.g. "dental clinics in
    Hyderabad". `limit` is how many listings to collect. `visit_details=True`
    (the default) opens each listing to read its website, phone, address and
    rating - this is the only way to know whether a business has a website,
    which is the field that matters for prospecting. Set it False for a fast
    name-and-link-only pass.

    Results come back grouped: businesses with NO website first, since those
    are usually the point of the search.

    Visiting details costs roughly a second per listing, so a limit of 20 takes
    around half a minute. Ask for what you need rather than a large round
    number.
    """
    page = await _get_page(session)
    limit = max(1, min(limit, 120))
    notes: list[str] = []

    search_url = "https://www.google.com/maps/search/" + query.strip().replace(" ", "+")
    await page.goto(search_url, wait_until="domcontentloaded")
    await page.wait_for_timeout(4500)

    scroll_info = await page.evaluate(SCROLL_FEED_JS, limit)
    if not scroll_info.get("hadFeed"):
        return (
            "Google Maps did not render its results panel. It may have shown a consent "
            "or CAPTCHA page instead - call request_human_help so a person can clear it, "
            "then try again."
        )

    places = await page.evaluate(COLLECT_PLACES_JS)
    if not places:
        return "Maps loaded but produced no listings for that query. Try a broader search term."
    if len(places) < limit:
        notes.append(
            f"Maps only returned {len(places)} listings for this query - that is all it has. "
            "Search a broader area or a nearby locality for more."
        )
    places = places[:limit]

    if not visit_details:
        rows = [{"name": p["name"], "url": p["url"]} for p in places]
        notes.append("Details not visited, so website presence is unknown for these.")
        return format_leads(rows, notes)

    rows: list[dict] = []
    failed = 0
    for place in places:
        try:
            await page.goto(place["url"], wait_until="domcontentloaded")
            await page.wait_for_timeout(2200)
            detail = await page.evaluate(PLACE_DETAIL_JS)
        except Exception:  # noqa: BLE001 - one bad listing must not lose the rest
            failed += 1
            continue
        detail["name"] = detail.get("name") or place["name"]
        detail["maps_url"] = place["url"]
        rows.append(detail)

    if failed:
        notes.append(f"{failed} listing(s) could not be opened and were skipped.")

    if verify_sites and rows:
        # Maps' website field is not trustworthy on its own: it happily shows a
        # social page, a directory listing, or a domain that no longer loads.
        # Fetching each one is what turns a raw list into a qualified one.
        rows = await verify_websites(rows)

    remember_extraction(session, rows, search_url)
    return format_leads(rows, notes)


@mcp.tool()
async def scroll_page(session: str = DEFAULT_SESSION, times: int = 1) -> str:
    """Scroll down to load more of a page, then report how much content grew.

    Listing pages commonly lazy-load: the first extract_records call sees only
    what has rendered. Scroll, then extract again to pick up the rest.
    """
    page = await _get_page(session)
    # Scroll the page AND the biggest inner scrollable panel. Apps like Google
    # Maps put their results in a fixed-height scrolling div, so scrolling the
    # window does nothing at all and more results never load - which is
    # exactly how a real run burned its whole step budget re-extracting the
    # same nine rows.
    scroll_js = """
    () => {
      window.scrollBy(0, window.innerHeight * 0.9);
      let best = null;
      let bestArea = 0;
      document.querySelectorAll('div,main,section,ul').forEach((el) => {
        const style = getComputedStyle(el);
        if (!/(auto|scroll)/.test(style.overflowY)) return;
        if (el.scrollHeight <= el.clientHeight + 40) return;
        const rect = el.getBoundingClientRect();
        const area = rect.width * rect.height;
        if (area > bestArea) { bestArea = area; best = el; }
      });
      if (best) best.scrollTop = best.scrollTop + best.clientHeight * 0.9;
      return best ? best.scrollHeight : 0;
    }
    """
    measure_js = """
    () => {
      let inner = 0;
      document.querySelectorAll('div,main,section,ul').forEach((el) => {
        const style = getComputedStyle(el);
        if (!/(auto|scroll)/.test(style.overflowY)) return;
        if (el.scrollHeight <= el.clientHeight + 40) return;
        const rect = el.getBoundingClientRect();
        if (rect.width * rect.height > 0) inner = Math.max(inner, el.scrollHeight);
      });
      return document.body.scrollHeight + inner;
    }
    """
    before = await page.evaluate(measure_js)
    for _ in range(max(1, min(times, 20))):
        await page.evaluate(scroll_js)
        await page.wait_for_timeout(700)
    after = await page.evaluate(measure_js)
    grew = after - before
    if grew > 0:
        return (
            f"Scrolled {times}x. Page grew {before} -> {after} (+{grew}px). "
            "Call extract_records again to read what loaded."
        )
    # Without this, a model that wants more rows keeps scrolling a page that
    # has nothing left to give - seen in a real run, three identical scrolls
    # in a row. Say plainly that this page is exhausted and what to do next.
    return (
        f"Scrolled {times}x but the page did not grow ({after}px both times). "
        "This page is fully loaded - scrolling again will not add anything. "
        "This site paginates instead of infinite-scrolling. To get more "
        "records, navigate to the next page (for a search URL, add or "
        "increment a `&page=N` parameter) and call extract_records there. "
        "Combine what you collect across pages."
    )


@mcp.tool()
async def get_text(index: int | None = None, session: str = DEFAULT_SESSION) -> str:
    """Return visible text of the element at the given index, or the whole page if index is omitted."""
    page = await _get_page(session)
    if index is None:
        text = await page.inner_text("body")
        return text[:5000]
    return await page.locator(f'[data-bmcp-idx="{index}"]').inner_text()


@mcp.tool()
async def screenshot(path: str = "/tmp/browser-mcp-screenshot.png", session: str = DEFAULT_SESSION) -> str:
    """Save a screenshot of a session's current page to the given path and return the path."""
    page = await _get_page(session)
    await page.screenshot(path=path)
    return path


@mcp.tool()
async def network_requests(session: str = DEFAULT_SESSION, clear: bool = False) -> str:
    """List XHR/fetch requests seen since the session started (or since last clear).

    This is how you find the real API behind a page instead of scraping
    rendered HTML - browse the page normally, then call this to see what
    it actually fetched. JSON response bodies are included (truncated).
    Set clear=true to reset the log after reading it.
    """
    browser_session = await manager.get(session)
    log = browser_session.network_log
    if not log:
        return "(no XHR/fetch requests recorded yet - browse the page first)"
    lines = []
    for i, entry in enumerate(log, 1):
        lines.append(f"[{i}] {entry['method']} {entry['status']} {entry['url']}")
        if entry["content_type"]:
            lines.append(f"    content-type: {entry['content_type']}")
        if entry["body"]:
            lines.append(f"    body: {entry['body']}")
    if clear:
        browser_session.network_log = []
        lines.append("\n(log cleared)")
    return "\n".join(lines)


@mcp.tool()
async def replay_request(url: str, session: str = DEFAULT_SESSION, method: str = "GET") -> str:
    """Call a URL via fetch() from inside the session's already-loaded page - not a bare HTTP request.

    This exists because a bare `requests.get()` to a URL found via
    network_requests commonly gets a 403, even with realistic headers -
    confirmed on a real site. Sites gate these endpoints on session
    cookies and/or TLS fingerprint, which a standalone HTTP call can't
    reproduce but an actual browser already has. Use this to replay a
    discovered API call (e.g. with a different query value) instead of
    writing standalone requests-based code for a protected endpoint.
    """
    browser_session = await manager.get(session)
    page = await browser_session.ensure_page()
    result = await page.evaluate(
        """async ({url, method}) => {
            const r = await fetch(url, {method});
            const text = await r.text();
            return {status: r.status, body: text.slice(0, 3000)};
        }""",
        {"url": url, "method": method},
    )
    return f"status={result['status']}\n{result['body']}"


@mcp.tool()
async def request_human_help(reason: str, session: str = DEFAULT_SESSION) -> str:
    """Ask a human to take over this session's browser for a step the agent can't do -
    2FA, a CAPTCHA it can't solve, a login wall, anything requiring a real person.

    Local-machine version: the browser is already headed and visible on this
    computer's screen, so "handoff" means bringing the right tab to front and
    pausing - not remote screen access. Full any-device remote-assist
    (BrowserAct's live-URL version) is a separate, larger piece for when
    this agent runs somewhere the human can't physically reach, e.g. a cloud
    deployment - not needed while everything runs on one machine.

    The calling agent must stop and wait for the human to confirm the step
    is done before calling any more tools against this session.
    """
    page = await _get_page(session)
    await page.bring_to_front()
    return (
        f"HUMAN HELP NEEDED on session '{session}': {reason}\n"
        f"Current page: {page.url}\n"
        "The browser window has been brought to the front on this machine. "
        "A person needs to complete this step by hand in that window. "
        "Do not call any more tools for this session until the human confirms "
        "the step is done - call get_state afterward to see the result."
    )


@mcp.tool()
async def close_browser(session: str = DEFAULT_SESSION) -> str:
    """Close one session's browser. Other sessions are unaffected."""
    closed = await manager.close(session)
    return "closed" if closed else "no such session"


def main() -> None:
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    if transport == "stdio":
        mcp.run()
    else:
        mcp.run(
            transport=transport,
            host=os.environ.get("HOST", "0.0.0.0"),
            port=int(os.environ.get("PORT", "8000")),
        )


if __name__ == "__main__":
    main()
