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

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from scrapr_core.config import get_settings

__all__ = ["SECURITY_HEADERS", "BodyLimit", "install_security_headers"]

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


MAX_BODY_BYTES = 1024 * 1024
"""No route in this API needs a larger body: uploads go straight to storage
(`DEC-12`), and the largest field is a 2,000-character objective (`DEC-23`)."""

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _refusal(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
        headers=SECURITY_HEADERS,
    )


class BodyLimit:
    """Counts a body as it arrives, so one without a `Content-Length` meets the cap too.

    The header check in `install_security_headers` refuses a declared oversize
    body without reading a byte. A chunked request declares nothing, so without
    this it would be read and parsed in full. Raised as an `HTTPException`,
    which FastAPI's body parsing re-raises rather than folding into a 400, and
    which the error handlers put in the envelope.
    """

    def __init__(self, app: ASGIApp, max_bytes: int = MAX_BODY_BYTES) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        received = 0

        async def counted() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise HTTPException(status_code=413)
            return message

        await self.app(scope, counted, send)


def install_security_headers(app: FastAPI) -> None:
    """Attach the headers to every response, errors included, and refuse
    oversized bodies and cross-origin writes before any route runs.

    Install before `CORSMiddleware` is added, so CORS stays outermost and a
    refusal sent to the web app's own origin is one it can read.
    """
    app.add_middleware(BodyLimit)
    settings = get_settings()
    send_hsts = settings.scrapr_env != "local"
    allowed_origins = frozenset(settings.allowed_origins)

    @app.middleware("http")
    async def _security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        declared = request.headers.get("content-length")
        if declared is not None and (not declared.isdigit() or int(declared) > MAX_BODY_BYTES):
            return _refusal(413, "request_too_large", "That request is too large.")

        # `DEC-23`: a state-changing request from a page on another origin is
        # refused, on top of `SameSite=Lax`. Requests with no `Origin` header
        # are not browser cross-site writes and pass: browsers always send one
        # on a cross-origin POST.
        origin = request.headers.get("origin")
        if request.method in UNSAFE_METHODS and origin is not None and origin not in allowed_origins:
            return _refusal(403, "forbidden_origin", "That request was not allowed.")

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
