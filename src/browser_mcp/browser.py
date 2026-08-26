from __future__ import annotations

import asyncio
import os
import random
import re
import string
from pathlib import Path

# patchright, not playwright: same API, but patches the CDP-level tells
# (navigator.webdriver, iframe/runtime leaks) that anti-bot scripts check
# first. Verified: vanilla playwright reports navigator.webdriver=True,
# patchright reports False. Drop-in - nothing else in this file changed.
from patchright.async_api import Browser, BrowserContext, Page, Playwright, Route, async_playwright

PROFILES_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "profiles"


def _profile_dir(session_name: str) -> str:
    """A stable on-disk profile per persistent session name, so cookies/
    localStorage/login state survive process restarts - real "signed-in
    session" persistence, not just a same-process reuse."""
    safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", session_name)
    path = PROFILES_DIR / safe_name
    path.mkdir(parents=True, exist_ok=True)
    return str(path)

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
    """Defaults OFF now. Blocking images measured only 8-11% bandwidth savings
    on real pages (JS bundles dominate, not images), and it makes every live
    screenshot render with holes where the content should be - a bad trade
    once you actually watch runs happen. Set BLOCK_MEDIA=1 to bring it back."""
    return os.environ.get("BLOCK_MEDIA", "0") not in ("0", "false", "False")


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


# Runtime override for headless mode, set by the console's toggle. None means
# "use the HEADLESS env var". A live toggle matters because the choice is a
# preference that changes hour to hour - watch the window while debugging a
# selector, hide it while a long harvest runs in the background - and
# restarting the server to flip an env var loses every open session.
_headless_override: bool | None = None


def set_headless(value: bool | None) -> None:
    """Applies to browsers launched from now on; already-open ones keep their mode."""
    global _headless_override
    _headless_override = value


def _headless_enabled() -> bool:
    if _headless_override is not None:
        return _headless_override
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

    def __init__(self, playwright: Playwright, name: str, persistent: bool = False) -> None:
        self._playwright = playwright
        self._name = name
        self.persistent = persistent
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._lock = asyncio.Lock()
        self._closing = False
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
                if self.persistent:
                    if self._context is None:
                        self._context = await self._playwright.chromium.launch_persistent_context(
                            _profile_dir(self._name),
                            headless=_headless_enabled(),
                            proxy=_proxy_config(),
                            args=_extra_launch_args(),
                        )
                    self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()
                else:
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
                # Closing the window with its own X button ends the page but
                # leaves Playwright's browser process running, so the session
                # became a zombie: no window, process still resident, still
                # listed in the UI, and the next run silently reopened inside
                # it. Measured: 7 Chromium processes survived a manual close.
                # Treat the user shutting the window as shutting the session.
                self._page.on("close", lambda _: asyncio.create_task(self._on_window_closed()))
            return self._page

    async def _on_window_closed(self) -> None:
        """Reap this session when its last window is closed by hand."""
        if self._closing:
            return  # our own close() is already tearing it down
        try:
            if self._context is not None and self._context.pages:
                return  # other tabs still open in a persistent context
        except Exception:  # noqa: BLE001 - context already gone
            pass
        await manager.close(self._name)

    async def close(self) -> None:
        async with self._lock:
            self._closing = True
            try:
                if self._context is not None:
                    await self._context.close()
                    self._context = None
                if self._browser is not None:
                    await self._browser.close()
                    self._browser = None
                self._page = None
            finally:
                self._closing = False

    @property
    def current_url(self) -> str | None:
        if self._page is None or self._page.is_closed():
            return None
        return self._page.url


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

    async def get(self, name: str, persistent: bool = False) -> BrowserSession:
        """`persistent` only takes effect the first time `name` is created -
        an already-open session keeps whatever mode it started with."""
        if name not in self._sessions:
            playwright = await self._ensure_playwright()
            self._sessions[name] = BrowserSession(playwright, name, persistent=persistent)
        return self._sessions[name]

    def list_sessions(self) -> list[str]:
        return list(self._sessions.keys())

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
