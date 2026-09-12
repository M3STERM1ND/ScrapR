# `OPEN-15` — Authority tier assignment

| | |
|---|---|
| **Decision ID** | `DEC-08` |
| **Closes** | `OPEN-15` |
| **Status** | Accepted |
| **Date** | 2026-09-12 |
| **Owner** | A |
| **Blocks released** | Phase 2 |
| **Affects** | `REQ-EVID-002`, `REQ-TOOL-005 AC-1`, `REQ-TOOL-006 AC-1`, `REQ-TOOL-007 AC-2`, `DEC-04 §3.2`, `DEC-09` |
| **Retires** | `DEFAULT_TIER = SECONDARY` in `db/repositories/evidence.py` |

---

## 1. Decision

A source's `authority_tier` is assigned by a **static, ordered rule table**
evaluated at insert. First matching rule wins. The rule that fired is recorded
in `tier_rationale`.

No model call. No heuristic scoring. No per-run variation.

`REQ-EVID-002 AC-3` requires assignment to be "deterministic and inspectable,
not per-run improvisation", and a table you can read top to bottom is the only
mechanism that is obviously both.

---

## 2. Why this one is load-bearing

Tier is not a display badge. Three things already read it:

1. **`DEC-04 §3.2` termination.** A question resolves on two distinct sources
   above `LOWER`, **or** on one `PRIMARY` alone. Tier therefore decides how much
   research a question costs.
2. **`DEC-09` confidence.** Tier is the first input to the confidence level a
   reader sees.
3. **`REQ-EVID-013` conflict explanation.** When two sources disagree, tier is
   how the report says which to believe.

Getting it wrong is not cosmetic in any of the three.

---

## 3. The rule table

Evaluated in order. First match wins.

| # | Rule | Tier |
|---|---|---|
| 1 | `source_category` is `FILING` | `PRIMARY` |
| 2 | `source_category` is `OFFICIAL` | `PRIMARY` |
| 3 | Host is or ends in a **government suffix** (`.gov`, `.gov.uk`, `.europa.eu`, …) | `PRIMARY` |
| 4 | Host matches the **research subject's own domain** | `PRIMARY` |
| 5 | Host is in the **curated publisher allowlist** | `SECONDARY` |
| 6 | Host is in the **curated data-provider allowlist** | `SECONDARY` |
| 7 | Everything else | `LOWER` |

Both allowlists are version-controlled data in the repository, reviewed like
code. They are lists of hosts, not patterns, and a host is matched on
registrable domain so that `www.` and locale subdomains do not need entries.

### 3.1 Rule 4 needs care

"The subject's own domain" is the one rule whose input comes from the run
rather than from the table. It is `PRIMARY` because a company's own statement
about itself is a primary source about its intentions and claims — and it is
emphatically **not** an impartial one about its performance.

That distinction belongs to conflict explanation (`REQ-EVID-013`), not to
tiering, and `DEC-10` treats company-sourced figures as reported rather than
independent. Tier answers "how close is this to the origin", not "how much
should I believe it".

The subject domain is resolved once, at plan time, from the interpretation's
subject and any `context_url` the user gave. It is recorded on the version, so
a re-run cannot silently retier existing evidence.

---

## 4. The default is `LOWER`, and that is the decision

Today every source is stamped `SECONDARY` by a placeholder. Moving the default
to `LOWER` is the single largest behavioural change in this record, so it is
stated plainly rather than buried in a table row.

**Consequence:** two unknown-domain sources no longer resolve a question. By
`DEC-04 §3.2`, resolution needs `above_lower >= 1`, so a question answered only
by sources nobody vouched for stays open, and — if nothing better is found —
becomes an honest uncertainty in the report.

**Why that is right.** The alternative is that any two pages agreeing with each
other resolve a question. The trust core exists to stop exactly that, and a
default of `SECONDARY` means "unknown" and "established publisher" are the same
thing to every consumer listed in §2. That is not a conservative default; it is
a claim about sources we have never seen.

**Why it is risky.** Runs will resolve fewer questions and produce more
uncertainties, especially on subjects with thin coverage. The failure mode is a
report that reads as weaker than the research actually was.

**What bounds the risk.** The allowlist is data, extending it is a reviewed
one-line change, and the no-progress rule already stops an area from grinding
against sources that will never qualify. An unresolved question costs one
uncertainty claim, not a failed run.

---

## 5. What this deliberately does not do

### 5.1 No model judgement — rejected

A model could plausibly tier an unfamiliar source. Rejected on `AC-3`: the same
source could be tiered differently in two runs, which makes both the
termination decision and the confidence level irreproducible, and makes "why
did this claim get Moderate" unanswerable.

### 5.2 No heuristic scoring — rejected

Domain age, TLS, inbound links, presence of a masthead. Each is defensible and
the combination is unauditable. When a user asks why a source was demoted, "it
scored 0.41" is not an answer, and `REQ-DATA-012` explainability is a Phase 2
requirement this would actively fight.

### 5.3 No per-tool tier override — rejected, with one exception

Tools do not get to declare their own tier, or the table stops being the
authority. The exception is `source_category`, which the tool does set, and
which rules 1 and 2 read — a filing from the filings tool is `PRIMARY` because
of what it is, not because the tool asserted it.

---

## 6. Acceptance criteria coverage

| Criterion | How |
|---|---|
| `REQ-EVID-002 AC-3` deterministic and inspectable | An ordered table of literals, plus `tier_rationale` naming the rule that fired |
| `REQ-TOOL-005 AC-1` filings are primary | Rule 1 |
| `REQ-TOOL-006 AC-1` official postings are primary | Rule 4, via the subject domain |
| `REQ-TOOL-007 AC-2` established news is high-quality secondary | Rule 5 |

---

## 7. Consequences

### 7.1 Code

- A `tiering` module holding the table and the two allowlists.
- `EvidenceRepository.record` stops using `DEFAULT_TIER` and calls it.
- `tier_rationale` changes from a fixed placeholder to the rule that fired.

### 7.2 Schema

**None.** `authority_tier` is already `not null` at insert and
`tier_rationale` already exists. `DEC-04 §14` anticipated this exact
decision landing without a migration, and it does.

### 7.3 Existing data

Versions are immutable, so evidence already written keeps the tier it was given.
No backfill. A re-run produces a new version, which is where the new tiers
appear — which is the versioning model working as designed rather than a
limitation.

### 7.4 Testing

The table is a pure function and tested as one. The cases that matter are the
boundaries: a subdomain of an allowlisted host, a lookalike domain that must
**not** match, a government suffix that is not `.gov`, and the subject's own
domain against a subject with no domain at all.

---

## 8. What remains open

- **Allowlist membership** is content, not architecture. It will be wrong at
  first and is meant to be edited.
- **Non-US government suffixes** are an open-ended set. The table starts with
  the common ones and grows on evidence.
- **`REQ-EVID-013` conflict explanation** reads tier but is not specified here.

---

## 9. Known risk

**An allowlist encodes whoever wrote it.** A curated list of established
publishers is a judgement about legitimacy, and it will under-represent
non-English and regional sources first. The mitigation is that it is visible,
diffable and reviewable, which is more than any scoring heuristic would offer —
but visibility is not neutrality, and this list should be read as a project
artefact rather than as a fact about the world.
