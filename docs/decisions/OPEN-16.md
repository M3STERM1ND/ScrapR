# `OPEN-16` — Conflict tolerance

| | |
|---|---|
| **Decision ID** | `DEC-10` |
| **Closes** | `OPEN-16` |
| **Status** | Accepted |
| **Date** | 2026-09-12 |
| **Owner** | A |
| **Blocks released** | Phase 2 |
| **Affects** | `REQ-EVID-012`, `REQ-EVID-013`, `REQ-EVID-008`, `REQ-EVID-009`, `REQ-TOOL-004 AC-3`, `DEC-09` |

---

## 1. Decision

Two values conflict when they are **comparable** and differ by more than the
**tolerance for their metric class**. Tolerances live in a version-controlled
table. The default for an unclassified metric is **1.0% relative**.

Comparability is a precondition, not a tolerance: values that cannot be brought
to the same unit, currency and reporting period are `NON_COMPARABLE`, which is
a different outcome from agreement and from conflict.

---

## 2. The thing this gets wrong if it is one number

`OPEN-16` states the problem exactly: *"A 0.4% difference in a revenue figure is
probably rounding; a 12% difference is probably a real conflict."* The register
also names the failure on both sides — tuned too tight and the product reports
rounding as disagreement; too loose and it misses real disagreement. Both
destroy the trust core, which is the whole point of Phase 2.

What makes a single number impossible is that the classes are not on the same
footing:

- **Revenue** differs by rounding and presentation. `$1.2bn` and `$1,198m` are
  the same figure. Relative tolerance.
- **Margin** is already a percentage. A 1% *relative* tolerance on a 2% margin
  is 0.02 points — absurdly tight. Absolute tolerance, in points.
- **Headcount** genuinely changes between two true reporting dates. Wider
  relative tolerance.
- **Share price** moves intraday. Two correct sources hours apart disagree.

So: per class, and the unit of the tolerance varies with the class.

---

## 3. The table

| Metric class | Tolerance | Why |
|---|---|---|
| Currency magnitude — revenue, cost, cash, market cap | **1.0% relative** | Covers rounding and unit presentation; `$1.2bn` vs `$1,198m` is 0.17% |
| Ratio or rate — margin, growth %, yield | **0.5 percentage points absolute** | Relative tolerance is meaningless on a small base |
| Count — headcount, postings, locations | **5.0% relative** | Genuinely fluctuates between true reporting dates |
| Share price | **2.0% relative** | Intraday movement between two correct retrievals |
| Date or period | **Exact match on period** | Q3 and Q4 are not a near-miss; they are different facts |
| Unclassified | **1.0% relative** | The currency default, as the most common shape |

Values are compared **after** normalization (`REQ-EVID-008`). Tolerance is the
last step, not the first.

---

## 4. Three things that are not conflicts

Each is a false positive that would otherwise flood the report with
disagreements that are not disagreements.

### 4.1 Different reporting periods

`REQ-EVID-009` requires a reporting period on financial values precisely so this
can be checked. FY2024 revenue and FY2025 revenue are two facts, not a
conflict. Comparison is scoped within a period; across periods it does not
happen.

### 4.2 An estimate against a reported figure

`REQ-TOOL-004 AC-3` requires estimates to be distinguishable from reported
values, and this is what that requirement is for. An analyst estimate of $1.3bn
against a reported $1.2bn is not a source being wrong — it is an estimate. It
is recorded, and it is surfaced as an estimate, but it does not raise a
conflict and does not demote confidence under `DEC-09 §4.2`.

Two *reported* values that disagree, or two estimates that disagree, are in
scope.

### 4.3 Non-comparable values

A figure with no currency, no period, or a unit that cannot be resolved is
`NON_COMPARABLE`. `REQ-EVID-008`'s normalization is non-destructive and records
this as a fact about the data. Treating it as agreement would hide a gap;
treating it as conflict would invent one.

---

## 5. Staleness, which `DEC-09` reads

Recency is per class for the same reason tolerance is. These windows are what
`DEC-09 §4.2` calls stale:

| Class | Stale after |
|---|---|
| Share price | 1 trading day |
| Count — headcount, postings | 90 days |
| Currency magnitude, ratio | One reporting period past its period end |
| News-derived claim | 180 days |

Staleness demotes confidence one level. It never creates a conflict on its own:
an old figure and a new figure that differ are usually both correct, and saying
so is what `REQ-EVID-013` explanation is for.

---

## 6. What a conflict produces

Detection only. The `conflicts` and `conflict_evidence` tables already exist in
the baseline schema. A detected conflict:

1. writes a `conflicts` row linking the disagreeing evidence,
2. caps the confidence of every claim that cites it (`DEC-09 §4.2`),
3. is explained by `REQ-EVID-013`, which is a separate piece of work.

Explanation reads tier (`DEC-08`) and recency to say which value to prefer.
This record supplies the trigger, not the sentence.

---

## 7. Alternatives considered

### 7.1 One global tolerance — rejected

See §2. It cannot be right for both margins and headcount.

### 7.2 Model-judged conflict — rejected

Same reasoning as `DEC-04 §3.5` and `DEC-09 §5`. It would also be
irreproducible in exactly the place users are most likely to challenge the
output, and `REQ-EVID-013` requires the disagreement be explainable.

### 7.3 Statistical outlier detection — rejected

Attractive with many sources per metric; useless with the two or three a real
run produces. Needs a distribution that does not exist.

### 7.4 Per-metric rather than per-class tolerance — rejected for V1

Strictly more accurate and unboundedly large. Classes are the level at which
the rule can be written down and reviewed. A specific metric that misbehaves
can get its own class.

---

## 8. Acceptance criteria coverage

| Criterion | How |
|---|---|
| `REQ-EVID-012 AC-4` tolerance defined and configurable | §3, version-controlled, per class |
| `REQ-EVID-009` reporting period captured | Precondition in §4.1 |
| `REQ-EVID-008` normalization before comparison | §3 closing line; `NON_COMPARABLE` in §4.3 |
| `REQ-TOOL-004 AC-3` estimates distinguishable | §4.2 |

---

## 9. Consequences

### 9.1 Code

A `conflict` module: metric-class inference, a comparison returning
`agrees | conflicts | non_comparable`, and the tolerance table. Runs after
normalization and dedupe, before synthesis — `DEC-09 §4.2` needs conflict state
to exist by the time confidence is computed.

### 9.2 Schema

**None for detection.** `conflicts` and `conflict_evidence` are in the baseline.
Metric class needs somewhere to live on evidence, which may be a migration
depending on how normalization stores its output — resolved when that lands,
not here.

### 9.3 Testing

The adversarial cases are the specification: `$1.2bn` vs `$1,198m` (agree),
2.0% vs 2.4% margin (conflict — 0.4 points, under a relative rule it would
pass), 4,000 vs 4,150 headcount (agree), FY2024 vs FY2025 (not compared),
estimate vs reported (not a conflict), and a value with no currency
(`NON_COMPARABLE`).

---

## 10. What remains open

- **Metric-class inference** — how a value is assigned a class. Part of
  normalization, and it is the input this whole table depends on.
- **`REQ-EVID-013`** conflict explanation.
- **Three-way disagreement.** The table is pairwise. Whether three values
  spanning a range are one conflict or three is left to the explanation work.

---

## 11. Known risk

**Every number here is a guess that looks like a measurement.** 1.0%, 0.5
points, 5.0%, 2.0% — none is derived from data about how often real sources
disagree, because that data does not exist yet. They are plausible starting
points chosen so the table can be written down and argued with.

The honest expectation is that at least one is wrong by enough to matter, and
the first real runs against live providers are what will show which. They are
configuration for exactly that reason. This risk is the same shape as
`DEC-04 §12`'s: a tolerance defect will present as a research-quality problem,
and it should be suspected early rather than late.
