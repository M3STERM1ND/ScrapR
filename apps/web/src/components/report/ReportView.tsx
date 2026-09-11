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
      <p className="claim-label">{CLAIM_WORD[claim.claim_type]}</p>
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
    </span>
  );
}
