# ScrapR V1 — Implementation Plan

**Status:** Draft for review
**Date:** 2026-09-09
**Sources of authority:** `masterplan.md` (product), `PRD.md` (requirements), `design-system/MASTER.md` (landing visual system)
**Audience:** Developer A (AI/research backend), Developer B (product/frontend), and coding agents executing tasks.

---

## 0. How to read this document

The PRD deliberately stops short of schema, API shape, and component design and routes those here (`PRD §1.6`). This document supplies exactly that layer and nothing above it.

Three rules govern everything below.

1. **No product decisions are made here.** Where the PRD leaves a decision open, this plan says so and designs around the gap. It does not close it.
2. **Where a decision is genuinely implementation-level** (migration tool, repo layout, test strategy), this plan decides it and says why.
3. **New gaps found while writing this plan** are recorded in §14 as `N-xx` and are *not* silently resolved.

### 0.1 The central design problem

The PRD registers **25 open questions**. Eight of them block Phase 1:

`OPEN-03` (queue/worker + where workers run), `OPEN-04` (AI provider), `OPEN-05` (web search), `OPEN-06` (financial data), `OPEN-07` (filings), `OPEN-08` (jobs), `OPEN-09` (news), `OPEN-10` (object storage).

> **`OPEN-13` (research termination) was the ninth and is now closed** by `DEC-04`, recorded in `docs/decisions/OPEN-13.md` and logged in `PRD.md §13.10`. §5.4 records what that means for the code; it did not make the decision.

If those are treated as prerequisites, nothing gets built for weeks. So the architecture's first job is to sort them into two piles:

| Kind | Open questions | Consequence |
|---|---|---|
| **Late-binding.** A provider behind a contract the PRD already mandates. | `OPEN-04`, `OPEN-05`, `OPEN-06`, `OPEN-07`, `OPEN-08`, `OPEN-09`, `OPEN-10` | Build the contract and a fake. Real provider is a config change later. **Does not block Phase 1 code.** |
| **Structural.** Changes the shape of the system, not just a plug. | `OPEN-03` (`OPEN-13` was here until `DEC-04` closed it) | **Genuinely blocks.** Must be answered before Phase 1 code that depends on it. |

`REQ-TOOL-001` (uniform tool contract), `REQ-TOOL-009` (add a tool without touching the orchestrator) and `REQ-TECH-006` (AI provider abstraction) already require the abstractions that make the first pile late-binding. This plan leans on that hard.

For the second pile, §5.6 and §7.2 propose a shape that satisfies the surrounding requirements without picking the technology, so that `OPEN-03` narrows to a hosting choice rather than a redesign.

---

## 1. Architecture

### 1.1 Layers

The PRD fixes four layers and forbids a multi-agent swarm (`PRD §11.3`). This plan adds no layers.

```
┌──────────────────────────────────────────────────────────────┐
│  apps/web — Next.js                                          │
│  marketing surface · intake · activity · workspace · export  │
└───────────────────────────┬──────────────────────────────────┘
                            │  HTTPS, JSON, SSE
┌───────────────────────────▼──────────────────────────────────┐
│  services/api — FastAPI (thin)                               │
│  validation · authz · job submission · read models           │
└───────────────────────────┬──────────────────────────────────┘
                            │  shared Postgres + durable job records
┌───────────────────────────▼──────────────────────────────────┐
│  services/worker — research + export + upload workers        │
└───────────────────────────┬──────────────────────────────────┘
                            │
┌───────────────────────────▼──────────────────────────────────┐
│  packages/scrapr_core — all domain logic                     │
│                                                              │
│   orchestrator/   plan, select tools, judge sufficiency      │
│   tools/          uniform contract + implementations         │
│   evidence/       extract, normalize, dedupe, tier,          │
│                   detect conflict, assign confidence         │
│   synthesis/      claims, sections, citation map, validate   │
│   viz/            chart-spec selection from sourced data     │
│   export/         PDF + PPTX renderers over themes           │
│   llm/            provider abstraction                       │
│   security/       trust boundary, untrusted envelopes        │
│   db/             SQLAlchemy models + Alembic migrations     │
└──────────────────────────────────────────────────────────────┘
```

**Why `api` and `worker` are thin and `scrapr_core` is fat.** `OPEN-03` may place workers on a different platform from the API. If domain logic lives in either entrypoint, that decision becomes a refactor. With a shared core, it becomes a deployment target. Both entrypoints are a few hundred lines of wiring.

### 1.2 The three invariants the architecture exists to protect

These come straight from the PRD's trust core, and every structural choice below serves one of them.

1. **No claim without evidence** (`REQ-EVID-017`). Enforced by a programmatic gate in the pipeline, explicitly *not* by prompt wording. See §5.5.
2. **Untrusted content never becomes an instruction** (`REQ-SEC-012..014`). Enforced by a type-level separation between trusted and untrusted strings, so concatenating them is a compile-visible mistake. See §9.
3. **Versions are immutable** (`REQ-VER-002`). Enforced by scoping sources, evidence, claims and sections to a `version_id` and never updating them after a version closes. See §4.

---

## 2. Repository structure

**Decision: single monorepo.** Two developers, one product, shared types across the API boundary. Polyrepo would cost more coordination than it buys.

```
scrapr/
├── apps/
│   └── web/                        Next.js 16 · React 19 · Tailwind v4
│       ├── src/app/
│       │   ├── (marketing)/        landing page (already built)
│       │   ├── (app)/
│       │   │   ├── research/new/           intake
│       │   │   ├── research/[id]/          workspace
│       │   │   ├── research/[id]/v/[n]/    version view
│       │   │   └── history/                saved research
│       │   └── api/                        BFF routes only if N-02 requires
│       ├── src/components/
│       │   ├── marketing/          landing sections (built)
│       │   ├── intake/             objective form, optional context, uploads
│       │   ├── activity/           timeline, status
│       │   ├── report/             sections, claim rendering, citation popover
│       │   ├── evidence/           inspection surface, conflict display
│       │   ├── viz/                chart + table renderers
│       │   ├── conversation/       follow-up panel
│       │   └── ui/                 primitives (built)
│       ├── src/lib/api/            generated client from OpenAPI
│       └── public/fonts/           self-hosted Satoshi (built)
│
├── services/
│   ├── api/                        FastAPI entrypoint (thin)
│   │   ├── main.py
│   │   ├── routers/                one per resource
│   │   ├── deps.py                 auth, db session, ownership resolution
│   │   └── errors.py               sanitized error envelope (REQ-SEC-010)
│   └── worker/                     worker entrypoint (thin)
│       ├── main.py                 claim → execute → checkpoint loop
│       └── handlers/               research, update, export, upload
│
├── packages/
│   ├── scrapr_core/                all Python domain logic
│   │   ├── domain/                 entities, enums, value objects
│   │   ├── db/
│   │   │   ├── models/             SQLAlchemy 2.0 declarative
│   │   │   ├── repositories/       ownership-scoped data access
│   │   │   └── migrations/         Alembic
│   │   ├── orchestrator/
│   │   ├── tools/
│   │   │   ├── contract.py         ToolRequest / ToolResult / ToolFailure
│   │   │   ├── registry.py
│   │   │   └── impl/               web_search, page_fetch, financial,
│   │   │                           filings, jobs, news, documents
│   │   ├── evidence/
│   │   ├── synthesis/
│   │   ├── viz/
│   │   ├── export/
│   │   ├── llm/
│   │   ├── security/
│   │   └── jobs/                   durable job state machine
│   └── contracts/
│       ├── openapi.json            generated from FastAPI
│       └── ts/                     generated TS types consumed by web
│
├── docs/
│   ├── superpowers/specs/          this document
│   └── decisions/                  one file per resolved OPEN-xx
├── design-system/
│   └── MASTER.md                   landing visual system (written)
├── infra/                          deploy config: worker image, database roles (DEC-26)
└── tests/
    ├── fixtures/cassettes/         recorded tool responses
    └── adversarial/                prompt-injection corpus (REQ-SEC-014 AC-3)
```

**Migration note.** The landing page currently lives at `web/` with its own git repo (created by `create-next-app`). Step one of Phase 0 is moving it to `apps/web` under a single root repo. Nothing in it needs rewriting.

**Implementation-level decisions made here:** monorepo; SQLAlchemy 2.0 + Alembic; `uv` for Python dependency management; pytest; Playwright for E2E; OpenAPI-generated TypeScript client so the API contract cannot drift from the frontend silently.

---

## 3. Major components

| Component | Owner | Responsibility | Key requirements |
|---|---|---|---|
| **Intake** | B | Objective, optional instructions/URL/company/ticker, optional uploads. Progressive disclosure. Boundary validation. | `REQ-INPUT-001..007` |
| **Orchestrator** | A | Interpret → plan → select tools → judge sufficiency → terminate. Single agent, no swarm. | `REQ-AGENT-001..010` |
| **Tool layer** | A | Uniform contract. Seven tools in V1: web search, page fetch, financial, filings, jobs, news, documents. | `REQ-TOOL-001..013` |
| **Evidence layer** | A | Extract, normalize, dedupe, tier, detect conflict, explain conflict, assign confidence. | `REQ-EVID-001..019` |
| **Synthesis layer** | A | Claims with types, dynamic sections, citation map, validation gate. | `REQ-SYNTH-001..010` |
| **Visualization** | A spec / B render | Agent picks form from sourced structured data; frontend renders; export renders server-side. | `REQ-VIZ-001..006` |
| **Activity stream** | A emit / B render | Meaningful labels, no raw queries, ordered, persisted, failures visible. | `REQ-ACT-001..006` |
| **Workspace** | B | Header, exec summary, sections, viz area, inline citation inspection, conflict display, conversation. | `REQ-WORK-001..009` |
| **Conversation** | A answer / B panel | Context-preserving follow-up, grounded, can trigger fresh research, cited. | `REQ-CONV-001..008` |
| **Documents** | A | Upload, extract, chunk, search via tool layer, separate attribution, untrusted. | `REQ-DOC-001..010` |
| **Versioning** | A | Immutable versions, fresh retrieval, comparison, What's Changed. | `REQ-VER-001..009` |
| **Export** | B design / A pipeline | PDF + PPTX, six themes, content adapts to theme, no per-export design generation. | `REQ-EXP-001..010` |
| **Auth & persistence** | B | Anonymous session, account creation, claiming, history, deletion. | `REQ-AUTH-001..009` |
| **Observability** | Both | Run failures, tool failures, latency, tokens, cost, source failures, workflow errors. | `REQ-OBS-001..008` |

---

## 4. Database design

PostgreSQL. `REQ-DATA-001..012`. All identifiers are UUIDv7 (time-ordered, non-enumerable — satisfies `REQ-SEC-009 AC-1` while keeping index locality).

**UUIDv7 is generated application-side, in Python, not by the database.** Native `uuidv7()` exists only in PostgreSQL 18 and later; on any earlier version a migration with `default uuidv7()` fails outright. Generating in the application removes the version dependency entirely.

### 4.1 The version-scoping decision

This is the most consequential schema choice, so it is stated explicitly.

**Sources, evidence, claims, sections and visualizations are scoped to `version_id`, not to `session_id`.**

Rationale: `REQ-VER-002 AC-2` requires that a prior version's claims, evidence, sources *and retrieval timestamps* are unchanged by an update. `REQ-VER-003 AC-2` requires new retrieval timestamps on update. A source re-fetched during Update Research is therefore a genuinely different record, not a mutation. Version-scoping makes immutability structural rather than a discipline someone has to remember.

Cost: storage duplication across versions. Accepted. It is cheap, and it is the difference between "explainable" and "we think it was probably this".

Deduplication (`REQ-EVID-006`) is therefore scoped within a version: unique on `(version_id, url_normalized)`.

### 4.2 Tables

**Identity and ownership**

```sql
users(
  id uuid pk, email citext unique not null,
  auth_ref text,                    -- shape depends on OPEN-11
  preferences jsonb not null default '{}',
  usage_meta jsonb not null default '{}',
  created_at timestamptz not null, deleted_at timestamptz
)

anonymous_sessions(                  -- shape depends on OPEN-17
  id uuid pk, token_hash text unique not null,
  created_at timestamptz not null, last_seen_at timestamptz not null,
  expires_at timestamptz not null,
  claimed_by_user_id uuid null references users(id), claimed_at timestamptz
)
```

**Research**

```sql
research_sessions(
  id uuid pk,
  owner_user_id uuid null references users(id),
  anonymous_session_id uuid null references anonymous_sessions(id),
  objective text not null,
  instructions text, context_url text, context_company text, context_ticker text,
  subject text,                      -- agent's interpretation (REQ-AGENT-001 AC-2)
  subject_interpretation_note text,  -- REQ-AGENT-001 AC-3
  status research_status not null,   -- pending|running|complete|partial|failed
  current_version_id uuid null,
  created_at timestamptz not null, updated_at timestamptz not null,
  deleted_at timestamptz,            -- semantics pending OPEN-24
  constraint one_owner check (num_nonnulls(owner_user_id, anonymous_session_id) = 1)
)

research_versions(
  id uuid pk, session_id uuid not null references research_sessions(id),
  version_number int not null,
  status version_status not null,    -- building|complete|partial|failed
  created_at timestamptz not null, closed_at timestamptz,
  change_summary jsonb,              -- What's Changed (REQ-VER-006)
  previous_version_id uuid null references research_versions(id),
  unique (session_id, version_number)
)
```

**Evidence core**

```sql
sources(
  id uuid pk, version_id uuid not null references research_versions(id),
  url text, url_normalized text, identifier text,
  name text not null, publisher text,
  category source_category not null,      -- filing|financial|news|jobs|web|official|document
  authority_tier authority_tier not null, -- primary|secondary|lower   (OPEN-15 sets assignment)
  tier_rationale jsonb not null,          -- REQ-EVID-002 AC-3: inspectable
  retrieved_at timestamptz not null,      -- REQ-EVID-004
  published_at timestamptz,               -- distinct from retrieved_at (REQ-TOOL-007 AC-1)
  accessibility accessibility not null,   -- accessible|paywalled|blocked|failed
  upload_id uuid null references uploads(id),
  unique (version_id, url_normalized)
)

evidence(
  id uuid pk, version_id uuid not null, source_id uuid not null references sources(id),
  content text not null,             -- extracted statement
  excerpt text,                      -- verbatim span supporting it
  value_raw text, value_numeric numeric,
  unit text, currency char(3), scale numeric,       -- REQ-EVID-008
  value_normalized numeric,          -- non-destructive: raw retained
  normalization normalization_status not null,      -- normalized|non_comparable|not_applicable
  period_start date, period_end date, period_label text,  -- REQ-EVID-009
  is_estimate boolean not null default false,       -- REQ-TOOL-004 AC-3
  extracted_at timestamptz not null,
  metadata jsonb not null default '{}'
)
```

**Claims and conflicts**

```sql
claims(
  id uuid pk, version_id uuid not null, section_id uuid null references report_sections(id),
  text text not null,
  claim_type claim_type not null,    -- fact|analysis|forecast|uncertainty
  confidence text null,              -- null until Phase 2; scale pending OPEN-14
  confidence_inputs jsonb not null default '{}',  -- REQ-EVID-015 AC-3: tier, corroboration,
                                     -- conflict, recency. Empty until Phase 2.
  assumptions jsonb,                 -- REQ-SYNTH-009: required when type = forecast
  is_important boolean not null default false,
  created_at timestamptz not null,
  constraint forecast_needs_assumptions
    check (claim_type <> 'forecast' or assumptions is not null)
)

claim_evidence(
  claim_id uuid, evidence_id uuid, role evidence_role not null,  -- supporting|conflicting
  primary key (claim_id, evidence_id, role)
)

conflicts(
  id uuid pk, version_id uuid not null, claim_id uuid not null references claims(id),
  status conflict_status not null,        -- explained|unresolved  (REQ-EVID-014)
  explanation text,
  explanation_category conflict_cause,    -- period|definition|currency|estimate_vs_reported|
                                          -- methodology|staleness   (REQ-EVID-013 AC-1)
  tolerance_applied numeric               -- OPEN-16
)

conflict_evidence(conflict_id uuid, evidence_id uuid, label text, primary key (conflict_id, evidence_id))
```

**`claims` is a Phase 1 table, not Phase 2.** `REQ-EVID-017` is a Phase 1 requirement and its `AC-1` rejects "any **fact-type** claim lacking evidence linkage", which requires both the Claim entity and claim typing in Phase 1. The PRD was internally inconsistent here; `DEC-05` resolved it by moving `REQ-DATA-006`, `REQ-EVID-010`, `REQ-SYNTH-001` and three others into Phase 1, with claim confidence left in Phase 2. `N-08` in §14.2 records the closure.

Phase 1 populates `text`, `claim_type`, and the evidence links. `confidence` and `confidence_inputs` stay empty until `REQ-EVID-015` in Phase 2, which is why both are nullable or defaulted.

**Report**

```sql
report_sections(
  id uuid pk, version_id uuid not null,
  title text not null,
  body jsonb not null,               -- ordered blocks, each referencing claim ids
  ordering int not null,             -- REQ-SYNTH-005
  is_executive_summary boolean not null default false,
  unique (version_id, ordering)
)

visualizations(
  id uuid pk, version_id uuid not null, section_id uuid not null references report_sections(id),
  kind viz_kind not null,            -- line|bar|table|matrix|metric|comparison
  spec jsonb not null,               -- renderer-agnostic (OPEN-25 picks the renderer)
  ordering int not null
)

visualization_evidence(visualization_id uuid, evidence_id uuid, primary key (visualization_id, evidence_id))
```

`visualization_evidence` is what makes `REQ-VIZ-002 AC-2` enforceable: a visualization with no rows cannot be persisted, so a decorative chart is structurally impossible.

**Conversation, documents, exports, activity, observability**

```sql
conversation_messages(
  id uuid pk, session_id uuid not null, version_id uuid not null,
  seq int not null, role message_role not null,   -- user|agent
  content text not null, context_ref jsonb, created_at timestamptz not null,
  unique (session_id, seq)
)
message_evidence(message_id uuid, evidence_id uuid, primary key (message_id, evidence_id))

uploads(
  id uuid pk, session_id uuid not null,
  filename text not null, content_type text not null, size_bytes bigint not null,
  storage_key text not null, sha256 text not null,
  processing_state upload_state not null,   -- pending|processing|ready|failed
  error text, created_at timestamptz not null, deleted_at timestamptz
)
upload_chunks(
  id uuid pk, upload_id uuid not null, ordinal int not null,
  text text not null, locator jsonb not null,   -- page / sheet / cell range
  -- no embedding column: DEC-15 closed OPEN-12 against vector search, and
  -- migration 0004 adds a GIN index on to_tsvector('english', text) instead
)

exports(
  id uuid pk, version_id uuid not null,
  format export_format not null, theme export_theme not null,
  status export_status not null, storage_key text, error text,
  created_at timestamptz not null, completed_at timestamptz
)

activity_events(
  id uuid pk, session_id uuid not null, version_id uuid null,
  seq int not null, label text not null,          -- user-facing only (REQ-ACT-003)
  tool_category text, status activity_status not null,
  created_at timestamptz not null,
  unique (session_id, seq)
)

research_runs(
  id uuid pk, session_id uuid not null, version_id uuid not null,
  kind run_kind not null,                          -- initial|update|conversation
  status run_status not null,
  termination_reason termination_reason,           -- sufficiency|ceiling|failure (REQ-AGENT-005 AC-4)
  effort_used jsonb not null default '{}',
  tokens jsonb not null default '{}', cost_micros bigint,
  started_at timestamptz, finished_at timestamptz
)

run_steps(                                          -- the durable job state machine (§5.6)
  id uuid pk, run_id uuid not null references research_runs(id),
  stage text not null,                              -- stage name from §5.1
  ordinal int not null,
  status step_status not null,                      -- pending|running|complete|failed|dead
  attempt int not null default 0,
  checkpoint jsonb not null default '{}',           -- resume point, written before completion
  lease_owner text, lease_expires_at timestamptz,   -- claim via FOR UPDATE SKIP LOCKED
  error text,
  created_at timestamptz not null, updated_at timestamptz not null,
  unique (run_id, ordinal)
)

tool_invocations(                                   -- internal only, never rendered (REQ-OBS-007)
  id uuid pk, run_id uuid not null,
  tool_name text not null, tool_category text not null,
  status text not null, error_kind text, latency_ms int,
  request_digest jsonb, cost_micros bigint, created_at timestamptz not null
)
```

### 4.3 Indexes and access rules

- Every child table carries `version_id` and is indexed on it. Loading a workspace is a small number of `WHERE version_id = $1` reads.
- `research_sessions(owner_user_id, updated_at desc)` for history (`REQ-AUTH-005`).
- `activity_events(session_id, seq)` for incremental polling and stream resume.
- **Ownership is enforced in a repository layer, never in a route handler.** Every read repository takes an `OwnerContext` (user id *or* anonymous session id) and every query filters on it. `REQ-SEC-002 AC-2` requires server-side enforcement on every access; putting it in one place makes the cross-account test suite (`AC-3`) meaningful rather than a spot check.

---

## 5. Research-agent pipeline

### 5.1 Stages

One orchestrator, twelve stages, each a pure-ish function with an explicit input and output, each independently testable. No swarm (`PRD §11.3`).

| # | Stage | Output | Requirements |
|---|---|---|---|
| 1 | **Interpret** | subject + research questions | `REQ-AGENT-001` |
| 2 | **Plan** | areas, each with questions and candidate tool categories | `REQ-AGENT-002`, `REQ-AGENT-003` |
| 3 | **Retrieve** | raw tool results, per area, budget-governed | `REQ-TOOL-001..013` |
| 4 | **Extract** | evidence items with excerpts and provenance | `REQ-EVID-007`, `REQ-EVID-001` |
| 5 | **Normalize** | units, currency, scale, period | `REQ-EVID-008`, `REQ-EVID-009` |
| 6 | **Dedupe + tier** | unique sources, each with an authority tier | `REQ-EVID-006`, `REQ-EVID-002` |
| 7 | **Assess sufficiency** | continue or stop, per area | `REQ-AGENT-004`, `REQ-AGENT-005` |
| 8 | **Detect + explain conflict** | conflict records, explained or unresolved | `REQ-EVID-012..014` |
| 9 | **Score confidence** | confidence per claim, with inputs recorded | `REQ-EVID-015`, `REQ-EVID-016` |
| 10 | **Synthesize** | typed claims, dynamic sections, ordering | `REQ-SYNTH-001..005` |
| 11 | **Visualize** | chart specs bound to evidence | `REQ-VIZ-001..004` |
| 12 | **Validate** | pass, or reject the version | `REQ-EVID-017`, `REQ-SYNTH-006` |

Stages 3 through 7 form the research loop; the orchestrator re-enters at stage 3 for areas judged insufficient, subject to the effort ceiling.

### 5.2 The tool contract

The single most important interface in the backend. It is what makes `OPEN-05..09` late-binding.

```python
class ToolRequest:
    tool: str
    category: ToolCategory      # web_search | page_fetch | financial | filings
                                # | jobs | news | documents
    params: Mapping[str, Any]
    budget: ToolBudget          # timeout (TBD-02), max results

class ToolResult:               # success
    items: Sequence[ToolItem]   # each carries provenance, non-optional
    retrieved_at: datetime

class ToolItem:
    source_url: str | None
    source_identifier: str | None
    source_name: str
    source_category: SourceCategory
    retrieved_at: datetime
    accessibility: Accessibility
    content: Untrusted[str]     # see §9 — typed, cannot be used as an instruction
    published_at: datetime | None
    structured: Mapping[str, Any] | None

class ToolFailure:              # typed failure, never an exception across the boundary
    kind: Literal["timeout", "error", "paywalled", "blocked", "not_found", "rate_limited"]
    message: str                # internal; sanitized before reaching a user
```

Rules the contract enforces:

- A `ToolItem` missing required provenance is **rejected at construction**, satisfying `REQ-TOOL-012 AC-2` structurally rather than by review.
- `content` is `Untrusted[str]`. There is no code path that turns it into a plain `str` for instruction context (§9).
- Failures return, they do not raise (`REQ-TOOL-010 AC-1`), so one dead provider cannot abort unrelated areas (`REQ-AGENT-009 AC-1`).
- Registration is by category. **The orchestrator never names a provider.** `REQ-TOOL-009 AC-2` is verified by a test that greps the orchestrator package for provider names and fails if any appear.

`REQ-TOOL-009 AC-3` (demonstrate extensibility by adding a second tool in an existing category) is satisfied for free: each category ships with a real provider *and* a cassette-backed fake.

### 5.3 The LLM abstraction

`REQ-TECH-006`. One interface, provider selected by config (`OPEN-04`).

```python
class LLMProvider(Protocol):
    async def complete_structured(
        self,
        instruction: Trusted,       # system / task, trusted only
        untrusted: Sequence[UntrustedDocument],   # research material, fenced
        schema: type[BaseModel],
        model_tier: ModelTier,           # cheap | standard | deep
    ) -> StructuredResult: ...
```

Two things this buys:

1. **Structured output everywhere.** Every stage that uses the model returns a validated Pydantic model, never free prose that later needs parsing. Extraction returns evidence records; synthesis returns claims; conflict explanation returns a category plus text. Free prose appears only in section body text and conversational answers, and even those carry claim references.
2. **Tiering is explicit.** `OPEN-04` asks which model tier for which stage. The `ModelTier` enum makes that a config table rather than scattered decisions.

### 5.4 Termination — resolved by `DEC-04`

**`OPEN-13` is closed.** The decision is `DEC-04`, recorded in full at `docs/decisions/OPEN-13.md` and logged in `PRD.md §13.10`. This section states what plugs into the seam and what the code owes; the decision record is authoritative on the rule itself.

The interface is unchanged from the shape this plan originally specified.

```python
class SufficiencyVerdict:
    decision: Literal["continue", "sufficient", "ceiling_reached"]
    rationale: str                  # REQ-AGENT-005 AC-2: explicit, logged, inspectable
    area_id: str

class TerminationPolicy(Protocol):
    def assess(self, area: ResearchArea, budget: RunBudget) -> SufficiencyVerdict: ...
```

**The implementation is `CoverageGatePolicy`.** Sufficiency is question coverage, not a model call and not an iteration count:

| Element | `DEC-04` |
|---|---|
| **Sufficiency signal** | Every question stage 2 planned for the area is `resolved` (≥ `MIN_SOURCES_PER_QUESTION` distinct accessible sources yielding evidence, ≥1 above `lower` tier; one source suffices when it is `primary`) or explicitly `unanswerable`. Evaluated as a query over `sources` and `evidence`. |
| **Per-area ceiling** | Derived, not constant: rounds allocated from the area's unresolved question count, clamped to a floor and a cap. This is what makes `REQ-AGENT-004 AC-2` structural. |
| **No-progress rule** | A round adding zero new distinct sources ends the area with `ceiling_reached`, regardless of remaining allocation. **Mandatory**, and what makes `AC-1` hold without burning the full ceiling. |
| **Per-run ceiling** | `RunBudget` bounds cost, wall clock and tool calls simultaneously; the run ends when any one is exhausted. Areas draw from a shared pool against a reservation, and unspent reservation returns to the pool. |
| **Ceiling before sufficiency** | `ceiling_reached` is never collapsed into `sufficient`. `termination_reason = 'ceiling'`, open and unanswerable questions become `uncertainty` claims, the version completes as `partial`, and from Phase 2 confidence is lowered. |

What this changes structurally, and what it does not:

- **Stage 2's questions become persisted rows**, `version_id`-scoped like every other research artefact per §4.1. This is the one schema consequence; there is no other.
- `RunBudget` carries three counters rather than one.
- **The validation gate in §5.5 needs no change.** `uncertainty` claims carry no evidence-linkage obligation, so an unanswered question passes the gate honestly and can never pass as a fact.
- The sufficiency stage makes **no model call and no tool call**, so it adds nothing to `NFR-COST-001`.

**`ProvisionalFixedBudgetPolicy` is retired.** It was the interim placeholder and it stopped on budget alone, which `AC-3` forbids as a sole rule. The three markers that kept it visible are now discharged:

- Delete the class.
- Remove `pytest.mark.skip(reason="blocked on OPEN-13: no sufficiency signal defined")` from the termination conformance test, which becomes **required**. It is a pure unit test with no model in the loop: fixture the evidence rows, assert the verdict. `DEC-04 §10.3` lists the seven adversarial cases it must cover.
- Remove the CI assertion that `docs/decisions/OPEN-13.md` does not exist. That file now exists, which is what discharges the check.

**Deferred, behind the same protocol.** A `cheap`-tier model sufficiency judge is the V1.1 upgrade (`DEC-04 §9.1`). It was rejected for V1 on cost, on team size, and because its advantage depends on `OPEN-15` and `OPEN-16`, both Phase 2 — adopting it now would re-block Phase 1 on two further open questions. Because it satisfies the same `TerminationPolicy` protocol, adopting it later is a config change plus one class.

### 5.5 The validation gate

`REQ-EVID-017 AC-3` is unusually explicit: the constraint is enforced in the pipeline, not by model instruction. Stage 12 is a hard gate that **rejects a version** rather than logging a warning.

```
For each claim in the version:
  claim_type == fact       → must have ≥1 supporting evidence row       (REQ-EVID-017)
  claim_type == forecast   → must have non-empty assumptions            (REQ-SYNTH-009)
  every supporting evidence → source.accessibility == 'accessible'      (REQ-EVID-018)
For each important statement in section body:
  must resolve to a claim id                                            (REQ-SYNTH-006 AC-3)
For each visualization:
  must have ≥1 visualization_evidence row                               (REQ-VIZ-002)
For each conflict:
  status == 'explained' → explanation_category is not null              (REQ-EVID-013)
  status == 'unresolved' → linked claim confidence is reduced           (REQ-EVID-014 AC-3)
```

A gate failure is a generation defect. The run retries synthesis once, then completes as `partial` with the defect recorded. It never ships silently (`REQ-SYNTH-006 AC-3`).

### 5.6 Checkpointing — the `OPEN-03` shape

`NFR-REL-001` requires a run to survive a worker restart. `PRD §11.5` notes Vercel's execution limit may be exceeded and says the queue answer must state how.

**This plan proposes a shape that makes the answer a hosting choice rather than a redesign**, and flags the remaining decision as still open.

Each run is a **durable state machine persisted in Postgres**:

- `research_runs` holds the run; a `run_steps` table holds one row per stage attempt with `status`, `attempt`, `checkpoint jsonb`.
- Every step is **idempotent** and writes its output before marking itself complete.
- A worker claims the next runnable step with `SELECT ... FOR UPDATE SKIP LOCKED`, executes it, checkpoints, and releases.
- A step that dies mid-flight is reclaimed after a lease timeout and retried from its last checkpoint, not from the start.

**Lease and retry constants.** These three values are where a resumable job system either works or silently loops forever, so they are fixed here rather than left to the implementer. All are implementation-level and touch no `OPEN` question.

| Constant | Value | Reason |
|---|---|---|
| `STEP_LEASE_SECONDS` | 300 | Longer than the slowest single step (retrieval against `TBD-02` tool timeouts), short enough that a dead worker's step is reclaimed promptly. Re-read from config once `TBD-02` is set. |
| `STEP_MAX_ATTEMPTS` | 3 | Two retries covers transient provider and network failure. A third failure is a real defect, not bad luck. |
| Poison-step policy | On attempt 4, the step is marked `dead`, the run terminates with `termination_reason = 'failure'`, and the failure is recorded per `REQ-OBS-001`. | A step that cannot succeed must stop the run visibly rather than cycle. `REQ-AGENT-009 AC-3` forbids presenting the result as complete. |

A worker renews its lease while a step is in flight. A step whose lease expires while still running is reclaimed by another worker; idempotency is what makes that safe, which is why it is a requirement of every step rather than a nice-to-have.

Consequences worth being explicit about:

- Postgres is the durable source of truth for job state. Whatever queue `OPEN-03` selects is a **dispatch and concurrency mechanism**, not the system of record. A Redis-backed queue, a hosted queue, or plain polling all satisfy this.
- Because each *step* is bounded, a run that exceeds a serverless execution limit is not a failure mode: the step completes, the invocation ends, the next invocation picks up the next step. This is the "decompose research into resumable steps" option `PRD §11.5` names.
- **`OPEN-03` does not block Phase 0 or Phase 1.** Because Postgres holds job state, a single-process local runner that polls `run_steps` in a loop satisfies every requirement in this section. The queue is a concurrency and scaling concern, so the decision moves to Phase 8. The PRD lists `OPEN-03` as a Phase 1 blocker; this design is what removes it, and §14.1 records that.
- **Still open:** where workers execute in production, and the dispatch mechanism. `OPEN-03` is *narrowed* by this design, not closed. See §14 `N-04`.

---

## 6. API boundaries

FastAPI, versioned under `/v1`. Every route resolves an `OwnerContext` first (account or anonymous session) and passes it to repositories.

### 6.1 Routes

| Method | Path | Purpose | Requirements |
|---|---|---|---|
| `POST` | `/v1/research` | Create session, enqueue run. **202** with `{session_id, version_id}` | `REQ-INPUT-001..006`, `REQ-AGENT-008 AC-1` |
| `GET` | `/v1/research/{id}` | Session header, status, current version pointer | `REQ-WORK-002` |
| `GET` | `/v1/research/{id}/versions` | Version list with timestamps | `REQ-VER-008` |
| `GET` | `/v1/research/{id}/versions/{n}` | Full report payload for one version | `REQ-WORK-003..005` |
| `GET` | `/v1/research/{id}/activity` | SSE stream; `?after={seq}` for resume/poll | `REQ-ACT-001`, `REQ-ACT-005` |
| `GET` | `/v1/claims/{id}` | Inspection: evidence, sources, tiers, times, confidence, conflicts | `REQ-EVID-019`, `REQ-WORK-006` |
| `POST` | `/v1/research/{id}/messages` | Follow-up question. **202**, answer streamed | `REQ-CONV-001..008` |
| `GET` | `/v1/research/{id}/messages` | Conversation history | `REQ-CONV-007` |
| `POST` | `/v1/research/{id}/update` | Update Research → new version | `REQ-VER-001`, `REQ-VER-003..005` |
| `POST` | `/v1/research/{id}/uploads` | Request upload; returns presigned target | `REQ-DOC-002`, `REQ-SEC-005` |
| `POST` | `/v1/uploads/{id}/complete` | Mark uploaded, enqueue extraction | `REQ-DOC-003..004` |
| `GET` | `/v1/research/{id}/uploads` | List with processing state | `REQ-AUTH-008` |
| `DELETE` | `/v1/uploads/{id}` | Delete file and record | `REQ-AUTH-008`, `REQ-SEC-008` |
| `POST` | `/v1/research/{id}/versions/{n}/exports` | `{format, theme}` → **202** | `REQ-EXP-001..003`, `REQ-EXP-007` |
| `GET` | `/v1/exports/{id}` | Status; signed short-lived URL when ready | `REQ-EXP-008` |
| `DELETE` | `/v1/research/{id}` | Delete session and all children | `REQ-SEC-008` |
| `GET` | `/v1/me/research` | History | `REQ-AUTH-005` |
| `POST` | `/v1/auth/*` | Shape pending `OPEN-11` | `REQ-AUTH-003`, `REQ-AUTH-009` |
| `POST` | `/v1/research/claim` | Attach anonymous research to new account | `REQ-AUTH-004` |

### 6.2 Cross-cutting rules

- **Errors are sanitized at the boundary.** One envelope, one mapping from internal failure to public message. `REQ-INPUT-006 AC-4`, `REQ-SEC-010 AC-5`, `REQ-ACT-006 AC-2`.
- **Nothing mutates on GET**, so a version read is always safe to retry.
- **The report payload is denormalized for read.** `GET /versions/{n}` returns sections, claims, viz specs and a claim-id-keyed citation map in one response, so the workspace renders without a request waterfall (`NFR-PERF-004`).
- **Claim inspection is a separate endpoint** because the full evidence body per claim would bloat the report payload. `REQ-EVID-011 AC-2` allows one interaction, which a single fetch satisfies.
- **OpenAPI is generated and the TS client is generated from it.** A route change that breaks the frontend fails the build rather than production.

### 6.3 Activity transport

`REQ-ACT-001 AC-3` requires events to reach the client during research, and `NFR-PERF-003` caps the silent interval. The PRD names no mechanism, so this plan picks the one that costs nothing to change later.

**Phase 1 ships polling.** `GET /activity?after={seq}` returns events newer than a sequence number; the client polls on an interval comfortably inside `TBD-05`. This satisfies `AC-3` on any runtime, serverless included, and needs no answer to `N-02` first.

**SSE is a Phase 3 upgrade, not a Phase 1 dependency.** The same endpoint gains a streaming content type when the client asks for one. Because `activity_events.seq` is monotonic per session and already the resume key, switching transports changes no schema, no route, and no client state model.

This deliberately removes `N-03` from the Phase 1 critical path. It remains open for Phase 3, where the answer depends on `N-02`: long-lived SSE connections are constrained on Vercel serverless functions, and that constraint only matters once SSE is actually wanted.

---

## 7. Background jobs

### 7.1 Job types

| Job | Trigger | Steps |
|---|---|---|
| `research.initial` | `POST /research` | interpret → plan → loop(retrieve, extract, normalize, dedupe/tier, sufficiency) → conflict → confidence → synthesize → visualize → validate → close version |
| `research.update` | `POST /research/{id}/update` | prioritize changed areas → same loop with cache bypass → compare versions → What's Changed → close version |
| `research.conversation` | `POST /messages` | ground in existing evidence → if insufficient, run a scoped retrieve loop → answer with citations |
| `upload.process` | worker poll after `POST /uploads/{id}/complete` | claim → fetch → detect type → extract → chunk → mark ready, or `failed` with a reason |
| `export.generate` | `POST /exports` | load version → map to theme → render → store → mark ready |

### 7.2 Rules

- Every job step is idempotent and checkpointed (§5.6).
- Every job carries the ownership context of the research it processes and repositories enforce it, so a worker cannot touch research it does not own (`REQ-SEC-011`).
- Export failure is retryable **without re-running research** (`NFR-REL-003`), which the version/export split already gives.
- No job re-runs research on a timer. There is no scheduler in V1 (`REQ-VER-009`).

---

## 8. Authentication and persistence

**Pending `OPEN-11` (mechanism) and `OPEN-17` (anonymous session semantics).** Both block Phase 5, not Phase 1.

What can be built now regardless of the answers:

**`OPEN-17` splits in two, and only one half is a Phase 5 decision.** The `one_owner` check constraint in §4.2 means a research session cannot be inserted without an owner row, and in Phase 1 that owner is always anonymous. So the *existence* of an anonymous owner cannot wait for Phase 5, while its *semantics* genuinely can:

| Half | Content | Phase | Needs a decision? |
|---|---|---|---|
| **Identity** | An `anonymous_sessions` row: `id`, `token_hash`, `created_at`, `last_seen_at`. Enough to own a session and isolate it. | **0** | No. Nothing here is a product choice. |
| **Semantics** | Lifetime, expiry behaviour, deletion on expiry, and the eligibility window and mechanism for claiming. | **5** | Yes. This is what `OPEN-17` is actually asking. |

Phase 0 builds the identity half. `expires_at` and `claimed_*` exist as columns from the baseline migration but are unused and unenforced until Phase 5, so resolving `OPEN-17` is a behaviour change, not a migration.

- **`OwnerContext` from day one.** Every repository call takes one. In Phase 1 it is always an anonymous session; in Phase 5 it becomes a user or an anonymous session. No later retrofit of authorization into query paths.
- **Anonymous session carrier:** an opaque, high-entropy token in an `HttpOnly`, `Secure`, `SameSite=Lax` cookie, stored hashed. Lifetime, expiry and deletion behaviour are `OPEN-17` and are read from config, not hardcoded.
- **Claiming** (`REQ-AUTH-004`) is a single transactional operation that sets `owner_user_id`, clears `anonymous_session_id`, and records `claimed_at`. It refuses if the session is already owned or the caller does not hold the anonymous token (`AC-2`). Eligibility window is `OPEN-17`.

`REQ-SEC-016` is a standing constraint: no sharing surface, and no endpoint serves research to anyone but its owner.

---

## 9. Trust boundary and prompt-injection defense

`REQ-SEC-012..015` require the boundary be enforced **structurally, not by instruction wording** (`REQ-SEC-012 AC-1`). The mechanism:

**Two distinct types, no conversion function.**

```python
class Trusted(str): ...       # system policy, agent instructions, application logic
class Untrusted:              # every web page, search result, uploaded document
    text: str
    origin: SourceRef

    # Not a str subclass, and actively hostile to becoming one.
    def __str__(self) -> str:
        raise UntrustedContentError(
            "Untrusted content cannot be stringified. Pass it to "
            "LLMProvider.complete_structured(untrusted=...) or read .text explicitly."
        )
    __format__ = __str__      # kills f-strings and format() too
    __repr__ = ...            # safe: origin only, never the text
```

- `LLMProvider.complete_structured` takes `instruction: Trusted` and `untrusted: Sequence[UntrustedDocument]` as **separate parameters**. There is no signature that accepts untrusted text as instruction.
- Untrusted documents are rendered into the request inside per-document envelopes with generated delimiters, labelled as material to analyze, never as directives.
- Tool availability is fixed by application config at run start. Retrieved content cannot add, name, or reach a tool (`REQ-SEC-015 AC-1`).
- Page fetch refuses private, link-local and loopback addresses, and re-validates after every redirect (`REQ-SEC-015 AC-2`, SSRF).
- **Enforcement comes from the type, not from a linter.** An earlier draft promised a lint rule failing the build on any `str()` cast or f-string interpolation of an `Untrusted` value. Ruff and flake8 are not type-aware and cannot know what a variable holds, so that rule was not implementable and the most important invariant in the system would have been protected by nothing. Instead:
  - `__str__` and `__format__` raise, so `f"{content}"` and `str(content)` fail loudly at the first test that executes them.
  - `mypy --strict` runs over `packages/scrapr_core/security`, `llm`, and `tools`, where the signatures make a wrong call a type error.
  - Reading `.text` is the single deliberate escape hatch. It is greppable, which review can rely on, and it is the only path evidence extraction uses.
- `tests/adversarial/` holds a corpus of injection pages and documents and runs on every release (`REQ-SEC-014 AC-3`, `REQ-DOC-009 AC-3`).

---

## 10. Document handling

`REQ-DOC-001..010`, Phase 4.

1. **Upload** via presigned PUT direct to object storage. The API never proxies file bytes. Server-side enforcement of size, count and type (`REQ-DOC-010 AC-1`) happens at presign time and again at `complete`, since a presigned URL is not a limit.
2. **Extract** per format: PDF, DOCX, XLSX, CSV, TXT and MD (`DEC-14` sets the full list). Extraction failure marks the upload `failed` and it stays visible (`REQ-DOC-003 AC-3`); it never becomes a silently empty document.
3. **Chunk** with a locator (page, sheet, cell range) so a citation resolves to a place in the file, not to the file as a whole.
4. **Search** through the same tool contract as external sources (`REQ-TOOL-008`). `DEC-15` settled the retrieval method: Postgres full-text search, no vectors. The corpus is one user's ten files, which is where keyword recall is high. One caveat learned the hard way: the query is `"{subject}: {question}"`, so the terms must be **OR**ed and ranked. `plainto_tsquery` and `websearch_to_tsquery` both AND them, and a passage inside a reader's own document does not repeat the company name — an AND query finds nothing in almost every real file.
5. **Attribute separately.** Document-derived sources carry `category = 'document'` and a non-null `upload_id`. The workspace renders them visibly differently and they are **excluded from corroboration counting** in confidence scoring (`REQ-DOC-008 AC-3`).
6. **Untrusted, always** (§9). A document is evidence about the document, never a command.

---

## 11. Report generation and export

### 11.1 Content and presentation are separate

`REQ-EXP-004` requires this, and `PRD §14` names it as the mitigation for poor layouts. The split:

- **Content** lives in `report_sections.body` as ordered structured blocks referencing claim ids. It is renderer-agnostic.
- **Presentation** is a theme: a static definition of type, color, spacing and slide masters. Six themes (`REQ-EXP-003`), visual definitions pending `OPEN-22`.
- **Export = content × theme.** No model call happens during export. `REQ-EXP-005 AC-2` forbids new analysis at export time, which also makes export cheap and deterministic.

### 11.2 What exports must preserve

`REQ-EXP-009`: citations, the fact/analysis/forecast/uncertainty distinction, conflicts, and confidence. The distinction must not rely on color alone (`REQ-SYNTH-002 AC-3`, `NFR-USE-002`), so themes carry a non-color channel — label, rule treatment, or glyph — for claim type.

### 11.3 Chart rendering

`OPEN-25` is unresolved and has a constraint the PRD states plainly: charts must render **server-side** for export as well as client-side for the workspace. The `visualizations.spec` column is therefore deliberately renderer-agnostic, so `OPEN-25` picks a renderer without a schema migration.

The landing page proves out one option already: hand-authored inline SVG on the design system's palette, which renders identically in a browser and in a headless PDF pipeline and adds no runtime dependency.

### 11.4 Relationship to the landing design system

`design-system/MASTER.md` governs the marketing surface. The product surface **inherits its foundation and extends it**, because the two surfaces have genuinely different jobs.

**Inherited unchanged:** the Hurricane grey palette, the ochre accent, Plus Jakarta Sans plus Satoshi, the type scale, the 4/8 spacing scale, the radii and the two shadows. This is what stops the app reading as a different product from the page that sold it.

**Not inherited: MASTER.md's accent-frequency rule.** "Ochre appears at most twice per viewport" and "no third color" are correct constraints for a landing page and unworkable in the workspace. Count what the workspace has to render simultaneously: 4 claim types, 3 or more confidence levels (`OPEN-14`), 4 source accessibility states, 3 activity statuses, 2 conflict states, 4 upload states, 4 export states. A two-color system with a twice-per-viewport cap cannot carry that.

**The product adds a semantic token layer built on non-color channels.** `REQ-SYNTH-002 AC-3` and `NFR-USE-002` already forbid conveying claim type or confidence by color alone, so the constraint and the requirement point the same way: distinctions are carried by weight, rule treatment, glyph, and position, with the inherited palette doing the quieter work of grouping. Defining that layer is Phase 3 work alongside `REQ-WORK-002..009`.

The six **export** themes (`REQ-EXP-003`) are a third system, separate from both. `OPEN-22` defines them.

---

## 12. Deployment

| Piece | Target | Status |
|---|---|---|
| Next.js app | Vercel | **Decided** (`DEC-03`) |
| Application API (FastAPI) | Vercel | Decided in principle, **runtime shape unclear** — see `N-02` |
| Workers | Long-running container, off Vercel; host vendor a deployment choice | **Decided** (`DEC-26`) |
| PostgreSQL | Not decided | **Unregistered gap** — see `N-01` |
| Object storage | Cloudflare R2 | **Decided** (`DEC-12`); S3 API is the contract, MinIO locally |
| Queue / dispatch | Postgres, `FOR UPDATE SKIP LOCKED` | **Decided** (`DEC-26`) |
| Secrets | Environment config, validated at startup | `REQ-SEC-007 AC-3` |

Standing constraints: TLS only (`REQ-SEC-003`), encryption at rest for database, uploads and artifacts (`REQ-SEC-004`), artifacts never publicly enumerable and served only via short-lived signed URLs to the owner (`REQ-EXP-008`).

**Environments:** local (docker compose for Postgres and storage), preview per PR, production. Migrations run as a gated step, never automatically on boot, so two developers cannot race a schema change.

---

## 13. Phased roadmap

The eight phases are the PRD's, unchanged. **Phase 0 is added** and is scaffolding only, not product scope.

Each phase lists parallel tracks for A and B, because the point of this document is that two people can work at once without blocking each other.

### Phase 0 — Foundations *(not a PRD phase; scaffolding)*

Phase 0 proves the **seams**, not the research loop. A real orchestrator is Phase 1 work, so Phase 0's target is a walking skeleton: every architectural boundary exercised once, by the thinnest thing that can cross it.

| A | B |
|---|---|
| **Root git repository** (none exists today; only `web/` is a repo) | Move `web/` → `apps/web`, keep landing intact |
| Monorepo, `uv`, Postgres via docker compose | Finish remaining landing sections |
| **Full baseline schema**, not just an Alembic init: `anonymous_sessions`, `research_sessions`, `research_versions`, `sources`, `evidence`, `claims`, `claim_evidence`, `report_sections`, `activity_events`, `research_runs`, `run_steps` | OpenAPI → TS client generation wired |
| App-side UUIDv7 generation (§4) | Intake form posting to `POST /v1/research` |
| Tool contract + registry + **hand-authored fixture tools** | Workspace shell rendering a version payload |
| **Fake LLM provider** returning canned structured responses | Activity list polling `?after={seq}` |
| `Trusted`/`Untrusted` types with raising `__str__` (§9) | Design tokens shared between marketing and app surfaces |
| `OwnerContext` + anonymous session identity row (§8) | CI: build, Playwright smoke, Lighthouse budget |
| **Single-process local job runner** over `run_steps` (§5.6) | |
| Two-stage no-op pipeline + the validation gate | |
| CI: lint, `mypy --strict` on security/llm/tools, pytest, coverage gate | |

**Exit — the walking skeleton:**

> `POST /v1/research` writes a session and a version owned by an anonymous session. The local runner picks up `run_steps` and executes a two-stage no-op pipeline against a fixture tool and a fake LLM. One report section containing one evidence-backed, fact-typed claim persists, with its source and retrieval timestamp. The validation gate passes it. `GET /v1/research/{id}/versions/1` returns it and the workspace renders it, with the activity list showing the two stages.

Every seam in §1.1 is crossed once and nothing in it requires an unresolved decision.

**Why the exit is stated this narrowly.** An earlier draft asked Phase 0 for "a fake-backed research run end to end", which silently required most of Phase 1's orchestrator, a fake LLM, the full schema, `run_steps`, a job runner and `OwnerContext` — none of which were in the task list. Phase 0 is scaffolding; the research loop belongs in Phase 1.

This is also the phase that converts seven Phase-1 open questions from blockers into config.

### Phase 1 — Research Engine Foundation

**Blocked by:** nothing. `OPEN-13` is closed by `DEC-04`; `N-08` is closed by `DEC-05`; `OPEN-04..10` are absorbed by Phase 0's fixtures and fakes; `OPEN-03` is removed from the critical path by §5.6's local runner.

**A — one task per pipeline stage.** Each is a separate unit of work with a declared input type, output type and requirement set, because "orchestrator stages 1 through 7" is a phase, not a task.

| # | Task | In → Out | Requirements |
|---|---|---|---|
| 1.1 | Interpret | objective + context → subject + research questions | `REQ-AGENT-001` |
| 1.2 | Plan | questions → areas with candidate tool categories | `REQ-AGENT-002`, `REQ-AGENT-003` |
| 1.3 | Retrieve | area → `ToolResult`/`ToolFailure`, budget-governed | `REQ-TOOL-001..007`, `REQ-TOOL-010..013` |
| 1.4 | Extract | `Untrusted` content → evidence + source records | `REQ-EVID-001`, `REQ-EVID-004`, `REQ-EVID-007` |
| 1.5 | Sufficiency | area + budget → `SufficiencyVerdict` | `REQ-AGENT-004`, `REQ-AGENT-005` · `CoverageGatePolicy` per `DEC-04` |
| 1.5a | Persist planned questions | area → question rows with `resolution_state` | prerequisite of 1.5; the one schema consequence of `DEC-04` |
| 1.6 | Synthesize | evidence → typed claims, sections, ordering | `REQ-SYNTH-001`, `REQ-SYNTH-003..005` |
| 1.7 | Validate | version → pass or reject | `REQ-EVID-017`, `REQ-SYNTH-009` (see gate note below) |
| 1.8 | Partial-result handling | failures → degraded version, gaps named | `REQ-AGENT-009`, `REQ-TOOL-011` |
| 1.9 | Real tool implementations | one task **per tool**, each unblocked by its own `OPEN` | `REQ-TOOL-002..007` |
| 1.10 | Activity emission | stage transitions → `activity_events` | `REQ-ACT-001` |

1.1 through 1.8 can proceed against Phase 0's fixtures. 1.9 is six independent tasks that unblock one at a time as `OPEN-05..09` resolve, so no provider decision holds up the pipeline.

**The validation gate grows across phases.** §5.5 lists the full rule set; Phase 1 enforces only the subset whose requirements exist by Phase 1 — fact claims need evidence (`REQ-EVID-017`), forecasts need assumptions (`REQ-SYNTH-009`), no claim may cite an inaccessible source (`REQ-EVID-018`), and an unevidenced objective component is stated as an uncertainty (`REQ-SYNTH-010`). Conflict, confidence and visualization rules switch on in Phases 2 and 3 with their requirements. `DEC-05` is what makes those four requirements Phase 1 in the PRD; before it, this note named requirements the roster placed in Phase 2.

| B |
|---|
| Intake UI (`REQ-INPUT-001..007`) |
| Activity display over polling (`REQ-ACT-001`, §6.3) |
| Report render from real version payload |
| Partial-result and failure states |

**Exit (PRD):** one query reliably becomes a useful source-backed report.

### Phase 2 — Evidence & Trust
**Blocked by:** `OPEN-14`, `OPEN-15`, `OPEN-16`.

A: tiering, normalization, dedupe, conflict detection and explanation, confidence scoring, claim typing, `REQ-DATA-012` explainability.
B: begins claim-type and confidence rendering so A gets feedback on what is legible.

**Exit:** the agent can explain where information came from and identify disagreement.

### Phase 3 — Interactive Workspace
**Blocked by:** `OPEN-25`.

A: conversation grounding, follow-up research, viz selection from evidence.
B: full workspace, inline citation inspection, conflict display, activity timeline, conversation panel, chart and table rendering.

This is the heaviest B phase. **Recommend A picks up activity and viz-spec work so the phase does not become single-threaded.**

**Exit:** users can interrogate the research, not just read it.

### Phase 4 — Documents
**Blocked by:** nothing. `OPEN-10` closed by `DEC-12`, `OPEN-12` by `DEC-15`, `OPEN-19` by `DEC-13`, `OPEN-20` by `DEC-14`.

A: upload processing, extraction, chunking, document tool, contradiction detection, separate attribution.
B: upload UI, processing states, document citation treatment.

### Phase 5 — Accounts & Persistence
**Blocked by:** nothing. `OPEN-11` closed by `DEC-16`, `OPEN-17` by `DEC-17`, `OPEN-24` by `DEC-18` (`docs/decisions/OPEN-11-17-24.md`). `POST /v1/auth/*` in §6.1 is now `signup`, `signin`, `signout` and `GET /v1/auth/session`.

B: auth, account creation, claiming, history, deletion UI.
A: deletion semantics, cascade behaviour, cross-account isolation test suite (`REQ-SEC-002 AC-3`).

### Phase 6 — Updates
**Blocked by:** nothing. `OPEN-26` closed by `DEC-19`, `OPEN-27` by `DEC-20` (`docs/decisions/OPEN-26-27.md`). `research.update` in §7.1 is `prioritize → research → synthesize → compare`; version routes are `/research/[id]` (latest) and `/research/[id]/v/[n]`.

A: cache bypass, version comparison over normalized evidence, What's Changed, changed-conclusion explanation.
B: version navigation, What's Changed presentation.

### Phase 7 — Exports
**Blocked by:** nothing. `OPEN-21` closed by `DEC-21`, `OPEN-22` by `DEC-22` (`docs/decisions/OPEN-21-22.md`), `OPEN-25` by `DEC-11`. `export.generate` in §7.1 runs from the `exports` row itself, not `run_steps`; §6.1's export routes gain `GET .../exports` (list) and `POST /v1/exports/{id}/retry`.

B: six theme definitions, PDF and PPTX renderers, export UI.
A: export job pipeline, version binding, artifact storage and signed retrieval.

### Phase 8 — Hardening
**Blocked by:** nothing. `OPEN-18` closed by `DEC-23`, `OPEN-23` by `DEC-24`, `OPEN-29` by `DEC-25`, and `OPEN-03` by `DEC-26` (`docs/decisions/OPEN-18-23-29-03.md`). Every `TBD` is set; `scrapr-ops report` measures each against production runs.

Both: rate limiting, abuse controls, least privilege, injection hardening pass, full observability, and **setting every `TBD` value** — the PRD calls shipping with unset `TBD`s a release blocker.

---

## 14. Open questions

### 14.1 PRD questions that genuinely block, restated with what is needed

| ID | Needed to unblock | Phase |
|---|---|---|
| `OPEN-14`, `OPEN-15`, `OPEN-16` | Confidence scale; tier assignment method; per-metric-class conflict tolerance. All three are the trust core; none can be improvised per run. | 2 |
| `OPEN-25` | Chart renderer that works both client-side and headless server-side. | 3, 7 |
| `OPEN-17` | **Semantics half only:** lifetime, expiry, and claim eligibility window. The identity half is Phase 0 and needs no decision — see §8. | 5 |

**`OPEN-13` is closed.** `DEC-04` adopts a question-coverage gate; §5.4 records the consequences for the code and `docs/decisions/OPEN-13.md` is authoritative. Two things it deliberately left open, so they are not mistaken for settled: the numeric ceiling values stay with `OPEN-23` and `OPEN-29` as `TBD-04` and `TBD-10`, and `OPEN-15` still governs how `authority_tier` is assigned — the gate reads the column, which is `not null` at insert, so its accuracy improves when `OPEN-15` lands without a migration.

**`OPEN-03` is no longer a Phase 1 blocker.** The PRD lists it as one. §5.6 removes it: Postgres holds job state, so a single-process local runner satisfies every requirement through Phase 1, and the queue becomes a concurrency concern in Phase 8. What remains open is where workers execute in production and what dispatches them, which is `N-04`.

The remaining registered questions (`OPEN-04..12`, `18..24`, `26`, `27`, `29`) were absorbed by abstractions or fell in later phases. As of 2026-09-13 every one is closed by a decision record (`DEC-06` through `DEC-26`); `PRD.md §13` is the register. What stays open are the deployment gaps in §14.2 (`N-01`, `N-02`), which are provider choices with no code consequence.

### 14.2 New gaps this plan surfaced — not resolved here

| ID | Gap | Why it matters | Suggested owner |
|---|---|---|---|
| `N-01` | **PostgreSQL hosting is not registered anywhere.** `REQ-TECH-003` names the engine only, and the open-questions register has no entry for it. | Blocks Phase 1 deployment. Also determines connection pooling strategy, which matters a lot if the API is serverless. | Both |
| `N-02` | **"Application API" in `REQ-TECH-010` is ambiguous.** Does FastAPI run on Vercel's Python runtime, or do Next.js route handlers front a FastAPI service hosted elsewhere? | Changes repo layout, latency budget, pooling, and whether SSE is viable. | Both |
| `N-03` | **Activity transport is unspecified.** `REQ-ACT-001 AC-3` requires events during research but names no mechanism, and serverless constrains long-lived connections. | Determines whether SSE, WebSocket, or polling. Interacts with `N-02`. | B |
| ~~`N-04`~~ | **Resolved by `DEC-26`, 2026-09-13.** `OPEN-03` narrowed but not closed by §5.6. Recording this so the narrowing is not mistaken for a resolution. | — | A |
| ~~`N-05`~~ | ~~**The landing brief names Redis workers and S3-compatible storage.**~~ **Resolved 2026-09-09: they are shorthand, not decisions.** The brief is `websitedesign.md`, a landing-page build spec. Its `STACK` block also reads "AI: LLM APIs" and "Research: Web search + specialized data APIs + News APIs", which are indisputably category placeholders, and the two contested lines sit at the same altitude in the same list. The lines that *did* become decisions carry independent provenance (masterplan §19 plus `DEC-01`, `DEC-02`, `DEC-03`); Redis and S3 have none, and masterplan §19 says only "object storage" and "a queue/worker architecture". | Neither `OPEN` closes under either reading. "S3-compatible" names an API surface, not a vendor, so it cannot answer `OPEN-10`. "Redis" answers at most half of `OPEN-03` and says nothing about where workers execute, which §5.6 defers to Phase 8 regardless. `websitedesign.md` now carries a dated note so the shorthand stops propagating. | Both |
| `N-06` | **Correction, 2026-09-09: `websitedesign.md` is not empty.** This row previously read "empty (0 bytes)"; the file holds ~4.3 KB and was read in full while resolving `N-05`. The real gap is narrower: it is a **landing-page build brief**, not a design specification, and `design-system/MASTER.md` is the visual system derived from it. Nothing references the brief as a spec except this plan. | Downgraded from a content gap to a naming one. Either retitle it to what it is (a build brief) or fold its still-live constraints into `MASTER.md` and retire it. No longer blocks anything. | B |
| ~~`N-07`~~ | **Resolved by `DEC-19`, 2026-09-13: no evidence is reused across versions; the cache is run-scoped.** **Evidence reuse across versions is unspecified.** §4.1 version-scopes everything for immutability, which means Update Research re-fetches rather than reusing. `REQ-TOOL-013` wants reuse for cost; `REQ-VER-003` wants freshness. The boundary between them is `OPEN-26`'s territory but is not stated as such. | Directly sets Update Research cost (`TBD-11`). | A |
| ~~`N-08`~~ | ~~**The PRD contradicts itself on when claims exist.**~~ **Resolved by `DEC-05`**, 2026-09-09. | The fix was **six** requirements, not the three this row originally estimated. `REQ-SYNTH-009` and `REQ-EVID-018` are named by §12's Phase 1 gate note but were rostered Phase 2, and `REQ-SYNTH-010` is required by `DEC-04`'s ceiling disclosure. All six now sit in Phase 1; claim confidence remains Phase 2. See `PRD.md §13.10`. | Both |

### 14.3 Recommended resolution order

1. **`N-02`, `N-01`** — where the application API runs, and where Postgres runs. They set the deployment shape everything else assumes.

Three items have been removed from this list. `OPEN-13` was the last genuine Phase-1 blocker and `DEC-04` closes it. `N-08` was item 1, without which Phase 1 was unbuildable, and `DEC-05` closes it. `N-05` was the last item and is resolved as shorthand; see §14.2. `OPEN-03` was removed earlier; see §14.1. Everything else follows its phase.

**Nothing now blocks Phase 1 code.** `N-01` and `N-02` block Phase 1 *deployment*, not Phase 1 development, because §5.6's local runner and a local Postgres carry the pipeline through the phase.

---

## 15. Testing strategy

The research loop is nondeterministic, so the test strategy has to be deliberate or coverage numbers will be meaningless.

| Layer | Approach |
|---|---|
| **Tool layer** | **Hand-authored fixtures now, recorded cassettes once providers exist.** A cassette is a recording of a real provider, and `OPEN-05..09` are unresolved, so there is nothing to record yet. Fixtures are what Phase 0 actually needs and they double as the `REQ-TOOL-009 AC-3` extensibility proof. Each tool keeps both once its provider lands: the fixture for contract tests, the cassette for integration tests. |
| **Evidence layer** | Pure unit tests over fixed inputs. Normalization, dedupe, tiering, conflict detection and confidence are all deterministic functions and should be tested as such. This is where the 80% coverage bar earns its keep. |
| **Validation gate** | Adversarial fixtures: a fact claim with no evidence, a forecast with no assumptions, a claim citing a paywalled source, an unmapped statement. Each must be rejected. |
| **Pipeline** | End-to-end over fixtures plus the fake LLM, asserting a version is produced with the invariants intact. |
| **Security** | `tests/adversarial/` injection corpus on every release (`REQ-SEC-014 AC-3`). Cross-account access suite (`REQ-SEC-002 AC-3`). SSRF suite for page fetch. |
| **Frontend** | Playwright for the seven flows; visual regression at 375/768/1024/1440; reduced-motion and keyboard passes. |
| **Model-dependent output** | Not unit tested for exact text. Tested for *structure*: every claim typed, every fact evidenced, every forecast assumed. A small golden-objective eval set tracks quality drift across prompt and model changes. |

---

## 16. What this plan does not do

- It does not resolve a single `OPEN-xx`. `OPEN-13` was closed by `DEC-04` in `docs/decisions/OPEN-13.md`, a decision record outside this plan; §5.4 records the consequence, it did not make the call.
- It does not add, remove, or reinterpret a product requirement.
- It does not specify visual design for the product surface beyond inheriting the landing tokens, or for the six export themes (`OPEN-22`).
- It does not set any `TBD` numeric value.
- It does not commit to a provider for search, financial data, filings, jobs, news, AI, storage, database hosting, or queueing.
