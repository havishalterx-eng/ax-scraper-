"""Shared-secret authentication for the HTTP API.

This exists because the API drives a real browser and spends real model
credits. Once it is reachable from the internet - which it must be for the
console to run on Vercel and be usable from a phone - an unauthenticated
endpoint is an open invitation to run arbitrary automation on someone else's
infrastructure and bill. Public URLs get scanned; this is not hypothetical.

A single shared token is the right weight for a single-operator tool: real
protection, nothing to administer. It is not a user system - there are no
accounts, roles, or per-caller identity, and it should not be mistaken for
one if this ever becomes multi-tenant.

Set `AX_API_TOKEN` to enable it. Unset, the API stays open, which is correct
for a local-only run and is reported by `/health` so the console can say
plainly which mode it is in.
"""

from __future__ import annotations

import hmac
import os

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

# Open so an uptime check or load balancer can probe the service without
# holding the secret. It deliberately exposes no run data, no results, and no
# configuration that would help an attacker.
PUBLIC_PATHS = frozenset({"/health"})


def configured_token() -> str | None:
    token = os.environ.get("AX_API_TOKEN", "").strip()
    return token or None


def auth_enabled() -> bool:
    return configured_token() is not None


def _presented_token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    # Query fallback for browser-initiated GETs that cannot set headers, such
    # as an <img> or a link the user opens directly.
    query_token = request.query_params.get("token")
    return query_token.strip() if query_token else None


class TokenAuthMiddleware(BaseHTTPMiddleware):
    """Rejects requests without the shared token once one is configured."""

    async def dispatch(self, request: Request, call_next):
        expected = configured_token()
        if expected is None:
            return await call_next(request)

        # CORS preflight carries no Authorization header by design; blocking it
        # would break every cross-origin call before the real request is sent.
        if request.method == "OPTIONS":
            return await call_next(request)

        path = request.url.path
        if path in PUBLIC_PATHS:
            return await call_next(request)

        # The console is a static bundle with no secrets in it; gating the
        # HTML would leave nowhere to type the token in the first place.
        if not _is_api_path(path):
            return await call_next(request)

        presented = _presented_token(request)
        # compare_digest to keep the check constant-time - a plain `==` leaks
        # how much of the token matched through timing.
        if presented is None or not hmac.compare_digest(presented, expected):
            return JSONResponse(
                {"error": "Unauthorized. Supply the API token as a Bearer header."},
                status_code=401,
            )
        return await call_next(request)


API_PREFIXES = (
    "/tasks",
    "/agents",
    "/sessions",
    "/templates",
    "/tools",
    "/infrastructure",
    "/config",
)


def _is_api_path(path: str) -> bool:
    return any(path == prefix or path.startswith(prefix + "/") for prefix in API_PREFIXES)
