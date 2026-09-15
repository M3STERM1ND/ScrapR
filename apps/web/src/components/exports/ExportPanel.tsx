"use client";

import { useCallback, useEffect, useState } from "react";

import {
  ApiError,
  getExport,
  listExports,
  requestExport,
  retryExport,
  type ExportFormat,
  type ExportJob,
  type ExportTheme,
} from "@/lib/api/client";
import { DownloadIcon, GenerateIcon } from "@/components/ui/icons";

/**
 * Export a version as PDF or PowerPoint (Flow D, `REQ-EXP-001..010`).
 *
 * Choose a format and one of the six themes (`REQ-EXP-003`), and the export is
 * queued at once (`REQ-EXP-007 AC-1`). Its progress shows in the list below,
 * polled while anything is still being made (`AC-2`); a failure says so and
 * offers to try again (`AC-3`). No account is asked for (`REQ-EXP-010`).
 *
 * **Download fetches a fresh link at the moment of the click.** The link is
 * signed for five minutes (`REQ-EXP-008`), so holding one in state from an
 * earlier poll would hand the reader an expired URL.
 *
 * The theme list mirrors `DEC-22` for display only: names, what each is for,
 * and a swatch of its ground and accent. The server renders from its own
 * definitions and is the authority on what each theme looks like.
 */

const THEMES: ReadonlyArray<{
  key: ExportTheme;
  name: string;
  purpose: string;
  ground: string;
  accent: string;
}> = [
  { key: "professional", name: "Professional", purpose: "Client-ready business brief", ground: "#FFFFFF", accent: "#1F4E79" },
  { key: "investor", name: "Investor", purpose: "Financial audience, figures first", ground: "#FFFFFF", accent: "#1B5E20" },
  { key: "modern", name: "Modern", purpose: "Creative and product teams", ground: "#FBF7F2", accent: "#B4442A" },
  { key: "corporate", name: "Corporate", purpose: "Internal and board papers", ground: "#FFFFFF", accent: "#00695C" },
  { key: "minimal", name: "Minimal", purpose: "Reading and printing", ground: "#FFFFFF", accent: "#111111" },
  { key: "dark", name: "Dark", purpose: "Screens and technical teams", ground: "#0F1419", accent: "#39C5CF" },
];

const FORMAT_WORD: Record<ExportFormat, string> = { pdf: "PDF", pptx: "PowerPoint" };
const STATUS_WORD: Record<ExportJob["status"], string> = {
  pending: "Queued",
  running: "Being made",
  ready: "Ready",
  failed: "Could not be made",
};

const POLL_MS = 2000;

export function ExportPanel({
  sessionId,
  versionNumber,
}: {
  sessionId: string;
  versionNumber: number;
}) {
  const [open, setOpen] = useState(false);
  const [format, setFormat] = useState<ExportFormat>("pdf");
  const [theme, setTheme] = useState<ExportTheme>("professional");
  const [jobs, setJobs] = useState<ExportJob[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setJobs(await listExports(sessionId, versionNumber));
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "Could not reach the service.");
    }
  }, [sessionId, versionNumber]);

  function onToggle() {
    const opening = !open;
    setOpen(opening);
    // Loaded when the reader opens the panel: that is the event, and the
    // list is only worth a request once someone is looking at it.
    if (opening) void refresh();
  }

  const working = jobs.some((job) => job.status === "pending" || job.status === "running");
  useEffect(() => {
    if (!open || !working) return;
    const timer = window.setInterval(() => void refresh(), POLL_MS);
    return () => window.clearInterval(timer);
  }, [open, working, refresh]);

  async function onCreate() {
    setBusy(true);
    setError(null);
    try {
      const job = await requestExport(sessionId, versionNumber, format, theme);
      setJobs((current) => [job, ...current.filter((existing) => existing.id !== job.id)]);
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "Could not reach the service.");
    } finally {
      setBusy(false);
    }
  }

  async function onDownload(job: ExportJob) {
    setError(null);
    try {
      const fresh = await getExport(job.id);
      if (fresh.download_url) {
        window.location.assign(fresh.download_url);
      } else {
        setError("That export is not ready yet.");
      }
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "Could not reach the service.");
    }
  }

  async function onRetry(job: ExportJob) {
    setError(null);
    try {
      const retried = await retryExport(job.id);
      setJobs((current) => current.map((existing) => (existing.id === retried.id ? retried : existing)));
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "Could not reach the service.");
    }
  }

  return (
    <div className="w-full">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        aria-controls="export-panel"
        className="inline-flex h-10 items-center gap-2 rounded-sm border border-line-strong px-4 text-small font-medium text-ink transition-colors duration-200 hover:border-ink hover:bg-surface"
      >
        <DownloadIcon />
        Export
      </button>

      {open ? (
        <section
          id="export-panel"
          aria-labelledby="export-heading"
          className="card mt-4 px-6 py-6"
        >
          <h2 id="export-heading" className="text-[1.25rem] leading-tight">
            Export version {versionNumber}
          </h2>
          <p className="measure mt-2 text-small text-ink-muted">
            The file carries exactly what this version says, with its citations,
            confidence and any disagreement between sources.
          </p>

          <fieldset className="mt-6">
            <legend className="claim-label">Format</legend>
            <div className="mt-2 flex flex-wrap gap-3">
              {(Object.keys(FORMAT_WORD) as ExportFormat[]).map((value) => (
                <label
                  key={value}
                  className={`flex cursor-pointer items-center gap-2 rounded-sm border px-3 py-2 text-small ${
                    format === value ? "border-ink text-ink" : "border-line text-ink-soft"
                  }`}
                >
                  <input
                    type="radio"
                    name="export-format"
                    value={value}
                    checked={format === value}
                    onChange={() => setFormat(value)}
                    className="accent-ink"
                  />
                  {FORMAT_WORD[value]}
                </label>
              ))}
            </div>
          </fieldset>

          <fieldset className="mt-6">
            <legend className="claim-label">Theme</legend>
            <div className="mt-2 grid gap-2 sm:grid-cols-2">
              {THEMES.map((option) => (
                <label
                  key={option.key}
                  className={`flex cursor-pointer items-center gap-3 rounded-sm border px-3 py-2.5 ${
                    theme === option.key ? "border-ink" : "border-line"
                  }`}
                >
                  <input
                    type="radio"
                    name="export-theme"
                    value={option.key}
                    checked={theme === option.key}
                    onChange={() => setTheme(option.key)}
                    className="accent-ink"
                  />
                  <span
                    aria-hidden
                    className="flex h-7 w-10 shrink-0 items-end overflow-hidden rounded-[4px] border border-line"
                    style={{ background: option.ground }}
                  >
                    <span className="h-2 w-full" style={{ background: option.accent }} />
                  </span>
                  <span className="flex flex-col">
                    <span className="text-small font-medium text-ink">{option.name}</span>
                    <span className="text-micro text-ink-muted">{option.purpose}</span>
                  </span>
                </label>
              ))}
            </div>
          </fieldset>

          <div className="mt-6 flex flex-wrap items-center gap-4">
            <button
              type="button"
              onClick={onCreate}
              disabled={busy}
              className="inline-flex h-11 items-center gap-2 rounded-sm bg-ink px-5 text-small font-medium text-paper shadow-soft transition-colors duration-200 hover:bg-ochre-deep disabled:cursor-not-allowed disabled:bg-line-strong disabled:text-ink-muted"
            >
              <GenerateIcon />
              {busy ? "Queuing" : `Create ${FORMAT_WORD[format]}`}
            </button>
            <p className="text-micro text-ink-muted">No account needed.</p>
          </div>

          {error ? (
            <p role="alert" className="mt-4 border-l-2 border-ochre-deep pl-3 text-small text-ink-soft">
              {error}
            </p>
          ) : null}

          {jobs.length > 0 ? (
            <ul aria-label="Exports" className="mt-6 flex flex-col border-t border-line">
              {jobs.map((job) => (
                <li key={job.id} className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-b border-line py-3">
                  <span className="text-small text-ink">
                    {FORMAT_WORD[job.format]} · {THEMES.find((t) => t.key === job.theme)?.name ?? job.theme}
                    <span className="text-ink-muted" aria-live="polite">
                      {" · "}
                      {STATUS_WORD[job.status]}
                      {job.status === "ready" && job.size_bytes ? `, ${formatSize(job.size_bytes)}` : ""}
                    </span>
                  </span>
                  {job.status === "ready" ? (
                    <button
                      type="button"
                      onClick={() => void onDownload(job)}
                      className="inline-flex items-center gap-1.5 text-small font-medium text-ochre-deep underline decoration-line-strong underline-offset-4"
                    >
                      <DownloadIcon />
                      Download
                    </button>
                  ) : job.status === "failed" ? (
                    <span className="flex flex-wrap items-center gap-3">
                      {job.error ? <span className="text-micro text-ink-muted">{job.error}</span> : null}
                      <button
                        type="button"
                        onClick={() => void onRetry(job)}
                        className="text-small font-medium text-ink underline decoration-line-strong underline-offset-4"
                      >
                        Try again
                      </button>
                    </span>
                  ) : null}
                </li>
              ))}
            </ul>
          ) : null}
        </section>
      ) : null}
    </div>
  );
}

function formatSize(bytes: number): string {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
