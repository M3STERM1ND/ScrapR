"""The FastAPI application. Thin by design.

Routing, dependency resolution and error shaping — nothing else. Every piece of
domain logic lives in `scrapr_core`, which is what keeps "where do workers run"
(`OPEN-03`) a deployment choice rather than a refactor: the API and the worker
are two thin wrappers over the same core.

The OpenAPI document generated from this app is the contract the frontend's TS
client is generated from, so a route change that breaks the frontend fails the
build rather than production (implementation plan §6.2).
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from scrapr_api.errors import install_error_handlers
from scrapr_api.routers import auth, exports, history, research, uploads
from scrapr_api.security import install_security_headers
from scrapr_core.config import get_settings

__all__ = ["create_app"]

DESCRIPTION = (
    "Turns a question into an evidence-backed report with sources, analysis, "
    "charts and follow-up."
)


def create_app() -> FastAPI:
    """Build the application.

    A factory rather than a module-level instance, so a test can build one
    without importing global state and so nothing connects to a database at
    import time.
    """
    # `REQ-SEC-007 AC-3`: a production process with an unsafe configuration
    # refuses to start, and says every reason at once.
    problems = get_settings().production_problems()
    if problems:
        raise RuntimeError("refusing to start: " + "; ".join(problems))

    app = FastAPI(
        title="ScrapR",
        description=DESCRIPTION,
        version="0.1.0",
        # Docs are on in every environment: this API has one consumer, the
        # first-party frontend, and a readable contract is worth more than the
        # obscurity of hiding it.
        docs_url="/docs",
        openapi_url="/openapi.json",
    )

    # The web app is served from a different origin in development and may be in
    # production. Credentials are allowed because the anonymous session cookie
    # *is* the authorization; origins are therefore listed explicitly and never
    # wildcarded, since `allow_origins=["*"]` with credentials would let any
    # site on the internet read a visitor's research.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_settings().allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["content-type"],
    )

    install_error_handlers(app)
    install_security_headers(app)
    app.include_router(auth.router)
    # Before the research router, so `/v1/research/claim` is matched as the
    # claim route and never mistaken for a session id.
    app.include_router(auth.claim_router)
    app.include_router(research.router)
    app.include_router(uploads.router)
    app.include_router(exports.research_exports)
    app.include_router(exports.router)
    app.include_router(history.router)

    @app.get("/health", tags=["meta"])
    def health() -> dict[str, str]:
        """Liveness only. Deliberately does not touch the database: a health
        check that fails when Postgres blips takes the API down with it."""
        return {"status": "ok"}

    return app


app = create_app()
