"""One error envelope, one place internal failure becomes a public message.

`REQ-SEC-010 AC-5` requires errors be sanitized before they reach a user, and
`REQ-INPUT-006 AC-4` and `REQ-ACT-006 AC-2` both want a consistent shape. Doing
that per-handler guarantees the day somebody forgets, so it happens here, at the
boundary, for every route at once.

**Nothing internal crosses this line.** A provider name, a URL, a stack trace or
an upstream message is diagnostic detail: it is logged, and the client is told
what happened in terms it can act on. That is not politeness — a tool failure
message can contain a URL the agent was tricked into fetching, and a validation
error can contain retrieved content.
"""

from __future__ import annotations

import logging
from typing import final

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

__all__ = ["ApiError", "ErrorBody", "ErrorEnvelope", "install_error_handlers"]

logger = logging.getLogger(__name__)


class ErrorBody(BaseModel):
    """The public shape of a failure."""

    code: str
    """A stable, machine-readable identifier the frontend can branch on."""

    message: str
    """Safe to display. Never contains internal detail."""


class ErrorEnvelope(BaseModel):
    """Every error response, from every route."""

    error: ErrorBody


@final
class ApiError(Exception):
    """Raised by a route when the request cannot be satisfied.

    Carrying the public message on the exception is what lets a handler fail in
    one line without inventing a response shape.
    """

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def _envelope(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=ErrorEnvelope(error=ErrorBody(code=code, message=message)).model_dump(),
    )


def install_error_handlers(app: FastAPI) -> None:
    """Attach the handlers that keep every failure in one shape."""

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        return _envelope(exc.status_code, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # FastAPI's default body echoes the offending input back. That input is
        # user-supplied and may be hostile, so it is logged and not returned
        # (`REQ-INPUT-006 AC-4`).
        logger.info("request validation failed for %s", request.url.path)
        return _envelope(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "invalid_request",
            "The request could not be understood. Check the required fields.",
        )

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error on %s", request.url.path)
        return _envelope(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "internal_error",
            "Something went wrong on our side. Please try again.",
        )
