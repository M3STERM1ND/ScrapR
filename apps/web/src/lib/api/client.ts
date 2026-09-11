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
