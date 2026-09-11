# ScrapR

Turns a question into an evidence-backed report with sources, analysis, charts and follow-up.

**Status: Phase 0, foundations.** The backend walking skeleton runs end to end:
`POST /v1/research` creates a session and a version owned by an anonymous
session, the local runner executes a two-stage pipeline over `run_steps` against
a fixture tool and a fake LLM, one evidence-backed fact claim persists with its
source and retrieval timestamp, the validation gate passes it, and
`GET /v1/research/{id}/versions/1` returns it with the activity timeline. What
remains in Phase 0 is the product frontend — intake, workspace and activity
surfaces. See
`docs/superpowers/specs/implementation-plan.md` §13 for the phase plan and
`docs/decisions/` for resolved open questions.

---

## Layout

```
apps/web/             Next.js 16 · React 19 · Tailwind v4 (landing page built)
services/api/         FastAPI entrypoint, thin
services/worker/      worker entrypoint, thin
packages/scrapr_core/ all Python domain logic
  domain/             ids (UUIDv7), ownership, json types
  db/                 models, migrations, ownership-scoped repositories
  security/           the trusted/untrusted boundary
  tools/              uniform tool contract, registry, fixture tools
  llm/                provider abstraction, envelopes, the fake provider
  jobs/               the durable step runner
  orchestrator/       Phase 0 skeleton; the real pipeline is Phase 1
  synthesis/          the validation gate
packages/contracts/   openapi.json, generated from the API
docs/                 specs and decision records
design-system/        MASTER.md, the landing visual system
```

The entrypoints are deliberately thin and the core is fat. Where workers run in
production is still open (`OPEN-03`), and that has to stay a deployment choice
rather than a refactor.

---

## Prerequisites

| Tool | Version | Notes |
|---|---|---|
| Python | 3.12+ | `uv` will fetch one if you have none |
| uv | 0.5+ | Python dependency and workspace management |
| Node | 20+ | for `apps/web` |
| Docker | any recent | local Postgres only |

---

## Setup

```bash
# 1. Python workspace
uv sync --group dev

# 2. Local Postgres
cp .env.example .env
docker compose up -d
uv run alembic upgrade head        # never runs automatically on boot

# 3. Frontend
cd apps/web && npm install
```

### If `uv sync` fails with `invalid peer certificate: UnknownIssuer`

Your network terminates TLS with its own certificate authority, which uv does not
trust by default. Use the system trust store:

```bash
uv sync --group dev --system-certs
```

To avoid repeating the flag, set `UV_NATIVE_TLS=1` in your shell profile.

---

## Everyday commands

```bash
# Tests
uv run pytest                       # everything
uv run pytest --cov                 # with the 80% coverage gate

# Quality gates, the same three CI runs
uv run ruff check packages/ services/
uv run mypy packages/scrapr_core/src
uv run pytest --cov

# Frontend
cd apps/web && npm run dev          # localhost:3000
cd apps/web && npm run build

# Migrations
uv run alembic upgrade head         # apply
uv run alembic check                # models vs. database, no drift
uv run alembic revision --autogenerate -m "what changed"

# Run the API and the worker
uv run uvicorn scrapr_api.main:app --reload --port 8000
uv run scrapr-worker

# The API contract, after any route or schema change
uv run python packages/contracts/export_openapi.py   # regenerate openapi.json
cd apps/web && npm run generate:api                  # regenerate TS types
```

Tests marked `integration` need the Postgres from `docker compose`; they run
against a scratch `scrapr_test` database and skip, rather than fail, if no
server is reachable.

---

## Conventions worth knowing before you write code

**Identifiers are UUIDv7, generated in Python.** Never `default uuidv7()` in a
migration: that function only exists in PostgreSQL 18+. Use
`scrapr_core.domain.ids.new_id()`.

**Retrieved content is `Untrusted` and cannot be stringified.** `str()`,
f-strings, `format()`, `print()` and `bytes()` on an `Untrusted` value all raise.
Reading `.text` is the single deliberate escape hatch, and it is meant to be
greppable so review can find every place untrusted content enters ordinary code.
Instructions are `Trusted`. The two never meet except as separate parameters to
`LLMProvider.complete_structured`. See `packages/scrapr_core/src/scrapr_core/security/trust.py`.

**Ownership lives in repositories, never in handlers.** Every repository takes
an `OwnerContext` and every query filters on it, so the cross-account test suite
tests one implementation rather than spot-checking twenty call sites. Research
owned by somebody else reads as absent, not as forbidden.

**Tools are asked for by category, never by name.** The orchestrator says
"web search" and the registry decides who answers, which is what keeps the
unresolved provider questions (`OPEN-04..10`) out of the critical path. The
registry freezes before a run starts.

**The API contract is generated, both halves.** `openapi.json` comes from the
FastAPI app and `apps/web/src/lib/api/schema.ts` comes from `openapi.json`. Both
are committed and CI fails if either is stale, so a route change that breaks the
frontend breaks the build instead of production. Never hand-edit either file.

**Job state lives in Postgres.** A run is rows in `research_runs` and
`run_steps`; a worker claims a step with `FOR UPDATE SKIP LOCKED`, checkpoints
before completing, and a dead worker's step is reclaimed after its lease
expires. Every step handler must therefore be idempotent.

**Line endings are LF, enforced by `.gitattributes`.** The repo previously grew a
CRLF-converted duplicate of the entire landing page because this was missing.

---

## Documents

| File | What it is |
|---|---|
| `masterplan.md` | product vision, the original source of truth |
| `PRD.md` | requirements, phases, open questions, decision log |
| `docs/superpowers/specs/implementation-plan.md` | architecture and build plan |
| `docs/decisions/` | one file per resolved open question |
| `design-system/MASTER.md` | landing visual system |
| `websitedesign.md` | the original landing build brief, not an architecture record |
