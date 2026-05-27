"""Security middleware: Host allowlist, Origin/Referer check, CSRF double-submit cookie.

Threat model: localhost tool holding prod DB credentials with no auth.
Primary risk is DNS-rebinding — a malicious site resolves to 127.0.0.1:7777
and drives requests via the user's browser.

Defences (applied in order):
  1. Host-header allowlist (ALL requests) — anti-DNS-rebinding.
  2. Origin/Referer check (unsafe methods) — secondary cross-origin guard.
  3. CSRF double-submit token (unsafe methods) — validates cookie vs. header/form.
"""

import secrets
from typing import Any
from urllib.parse import parse_qs, urlparse

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from pydbplay.config import Settings

_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_CSRF_COOKIE = "csrf_token"
_CSRF_HEADER = "X-CSRFToken"
_CSRF_FIELD = "csrf_token"


def _hostname(value: str) -> str:
    """Extract the hostname from a Host/Origin/Referer header value.

    Handles bare hostnames, hostnames with ports, full URLs, and bracketed
    IPv6 addresses (e.g. ``[::1]:7777`` → ``::1``).
    """
    # Origin and Referer are full URLs; Host may be "host:port" (no scheme).
    if "://" in value:
        return urlparse(value).hostname or ""
    # Bracketed IPv6: "[::1]" or "[::1]:port" → "::1"
    if value.startswith("["):
        bracket_end = value.find("]")
        if bracket_end == -1:
            return ""
        return value[1:bracket_end]
    # Bare hostname or "host:port" — only strip port when exactly one colon.
    if value.count(":") == 1:
        return value.rsplit(":", 1)[0]
    return value


def _csrf_from_body(body: bytes, content_type: str) -> str | None:
    """Extract the csrf_token value from a URL-encoded form body.

    We parse the raw bytes rather than using request.form() to avoid consuming
    the body stream — Starlette caches request.body() but form() uses a separate
    channel that is NOT re-playable through BaseHTTPMiddleware's call_next.

    Only ``application/x-www-form-urlencoded`` bodies are parsed here.
    multipart/form-data and other non-HTMX clients MUST send the CSRF token
    via the ``X-CSRFToken`` header instead; the form-field fallback is
    intentionally limited to urlencoded bodies (YAGNI).
    """
    if "application/x-www-form-urlencoded" not in content_type:
        return None
    try:
        parsed = parse_qs(body.decode("utf-8", errors="replace"))
        values = parsed.get(_CSRF_FIELD)
        return values[0] if values else None
    except Exception:
        return None


class LocalhostSecurityMiddleware(BaseHTTPMiddleware):
    """Enforce Host allowlist, Origin/Referer guard, and CSRF double-submit token."""

    def __init__(self, app: ASGIApp, settings: Settings, **kwargs: Any) -> None:
        super().__init__(app, **kwargs)
        self._settings = settings

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if not self._settings.security_enabled:
            return await call_next(request)

        allowed = self._settings.allowed_hosts

        # ── 1. Host allowlist (all requests) ─────────────────────────────────
        host_header = request.headers.get("host", "")
        host = _hostname(host_header)
        if not host or host not in allowed:
            return Response(
                content="Host not allowed",
                status_code=400,
                media_type="text/plain",
            )

        # ── 2. Origin / Referer check (unsafe methods only) ──────────────────
        if request.method in _UNSAFE_METHODS:
            origin_header = request.headers.get("origin")
            referer_header = request.headers.get("referer")

            if origin_header is not None:
                origin_host = _hostname(origin_header)
                if origin_host not in allowed:
                    return Response(
                        content="Cross-origin request blocked",
                        status_code=403,
                        media_type="text/plain",
                    )
            elif referer_header is not None:
                referer_host = _hostname(referer_header)
                if referer_host not in allowed:
                    return Response(
                        content="Cross-origin request blocked",
                        status_code=403,
                        media_type="text/plain",
                    )
            # Neither header → non-browser client; Host check already applied.

        # ── 3. CSRF double-submit token (unsafe methods only) ─────────────────
        if request.method in _UNSAFE_METHODS:
            cookie_token = request.cookies.get(_CSRF_COOKIE)
            if not cookie_token:
                return Response(
                    content="CSRF token invalid",
                    status_code=403,
                    media_type="text/plain",
                )

            # Check X-CSRFToken header first; fall back to form field.
            # Use request.body() (cached by Starlette) rather than request.form()
            # to avoid consuming the body channel before call_next sees it.
            submitted_token: str | None = request.headers.get(_CSRF_HEADER)
            if submitted_token is None:
                body = await request.body()
                content_type = request.headers.get("content-type", "")
                submitted_token = _csrf_from_body(body, content_type)

            # compare_digest raises TypeError on non-ASCII strings (attacker-
            # controlled input).  Real tokens are always ASCII (token_urlsafe),
            # so rejecting non-ASCII is correct and avoids a 500.
            # Also guard against an empty cookie_token slipping through (belt-
            # and-suspenders; the `not cookie_token` check above handles it).
            if (
                not submitted_token
                or not submitted_token.isascii()
                or not cookie_token.isascii()
                or not secrets.compare_digest(cookie_token, submitted_token)
            ):
                return Response(
                    content="CSRF token invalid",
                    status_code=403,
                    media_type="text/plain",
                )

        # ── Pass through; set CSRF cookie if missing ─────────────────────────
        response = await call_next(request)

        if not request.cookies.get(_CSRF_COOKIE):
            token = secrets.token_urlsafe(32)
            response.set_cookie(
                key=_CSRF_COOKIE,
                value=token,
                samesite="strict",
                path="/",
                httponly=False,  # JS (HTMX hook) must read it
                secure=False,  # localhost HTTP
            )

        return response
