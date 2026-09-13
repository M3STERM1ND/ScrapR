import Link from "next/link";

import type { VersionListing } from "@/lib/api/client";

/**
 * Version navigation (`REQ-VER-008`).
 *
 * Every version, with when it was made and why it exists (`AC-1`); selecting
 * one opens that version's workspace (`AC-2`); the one on screen is marked in
 * words and by `aria-current`, not only by emphasis (`AC-3`).
 */

const ORIGIN_WORD: Record<NonNullable<VersionListing["origin"]>, string> = {
  initial: "First research",
  update: "Update",
  conversation: "Follow-up research",
};

const STATUS_WORD: Record<VersionListing["status"], string> = {
  building: "In progress",
  complete: "Complete",
  partial: "Complete with gaps",
  failed: "Could not complete",
};

export function VersionNav({
  sessionId,
  versions,
  shownNumber,
  latestNumber,
}: {
  sessionId: string;
  versions: VersionListing[];
  shownNumber: number | null;
  latestNumber: number | null;
}) {
  if (versions.length < 2) return null;

  return (
    <nav aria-label="Versions" className="mt-10">
      <p className="claim-label">Versions</p>
      <ol className="mt-3 flex flex-col border-t border-line">
        {[...versions].reverse().map((version) => {
          const current = version.version_number === shownNumber;
          const href =
            version.version_number === latestNumber
              ? `/research/${sessionId}`
              : `/research/${sessionId}/v/${version.version_number}`;
          const label = (
            <>
              <span className={current ? "font-medium text-ink" : ""}>
                Version {version.version_number}
              </span>
              <span className="text-ink-muted">
                {" · "}
                {version.origin ? ORIGIN_WORD[version.origin] : "Research"}
                {" · "}
                {STATUS_WORD[version.status]}
              </span>
            </>
          );

          return (
            <li key={version.id} className="border-b border-line py-2.5">
              <div className="flex flex-wrap items-baseline justify-between gap-x-4 text-small">
                {current || !version.closed_at ? (
                  <span aria-current={current ? "page" : undefined}>
                    {label}
                    {current ? <span className="text-ink-muted"> · Showing</span> : null}
                  </span>
                ) : (
                  <Link
                    href={href}
                    className="underline decoration-line-strong underline-offset-4 hover:decoration-ochre-deep"
                  >
                    {label}
                  </Link>
                )}
                <span className="tnum text-micro text-ink-muted">
                  {formatDateTime(version.created_at)}
                </span>
              </div>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

function formatDateTime(value: string): string {
  return new Date(value).toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}
