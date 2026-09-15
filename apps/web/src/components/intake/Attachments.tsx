"use client";

import { useRef, useState, type ChangeEvent, type DragEvent } from "react";

import {
  ACCEPTED_UPLOAD_EXTENSIONS,
  MAX_UPLOAD_BYTES,
  declaredContentType,
  ACCEPTED_UPLOAD_TYPES,
} from "@/lib/api/client";
import { UploadIcon } from "@/components/ui/icons";

/**
 * Attaching documents at intake (`REQ-INPUT-004`, `REQ-DOC-001`).
 *
 * The files are held in the browser until the form is submitted. Nothing is
 * uploaded on selection, because an upload needs a research session to belong
 * to and the session does not exist until the reader asks for research. Picking
 * a file is not a commitment to run anything.
 *
 * **Optional, and it looks optional** (`AC-3`). This sits inside the same folded
 * context panel as the other three optional fields, so the page still reads as
 * one question rather than as a form with an uploader bolted to it.
 *
 * Client-side checks here are for immediate feedback only. The server enforces
 * the same limits and is the authority (`REQ-DOC-010 AC-1`): the point of
 * checking twice is that a reader learns their 40 MB PDF is too large before
 * they wait for it to transfer, not that the browser is trusted.
 */

const MAX_FILES = 10;
const MAX_TOTAL_BYTES = 100 * 1024 * 1024;

export type Attachment = {
  file: File;
  /** Why this file cannot be sent, or null. Shown, never silently dropped. */
  problem: string | null;
};

export function Attachments({
  files,
  onChange,
  disabled = false,
}: {
  files: Attachment[];
  onChange: (files: Attachment[]) => void;
  disabled?: boolean;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);

  function add(incoming: FileList | null) {
    if (!incoming || disabled) return;

    const next = [...files];
    for (const file of Array.from(incoming)) {
      // Same name and size twice is the reader picking the same file again,
      // which is a slip rather than an intent to attach it twice.
      const duplicate = next.some(
        (held) => held.file.name === file.name && held.file.size === file.size,
      );
      if (duplicate) continue;
      next.push({ file, problem: check(file, next) });
    }

    onChange(next.slice(0, MAX_FILES));
    // Cleared so picking the same file after removing it fires a change event.
    if (input.current) input.current.value = "";
  }

  function remove(index: number) {
    const next = files.filter((_, position) => position !== index);
    // Re-checked, because removing a large file can make a later one fit.
    onChange(
      next.map((held, position) => ({
        ...held,
        problem: check(held.file, next.slice(0, position)),
      })),
    );
  }

  function onDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragging(false);
    add(event.dataTransfer.files);
  }

  return (
    <div>
      <p className="claim-label">Documents (optional)</p>

      <div
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        className={`well mt-2 flex flex-col items-start gap-2 bg-sunk px-4 py-4 transition-shadow duration-200 ${
          dragging ? "shadow-soft" : ""
        }`}
      >
        <button
          type="button"
          onClick={() => input.current?.click()}
          disabled={disabled || files.length >= MAX_FILES}
          className="group inline-flex items-center gap-2 text-small text-ink underline decoration-line-strong underline-offset-4 transition-colors hover:text-ochre-deep disabled:cursor-not-allowed disabled:text-ink-muted disabled:no-underline"
        >
          <UploadIcon />
          {files.length >= MAX_FILES
            ? `That is the limit of ${MAX_FILES} files`
            : "Choose files, or drop them here"}
        </button>

        <p className="text-micro text-ink-muted">
          {Object.values(ACCEPTED_UPLOAD_TYPES)
            .filter((label, index, all) => all.indexOf(label) === index)
            .join(", ")}
          . Up to 25 MB each.
        </p>

        <input
          ref={input}
          type="file"
          multiple
          accept={ACCEPTED_UPLOAD_EXTENSIONS}
          onChange={(event: ChangeEvent<HTMLInputElement>) =>
            add(event.target.files)
          }
          className="sr-only"
          tabIndex={-1}
        />
      </div>

      {files.length > 0 ? (
        <ul className="mt-3">
          {files.map((held, index) => (
            <li key={`${held.file.name}-${held.file.size}`} className="attachment-row">
              <span className="min-w-0 flex-1">
                <span className="block truncate text-small text-ink-soft">
                  {held.file.name}
                </span>
                {held.problem ? (
                  <span className="attachment-state attachment-failed">
                    {held.problem}
                  </span>
                ) : (
                  <span className="attachment-state">
                    {megabytes(held.file.size)}
                  </span>
                )}
              </span>

              <button
                type="button"
                onClick={() => remove(index)}
                disabled={disabled}
                className="text-micro text-ink-muted underline decoration-line-strong underline-offset-4 transition-colors hover:text-ink disabled:cursor-not-allowed"
              >
                Remove
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

/**
 * Whether this file can be sent, given the ones already held.
 *
 * Returns the sentence to show, so the reason a file was refused is always
 * attached to that file rather than to the form as a whole. A reader with six
 * attachments and one error needs to know which one.
 */
function check(file: File, existing: Attachment[]): string | null {
  if (file.size === 0) return "This file is empty.";

  if (file.size > MAX_UPLOAD_BYTES) {
    return `This file is ${megabytes(file.size)}. The limit is 25 MB per file.`;
  }

  if (!(declaredContentType(file) in ACCEPTED_UPLOAD_TYPES)) {
    return "ScrapR cannot read this kind of file.";
  }

  const total = existing.reduce((sum, held) => sum + held.file.size, 0);
  if (total + file.size > MAX_TOTAL_BYTES) {
    return "This would take the total past 100 MB.";
  }

  return null;
}

function megabytes(size: number): string {
  const value = size / (1024 * 1024);
  return value < 0.1 ? "under 0.1 MB" : `${value.toFixed(1)} MB`;
}
