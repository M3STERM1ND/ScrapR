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

from scrapr_api.errors import install_error_handlers
from scrapr_api.routers import research

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

    install_error_handlers(app)
    app.include_router(research.router)

    @app.get("/health", tags=["meta"])
    def health() -> dict[str, str]:
        """Liveness only. Deliberately does not touch the database: a health
        check that fails when Postgres blips takes the API down with it."""
        return {"status": "ok"}

    return app


app = create_app()
