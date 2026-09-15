"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { DeleteResearch } from "@/components/account/DeleteResearch";
import { ArrowRight, WorkspaceIcon } from "@/components/ui/icons";
import { ApiError, listHistory, type HistoryItem } from "@/lib/api/client";

/**
 * The saved research list (`REQ-AUTH-005 AC-1`): subject, objective and when it
 * last changed, newest first, as the server ordered it.
 *
 * A reader who is not signed in is told what would put research here, rather
 * than shown an empty list that looks like something was lost.
 */

type State =
  | { kind: "loading" }
  | { kind: "signed-out" }
  | { kind: "error"; message: string }
  | { kind: "ready"; items: HistoryItem[] };

const STATUS_WORD: Record<HistoryItem["status"], string> = {
  pending: "Queued",
  running: "Researching",
  complete: "Complete",
  partial: "Complete with gaps",
  failed: "Could not complete",
};

export function HistoryList() {
  const [state, setState] = useState<State>({ kind: "loading" });

  useEffect(() => {
    let active = true;
    listHistory()
      .then((items) => {
        if (active) setState({ kind: "ready", items });
      })
      .catch((cause: unknown) => {
        if (!active) return;
        if (cause instanceof ApiError && cause.status === 401) {
          setState({ kind: "signed-out" });
        } else {
          setState({
            kind: "error",
            message:
              cause instanceof ApiError
                ? cause.message
                : "Could not reach the service. Try again in a moment.",
          });
        }
      });
    return () => {
      active = false;
    };
  }, []);

  if (state.kind === "loading") {
    return <p className="mt-10 text-body text-ink-muted">Loading your research.</p>;
  }

  if (state.kind === "signed-out") {
    return (
      <div className="mt-10 measure">
        <p className="text-body text-ink-soft">
          Saved research lives in an account. Sign in to see yours, or create
          one to keep the research you do from here on.
        </p>
        <div className="mt-6 flex flex-wrap gap-6">
          <Link
            href="/signin?next=/history"
            className="text-small text-ink underline decoration-line-strong underline-offset-4 hover:decoration-ochre-deep"
          >
            Sign in
          </Link>
          <Link
            href="/signup?next=/history"
            className="text-small text-ink underline decoration-line-strong underline-offset-4 hover:decoration-ochre-deep"
          >
            Create an account
          </Link>
        </div>
      </div>
    );
  }

  if (state.kind === "error") {
    return (
      <p role="alert" className="mt-10 border-l-2 border-ochre-deep pl-4 text-small text-ink-soft">
        {state.message}
      </p>
    );
  }

  if (state.items.length === 0) {
    return (
      <div className="mt-10 measure">
        <WorkspaceIcon className="mb-4 h-6 w-6 text-ink-muted" />
        <p className="text-body text-ink-soft">
          Nothing saved yet. Research you start while signed in is kept here.
        </p>
        <Link
          href="/research/new"
          className="mt-6 inline-flex items-center gap-2 text-small text-ink underline decoration-line-strong underline-offset-4 hover:decoration-ochre-deep"
        >
          Start research <ArrowRight />
        </Link>
      </div>
    );
  }

  const remove = (id: string) =>
    setState((current) =>
      current.kind === "ready"
        ? { kind: "ready", items: current.items.filter((item) => item.id !== id) }
        : current,
    );

  return (
    <ul className="mt-12 border-t border-line" aria-label="Saved research">
      {state.items.map((item) => (
        <li key={item.id} className="border-b border-line py-6">
          <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-2">
            <p className="claim-label">
              {item.subject ?? "Subject not yet identified"}
              {" · "}
              {STATUS_WORD[item.status]}
              {" · "}
              {item.version_count === 1 ? "1 version" : `${item.version_count} versions`}
            </p>
            <p className="tnum text-micro text-ink-muted">
              Updated {formatDate(item.updated_at)}
            </p>
          </div>
          <Link
            href={`/research/${item.id}`}
            className="group mt-2 flex items-start justify-between gap-6 text-lead text-ink transition-colors hover:text-ochre-deep"
          >
            <span className="measure">{item.objective}</span>
            <ArrowRight className="mt-1.5 shrink-0 text-ink-muted transition-colors group-hover:text-ochre-deep" />
          </Link>
          <div className="mt-3">
            <DeleteResearch sessionId={item.id} onDeleted={() => remove(item.id)} />
          </div>
        </li>
      ))}
    </ul>
  );
}

function formatDate(value: string): string {
  return new Date(value).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}
