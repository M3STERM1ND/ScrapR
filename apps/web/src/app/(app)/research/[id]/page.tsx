import type { Metadata } from "next";

import { Workspace } from "@/components/workspace/Workspace";

export const metadata: Metadata = {
  title: "Research",
  // Research is private to its owner (`REQ-SEC-016`), so there is nothing here
  // for a crawler to index even if a link were shared.
  robots: { index: false, follow: false },
};

/**
 * The workspace route.
 *
 * A server component that does nothing but hand the id to the client: the
 * session cookie is the authorization, and it belongs to the browser. Fetching
 * here would mean either forwarding the cookie through the server or rendering
 * somebody else's research, and neither is worth the first paint.
 *
 * `params` is a promise in Next 16 and must be awaited.
 */
export default async function WorkspacePage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <Workspace sessionId={id} />;
}
