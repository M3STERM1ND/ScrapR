import Link from "next/link";

import type { Change, ChangeSummary } from "@/lib/api/client";

/**
 * What's Changed (`REQ-VER-006`, `REQ-VER-007`, `DEC-20`).
 *
 * Leads the report of any version that updates an earlier one. The headline is
 * the server's, and it says "no meaningful changes" in words when that is the
 * truth (`AC-3`) rather than leaving an empty list to interpret.
 *
 * **Each change is labelled by kind in words, not colour** (`NFR-USE-002`), and
 * links both ways: to the claim in this version whose evidence caused it, and
 * to the claim as it stood in the version it was compared with (`REQ-VER-007
 * AC-1`, `AC-2`). A confidence change is stated beside the conclusion it moved
 * (`AC-3`).
 */

const KIND_WORD: Record<Change["kind"], string> = {
  conclusion_changed: "Conclusion changed",
  figure_changed: "Figure changed",
  newer_period: "Newer figure",
  assumptions_changed: "Assumptions changed",
  new_finding: "New",
  gap_closed: "Now confirmed",
  gap_opened: "No longer confirmed",
  no_longer_found: "Not found again",
};

const CATEGORY_WORD: Record<Change["category"], string> = {
  financial_figures: "Financial figures",
  stock_information: "Stock information",
  job_postings: "Job postings",
  new_products: "Products",
  new_competitors: "Competitors",
  forecast_assumptions: "Forecast assumptions",
  other: "Other developments",
};

const CONFIDENCE_WORD: Record<string, string> = {
  high: "high",
  moderate: "moderate",
  low: "low",
};

export function WhatsChanged({
  summary,
  sessionId,
}: {
  summary: ChangeSummary;
  sessionId: string;
}) {
  const compared = summary.compared_with;
  const changes = summary.changes ?? [];
  const earlier = `/research/${sessionId}/v/${compared.version_number}`;

  return (
    <section
      aria-labelledby="whats-changed-heading"
      className="card px-6 py-6 md:px-8"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-2">
        <h2 id="whats-changed-heading" className="text-[1.375rem] leading-tight">
          What&rsquo;s changed
        </h2>
        <Link
          href={earlier}
          className="text-micro text-ink-muted underline decoration-line-strong underline-offset-4 hover:text-ink"
        >
          Compared with version {compared.version_number}, {formatDate(compared.created_at)}
        </Link>
      </div>

      {!summary.available ? (
        <p className="measure mt-4 text-small text-ink-soft">
          The comparison with version {compared.version_number} could not be made for
          this update. The report below is complete; only the summary of differences
          is missing.
        </p>
      ) : (
        <>
          <p className="measure mt-3 text-body text-ink">{summary.headline}</p>

          {changes.length > 0 ? (
            <ul className="mt-6 flex flex-col divide-y divide-line border-t border-line">
              {changes.map((change, index) => (
                <li key={`${change.kind}-${index}`} className="py-5">
                  <ChangeItem change={change} earlier={earlier} />
                </li>
              ))}
            </ul>
          ) : null}

          {summary.counts ? (
            <p className="mt-5 text-micro text-ink-muted">
              {summary.counts.unchanged === 1
                ? "1 finding held."
                : `${summary.counts.unchanged} findings held.`}{" "}
              {summary.counts.new_sources === 1
                ? "1 source was new to this update."
                : `${summary.counts.new_sources} sources were new to this update.`}{" "}
              Rewording and sources that came and went without changing a finding
              are not listed.
            </p>
          ) : null}
        </>
      )}
    </section>
  );
}

function ChangeItem({ change, earlier }: { change: Change; earlier: string }) {
  const confidence = change.confidence_change;

  return (
    <div>
      <p className="claim-label">
        {KIND_WORD[change.kind]} · {CATEGORY_WORD[change.category]}
      </p>
      <p className="measure mt-2 text-small text-ink-soft">{change.summary}</p>

      {change.kind === "figure_changed" || change.kind === "newer_period" ? (
        <p className="tnum mt-2 text-small text-ink">
          <span className="text-ink-muted line-through decoration-line-strong">
            {change.before?.value}
          </span>{" "}
          <span aria-hidden>→</span>
          <span className="sr-only">changed to</span> {change.after?.value}
        </p>
      ) : null}

      {confidence && (confidence.from || confidence.to) ? (
        <p className="mt-2 text-micro text-ink-muted">
          Confidence {CONFIDENCE_WORD[confidence.from ?? ""] ?? "unassessed"} to{" "}
          {CONFIDENCE_WORD[confidence.to ?? ""] ?? "unassessed"}
        </p>
      ) : null}

      <p className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-micro">
        {change.after ? (
          <a
            href={`#claim-${change.after.claim_id}`}
            className="text-ochre-deep underline decoration-line-strong underline-offset-4"
          >
            {change.evidence_ids.length > 0 ? "See the new evidence" : "See it in this version"}
          </a>
        ) : null}
        {change.before ? (
          <Link
            href={`${earlier}#claim-${change.before.claim_id}`}
            className="text-ink-muted underline decoration-line-strong underline-offset-4 hover:text-ink"
          >
            See how it stood before
          </Link>
        ) : null}
      </p>
    </div>
  );
}

function formatDate(value: string): string {
  return new Date(value).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}
