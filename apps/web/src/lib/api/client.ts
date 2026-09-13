/**
 * The typed client the app talks to the API through.
 *
 * Types come from `schema.ts`, which is generated from `packages/contracts/openapi.json`,
 * which is generated from the FastAPI app. Nothing here is hand-maintained: a route
 * whose shape changes breaks this file at build time rather than in production
 * (implementation plan §6.2).
 *
 * Two things this module owns, because getting either wrong is silent:
 *
 * - **Credentials are always included.** Ownership is carried by an HttpOnly cookie,
 *   so a fetch without `credentials` reaches the API as an anonymous stranger and
 *   gets a 401 that looks like a bug in the backend.
 * - **Errors arrive in one envelope.** The API answers every failure with
 *   `{ error: { code, message } }`, and `message` is the only text safe to show a
 *   user — internal detail never crosses that boundary, so there is nothing else
 *   worth reading.
 */

import type { components, paths } from "./schema";

type Schemas = components["schemas"];

export type CreateResearchRequest = Schemas["CreateResearchRequest"];
export type CreateResearchResponse = Schemas["CreateResearchResponse"];
export type ResearchSession = Schemas["ResearchSessionOut"];
export type Version = Schemas["VersionOut"];
export type Claim = Schemas["ClaimOut"];
export type Section = Schemas["SectionOut"];
export type Source = Schemas["SourceOut"];
export type Conflict = Schemas["ConflictOut"];
export type ConflictSide = Schemas["ConflictSideOut"];
export type EvidenceItem = Schemas["EvidenceOut"];
export type Message = Schemas["MessageOut"];
export type AskResult = Schemas["AskOut"];
export type VisualizationSpec = Schemas["VisualizationOut"];
export type ActivityEvent = Schemas["ActivityEventOut"];
export type ActivityPage = Schemas["ActivityPage"];

/** Where the API lives. Same-origin in production; a dev server otherwise. */
export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

/** A failure the API reported, carrying the one message that is safe to display. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

type ErrorEnvelope = { error: { code: string; message: string } };

function isErrorEnvelope(value: unknown): value is ErrorEnvelope {
  if (typeof value !== "object" || value === null || !("error" in value)) {
    return false;
  }
  const { error } = value as { error: unknown };
  return (
    typeof error === "object" &&
    error !== null &&
    "code" in error &&
    "message" in error
  );
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    // The anonymous session cookie is the entire authorization story.
    credentials: "include",
    headers: { "content-type": "application/json", ...init.headers },
  });

  if (!response.ok) {
    // A failure that is not the envelope means the request never reached the
    // application — a proxy, a network error, a crash before the handlers ran.
    const body: unknown = await response.json().catch(() => null);
    throw isErrorEnvelope(body)
      ? new ApiError(response.status, body.error.code, body.error.message)
      : new ApiError(
          response.status,
          "unavailable",
          "The service is unavailable. Please try again.",
        );
  }

  return (await response.json()) as T;
}

/** Start research. Returns immediately: the report does not exist yet. */
export function createResearch(
  body: CreateResearchRequest,
): Promise<CreateResearchResponse> {
  return request<CreateResearchResponse>("/v1/research", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/** The session header: objective, status, and the versions produced so far. */
export function getResearch(sessionId: string): Promise<ResearchSession> {
  return request<ResearchSession>(`/v1/research/${sessionId}`);
}

/** One whole version — sections, claims, sources and the citation map. */
export function getVersion(
  sessionId: string,
  versionNumber: number,
): Promise<Version> {
  return request<Version>(
    `/v1/research/${sessionId}/versions/${versionNumber}`,
  );
}

/**
 * Activity newer than `after`.
 *
 * `after` is a sequence number, not a timestamp, and the response says what to
 * send next. That is what lets this become a stream later without the caller
 * changing: the resume key is identical either way (implementation plan §6.3).
 */
export function getActivity(
  sessionId: string,
  after = 0,
): Promise<ActivityPage> {
  const query = new URLSearchParams({ after: String(after) });
  return request<ActivityPage>(`/v1/research/${sessionId}/activity?${query}`);
}

/** Proof the generated paths are the ones this module calls. */
type KnownPaths = keyof paths;
type UsedPath =
  | "/v1/research"
  | "/v1/research/{session_id}"
  | "/v1/research/{session_id}/versions/{version_number}"
  | "/v1/research/{session_id}/activity";
const _pathsExist: UsedPath extends KnownPaths ? true : never = true;
void _pathsExist;

/** The conversation so far (`REQ-CONV-007 AC-2`). */
export function getMessages(sessionId: string): Promise<Message[]> {
  return request<Message[]>(`/v1/research/${sessionId}/messages`);
}

/**
 * Ask a follow-up (`REQ-CONV-001`).
 *
 * Answered synchronously, unlike starting research: a question against
 * evidence already gathered is one model call, and polling for it would make
 * the reader wait on machinery built for a job that takes minutes.
 */
export function ask(sessionId: string, question: string): Promise<AskResult> {
  return request<AskResult>(`/v1/research/${sessionId}/messages`, {
    method: "POST",
    body: JSON.stringify({ question }),
  });
}

/* -------------------------------------------------------------------------
 * Uploads (`REQ-INPUT-004`, `REQ-DOC-001..003`, `REQ-DOC-010`)
 * ---------------------------------------------------------------------- */

export type UploadTicketRequest = Schemas["UploadTicketRequest"];
export type UploadTicket = Schemas["UploadTicket"];
export type Upload = Schemas["UploadOut"];
export type UploadState = Upload["state"];

/**
 * What the browser is allowed to attach, and what to call each kind.
 *
 * Mirrors the server's allowlist so the file picker filters rather than
 * offering everything and rejecting most of it. The server is still the
 * authority: this list is a convenience, and `REQ-DOC-010 AC-1` is satisfied
 * on the other side of the wire.
 */
export const ACCEPTED_UPLOAD_TYPES: Record<string, string> = {
  "application/pdf": "PDF",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
    "Word",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "Excel",
  "text/csv": "CSV",
  "text/plain": "Text",
  "text/markdown": "Markdown",
};

/** Extensions for the picker, since browsers report `.md` and `.csv` unevenly. */
export const ACCEPTED_UPLOAD_EXTENSIONS =
  ".pdf,.docx,.xlsx,.csv,.txt,.md,.markdown";

export const MAX_UPLOAD_BYTES = 25 * 1024 * 1024;

/**
 * The content type to declare for a file.
 *
 * Browsers report `text/markdown` as `text/plain` or as an empty string, and
 * `.csv` as `application/vnd.ms-excel` on Windows. Rather than let the picker's
 * guess decide, the extension picks among the text formats — the same rule the
 * server applies to the bytes, so the two agree.
 */
export function declaredContentType(file: File): string {
  const extension = file.name.split(".").pop()?.toLowerCase() ?? "";

  if (extension === "md" || extension === "markdown") return "text/markdown";
  if (extension === "csv") return "text/csv";
  if (extension === "txt") return "text/plain";
  if (extension === "pdf") return "application/pdf";
  if (extension === "docx") {
    return "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
  }
  if (extension === "xlsx") {
    return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
  }
  return file.type;
}

/** Reserve a slot and get a URL to PUT one file to. */
export function createUploadTicket(
  sessionId: string,
  body: UploadTicketRequest,
): Promise<UploadTicket> {
  return request<UploadTicket>(`/v1/research/${sessionId}/uploads`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/**
 * PUT the bytes straight to storage.
 *
 * Not through `request`: this does not go to our API at all (implementation
 * plan §10), so it carries no credentials, expects no JSON envelope, and must
 * send exactly the content type that was signed into the URL — a different one
 * fails the signature check rather than storing a mislabelled object.
 */
export async function putToStorage(
  ticket: UploadTicket,
  file: File,
): Promise<void> {
  const response = await fetch(ticket.url, {
    method: "PUT",
    body: file,
    headers: { "content-type": ticket.content_type },
  });

  if (!response.ok) {
    throw new ApiError(
      response.status,
      "upload_failed",
      "That file could not be uploaded. Check your connection and try again.",
    );
  }
}

/** Tell the API the bytes are in place, so the worker can read them. */
export function completeUpload(
  sessionId: string,
  uploadId: string,
): Promise<Upload> {
  return request<Upload>(
    `/v1/research/${sessionId}/uploads/${uploadId}/complete`,
    { method: "POST" },
  );
}

/** Every file attached to a session, with its processing state. */
export function listUploads(sessionId: string): Promise<Upload[]> {
  return request<Upload[]>(`/v1/research/${sessionId}/uploads`);
}

/** Remove a file and its extracted text (`REQ-SEC-008 AC-2`). */
export async function deleteUpload(
  sessionId: string,
  uploadId: string,
): Promise<void> {
  const response = await fetch(
    `${API_BASE_URL}/v1/research/${sessionId}/uploads/${uploadId}`,
    { method: "DELETE", credentials: "include" },
  );

  if (!response.ok) {
    throw new ApiError(
      response.status,
      "delete_failed",
      "That file could not be removed. Try again in a moment.",
    );
  }
}

/**
 * Enqueue the run for a session created with `defer_start`.
 *
 * Idempotent on the server, so a retry after a dropped connection is safe.
 */
export function startResearch(
  sessionId: string,
): Promise<CreateResearchResponse> {
  return request<CreateResearchResponse>(`/v1/research/${sessionId}/start`, {
    method: "POST",
  });
}

/**
 * Attach one file end to end: presign, PUT, complete.
 *
 * Three calls rather than one because the middle one does not touch our API.
 * Grouped here so no caller can do two of the three and leave a `pending` row
 * pointing at bytes that never arrived.
 */
export async function attachFile(
  sessionId: string,
  file: File,
): Promise<Upload> {
  const ticket = await createUploadTicket(sessionId, {
    filename: file.name,
    content_type: declaredContentType(file),
    size_bytes: file.size,
  });

  await putToStorage(ticket, file);
  return completeUpload(sessionId, ticket.upload_id);
}
