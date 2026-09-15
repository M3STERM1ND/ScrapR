/**
 * Small pieces of account state the product surface shares.
 *
 * The session itself lives in an HttpOnly cookie the browser holds and scripts
 * cannot read (`DEC-16`), so the client never knows it is signed in except by
 * asking the API. These helpers keep the places that ask in agreement.
 */

import { signOut } from "@/lib/api/client";

/** Fired on `window` after signing in, up or out, so the header re-asks. */
export const ACCOUNT_CHANGED = "scrapr:account-changed";

export function announceAccountChange(): void {
  window.dispatchEvent(new Event(ACCOUNT_CHANGED));
}

/**
 * Sign out, then tell every header on the page to re-ask.
 *
 * Announced even if the request fails: the re-ask reports whatever the server
 * actually holds, so a sign-out that did not happen still shows as signed in.
 */
export async function signOutAndAnnounce(): Promise<void> {
  try {
    await signOut();
  } finally {
    announceAccountChange();
  }
}

/**
 * Where to go after signing in, if the link that sent the reader here said.
 *
 * Only a same-site path is honoured. `//evil.example` and `https://…` are both
 * refused, because a sign-in page that redirects wherever its query string
 * points is an open redirect with a trustworthy-looking URL in front of it.
 */
export function safeNext(raw: string | string[] | undefined): string | null {
  const value = Array.isArray(raw) ? raw[0] : raw;
  if (!value || !value.startsWith("/") || value.startsWith("//") || value.includes("\\")) {
    return null;
  }
  return value;
}
