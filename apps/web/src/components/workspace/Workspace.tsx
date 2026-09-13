"use client";

import { useEffect, useRef, useState } from "react";

import { ActivityTimeline } from "@/components/activity/ActivityTimeline";
import { ConversationPanel } from "@/components/conversation/ConversationPanel";
import { ReportView } from "@/components/report/ReportView";
import {
  ApiError,
  getActivity,
  getResearch,
  getMessages,
  getVersion,
  type ActivityEvent,
  type Message,
  type ResearchSession,
  type Source,
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
  const [messages, setMessages] = useState<Message[]>([]);
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

          // The conversation comes back with the report (`REQ-CONV-007 AC-2`).
          // Fetched here rather than in the panel so returning to saved
          // research restores the transcript with everything else, in one
          // pass, instead of the panel flashing empty and filling in.
          const transcript = await getMessages(sessionId);
          if (!stopped) setMessages(transcript);
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

        {session ? <ShortfallNotice status={session.status} /> : null}
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
                : "No report was produced. Nothing usable could be gathered for this question, so there is nothing to show rather than a report built on nothing."}
            </p>
          )}

          {/* `REQ-WORK-007`: the conversation lives with the report, not on a
              separate page. A question is about what the reader is looking at,
              and making them navigate away to ask it breaks the context the
              whole feature exists to preserve. Only once there is research to
              interrogate. */}
          {version ? (
            <div className="mt-20">
              <div className="hairline mb-10" />
              <ConversationPanel
                sessionId={sessionId}
                initialMessages={messages}
                sourceForEvidence={sourceForEvidence(version)}
              />
            </div>
          ) : null}
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

/**
 * Says plainly that the research came back short (`REQ-AGENT-009 AC-2`, `AC-4`).
 *
 * The gaps themselves are in the report, as uncertainty claims naming each area
 * that could not be researched — that is where a reader meets them in context.
 * This is the part that has to be visible before any of it is read, so nobody
 * mistakes a partial report for a complete one.
 */
function ShortfallNotice({ status }: { status: ResearchSession["status"] }) {
  if (status !== "partial" && status !== "failed") return null;

  return (
    <p
      className="measure mt-5 border-l-2 border-ochre-deep pl-4 text-small text-ink-soft"
      role="status"
    >
      {status === "partial"
        ? "Parts of this question could not be answered. What is missing is marked in the report, and nothing has been filled in with guesswork."
        : "This research could not be completed. Nothing below should be read as a finding."}
    </p>
  );
}

const STATUS_WORD: Record<ResearchSession["status"], string> = {
  pending: "Queued",
  running: "Researching",
  complete: "Complete",
  partial: "Complete with gaps",
  failed: "Could not complete",
};


/**
 * Evidence id to the source behind it.
 *
 * An answer cites *evidence* (`REQ-CONV-008 AC-1`) and a reader needs the
 * source, so the mapping is built once here from what the version already
 * carries rather than fetched again per turn.
 */
function sourceForEvidence(version: Version): Map<string, Source> {
  const sources = new Map(version.sources.map((source) => [source.id, source]));
  const byEvidence = new Map<string, Source>();

  for (const claim of version.claims) {
    for (const item of claim.evidence) {
      const source = sources.get(item.source_id);
      if (source) byEvidence.set(item.id, source);
    }
  }

  return byEvidence;
}
