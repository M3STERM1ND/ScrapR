"""Response headers every API answer carries (`REQ-SEC-003`, `REQ-SEC-001`).

**TLS is the platform's job; insisting on it is ours.** Vercel terminates TLS,
so the API never sees a certificate — but `Strict-Transport-Security` is what
stops a browser from ever trying plain HTTP against the domain again, and only
the application can send it. It is sent outside local development, where there
is no TLS to insist on.

**Private by default reaches caches too.** Every response is someone's
research or someone's session, so `Cache-Control: no-store` keeps it out of
shared proxies and the browser's back-forward cache alike. Nothing this API
serves is public, so there is no route to exempt.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response

from scrapr_core.config import get_settings

__all__ = ["SECURITY_HEADERS", "install_security_headers"]

SECURITY_HEADERS: dict[str, str] = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    # A JSON API has no business being rendered as a document.
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
}

HSTS = "max-age=63072000; includeSubDomains"
"""Two years, the preload-list minimum. Not `preload` itself: joining the
preload list is a decision about the whole domain, not about this service."""


def install_security_headers(app: FastAPI) -> None:
    """Attach the headers to every response, errors included."""
    send_hsts = get_settings().scrapr_env != "local"

    @app.middleware("http")
    async def _security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            # The interactive docs need their own scripts; everything else
            # gets the strict policy.
            if name == "Content-Security-Policy" and request.url.path in {"/docs", "/redoc"}:
                continue
            response.headers.setdefault(name, value)
        if send_hsts:
            response.headers.setdefault("Strict-Transport-Security", HSTS)
        return response
