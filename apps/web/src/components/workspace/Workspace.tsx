"use client";

import { useEffect, useRef, useState } from "react";

import { ActivityTimeline } from "@/components/activity/ActivityTimeline";
import { ReportView } from "@/components/report/ReportView";
import {
  ApiError,
  getActivity,
  getResearch,
  getVersion,
  type ActivityEvent,
  type ResearchSession,
  type Version,
} from "@/lib/api/client";

/**
 * The workspace (`REQ-WORK-002..005`).
 *
 * **Polling, not streaming.** `GET /activity?after={seq}` returns events newer
 * than a sequence number and says what to ask for next. The Phase 3 upgrade to
 * SSE reuses that exact resume key, so this component's state model does not
 * change when the transport does (implementation plan §6.3).
 *
 * **Polling stops when the run does.** A tab left open on finished research
 * should cost nothing, so the interval clears the moment the session reaches a
 * terminal status — and again whenever the tab is hidden.
 */

const POLL_MS = 1500;
const TERMINAL: ReadonlyArray<ResearchSession["status"]> = [
  "complete",
  "partial",
  "failed",
];

type Props = {
  sessionId: string;
};

export function Workspace({ sessionId }: Props) {
  const [session, setSession] = useState<ResearchSession | null>(null);
  const [events, setEvents] = useState<ActivityEvent[]>([]);
  const [version, setVersion] = useState<Version | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Refs, not state: the loop reads them on every tick, and re-rendering on a
  // cursor change would tear the interval down and build it again.
  const cursor = useRef(0);
  const loadedVersion = useRef<string | null>(null);

  const running = session ? !TERMINAL.includes(session.status) : true;

  useEffect(() => {
    let stopped = false;
    let timer = 0;

    const stop = () => {
      stopped = true;
      window.clearInterval(timer);
    };

    const tick = async () => {
      try {
        const [nextSession, page] = await Promise.all([
          getResearch(sessionId),
          getActivity(sessionId, cursor.current),
        ]);
        if (stopped) return;

        setSession(nextSession);
        setError(null);

        if (page.events.length > 0) {
          cursor.current = page.next_after;
          setEvents((current) => [...current, ...page.events]);
        }

        // The report is only worth fetching once a version has closed: before
        // that it is half-written by definition, and `REQ-VER-002` means a
        // closed version never changes again, so it is fetched exactly once.
        const latest = nextSession.versions.at(-1);
        if (latest?.closed_at && loadedVersion.current !== latest.id) {
          loadedVersion.current = latest.id;
          const loaded = await getVersion(sessionId, latest.version_number);
          if (!stopped) setVersion(loaded);
        }

        // A tab left open on finished research should cost nothing.
        if (TERMINAL.includes(nextSession.status)) stop();
      } catch (cause) {
        if (stopped) return;
        setError(
          cause instanceof ApiError
            ? cause.message
            : "Could not reach the service. Retrying.",
        );
      }
    };

    void tick();
    timer = window.setInterval(() => {
      // Polling a hidden tab burns the user's battery to learn nothing they
      // are looking at.
      if (document.visibilityState === "visible") void tick();
    }, POLL_MS);

    return stop;
  }, [sessionId]);

  if (error && !session) {
    return (
      <div className="shell py-20">
        <p className="measure border-l-2 border-ochre-deep pl-4 text-body text-ink-soft">
          {error}
        </p>
      </div>
    );
  }

  return (
    <div className="shell py-14 md:py-20">
      <header className="max-w-[46rem]">
        <p className="claim-label">
          {session ? STATUS_WORD[session.status] : "Loading"}
        </p>
        <h1 className="display-m mt-3 text-ink">
          {session?.objective ?? " "}
        </h1>
        {session?.subject_interpretation_note ? (
          <p className="measure mt-4 text-small text-ink-muted">
            {session.subject_interpretation_note}
          </p>
        ) : null}
      </header>

      <div className="hairline mt-10" />

      <div className="mt-10 grid gap-14 lg:grid-cols-[minmax(0,1fr)_18rem] lg:gap-20">
        <div className="order-2 lg:order-1">
          {version ? (
            <ReportView version={version} />
          ) : (
            <p className="measure text-body text-ink-muted">
              {running
                ? "ScrapR is reading the sources. The report appears here as soon as it holds together."
                : "No report was produced for this research."}
            </p>
          )}
        </div>

        <aside className="order-1 lg:order-2 lg:sticky lg:top-28 lg:self-start">
          <ActivityTimeline events={events} running={running} />
          {error ? (
            <p className="mt-6 text-micro text-ink-muted">{error}</p>
          ) : null}
        </aside>
      </div>
    </div>
  );
}

const STATUS_WORD: Record<ResearchSession["status"], string> = {
  pending: "Queued",
  running: "Researching",
  complete: "Complete",
  partial: "Complete with gaps",
  failed: "Could not complete",
};
