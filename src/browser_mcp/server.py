from __future__ import annotations

from mcp.server import MCPServer

from .browser import manager
from .state import STATE_JS, format_state

mcp = MCPServer("browser-mcp")

DEFAULT_SESSION = "default"


async def _current_state(session: str) -> str:
    browser_session = await manager.get(session)
    page = await browser_session.ensure_page()
    data = await page.evaluate(STATE_JS)
    return format_state(data)


async def _get_page(session: str):
    browser_session = await manager.get(session)
    return await browser_session.ensure_page()


@mcp.tool()
async def browser_open(url: str, session: str = DEFAULT_SESSION) -> str:
    """Open a browser and navigate to a URL.

    `session` names an isolated browser - its own cookies, its own
    fingerprint. Different session names never share state, so parallel
    tasks or separate accounts stay isolated. Omit it for the single
    default session. Returns the page's indexed list of interactive
    elements, e.g. [3]<button> Sign In. Use the index with click/type_text.
    """
    page = await _get_page(session)
    await page.goto(url, wait_until="domcontentloaded")
    return await _current_state(session)


@mcp.tool()
async def navigate(url: str, session: str = DEFAULT_SESSION) -> str:
    """Navigate a session's page to a new URL. Returns the updated state."""
    page = await _get_page(session)
    await page.goto(url, wait_until="domcontentloaded")
    return await _current_state(session)


@mcp.tool()
async def get_state(session: str = DEFAULT_SESSION) -> str:
    """Return a session's current indexed list of interactive elements."""
    return await _current_state(session)


@mcp.tool()
async def click(index: int, session: str = DEFAULT_SESSION) -> str:
    """Click the element at the given index (from get_state/browser_open).

    Returns the updated state after the click.
    """
    page = await _get_page(session)
    await page.locator(f'[data-bmcp-idx="{index}"]').click()
    await page.wait_for_load_state("domcontentloaded")
    return await _current_state(session)


@mcp.tool()
async def type_text(index: int, text: str, submit: bool = False, session: str = DEFAULT_SESSION) -> str:
    """Type text into the element at the given index.

    If submit is true, presses Enter afterward. Returns the updated state.
    """
    page = await _get_page(session)
    locator = page.locator(f'[data-bmcp-idx="{index}"]')
    await locator.fill(text)
    if submit:
        await locator.press("Enter")
        await page.wait_for_load_state("domcontentloaded")
    return await _current_state(session)


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
    mcp.run()


if __name__ == "__main__":
    main()
