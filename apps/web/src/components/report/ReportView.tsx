import type {
  Claim,
  Conflict,
  EvidenceItem,
  Source,
  Version,
} from "@/lib/api/client";

/**
 * A version, rendered (`REQ-WORK-003..005`).
 *
 * The design rule from implementation plan §11.4 is load-bearing here: claim
 * type is carried by a rule treatment *and* a word, never by colour alone
 * (`REQ-SYNTH-002 AC-3`, `NFR-USE-002`). A fact sits behind a solid rule in the
 * darkest ink; an uncertainty behind an open rule in lighter italic. Print the
 * page in greyscale and nothing is lost.
 *
 * Every claim shows its sources inline. That is the product's whole promise —
 * "no claim without evidence" (`REQ-EVID-017`) is enforced in the pipeline, and
 * this is where a reader gets to check it — so citations are part of the claim,
 * not a footnote somewhere else.
 *
 * Phase 2 adds three more things a reader is entitled to see. **Confidence**
 * sits beside every claim (`REQ-EVID-015 AC-1`), on three non-colour channels:
 * the word, filled and hollow marks, and weight. **Source tier** sits beside
 * every citation (`REQ-EVID-003 AC-2`), because a claim rated moderate should
 * let the reader see *why* rather than asking them to trust the rating.
 *
 * Phase 3 adds **inline citation inspection** (`REQ-EVID-019`,
 * `REQ-WORK-006`): every claim opens to show the evidence under it, each
 * excerpt beside the source that printed it, its tier, when it was read and
 * the period it covers. A native `<details>` rather than a modal, because
 * `AC-1` asks that inspection be reachable without leaving the section and a
 * disclosure collapses back so the report still reads as a report.
 *
 * And **conflicts are shown, not hidden** (`REQ-WORK-009`). Both values appear
 * with their source, the explanation appears where one exists, and where none
 * does the disagreement is labelled unresolved rather than quietly resolved in
 * favour of whichever value the model happened to write down. That is the
 * whole reason `REQ-EVID-014` exists: the honest output when two credible
 * sources disagree is to say so.
 */

type Props = {
  version: Version;
};

const CLAIM_CLASS: Record<Claim["claim_type"], string> = {
  fact: "claim-fact",
  analysis: "claim-analysis",
  forecast: "claim-forecast",
  uncertainty: "claim-uncertainty",
};

const CLAIM_WORD: Record<Claim["claim_type"], string> = {
  fact: "Fact",
  analysis: "Analysis",
  forecast: "Forecast",
  uncertainty: "Uncertain",
};

/* How many of three dots are filled. Inline SVG, 1.5px stroke, per
   MASTER.md's "no emoji as icons" rule: geometric-shape characters are not
   emoji, but they were doing an icon's job, and they render differently in
   every font. Paired always with the word, never shown alone. */
const CONFIDENCE_FILLED: Record<string, number> = {
  high: 3,
  moderate: 2,
  low: 1,
};

const CONFIDENCE_WORD: Record<string, string> = {
  high: "High confidence",
  moderate: "Moderate confidence",
  low: "Low confidence",
};

const CONFIDENCE_CLASS: Record<string, string> = {
  high: "confidence-high",
  moderate: "confidence-moderate",
  low: "confidence-low",
};

/* `REQ-EVID-002`'s vocabulary, in words a non-technical reader can act on.
   "Primary" alone means nothing to someone who has not read the PRD. */
const TIER_WORD: Record<string, string> = {
  primary: "Primary source",
  secondary: "Established source",
  lower: "Unverified source",
};

/* `REQ-EVID-013 AC-1`'s causes, in words that mean something to a reader.
   "estimate_vs_reported" is a database value; "one figure is an estimate" is
   an explanation. */
const CAUSE_WORD: Record<string, string> = {
  period: "the figures cover different reporting periods",
  definition: "the sources are measuring different things",
  currency: "the figures are in different currencies",
  estimate_vs_reported: "one figure is an estimate and the other is reported",
  methodology: "the sources used different methods",
  staleness: "one figure is significantly older than the other",
};

function formatDate(value: string): string {
  return new Date(value).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

export function ReportView({ version }: Props) {
  const claimsById = new Map(version.claims.map((claim) => [claim.id, claim]));
  const sourcesById = new Map(
    version.sources.map((source) => [source.id, source]),
  );

  // Conflicts are addressed to a claim, so they are grouped once here rather
  // than scanned per claim while rendering.
  const conflictsByClaim = new Map<string, Conflict[]>();
  for (const conflict of version.conflicts) {
    const found = conflictsByClaim.get(conflict.claim_id) ?? [];
    found.push(conflict);
    conflictsByClaim.set(conflict.claim_id, found);
  }

  if (version.sections.length === 0) {
    return (
      <p className="measure text-body text-ink-muted">
        This version has no sections yet.
      </p>
    );
  }

  return (
    <article className="flex flex-col gap-16">
      {version.sections.map((section) => (
        <section key={section.id} aria-labelledby={`section-${section.id}`}>
          <div className="flex items-baseline gap-4">
            <h2
              id={`section-${section.id}`}
              className="display-m text-ink"
            >
              {section.title}
            </h2>
            {section.is_executive_summary ? (
              <span className="claim-label">Summary</span>
            ) : null}
          </div>

          <div className="mt-8 flex flex-col gap-8">
            {section.claim_ids.map((claimId) => {
              const claim = claimsById.get(claimId);
              if (!claim) return null;
              return (
                <ClaimBlock
                  key={claim.id}
                  claim={claim}
                  conflicts={conflictsByClaim.get(claim.id) ?? []}
                  sourcesById={sourcesById}
                  sources={claim.source_ids
                    .map((id) => sourcesById.get(id))
                    .filter((source): source is Source => Boolean(source))}
                />
              );
            })}
          </div>
        </section>
      ))}
    </article>
  );
}

function ClaimBlock({
  claim,
  sources,
  conflicts,
  sourcesById,
}: {
  claim: Claim;
  sources: Source[];
  conflicts: Conflict[];
  sourcesById: Map<string, Source>;
}) {
  return (
    <div className={`claim ${CLAIM_CLASS[claim.claim_type]}`}>
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <p className="claim-label">{CLAIM_WORD[claim.claim_type]}</p>
        <ConfidenceTag confidence={claim.confidence} />
      </div>
      <p className="measure mt-2 text-body">{claim.text}</p>

      {/* `REQ-EVID-009 AC-2`: which period the figures cover, beside the claim
          they support. Absent when the evidence spans more than one year,
          because labelling a two-year claim with one of them would be wrong
          about the other. */}
      {claim.reporting_period ? (
        <p className="mt-1 text-micro text-ink-muted">
          Covers the period ending {formatDate(claim.reporting_period)}
        </p>
      ) : null}

      {conflicts.map((conflict) => (
        <ConflictBlock
          key={conflict.id}
          conflict={conflict}
          sourcesById={sourcesById}
        />
      ))}

      {sources.length > 0 ? (
        <ul className="mt-4 flex flex-col gap-2">
          {sources.map((source) => (
            <li key={source.id} className="text-micro text-ink-muted">
              <Citation source={source} />
            </li>
          ))}
        </ul>
      ) : null}

      {claim.evidence.length > 0 ? (
        <ClaimInspector claim={claim} sourcesById={sourcesById} />
      ) : null}
    </div>
  );
}

/**
 * Everything a reader needs to check a claim (`REQ-EVID-019`).
 *
 * `AC-1` lists it: source, tier, retrieval time, the relevant evidence,
 * confidence, and the reporting period where applicable. The evidence is the
 * part that matters most and the part that was missing — a link tells you
 * where to look, an excerpt tells you what was written.
 *
 * `AC-3` puts conflicting evidence in this same surface, which it already is:
 * the conflict panel sits directly above, on the same claim.
 */
function ClaimInspector({
  claim,
  sourcesById,
}: {
  claim: Claim;
  sourcesById: Map<string, Source>;
}) {
  return (
    <details className="mt-3">
      <summary className="inspect-toggle">
        Inspect {claim.evidence.length}{" "}
        {claim.evidence.length === 1 ? "source" : "sources"}
      </summary>

      <div className="inspect-panel mt-3 flex flex-col gap-4">
        {claim.evidence.map((item) => (
          <EvidenceDetail
            key={item.id}
            item={item}
            source={sourcesById.get(item.source_id)}
          />
        ))}

        {claim.confidence_rationale ? (
          /* Why the level, not just the level. `REQ-DATA-012` wants the
             reasoning reconstructable, and it is generated from the same
             inputs as the level so it cannot disagree with it. */
          <p className="text-micro text-ink-muted">
            Confidence: {claim.confidence_rationale}
          </p>
        ) : null}
      </div>
    </details>
  );
}

function EvidenceDetail({
  item,
  source,
}: {
  item: EvidenceItem;
  source: Source | undefined;
}) {
  return (
    <div className="flex flex-col gap-1">
      {item.excerpt ? (
        /* The verbatim span, checked against the source at extraction so it
           cannot be a paraphrase (`REQ-EVID-007`). Quoted, because a reader
           has to be able to tell the source's words from ScrapR's. */
        <p className="inspect-excerpt text-micro">
          &ldquo;{item.excerpt}&rdquo;
        </p>
      ) : null}

      <p className="text-micro text-ink-muted">{item.statement}</p>

      <p className="text-micro text-ink-muted">
        {source ? (
          <>
            {source.url ? (
              <a
                href={source.url}
                target="_blank"
                rel="noreferrer noopener"
                className="text-ochre-deep underline decoration-line-strong underline-offset-4"
              >
                {source.name}
              </a>
            ) : (
              <span>{source.name}</span>
            )}
            {". "}
            <span className="tier-tag">
              {TIER_WORD[source.authority_tier] ?? source.authority_tier}
            </span>
            {". "}
            <span className="tnum">Read {formatDate(source.retrieved_at)}</span>
            {item.reporting_period ? (
              <>
                {". "}
                <span className="tnum">
                  Period ending {formatDate(item.reporting_period)}
                </span>
              </>
            ) : null}
          </>
        ) : null}
      </p>
    </div>
  );
}

/**
 * A disagreement, shown (`REQ-WORK-009`, `REQ-EVID-012 AC-3`).
 *
 * Laid out the way masterplan §6 works the example: the primary value first
 * with its source, then the conflicting one, then the explanation. Which value
 * leads is decided by source tier in the pipeline, not by retrieval order, so
 * the figure closer to the origin is the one a skimming reader takes away.
 *
 * The explanation appears when the evidence supports one (`REQ-EVID-013
 * AC-2`); when it does not, the block says unresolved in as many words
 * (`REQ-EVID-014 AC-1`) rather than inventing a reason.
 */
function ConflictBlock({
  conflict,
  sourcesById,
}: {
  conflict: Conflict;
  sourcesById: Map<string, Source>;
}) {
  const unresolved = conflict.status === "unresolved";
  const cause = conflict.explanation_category
    ? CAUSE_WORD[conflict.explanation_category]
    : null;

  const primary = conflict.sides.find((side) => side.label === "primary");
  const competing = conflict.sides.filter((side) => side.label !== "primary");
  const ordered = primary ? [primary, ...competing] : conflict.sides;

  return (
    <div className="conflict mt-4">
      <p className="conflict-label">
        {unresolved ? "Sources disagree, unresolved" : "Sources disagree"}
      </p>

      <ul className="mt-2 flex flex-col gap-2">
        {ordered.map((side, index) => {
          const source = sourcesById.get(side.source_id);
          return (
            <li key={side.evidence_id} className="text-micro text-ink-muted">
              <span className="text-ink-soft">
                {index === 0 ? "Primary value" : "Conflicting value"}:
              </span>{" "}
              <span className="tnum text-ink">{side.value}</span>
              {source ? (
                <>
                  {", from "}
                  {source.url ? (
                    <a
                      href={source.url}
                      target="_blank"
                      rel="noreferrer noopener"
                      className="text-ochre-deep underline decoration-line-strong underline-offset-4"
                    >
                      {source.name}
                    </a>
                  ) : (
                    <span>{source.name}</span>
                  )}
                  {". "}
                  <span className="tier-tag">
                    {TIER_WORD[source.authority_tier] ?? source.authority_tier}
                  </span>
                  {". "}
                  <span className="tnum">Read {formatDate(source.retrieved_at)}</span>
                </>
              ) : null}
            </li>
          );
        })}
      </ul>

      {cause ? (
        <p className="mt-2 text-micro text-ink-soft">Possible explanation: {cause}.</p>
      ) : (
        /* `REQ-EVID-013 AC-3`: explanations are never invented, so the honest
           output here is to say nothing accounts for the gap. */
        <p className="mt-2 text-micro text-ink-soft">
          Nothing in the evidence explains the difference, so neither figure is
          presented as settled.
        </p>
      )}
    </div>
  );
}

/**
 * Three dots, as many filled as the level warrants.
 *
 * Inline SVG at 1.5px stroke, per MASTER.md's "no emoji as icons" rule.
 * Geometric-shape characters are not emoji, but they were doing an icon's job
 * and they render at a different size in every font, which is exactly the
 * inconsistency that rule exists to prevent.
 */
function ConfidenceDots({ filled }: { filled: number }) {
  return (
    <svg
      width="30"
      height="8"
      viewBox="0 0 30 8"
      fill="none"
      aria-hidden="true"
      focusable="false"
    >
      {[0, 1, 2].map((index) => (
        <circle
          key={index}
          cx={4 + index * 11}
          cy="4"
          r="3"
          fill={index < filled ? "currentColor" : "none"}
          stroke="currentColor"
          strokeWidth="1.5"
        />
      ))}
    </svg>
  );
}

/* `REQ-EVID-015 AC-1`: shown wherever the claim appears. Null is possible only
   for a version written before Phase 2, so it renders nothing rather than
   inventing a level for research that never had one assessed. */
function ConfidenceTag({ confidence }: { confidence: string | null }) {
  if (!confidence || !(confidence in CONFIDENCE_WORD)) return null;

  return (
    <span
      className={`confidence-mark ${CONFIDENCE_CLASS[confidence]}`}
      /* The dots are decorative; the word is the accessible name, so a screen
         reader hears "Moderate confidence" rather than counting circles. */
      aria-label={CONFIDENCE_WORD[confidence]}
    >
      <ConfidenceDots filled={CONFIDENCE_FILLED[confidence]} />
      {CONFIDENCE_WORD[confidence]}
    </span>
  );
}

function Citation({ source }: { source: Source }) {
  const label = source.publisher ? `${source.name} · ${source.publisher}` : source.name;

  return (
    <span className="inline-flex flex-wrap items-baseline gap-x-2">
      {source.url ? (
        <a
          href={source.url}
          target="_blank"
          rel="noreferrer noopener"
          className="text-ochre-deep underline decoration-line-strong underline-offset-4 transition-colors hover:decoration-ochre-deep"
        >
          {label}
        </a>
      ) : (
        <span className="text-ink-soft">{label}</span>
      )}
      {/* Retrieval time is shown, not hidden: `REQ-EVID-004` makes it part of
          what a reader is entitled to see when judging a claim. */}
      <span className="tnum">Read {formatDate(source.retrieved_at)}</span>
      {/* `REQ-EVID-003 AC-2`: the tier is visible, so a lower-tier source is
          shown with its standing rather than quietly dropped (`AC-3`). */}
      <span className="tier-tag">{TIER_WORD[source.authority_tier] ?? source.authority_tier}</span>
    </span>
  );
}
