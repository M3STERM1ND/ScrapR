"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { DeleteResearch } from "@/components/account/DeleteResearch";
import { ActivityTimeline } from "@/components/activity/ActivityTimeline";
import { ConversationPanel } from "@/components/conversation/ConversationPanel";
import { ExportPanel } from "@/components/exports/ExportPanel";
import { ReportView } from "@/components/report/ReportView";
import { VersionNav } from "@/components/versions/VersionNav";
import { WhatsChanged } from "@/components/versions/WhatsChanged";
import {
  ApiError,
  getActivity,
  getMessages,
  getResearch,
  getVersion,
  listUploads,
  updateResearch,
  type ActivityEvent,
  type Message,
  type ResearchSession,
  type Source,
  type Upload,
  type Version,
  type VersionListing,
} from "@/lib/api/client";
import { useAccount } from "@/lib/useAccount";
import { AttachedDocuments } from "./AttachedDocuments";

/**
 * The workspace (`REQ-WORK-002..005`), for the latest version or a chosen one
 * (`REQ-VER-008`).
 *
 * **Polling, not streaming.** `GET /activity?after={seq}` returns events newer
 * than a sequence number and says what to ask for next. The Phase 3 upgrade to
 * SSE reuses that exact resume key, so this component's state model does not
 * change when the transport does (implementation plan §6.3).
 *
 * **Polling stops when the run does**, and starts again when the reader presses
 * Update Research. A tab left open on finished research should cost nothing.
 *
 * **The report on screen is always a finished version.** While an update runs,
 * the previous version stays in place and the activity timeline shows the new
 * one being built; when it closes, it replaces the old one with its What's
 * Changed at the top. A version that failed is never shown as the report — the
 * most recent one that finished is, with a line saying the update did not.
 */

const POLL_MS = 1500;
const TERMINAL: ReadonlyArray<ResearchSession["status"]> = ["complete", "partial", "failed"];

type Props = {
  sessionId: string;
  /** A specific version to show. Absent means the latest finished one. */
  versionNumber?: number;
};

export function Workspace({ sessionId, versionNumber }: Props) {
  const router = useRouter();
  const [session, setSession] = useState<ResearchSession | null>(null);
  const [events, setEvents] = useState<ActivityEvent[]>([]);
  const [version, setVersion] = useState<Version | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [uploads, setUploads] = useState<Upload[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [missing, setMissing] = useState(false);

  // Bumped to restart polling after the reader starts an update.
  const [generation, setGeneration] = useState(0);
  const [updating, setUpdating] = useState(false);
  const [updateError, setUpdateError] = useState<string | null>(null);

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
        const [nextSession, page, attached] = await Promise.all([
          getResearch(sessionId),
          getActivity(sessionId, cursor.current),
          // Polled with everything else rather than fetched once: a document
          // can still be extracting when the workspace opens, and `REQ-DOC-003
          // AC-2` wants the state visible as it changes, not as it was.
          listUploads(sessionId),
        ]);
        if (stopped) return;

        setSession(nextSession);
        setUploads(attached);
        setError(null);

        if (page.events.length > 0) {
          cursor.current = page.next_after;
          setEvents((current) => [...current, ...page.events]);
        }

        // Only a closed version is worth fetching: before that it is half
        // written, and `REQ-VER-002` means a closed version never changes, so
        // each one is fetched exactly once.
        const target = chooseVersion(nextSession.versions, versionNumber);
        if (versionNumber !== undefined && !target) {
          const exists = nextSession.versions.some((v) => v.version_number === versionNumber);
          setMissing(!exists);
        }
        if (target && loadedVersion.current !== target.id) {
          loadedVersion.current = target.id;
          const loaded = await getVersion(sessionId, target.version_number);
          if (!stopped) setVersion(loaded);

          // The conversation comes back with the report (`REQ-CONV-007 AC-2`).
          const transcript = await getMessages(sessionId);
          if (!stopped) setMessages(transcript);
        }

        if (TERMINAL.includes(nextSession.status)) stop();
      } catch (cause) {
        if (stopped) return;
        setError(
          cause instanceof ApiError ? cause.message : "Could not reach the service. Retrying.",
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
  }, [sessionId, versionNumber, generation]);

  async function onUpdate() {
    setUpdating(true);
    setUpdateError(null);
    try {
      await updateResearch(sessionId);
      setGeneration((value) => value + 1);
    } catch (cause) {
      setUpdateError(
        cause instanceof ApiError ? cause.message : "Could not reach the service. Try again.",
      );
    } finally {
      setUpdating(false);
    }
  }

  if (error && !session) {
    return (
      <div className="shell py-20">
        <p className="measure border-l-2 border-ochre-deep pl-4 text-body text-ink-soft">
          {error}
        </p>
      </div>
    );
  }

  const versions = session?.versions ?? [];
  const latestFinished = chooseVersion(versions, undefined);
  const newest = versions.at(-1);
  const viewingLatest =
    versionNumber === undefined || versionNumber === latestFinished?.version_number;
  const updateFailed =
    viewingLatest && !running && newest?.status === "failed" && latestFinished !== null;

  return (
    <div className="shell py-14 md:py-20">
      <header className="max-w-[46rem]">
        <p className="claim-label">
          {headerWord(session, version, running)}
        </p>
        <h1 className="display-m mt-3 text-ink">{session?.objective ?? " "}</h1>
        {session?.subject_interpretation_note ? (
          <p className="measure mt-4 text-small text-ink-muted">
            {session.subject_interpretation_note}
          </p>
        ) : null}

        {version ? (
          <ShortfallNotice status={version.status} />
        ) : session && !running ? (
          <ShortfallNotice status={session.status} />
        ) : null}

        {updateFailed && latestFinished ? (
          <p role="status" className="measure mt-5 border-l-2 border-ochre-deep pl-4 text-small text-ink-soft">
            The latest update could not be completed. Version {latestFinished.version_number},
            below, is the most recent finished report, unchanged.
          </p>
        ) : null}

        {!viewingLatest && version && latestFinished ? (
          <p role="status" className="measure mt-5 border-l-2 border-ink pl-4 text-small text-ink-soft">
            You are viewing version {version.version_number}, from{" "}
            {formatDate(version.created_at)}. It is kept exactly as it was.{" "}
            <Link
              href={`/research/${sessionId}`}
              className="whitespace-nowrap text-ink underline decoration-line-strong underline-offset-4 hover:decoration-ochre-deep"
            >
              Go to the latest, version {latestFinished.version_number}
            </Link>
          </p>
        ) : null}

        {running && version ? (
          <p role="status" className="measure mt-5 text-small text-ink-muted">
            Updating. Version {version.version_number} stays here until the new version is ready.
          </p>
        ) : null}

        {missing ? (
          <p role="alert" className="measure mt-5 border-l-2 border-ochre-deep pl-4 text-small text-ink-soft">
            That version does not exist.
          </p>
        ) : null}

        {session ? (
          <div className="mt-6 flex flex-wrap items-center gap-x-6 gap-y-3">
            {viewingLatest && !running && latestFinished ? (
              <button
                type="button"
                onClick={onUpdate}
                disabled={updating}
                className="inline-flex h-10 items-center rounded-sm border border-line-strong px-4 text-small font-medium text-ink transition-colors duration-200 hover:border-ink hover:bg-surface disabled:cursor-not-allowed disabled:text-ink-muted"
              >
                {updating ? "Starting update" : "Update research"}
              </button>
            ) : null}
            <SaveToAccount sessionId={sessionId} />
            <DeleteResearch sessionId={sessionId} onDeleted={() => router.push("/research/new")} />
          </div>
        ) : null}

        {updateError ? (
          <p role="alert" className="mt-4 text-small text-ink-soft">
            {updateError}
          </p>
        ) : null}

        {/* Flow D: any finished version can leave the application, the one on
            screen being the one exported (`REQ-EXP-006`). */}
        {version ? (
          <div className="mt-4">
            <ExportPanel sessionId={sessionId} versionNumber={version.version_number} />
          </div>
        ) : null}

        {session ? (
          <VersionNav
            sessionId={sessionId}
            versions={versions}
            shownNumber={version?.version_number ?? null}
            latestNumber={latestFinished?.version_number ?? null}
          />
        ) : null}
      </header>

      <div className="hairline mt-10" />

      <div className="mt-10 grid gap-14 lg:grid-cols-[minmax(0,1fr)_18rem] lg:gap-20">
        <div className="order-2 flex flex-col gap-14 lg:order-1">
          {version?.change_summary ? (
            <WhatsChanged summary={version.change_summary} sessionId={sessionId} />
          ) : null}

          {version ? (
            <ReportView version={version} />
          ) : (
            <p className="measure text-body text-ink-muted">
              {running
                ? "ScrapR is reading the sources. The report appears here as soon as it holds together."
                : "No report was produced. Nothing usable could be gathered for this question, so there is nothing to show rather than a report built on nothing."}
            </p>
          )}

          {/* `REQ-WORK-007`: the conversation lives with the report. Only on the
              latest version, because a follow-up is answered from the research
              as it stands now, and asking it beside an older version would
              suggest otherwise. */}
          {version && viewingLatest ? (
            <div className="mt-6">
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
          {/* `REQ-DOC-003 AC-3`: a failed upload does not silently vanish. */}
          <AttachedDocuments uploads={uploads} />
          {error ? <p className="mt-6 text-micro text-ink-muted">{error}</p> : null}
        </aside>
      </div>
    </div>
  );
}

/**
 * Which version to show: the one asked for once it has closed, or else the
 * newest that finished with a report. A failed version produced no report, so
 * it is never chosen as "the latest".
 */
function chooseVersion(
  versions: VersionListing[],
  requested: number | undefined,
): VersionListing | null {
  if (requested !== undefined) {
    const found = versions.find((v) => v.version_number === requested);
    return found?.closed_at ? found : null;
  }
  const finished = versions.filter((v) => v.closed_at && v.status !== "failed");
  return finished.at(-1) ?? null;
}

function headerWord(
  session: ResearchSession | null,
  version: Version | null,
  running: boolean,
): string {
  if (!session) return "Loading";
  if (running) return version ? "Updating" : STATUS_WORD[session.status];
  if (version) return VERSION_WORD[version.status];
  return STATUS_WORD[session.status];
}

/**
 * Says plainly that the research came back short (`REQ-AGENT-009 AC-2`, `AC-4`).
 *
 * The gaps themselves are in the report, as uncertainty claims naming each area
 * that could not be researched — that is where a reader meets them in context.
 * This is the part that has to be visible before any of it is read, so nobody
 * mistakes a partial report for a complete one.
 */
function ShortfallNotice({ status }: { status: string }) {
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

/**
 * Offers an account from inside the research (`REQ-AUTH-003 AC-1`).
 *
 * Only to a visitor who is not signed in, and only as an offer: the research is
 * already fully usable, and the line says what an account adds rather than
 * implying anything is at risk without one. Signing up from here brings this
 * research into the account and returns the reader to it (`DEC-17`).
 */
function SaveToAccount({ sessionId }: { sessionId: string }) {
  const account = useAccount();

  // Nothing while asking, and nothing to a reader who already has an account.
  if (account !== null) return null;

  const next = encodeURIComponent(`/research/${sessionId}`);
  return (
    <p className="text-micro text-ink-muted">
      <Link
        href={`/signup?next=${next}`}
        className="font-medium text-ink underline decoration-line-strong underline-offset-4 hover:decoration-ochre-deep"
      >
        Save to an account
      </Link>{" "}
      to come back to this research, its versions and conversation later.
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

const VERSION_WORD: Record<Version["status"], string> = {
  building: "In progress",
  complete: "Complete",
  partial: "Complete with gaps",
  failed: "Could not complete",
};

function formatDate(value: string): string {
  return new Date(value).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

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
