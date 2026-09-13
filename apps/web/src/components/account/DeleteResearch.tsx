"use client";

import { useState } from "react";

import { ApiError, deleteResearch } from "@/lib/api/client";

/**
 * Delete research, with a confirmation step in the page (`REQ-SEC-008`).
 *
 * Inline rather than a browser `confirm()`: a native dialog blocks the page,
 * cannot be styled to say what exactly is lost, and reads as an error rather
 * than a decision. Deletion is permanent (`DEC-18`), so the second step names
 * everything that goes.
 */
export function DeleteResearch({
  sessionId,
  onDeleted,
  compact = false,
}: {
  sessionId: string;
  onDeleted: () => void;
  compact?: boolean;
}) {
  const [confirming, setConfirming] = useState(false);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onConfirm() {
    setWorking(true);
    setError(null);
    try {
      await deleteResearch(sessionId);
      onDeleted();
    } catch (cause) {
      setError(
        cause instanceof ApiError ? cause.message : "Could not reach the service. Try again.",
      );
      setWorking(false);
    }
  }

  const quiet =
    "text-micro text-ink-muted underline decoration-line-strong underline-offset-4 transition-colors hover:text-ink";

  if (!confirming) {
    return (
      <button type="button" onClick={() => setConfirming(true)} className={quiet}>
        Delete
      </button>
    );
  }

  return (
    <div
      role="group"
      aria-label="Confirm deletion"
      className={`flex flex-wrap items-center gap-x-4 gap-y-2 ${compact ? "" : "border-l-2 border-ochre-deep pl-4"}`}
    >
      <p className="text-micro text-ink-soft">
        Delete this research, every version, its conversation, attachments and
        exports? This cannot be undone.
      </p>
      <button
        type="button"
        onClick={onConfirm}
        disabled={working}
        className="text-micro font-medium text-ochre-deep underline decoration-ochre-deep underline-offset-4 disabled:text-ink-muted"
      >
        {working ? "Deleting" : "Delete permanently"}
      </button>
      <button
        type="button"
        onClick={() => setConfirming(false)}
        disabled={working}
        className={quiet}
      >
        Keep it
      </button>
      {error ? (
        <p role="alert" className="w-full text-micro text-ink-soft">
          {error}
        </p>
      ) : null}
    </div>
  );
}
