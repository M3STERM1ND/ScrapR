# `OPEN-13` — Research sufficiency and termination

| | |
|---|---|
| **Decision ID** | `DEC-04` |
| **Closes** | `OPEN-13` |
| **Status** | Accepted |
| **Date** | 2026-09-09 |
| **Owner** | A |
| **Blocks released** | Phase 1 |
| **Affects** | `REQ-AGENT-004`, `REQ-AGENT-005`, `REQ-AGENT-009`, `REQ-SYNTH-010`, `NFR-COST-001`, `NFR-PERF-002`, implementation plan §5.4 |
| **Retires** | `ProvisionalFixedBudgetPolicy` |

---

## 1. Decision

ScrapR terminates research per area on a **question-coverage gate**: an area is sufficient when every question the agent planned for it has been resolved by evidence, or has been explicitly marked unanswerable. A **no-progress rule** is a mandatory secondary stop. Effort is bounded per area by an allocation derived from unresolved question count, and per run by a shared budget metered in cost, wall clock, and tool calls, whichever binds first.

The implementing class is **`CoverageGatePolicy`**, satisfying the `TerminationPolicy` protocol in implementation plan §5.4. No model call is made to decide sufficiency.

A model-judged sufficiency signal (**deferred**, §7.1) is a V1.1 upgrade behind the same protocol. It is not built for V1.

---

## 2. What this plugs into — unchanged

The interface specified in implementation plan §5.4 is adopted as written. This decision supplies the body, not the shape.

```python
class SufficiencyVerdict:
    decision: Literal["continue", "sufficient", "ceiling_reached"]
    rationale: str                  # REQ-AGENT-005 AC-2: explicit, logged, inspectable
    area_id: str

class TerminationPolicy(Protocol):
    def assess(self, area: ResearchArea, budget: RunBudget) -> SufficiencyVerdict: ...
```

---

## 3. The sufficiency signal

### 3.1 Questions become first-class

Stage 2 (**Plan**) already emits research areas "each with questions and candidate tool categories" (implementation plan §5.1). Those questions are **persisted** rather than held in memory, because the termination decision is defined over them and `REQ-AGENT-005 AC-2` requires the criteria to be inspectable after the fact.

Each question carries a `resolution_state` of `open`, `resolved`, or `unanswerable`.

### 3.2 When a question is resolved

A question is `resolved` when **both** hold:

1. At least `MIN_SOURCES_PER_QUESTION` **distinct** sources (distinct by `sources.url_normalized`) with `accessibility = 'accessible'` each yield at least one `evidence` row addressing it; and
2. at least one of those sources has `authority_tier` above `lower`.

The count drops to **one** source where that source's `authority_tier` is `primary`. A figure taken directly from a filing does not need a second-hand corroborator to be considered established.

### 3.3 When a question is unanswerable

A question is marked `unanswerable` when its candidate tool categories have been exhausted and the only returns were `ToolFailure` values, or the only sources found were `paywalled`, `blocked`, or `failed`. The failing kinds and the sources involved are recorded on the question.

`unanswerable` is a **terminal, honest state**, not a form of success. It resolves the area for termination purposes and produces an `uncertainty` claim in the report, exactly as a ceiling-terminated question does (§6).

### 3.4 The verdict

```
open_questions = questions where resolution_state = 'open'

if open_questions is empty                          → sufficient
else if no_progress_this_round                      → ceiling_reached   (§4.2)
else if area allocation exhausted                   → ceiling_reached
else if run budget exhausted                        → ceiling_reached
else                                                → continue
```

The evaluation is a query over `sources` and `evidence`. **It makes no model call and no tool call.**

### 3.5 The rationale string

`SufficiencyVerdict.rationale` is generated from the same query, not written by a model, so it cannot disagree with the decision it explains:

> `4 of 5 questions resolved; "FY2025 segment revenue" has 1 accessible source, all lower-tier.`

> `2 of 6 questions resolved; area allocation exhausted at 4 rounds; 3 open, 1 unanswerable (paywalled).`

---

## 4. Per-area effort ceiling

### 4.1 Allocation

An area's ceiling is **derived, never constant**. This is what satisfies `REQ-AGENT-004 AC-2` structurally rather than by hoping depth varies:

```
area_rounds = clamp(ceil(open_question_count / QUESTIONS_PER_ROUND),
                    AREA_ROUNDS_FLOOR,
                    AREA_ROUNDS_CAP)
```

A two-question area draws materially less than a seven-question area, within the same run. A narrow objective plans fewer questions across fewer areas and therefore consumes measurably less effort than a broad one, satisfying `REQ-AGENT-004 AC-1`.

`AREA_ROUNDS_CAP` is a **ceiling, not the stopping rule**. Reaching it produces `ceiling_reached`, never `sufficient`. This is the distinction `REQ-AGENT-004 AC-3` turns on.

### 4.2 The no-progress rule — mandatory

A retrieval round that adds **zero new distinct sources** to the area ends that area with `ceiling_reached`, regardless of remaining allocation.

This rail is not optional and is not a separate policy. It is what makes `REQ-AGENT-005 AC-1` (research terminates on every run) hold without waiting to burn the full ceiling, and it is the guard against an area whose queries have gone circular quietly consuming the run's budget.

---

## 5. Per-run effort ceiling

`RunBudget` carries three counters, decremented by every tool call and every model call:

| Counter | Bounds | Satisfies |
|---|---|---|
| `estimated_cost_usd` | total AI and API spend for the run | `NFR-COST-001` (`TBD-10`) |
| `wall_clock_seconds` | elapsed time since run start | `NFR-PERF-002` (`TBD-04`) |
| `tool_calls` | total calls across all categories | operational backstop |

**The run terminates when any one counter is exhausted.** Whichever binds first, binds.

**Allocation across areas.** Areas draw from one shared pool against a reservation sized by §4.1. Reservation unspent when an area completes early **returns to the pool**, so cheap areas fund expensive ones and a run does not leave budget unused while an area goes under-researched.

**Ordering.** Areas are assessed in plan order. A single area cannot consume more than its reservation plus whatever the pool has returned, which prevents the first area from starving the rest.

---

## 6. When a ceiling is hit before sufficiency

The behaviour is identical whether the ceiling was per-area, per-run, or the no-progress rule.

1. The verdict is `ceiling_reached`, never `sufficient`. The two are distinct values and are never collapsed.
2. `research_runs.termination_reason` is set to `ceiling` (`REQ-AGENT-005 AC-4`).
3. Every question still `open`, and every question marked `unanswerable`, produces an **`uncertainty`-type claim** naming what could not be established (`REQ-SYNTH-010 AC-1`). The gap is never filled with unevidenced speculation (`REQ-SYNTH-010 AC-2`).
4. The section states which areas were incompletely researched (`REQ-AGENT-009 AC-2`) and no section implies coverage the evidence does not support (`REQ-AGENT-009 AC-4`).
5. The version completes as `partial`, not `complete`.
6. From Phase 2, `termination_reason = 'ceiling'` is an input to `claims.confidence_inputs` and lowers confidence for claims in the affected area (`REQ-EVID-015 AC-3`, `REQ-AGENT-009 AC-4`).

**The validation gate in §5.5 needs no change.** `uncertainty` claims carry no evidence-linkage obligation, so an unanswered question passes the gate as an honest uncertainty and can never pass as a fact (`REQ-EVID-017 AC-2`).

**Total failure is not this path.** An area that produced no evidence at all still reports as a gap; a *run* that produced no evidence at all terminates with `termination_reason = 'failure'` and a clear error state, never an empty report presented as complete (`REQ-AGENT-009 AC-3`).

---

## 7. Parameters

All values are **configuration**, not constants in code, and are read through the same config layer as `STEP_LEASE_SECONDS`.

| Parameter | V1 value | Note |
|---|---|---|
| `MIN_SOURCES_PER_QUESTION` | `2` | Drops to `1` when the source's `authority_tier` is `primary`. |
| `TIER_FLOOR` | at least one source above `lower` | Reads `sources.authority_tier`, which is `not null` at insert. See §9.1. |
| `QUESTIONS_PER_ROUND` | `2` | Sets the slope of the per-area allocation in §4.1. |
| `AREA_ROUNDS_FLOOR` | `1` | Every planned area gets at least one retrieval round. |
| `AREA_ROUNDS_CAP` | `4` | A ceiling, never the stopping rule. |
| `NO_PROGRESS_ROUNDS` | `1` | One round adding zero new distinct sources ends the area. Mandatory. |
| `RUN_CEILING_COST_USD` | placeholder until `TBD-10` | Generous for Phase 1; set from measurement. |
| `RUN_CEILING_WALL_CLOCK` | placeholder until `TBD-04` | Generous for Phase 1; set from measurement. |
| `RUN_CEILING_TOOL_CALLS` | placeholder | Operational backstop. |

---

## 8. Acceptance criteria coverage

| Criterion | How this decision satisfies it |
|---|---|
| `REQ-AGENT-004 AC-1` | Narrow objectives plan fewer questions, so §4.1 allocates less effort. Verified by an eval comparing tool-call totals for a narrow and a broad objective. |
| `REQ-AGENT-004 AC-2` | §4.1 derives the per-area ceiling from that area's unresolved question count, so depth varies within one run by construction. |
| `REQ-AGENT-004 AC-3` | The stopping rule is coverage of a plan-derived question set. `AREA_ROUNDS_CAP` is a ceiling that yields `ceiling_reached`, never `sufficient`, so no fixed count is the sole rule. |
| `REQ-AGENT-005 AC-1` | §3.4 returns a terminal verdict in every branch, and §4.2 guarantees termination even when the budget is not exhausted. |
| `REQ-AGENT-005 AC-2` | §3.5 generates the rationale from the same query that produced the verdict, and persists it. Nothing is emergent. |
| `REQ-AGENT-005 AC-3` | §5 bounds the run on cost, time, and call count simultaneously. |
| `REQ-AGENT-005 AC-4` | §6.2 and §6.6 record `termination_reason` and feed it into confidence. |
| `REQ-AGENT-009 AC-2` `AC-4` | §6.3 and §6.4 name the gap in the report and forbid implied coverage. |
| `REQ-SYNTH-010 AC-1` `AC-2` | §6.3 emits `uncertainty` claims and forbids speculative fill. |

---

## 9. Alternatives considered and rejected

### 9.1 Model sufficiency judge — deferred to V1.1

A `cheap`-tier structured call, per area per round, receiving a digest of gathered evidence and ruling on sufficiency.

**Best research quality of the three.** It is the only candidate that can distinguish genuine corroboration from three copies of one wire story. Rejected for V1 on three grounds:

- **Cost.** It adds a model call per area per round on the largest cost lever in the product, roughly eighteen extra calls on a six-area, three-round run, landing directly against `NFR-COST-001`.
- **Sequencing.** Its advantage is judging source quality and corroboration, which depends on `OPEN-15` (tier assignment) and `OPEN-16` (conflict tolerance). Both are Phase 2. At Phase 1 the judge would reason without its best inputs, and adopting it would re-block Phase 1 on two further open questions.
- **Team size.** Non-deterministic verdicts need cassette-backed or tolerance-band tests and ongoing prompt maintenance. For a two-person team during Phase 1, that is a standing cost against a benefit that arrives later.

**It is deferred, not discarded.** It implements the same `TerminationPolicy` protocol, so adopting it in V1.1 is a config change plus one class, with the coverage gate remaining available as the fallback and as the test oracle.

### 9.2 Marginal-yield saturation — rejected

Stop when the rate of new information falls below a threshold for `k` consecutive rounds.

Cheap, deterministic, and the most naturally adaptive of the three. Rejected because **saturation is not sufficiency**. An area whose planned queries are poor saturates immediately, at zero coverage, and reports itself `sufficient`. That failure is silent, and a silent false-sufficient is precisely the false-complete report `NFR-REL-002` and `REQ-AGENT-009 AC-3` forbid. Its tuning also depends on distributions that do not exist until Phase 1 and Phase 3 runs produce them, which is `OPEN-23` territory.

**Its useful half is adopted.** The no-progress rule in §4.2 is saturation reduced to its one safe form: it may only trigger `ceiling_reached`, never `sufficient`, so it can end a stalled area without ever declaring an unresearched one complete.

---

## 10. Consequences

### 10.1 Code

| Change | Where |
|---|---|
| Delete `ProvisionalFixedBudgetPolicy` | orchestrator |
| Implement `CoverageGatePolicy` behind `TerminationPolicy` | orchestrator |
| Persist planned questions with `resolution_state` | schema, stage 2 |
| Add three counters to `RunBudget` | orchestrator |
| Remove `pytest.mark.skip(reason="blocked on OPEN-13: ...")` | termination conformance test |
| Remove the CI assertion that this file does not exist | CI config |

### 10.2 Schema

Stage 2's questions become persisted rows rather than in-memory objects. They are `version_id`-scoped like every other research artefact, per implementation plan §4.1's immutability rule.

### 10.3 Testing

The conformance test becomes required, and it is a **pure unit test with no model in the loop**: fixture `sources` and `evidence` rows, assert the `SufficiencyVerdict`. The adversarial cases that must be covered:

- an area with every question resolved → `sufficient`
- an area one source short on one question → `continue`
- an area whose only sources are `lower` tier → `continue`
- an area with a single `primary`-tier source per question → `sufficient`
- a round adding zero new distinct sources → `ceiling_reached`
- allocation exhausted with questions open → `ceiling_reached`, never `sufficient`
- an area with all questions `unanswerable` → `sufficient` for termination, `uncertainty` claims in the report, version `partial`

---

## 11. What remains open

- **`TBD-04` and `TBD-10`** are not set here. This decision fixes the *mechanism* and the *shape* of the ceiling, not its numeric values, which stay with `OPEN-23` and `OPEN-29` for Phase 8 measurement. Phase 1 ships with generous placeholders.
- **`OPEN-15`** still governs how `authority_tier` is *assigned*. The gate reads the column, which is `not null` at insert per implementation plan §4.2, so `TIER_FLOOR` works from Phase 1 and improves in accuracy when `OPEN-15` lands. No migration is involved.
- **`OPEN-14`** still governs the confidence scale. §6.6's confidence effect switches on in Phase 2 with it.

---

## 12. Known risk

**The gate is only as good as stage 2's question decomposition.** If planning emits vague or overlapping questions, coverage becomes a meaningless measure and the gate will happily declare a shallow area sufficient.

This puts real weight on `REQ-AGENT-002 AC-3` — that two materially different objectives produce materially different plans. **Treat that test as load-bearing for termination, not only for planning.** A regression in question quality is a termination defect before it is a planning defect, and the eval set should assert question count and specificity, not just plan difference.
