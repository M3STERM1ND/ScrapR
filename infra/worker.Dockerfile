# The worker image (`DEC-26`): one long-lived process per replica, on any
# container host.
#
#   docker build -f infra/worker.Dockerfile -t scrapr-worker .
#
# Built from the repository root. Only the worker and the core it wraps are
# installed, from the lockfile, as non-editable wheels, so the runtime image
# holds a virtualenv and nothing of the source tree: no `.env`, no tests, no web
# app. `.dockerignore` keeps those out of the build context as well.
#
# It defaults to `SCRAPR_ENV=production`, so a container started without TLS
# to the database and storage, or with the development credentials, refuses to
# start rather than running unsafely (`REQ-SEC-003`, `REQ-SEC-007 AC-3`).
# Configuration arrives as environment variables; see `.env.example`.
#
# Stopping is graceful: SIGTERM finishes the step in hand and exits. Give the
# host a stop timeout above the step lease so a step is never cut off mid-way;
# if one is, its lease expires and another replica resumes it (`NFR-REL-001`).

FROM ghcr.io/astral-sh/uv:python3.14-trixie-slim AS build

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Every workspace member's metadata is needed to read the lockfile, but only the
# worker and the core are built.
COPY pyproject.toml uv.lock ./
COPY packages/scrapr_core packages/scrapr_core
COPY services/api/pyproject.toml services/api/pyproject.toml
COPY services/worker services/worker

RUN uv sync --frozen --no-dev --no-editable --package scrapr-worker


FROM python:3.14-slim-trixie

RUN useradd --system --uid 10001 --no-create-home scrapr

COPY --from=build /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    SCRAPR_ENV=production

USER scrapr

CMD ["scrapr-worker"]
