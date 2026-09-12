# `OPEN-14` — The confidence scale

| | |
|---|---|
| **Decision ID** | `DEC-09` |
| **Closes** | `OPEN-14` |
| **Status** | Accepted |
| **Date** | 2026-09-12 |
| **Owner** | A |
| **Blocks released** | Phase 2 |
| **Affects** | `REQ-EVID-015`, `REQ-EVID-016`, `REQ-SYNTH-002`, `REQ-DATA-012`, `DEC-05` |
| **Depends on** | `DEC-08` (tier), `DEC-10` (conflict) |

---

## 1. Decision

Confidence is **three discrete levels — `HIGH`, `MODERATE`, `LOW`** — attached
to a claim, computed by a pure function of four inputs:

1. the **authority tier** of the sources behind it (`DEC-08`)
2. the number of **distinct corroborating sources**
3. whether an **unresolved conflict** touches it (`DEC-10`)
4. whether its evidence is **stale** for its metric class

No model call. The value is derived from the same rows a reader can click
through to, which is what makes `REQ-DATA-012` explainability achievable rather
than aspirational.

---

## 2. Discrete, not numeric

The masterplan already uses "High" and "Moderate" in its examples, so the words
are not invented here. The question `OPEN-14` actually asks is whether they
stand for a scale or for buckets of a number.

**Buckets of a number, and the number is not shown.** Three reasons:

- **The inputs do not support precision.** Tier is one of three values,
  corroboration is a small integer, conflict is a boolean. A score of `0.73`
  computed from those is arithmetic theatre — it implies a resolution the
  underlying data does not have.
- **A number invites comparison it cannot support.** Users will read `0.73`
  against `0.71` as meaningful. They are not.
- **Three levels are what a reader can act on.** Trust it, check it, treat it
  as a lead.

---

## 3. The levels

| Level | Meaning to a reader |
|---|---|
| `HIGH` | Well-sourced and uncontested. Act on it. |
| `MODERATE` | Sourced, but thinly or with a caveat. Check before acting. |
| `LOW` | Weakly sourced or contested. A lead, not a finding. |

---

## 4. Computation

A base level from sourcing, then demotions. Demotions only ever lower.

### 4.1 Base, from tier and corroboration

| Condition | Base |
|---|---|
| At least one `PRIMARY` source | `HIGH` |
| At least two distinct sources, at least one above `LOWER` | `HIGH` |
| Exactly one source above `LOWER` | `MODERATE` |
| Only `LOWER`-tier sources, two or more distinct | `MODERATE` |
| Only `LOWER`-tier sources, one | `LOW` |

The first two rows are deliberately the same shape as `DEC-04 §3.2`'s
resolution rule. A question that resolved and a claim that is well-sourced
should not be able to disagree — if they did, the report would show a `LOW`
claim about a question the run declared answered, and neither number would mean
anything.

### 4.2 Demotions

| Trigger | Effect |
|---|---|
| An **unresolved conflict** touches the claim (`DEC-10`) | Cap at `LOW` |
| A conflict exists but is **explained** (`REQ-EVID-013`) | One level down |
| Evidence is **stale** for its metric class (`DEC-10 §5`) | One level down |
| Any cited source is **not** `ACCESSIBLE` | Cap at `LOW` |

The conflict cap is the sharp one. A fact two credible sources disagree about
is not a high-confidence fact however good those sources are — the disagreement
*is* the finding, and `REQ-EVID-013` is where it gets explained.

### 4.3 Every claim type, because every claim carries one

`REQ-EVID-015` is unambiguous: *"Every claim MUST carry a confidence level"*,
and `AC-1` requires it displayed wherever the claim appears. So there is no
exempt type. What varies is the ceiling.

| Type | Confidence |
|---|---|
| `FACT` | Computed per §4.1–4.2 |
| `ANALYSIS` | Computed from the evidence it cites, capped at `MODERATE` |
| `FORECAST` | Computed from the evidence its assumptions rest on, capped at `MODERATE` |
| `UNCERTAINTY` | Always `LOW` |

The two caps exist because neither an analysis nor a forecast can be better
established than the facts under it. `HIGH` on an interpretation would tell the
reader the wrong thing about what kind of statement they are reading.

`UNCERTAINTY` is fixed at `LOW` rather than computed, and the reading is
deliberate: the level describes **how well established the claim's content is**,
and an uncertainty claim's content is something the evidence does not settle.
Reading it instead as "how sure are we that this is unknown" inverts the scale
and puts `HIGH` next to every gap in the report.

An earlier draft of this record exempted `FORECAST` and `UNCERTAINTY` from
carrying confidence at all. That contradicted `REQ-EVID-015` and is recorded
here because the exemption is the intuitive move and someone will propose it
again.

---

## 5. Not a model call, and not negotiable

Same reasoning as `DEC-04 §3.5` for the sufficiency rationale: a value the
model writes can disagree with the evidence it is derived from, and the
disagreement is invisible. A pure function cannot.

This also makes the explanation free. "Moderate — one secondary source, no
corroboration" is generated from the same inputs as the level, so it cannot
drift from it.

---

## 6. Acceptance criteria coverage

| Criterion | How |
|---|---|
| `REQ-EVID-015 AC-1` every claim carries one, displayed where it appears | §4.3 — no exempt type; rendering is Phase 3 |
| `REQ-EVID-015 AC-2` the scale is defined and used consistently | Three levels, one function, one call site |
| `REQ-EVID-015 AC-3` derived from tier, corroboration, conflict, recency | Those are exactly the four inputs in §1 |
| `REQ-EVID-016 AC-1` a primary source outranks a lower-tier one | §4.1 rows one and five |
| `REQ-EVID-016 AC-2` the relationship is consistent across runs | A pure function of persisted rows — §5 |
| `REQ-DATA-012` explainability | The inputs are rows the reader can open; the sentence is generated from them |
| `DEC-05` claim confidence is Phase 2 | This record is Phase 2 and changes nothing in Phase 1 |

---

## 7. Alternatives considered

### 7.1 A 0–1 numeric score — rejected

See §2. It would also make `OPEN-16`'s tolerances and this scale share a
false precision, compounding the problem.

### 7.2 Five levels — rejected

`HIGH / MEDIUM-HIGH / MEDIUM / MEDIUM-LOW / LOW` distinguishes cases the inputs
cannot distinguish. With three inputs of three, ~four and two values, five
output levels means inventing boundaries.

### 7.3 Model-judged confidence — rejected

See §5.

### 7.4 Confidence on evidence rather than on claims — rejected

Evidence is a quotation; it is either accurately extracted or it is not, and
`extract._ground` already enforces that. What a reader needs calibrating is the
**claim**, which is where several pieces of evidence combine.

---

## 8. Consequences

### 8.1 Code

A `confidence` module: a `Confidence` enum, a pure `assess()` over the four
inputs, and a rationale generator. Called during synthesis, after conflict
detection has run — the ordering matters, because §4.2 reads conflict state.

### 8.2 Schema

A `confidence` column on `claims`, **not null** — `REQ-EVID-015` admits no
exempt type, so nullable would permit exactly the state the requirement
forbids — plus a `confidence_rationale` JSON column matching the
`tier_rationale` pattern. **This needs a migration.**

### 8.3 Testing

A table test per row of §4.1 and §4.2, and one property worth asserting
directly: no demotion ever raises a level.

---

## 9. What remains open

- **Staleness windows** are defined per metric class in `DEC-10 §5`.
- **`REQ-EVID-013` conflict explanation** is consumed here and specified there.
- **Rendering.** Whether `LOW` is a badge, a colour or a sentence is Phase 3.

---

## 10. Known risk

**Three levels will feel coarse on the boundary cases**, and the boundary is
where the inputs are weakest. A claim with two secondary sources and one with
five both read `HIGH`, and the second is meaningfully better established. The
alternative — a finer scale — makes that boundary blurrier rather than sharper,
because the extra levels would be drawn where the data is thinnest. Chosen
deliberately, but it is a real loss of information.
