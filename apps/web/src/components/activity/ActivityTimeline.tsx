import type { ActivityEvent } from "@/lib/api/client";

/**
 * The ordered activity timeline (`REQ-ACT-001`, `REQ-ACT-004`).
 *
 * Two rules the component keeps:
 *
 * - **Status is never colour alone** (`NFR-USE-002`). Each row carries a dot
 *   *and* a word, so the distinction survives greyscale, a printout, and any
 *   colour vision.
 * - **Labels are whatever the server sent** (`REQ-ACT-003`). The API is what
 *   guarantees no tool or provider name leaks into them; the client does not
 *   reinterpret, prettify or map them, because that would be a second place for
 *   internal detail to appear.
 *
 * Consecutive events with the same label are collapsed into one row that takes
 * the later status, so a long run reads as a list of stages rather than a log
 * (`REQ-ACT-004 AC-3`).
 */

type Props = {
  events: ActivityEvent[];
  running: boolean;
};

const STATUS_WORD: Record<ActivityEvent["status"], string> = {
  in_progress: "Working",
  complete: "Done",
  failed: "Failed",
};

const STATUS_CLASS: Record<ActivityEvent["status"], string> = {
  in_progress: "status-running",
  complete: "status-done",
  failed: "status-failed",
};

function collapse(events: ActivityEvent[]): ActivityEvent[] {
  const stages: ActivityEvent[] = [];
  for (const event of events) {
    const last = stages.at(-1);
    if (last && last.label === event.label) {
      stages[stages.length - 1] = event;
    } else {
      stages.push(event);
    }
  }
  return stages;
}

export function ActivityTimeline({ events, running }: Props) {
  const stages = collapse(events);

  return (
    <section aria-labelledby="activity-heading">
      <h2 id="activity-heading" className="claim-label">
        Activity
      </h2>

      {stages.length === 0 ? (
        <p className="mt-4 text-small text-ink-muted">
          {running ? "Getting started." : "Nothing has happened yet."}
        </p>
      ) : (
        <ol className="mt-4 flex flex-col">
          {stages.map((event) => (
            <li
              key={event.seq}
              className="flex items-baseline gap-3 border-b border-line py-3 last:border-b-0"
            >
              <span
                aria-hidden
                className={`status-dot ${STATUS_CLASS[event.status]} translate-y-[-1px]`}
              />
              <span className="flex-1 text-small text-ink-soft">
                {event.label}
              </span>
              <span className="text-micro text-ink-muted">
                {STATUS_WORD[event.status]}
              </span>
            </li>
          ))}
        </ol>
      )}

      {/* The live region announces progress without moving focus, so a screen
          reader user is told what is happening while they read the page. */}
      <p aria-live="polite" className="sr-only">
        {stages.length > 0
          ? `${stages.at(-1)?.label}: ${STATUS_WORD[stages.at(-1)!.status]}`
          : ""}
      </p>
    </section>
  );
}
