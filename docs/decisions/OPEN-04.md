# `OPEN-04` — AI provider and model tiers

| | |
|---|---|
| **Decision ID** | `DEC-06` |
| **Closes** | `OPEN-04` |
| **Status** | Accepted |
| **Date** | 2026-09-12 |
| **Owner** | A |
| **Blocks released** | Phase 1 |
| **Affects** | `REQ-TECH-006`, `REQ-AGENT-001`, `REQ-EVID-004`, `REQ-SYNTH-001`, `NFR-COST-001`, implementation plan §5.3 |
| **Retires** | `ScriptedProvider` as the only usable provider |

---

## 1. Decision

**Anthropic** is the AI provider. The three `ModelTier` values already in
`scrapr_core.llm.contract` map to three models:

| Tier | Model | Input $/1M | Output $/1M | Context |
|---|---|---|---|---|
| `CHEAP` | `claude-haiku-4-5` | $1.00 | $5.00 | 200K |
| `STANDARD` | `claude-sonnet-5` | $2.00 | $10.00 | 1M |
| `DEEP` | `claude-opus-5` | $5.00 | $25.00 | 1M |

The mapping is **configuration, not code**. `REQ-TECH-006` requires the provider
sit behind an abstraction, and that abstraction already exists: the stages ask
for a tier, never a model. Changing any row is an environment change.

---

## 2. What this plugs into — unchanged

`LLMProvider.complete_structured(instruction: Trusted, untrusted:
Sequence[UntrustedDocument], schema, tier)` is adopted as written. This decision
supplies a body, not a shape. `FakeLLMProvider` and `ScriptedProvider` continue
to satisfy the same protocol and remain what the test suite runs against.

---

## 3. Why a tier is not a model name

The stages were written to declare **what the work is worth**, and that
judgement does not change when a model is released. Four stages currently name
a tier:

| Stage | Tier | Why |
|---|---|---|
| Interpret | `STANDARD` | Every later stage inherits from it, so a cheap misreading here is the most expensive mistake in the run |
| Plan | `STANDARD` | Grouping questions is judgement, but bounded judgement |
| Extract | `CHEAP` | High volume, low judgement, over text already in front of the model — and the grounding check catches what a larger model would be bought for |
| Synthesize | `STANDARD` | Where the report is written |

Sufficiency makes **no model call at all** (`DEC-04 §3.4`), which is why
termination costs nothing against `NFR-COST-001`.

**`DEEP` is unused in Phase 1, deliberately.** Nothing today asks for it. It is
mapped so that the tier is not a dangling enum value, and so that the first
stage that genuinely needs cross-area reasoning — contested-claim validation in
Phase 2 — can ask for it without a provider change. A tier nothing requests
costs nothing.

---

## 4. Where the money goes

Extraction is the only stage whose call count scales with retrieval: one call
per eight retrieved items, per question, per round. Interpret and plan run once
per run; synthesis runs once per version. So the run's cost is dominated by the
cheapest tier by design, and the expensive tiers are called a bounded number of
times.

That is the whole reason `CHEAP` exists as a separate tier rather than
everything running on one model. Putting extraction on `STANDARD` would roughly
double a run's model spend to improve a stage whose output is already verified
against the source text (`extract._ground`).

---

## 5. API shape this commits us to

Recorded here because these are not stable across model generations, and code
written against a stale prior fails at runtime rather than at type-check.

| Concern | What applies |
|---|---|
| Structured output | `output_config={"format": ...}` on `messages.create`, or `client.messages.parse()`. **Not** the deprecated `output_format` parameter. |
| Thinking — Sonnet 5, Opus 5 | `thinking={"type": "adaptive"}`. `budget_tokens` returns a 400 on both. |
| Thinking — Haiku 4.5 | Still takes `{"type": "enabled", "budget_tokens": N}`; `output_config.effort` **errors** on Haiku 4.5. |
| Effort | `output_config={"effort": ...}` on Sonnet 5 and Opus 5 only. |
| Assistant prefill | Removed — returns 400 on all three. Use structured outputs. |
| Prompt caching | The `INSTRUCTION` constant in each stage is a stable prefix and is where `cache_control` belongs. Untrusted material is volatile and goes after it. |

The caching row is not an optimisation note. Every stage already holds its
instruction in a module-level `Trusted` constant, which is exactly the shape
that caches well — the prefix is byte-identical across every run. Putting
anything per-run ahead of it would silently forfeit the discount.

---

## 6. The trust boundary is unchanged and non-negotiable

`complete_structured` takes instruction and untrusted material as **separate
parameters**, and this provider implements that split by placing material in
per-document envelopes labelled as content to analyse (§9). There is no
signature on the Anthropic client that would let retrieved text reach the
system prompt, and this implementation must not create one.

An Anthropic-specific temptation to name and refuse: the API accepts a
`system` parameter that takes arbitrary text. Nothing from `UntrustedDocument`
may ever be concatenated into it.

---

## 7. Alternatives considered

### 7.1 One model for every tier — rejected

Simpler, and defensible while the pipeline was fixtures. Rejected on cost: see
§4. It also makes the tier enum a lie, and a lie in an enum is worse than a
missing feature because later code trusts it.

### 7.2 Model choice per stage rather than per tier — rejected

Strictly more expressive. Rejected because it puts a model name in the
orchestrator, which is the thing `REQ-TECH-006` exists to prevent. The tier is
the indirection; adding a second one would not help.

### 7.3 Deferring `DEEP` until something needs it — rejected, narrowly

Leaving `DEEP` unmapped would have been honest about Phase 1. Rejected because
an unmapped tier fails at runtime on first use, and the first use will be in
Phase 2 code written by someone who reasonably assumed the enum worked.

---

## 8. Consequences

### 8.1 Code

- A new `AnthropicProvider` in `scrapr_core/llm/`, satisfying `LLMProvider`.
- The tier-to-model table is read from settings, with these values as defaults.
- `services/worker/main.py` stops raising in production, *provided* a key is
  configured. The absence of a key must still refuse to start: a provider that
  silently degrades to the stand-in would reintroduce exactly the failure this
  guard exists to prevent.

### 8.2 Testing

`FakeLLMProvider` remains what the suite runs against — the pipeline tests
assert structure, not text (§15). The Anthropic provider gets contract tests
over a recorded cassette, and is never called from the default test run.

### 8.3 Cost

`TBD-10` (cost ceiling per run) remains open and is Phase 8 measurement work.
`RUN_CEILING_COST_MICROS` stays at its placeholder. What changes is that the
counter now has real numbers to count, so Phase 8 has something to measure.

---

## 9. What remains open

- **`TBD-10` / `OPEN-29`** — the per-run cost ceiling. Unchanged.
- **Token accounting.** `TokenUsage` exists on the contract; wiring real usage
  into `research_runs.effort_used` is part of the implementation, not of this
  decision.
- **Refusal handling.** Anthropic may decline a request with
  `stop_reason: "refusal"`. For a research pipeline this is a tool-failure-shaped
  event, not a crash, and it should map onto the existing degraded-run path.
  Named here so it is not discovered in production.

---

## 10. Known risk

**A single provider is a single point of failure.** `REQ-TECH-006` mandated the
abstraction precisely so this decision would be reversible, and it is — but
reversible is not free. If Anthropic is unavailable, every stage stops, and the
run fails rather than degrades, because unlike a dead tool there is no other
provider registered to carry the work.
