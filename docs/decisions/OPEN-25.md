# `OPEN-25` — Chart rendering, and the product's semantic token layer

| | |
|---|---|
| **Decision ID** | `DEC-11` |
| **Closes** | `OPEN-25` |
| **Status** | Accepted |
| **Date** | 2026-09-12 |
| **Owner** | B |
| **Blocks released** | Phase 3; unblocks Phase 7 export rendering |
| **Affects** | `REQ-VIZ-001..006`, `REQ-EXP-009`, `REQ-SYNTH-002 AC-3`, `NFR-USE-002`, implementation plan §11.3, §11.4 |

---

## 1. Decision

**Charts are hand-authored inline SVG**, generated from a renderer-agnostic
spec, on the design system's palette. No charting library.

The same decision record also defines the **semantic token layer** for the
product surface, because implementation plan §11.4 assigns that to Phase 3 and
the two are the same problem: how the workspace carries meaning that a
two-colour marketing palette was never asked to.

---

## 2. Why a library is the wrong answer here

`OPEN-25` asks two questions and the second one is the constraint that decides
it: *how are charts rendered **server-side** for export?*

A chart in this product has to render twice — in the workspace, and again in a
PDF at Phase 7 — and `REQ-EXP-005 AC-2` forbids any model call at export time,
so the second render has to produce the same picture from the same stored spec
without re-deriving anything.

That rules out most of the field:

- **Canvas-based libraries** (Chart.js and kin) draw to a bitmap. Server-side
  means a headless browser in the export path, and a raster chart in a PDF is
  visibly worse than the type around it.
- **React-only libraries** (Recharts, Victory, Nivo) need a React runtime in
  the export pipeline. Workable, and it makes the export path depend on the
  frontend framework — which §11.1 deliberately separates.
- **D3** is a toolkit, not a renderer. It would be a dependency to do the thing
  we would still be hand-authoring.

**Inline SVG has none of that shape.** It is markup. A browser renders it, a
PDF pipeline renders it, and the two agree because there is nothing to agree
about — the same bytes describe the same picture. §11.3 already observed that
the landing page proves this out.

---

## 3. What this costs, stated plainly

A charting library gives you axes, ticks, scales, legends, tooltips and
responsive reflow for free. Hand-authored SVG gives you none of them, and the
first chart therefore costs more than importing Recharts would.

The bet is that the *fifth* chart costs less, because this product needs a
narrow set of forms — masterplan §13 lists them: revenue history as a line,
margins as a trend, competitors as a comparison table, market share, job roles,
multi-year metrics — and a library's generality is mostly surface we would
never use but would still have to constrain to fit MASTER.md.

**If that bet is wrong**, the signal is specific: a fourth chart form arrives
that needs real axis-scaling logic, and the shared helpers start reimplementing
d3-scale badly. At that point the honest move is to take the dependency for
scale computation only and keep rendering SVG.

---

## 4. The spec is the contract

`visualizations.spec` is `jsonb` and already renderer-agnostic (§11.3, and
implementation plan §343 says so at the schema). This decision does not change
that, deliberately: the spec describes **what to draw**, never how, so a future
renderer swap is a new component and no migration.

A spec carries the data points, their evidence ids, the axis labels and the
form. It does not carry colours, sizes or fonts — those come from the token
layer at render time, which is what lets one spec render into the workspace and
into an export theme (`REQ-EXP-009`) without being regenerated.

**Every data point carries its evidence id.** `REQ-VIZ-002 AC-1` requires every
point trace to sourced evidence and `AC-2` forbids a chart from data lacking
source linkage — so the spec has nowhere to put an unsourced number, and a
chart that wanted one cannot be built rather than being built and caught later.

---

## 5. The semantic token layer

§11.4 states the problem: the workspace renders 4 claim types, 3 confidence
levels, 4 accessibility states, 3 activity statuses and 2 conflict states
**simultaneously**, and MASTER.md's "ochre at most twice per viewport, no third
colour" cannot carry that. It also states the resolution — inherit the
foundation, extend with non-colour channels — without saying which channel
carries what. This does.

| Distinction | Channel | Already built |
|---|---|---|
| Claim type | Left rule treatment (solid/dashed/dotted/open) **plus the word** | Yes, Phase 0 |
| Confidence | Three dots, filled count, **plus the word** | Yes, Phase 2 |
| Source tier | Word beside the citation | Yes, Phase 2 |
| Conflict | Inset panel with a heading | Yes, Phase 2 |
| Activity status | Dot plus status word | Yes, Phase 0 |
| **Chart series** | **Position, then shape, then ochre for one emphasis series** | This decision |

The chart row is the only new one, and it is the one where the temptation to
reach for colour is strongest. **Charts get one ochre series at most.** A
second series is distinguished by position and, where lines overlap, by dash
pattern — the same discipline the claim types already use, applied to a surface
where every other product reaches for a categorical palette.

`REQ-SYNTH-002 AC-3` and `NFR-USE-002` forbid conveying claim type or
confidence by colour alone. This extends that to series identity, which neither
requirement demands and both imply: a reader who cannot distinguish two lines
in greyscale cannot read the chart in an exported PDF printed in black and
white either.

---

## 6. Acceptance criteria coverage

| Criterion | How |
|---|---|
| `REQ-VIZ-002 AC-1` every point traces to evidence | The spec has no field for an unsourced point |
| `REQ-VIZ-002 AC-3` no placeholder chart | A spec with no points renders nothing, not an empty chart frame |
| `REQ-VIZ-004 AC-1` a chart exposes its sources | Evidence ids on the points resolve to the same citation surface claims use |
| `REQ-VIZ-006 AC-3` tables survive export | A table is markup, and so is an SVG chart |
| `REQ-EXP-009` exports preserve the distinctions | Non-colour channels survive a theme swap and a greyscale print |
| `NFR-USE-002` | §5 |

---

## 7. Alternatives considered

### 7.1 Recharts, and render server-side with React SSR — rejected, narrowly

The closest call. It works, and it would make the first chart much cheaper.
Rejected because it puts React in the export path, which §11.1 separates on
purpose, and because Phase 7 has six themes to render — a dependency that has
to be re-themed six times is worse than markup that already is.

### 7.2 A headless browser in the export pipeline — rejected

Answers `OPEN-25` for any client-side library at once, and adds a browser to
the deployment. `N-02` has not settled where the API runs; committing to a
Chromium binary before that is decided would constrain a decision this one has
no business constraining.

### 7.3 Server-rendered raster images — rejected

A PNG in a PDF is soft against vector type, and it discards the accessibility
of the markup. `REQ-VIZ-004` wants a chart to expose its sources, which is
harder when the chart is a picture of itself.

---

## 8. Consequences

### 8.1 Code

A `viz` module producing specs from evidence, and a small set of React
components rendering them. The renderer is deliberately dumb: it draws what the
spec says and makes no decisions, so the export renderer can be a second dumb
reader of the same spec.

### 8.2 Schema

**None.** `visualizations.spec` and `visualization_evidence` are in the
baseline.

### 8.3 Testing

The spec is a pure function of evidence and is tested as one. The renderer is
tested for the two properties that matter: a spec with no sourced points draws
nothing, and every drawn point has an evidence id behind it.

---

## 9. What remains open

- **Which forms exist.** This decision fixes the rendering approach, not the
  catalogue. Masterplan §13 lists the shapes; which of them Phase 3 ships is
  scope, not architecture.
- **Export themes** stay `OPEN-22`.
- **Interaction.** Tooltips and hover states are workspace-only by nature and
  are not part of the spec, since an exported chart has no pointer.

---

## 10. Known risk

**Hand-authored SVG is a decision that gets re-litigated every time someone new
adds a chart**, because importing a library will always look faster at the
moment of writing the fourth one. The cost is real and paid up front; the
benefit is paid at Phase 7, by which point whoever is adding charts may not
remember why. §3 names the specific signal that would justify changing course,
so that a future reversal is a decision rather than an erosion.
