import type { Upload } from "@/lib/api/client";

import { DocumentIcon } from "@/components/ui/icons";

/**
 * The documents attached to this research, and what became of each
 * (`REQ-DOC-003 AC-2`, `AC-3`).
 *
 * **A failed upload must not vanish** (`AC-3`), and until this existed it did.
 * Intake waits for extraction and then routes to the workspace, so a file that
 * could not be read left the reader in a report with no mention of the document
 * they attached and no way to learn why it is absent. The state is visible
 * where the research is, for as long as the research is.
 *
 * Rendered only when there is something to show. A panel reading "no documents"
 * on the great majority of runs is noise, and `REQ-INPUT-004 AC-3` makes
 * attaching nothing the normal case.
 */
export function AttachedDocuments({ uploads }: { uploads: Upload[] }) {
  if (uploads.length === 0) return null;

  return (
    <section className="mt-10">
      <h2 className="claim-label">Your documents</h2>

      <ul className="mt-2">
        {uploads.map((upload) => (
          <li key={upload.id} className="attachment-row">
            <span className="min-w-0 flex-1">
              <span className="flex items-baseline gap-2">
                <DocumentIcon className="translate-y-px text-ink-muted" />
                <span className="min-w-0 truncate text-small text-ink-soft">
                  {upload.filename}
                </span>
              </span>

              {/* The reason lives next to the file, not in a toast that has
                  already gone by the time the reader looks (`REQ-SEC-010`
                  keeps it free of anything internal). */}
              {upload.state === "failed" && upload.error ? (
                <span className="attachment-state attachment-failed">
                  {upload.error}
                </span>
              ) : (
                <span className="attachment-state">{describe(upload)}</span>
              )}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

/** One line per state, in plain words rather than the enum's spelling. */
function describe(upload: Upload): string {
  switch (upload.state) {
    case "pending":
      return "Waiting to upload";
    case "processing":
      return "Being read";
    case "ready":
      return upload.chunk_count === 1
        ? "Read, 1 passage available"
        : `Read, ${upload.chunk_count} passages available`;
    case "failed":
      return "Could not be read";
  }
}
