from __future__ import annotations

import asyncio
import os
import random
import string

# patchright, not playwright: same API, but patches the CDP-level tells
# (navigator.webdriver, iframe/runtime leaks) that anti-bot scripts check
# first. Verified: vanilla playwright reports navigator.webdriver=True,
# patchright reports False. Drop-in - nothing else in this file changed.
from patchright.async_api import Browser, Page, Playwright, Route, async_playwright

_BLOCKED_RESOURCE_TYPES = {"image", "media", "font"}

# Common third-party analytics/ads/session-replay domains. Blocking these
# doesn't touch the page's own app bundle - it removes code the page never
# needed to render or expose its DOM/text, which is all these tools read.
# Not an ad-blocker-scale list; covers the trackers that show up on most
# sites, not every possible one.
_TRACKER_DOMAINS = (
    "google-analytics.com",
    "googletagmanager.com",
    "doubleclick.net",
    "connect.facebook.net",
    "facebook.com/tr",
    "hotjar.com",
    "segment.io",
    "segment.com",
    "fullstory.com",
    "mixpanel.com",
    "amplitude.com",
    "criteo.com",
    "criteo.net",
    "scorecardresearch.com",
    "quantserve.com",
    "adnxs.com",
    "taboola.com",
    "outbrain.com",
    "newrelic.com",
    "nr-data.net",
    "sentry.io",
    "bugsnag.com",
    "intercom.io",
    "intercomcdn.com",
    "clarity.ms",
    "bat.bing.com",
    "cloudflareinsights.com",
)


def _block_media_enabled() -> bool:
    return os.environ.get("BLOCK_MEDIA", "1") not in ("0", "false", "False")


def _block_trackers_enabled() -> bool:
    return os.environ.get("BLOCK_TRACKERS", "1") not in ("0", "false", "False")


def _is_tracker(url: str) -> bool:
    return any(domain in url for domain in _TRACKER_DOMAINS)


async def _block_route(route: Route) -> None:
    request = route.request
    if _block_media_enabled() and request.resource_type in _BLOCKED_RESOURCE_TYPES:
        await route.abort()
        return
    if _block_trackers_enabled() and _is_tracker(request.url):
        await route.abort()
        return
    await route.continue_()


def _headless_enabled() -> bool:
    return os.environ.get("HEADLESS", "0") in ("1", "true", "True")


def _extra_launch_args() -> list[str]:
    raw = os.environ.get("BROWSER_MCP_EXTRA_ARGS", "")
    return raw.split() if raw else []


def _proxy_config() -> dict[str, str] | None:
    """Reads PROXY_SERVER/PROXY_USERNAME/PROXY_PASSWORD from the environment.

    No vendor is wired in by default - this just passes through whatever
    proxy endpoint you point it at (host:port + credentials from any
    residential proxy vendor). Returns None when PROXY_SERVER is unset, so
    the no-proxy path is unaffected.

    Set PROXY_ROTATE=1 to get a distinct IP per BrowserSession (not per
    request - a session keeps one IP for its whole life, matching how
    sessions already work elsewhere in this file). Appends
    `-session-<random>` to the username, which vendors like Bright Data
    treat as a request for a fresh IP from the zone's pool. Confirmed real:
    4 different session suffixes against a 5-IP zone returned 3 distinct
    real locations (Virginia, LA, Chicago) - a 1-IP zone returns the same
    IP regardless of suffix, since there's only one to hand out.
    """
    server = os.environ.get("PROXY_SERVER")
    if not server:
        return None
    config = {"server": server}
    username = os.environ.get("PROXY_USERNAME")
    password = os.environ.get("PROXY_PASSWORD")
    if username and os.environ.get("PROXY_ROTATE") == "1":
        suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
        username = f"{username}-session-{suffix}"
    if username:
        config["username"] = username
    if password:
        config["password"] = password
    return config


class BrowserSession:
    """One isolated browser: its own Chromium process, own cookies, own
    fingerprint. Everything under one session name shares this; different
    session names never share state - this is what makes parallel
    multi-account use safe (BrowserAct's `--session <name>` concept).

    Images/media/fonts (BLOCK_MEDIA=0 to disable) and known tracker/ad
    domains (BLOCK_TRACKERS=0 to disable) are blocked by default - measured
    8-11% byte savings from media alone on real sites; trackers are the
    other real lever since modern pages are script-heavy, not image-heavy.
    Trade-off: screenshot() will show missing images while BLOCK_MEDIA is on.

    Every XHR/fetch response is recorded into `network_log` (JSON bodies
    kept, truncated; other content types logged without a body) - this is
    what lets an agent find the real API behind a page instead of scraping
    rendered HTML. Accumulates until cleared; never auto-clears on navigate.

    Headless defaults to off (HEADLESS=1 to enable) - deliberate for local
    use, so you can watch it work. A container has no display at all, so
    the Dockerfile sets HEADLESS=1; forgetting this crashes the launch
    outright, not something to find out at deploy time.
    """

    def __init__(self, playwright: Playwright) -> None:
        self._playwright = playwright
        self._browser: Browser | None = None
        self._page: Page | None = None
        self._lock = asyncio.Lock()
        self.network_log: list[dict] = []

    async def _record_response(self, response) -> None:
        request = response.request
        if request.resource_type not in ("xhr", "fetch"):
            return
        entry = {
            "method": request.method,
            "url": response.url,
            "status": response.status,
            "content_type": response.headers.get("content-type", ""),
            "body": None,
        }
        if "application/json" in entry["content_type"]:
            try:
                body = await response.text()
                entry["body"] = body[:3000]
            except Exception:
                pass
        self.network_log.append(entry)

    async def ensure_page(self) -> Page:
        async with self._lock:
            if self._page is None or self._page.is_closed():
                if self._browser is None or not self._browser.is_connected():
                    self._browser = await self._playwright.chromium.launch(
                        headless=_headless_enabled(),
                        proxy=_proxy_config(),
                        args=_extra_launch_args(),
                    )
                self._page = await self._browser.new_page()
                if _block_media_enabled() or _block_trackers_enabled():
                    await self._page.route("**/*", _block_route)
                self._page.on("response", lambda r: asyncio.create_task(self._record_response(r)))
            return self._page

    async def close(self) -> None:
        async with self._lock:
            if self._browser is not None:
                await self._browser.close()
                self._browser = None
            self._page = None


class SessionManager:
    """Looks up a BrowserSession by name, creating it on first use.

    One Playwright driver connection is shared across every session (it's
    just the control channel to the driver process, not a browser) - each
    session still gets its own Chromium launch, so cookies/fingerprints
    stay fully isolated per name.
    """

    def __init__(self) -> None:
        self._playwright: Playwright | None = None
        self._sessions: dict[str, BrowserSession] = {}
        self._lock = asyncio.Lock()

    async def _ensure_playwright(self) -> Playwright:
        async with self._lock:
            if self._playwright is None:
                self._playwright = await async_playwright().start()
            return self._playwright

    async def get(self, name: str) -> BrowserSession:
        if name not in self._sessions:
            playwright = await self._ensure_playwright()
            self._sessions[name] = BrowserSession(playwright)
        return self._sessions[name]

    async def close(self, name: str) -> bool:
        session = self._sessions.pop(name, None)
        if session is None:
            return False
        await session.close()
        return True

    async def close_all(self) -> None:
        for session in list(self._sessions.values()):
            await session.close()
        self._sessions.clear()
        async with self._lock:
            if self._playwright is not None:
                await self._playwright.stop()
                self._playwright = None


manager = SessionManager()
