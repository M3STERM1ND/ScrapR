import type { Claim, Source, Version } from "@/lib/api/client";

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
 * Phase 2 adds two more things a reader is entitled to see. **Confidence** sits
 * beside every claim (`REQ-EVID-015 AC-1`), on three non-colour channels: the
 * word, filled and hollow marks, and weight. **Source tier** sits beside every
 * citation (`REQ-EVID-003 AC-2`), because a claim rated moderate should let the
 * reader see *why* rather than asking them to trust the rating.
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

/* Filled and hollow marks, so the level survives greyscale and a screenshot.
   Paired with the word below — never the marks alone. */
const CONFIDENCE_MARK: Record<string, string> = {
  high: "●●●",
  moderate: "●●○",
  low: "●○○",
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

function ClaimBlock({ claim, sources }: { claim: Claim; sources: Source[] }) {
  return (
    <div className={`claim ${CLAIM_CLASS[claim.claim_type]}`}>
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <p className="claim-label">{CLAIM_WORD[claim.claim_type]}</p>
        <ConfidenceTag confidence={claim.confidence} />
      </div>
      <p className="measure mt-2 text-body">{claim.text}</p>

      {sources.length > 0 ? (
        <ul className="mt-4 flex flex-col gap-2">
          {sources.map((source) => (
            <li key={source.id} className="text-micro text-ink-muted">
              <Citation source={source} />
            </li>
          ))}
        </ul>
      ) : null}
    </div>
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
      /* The marks are decorative; the word is the accessible name, so a screen
         reader hears "Moderate confidence" rather than three bullet glyphs. */
      aria-label={CONFIDENCE_WORD[confidence]}
    >
      <span aria-hidden="true">{CONFIDENCE_MARK[confidence]}</span>{" "}
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
