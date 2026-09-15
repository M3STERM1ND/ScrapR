import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { Workspace } from "@/components/workspace/Workspace";

export const metadata: Metadata = {
  title: "Research version",
  robots: { index: false, follow: false },
};

/**
 * One version of a piece of research (`REQ-VER-008 AC-2`).
 *
 * The same workspace, pinned to a version. Earlier versions stay readable in
 * full after an update (`REQ-VER-002 AC-1`), and this is the address they are
 * read at. Ownership is checked by the API on every fetch, exactly as for the
 * latest version.
 */
export default async function VersionPage({
  params,
}: {
  params: Promise<{ id: string; n: string }>;
}) {
  const { id, n } = await params;
  const versionNumber = Number(n);
  if (!Number.isInteger(versionNumber) || versionNumber < 1) notFound();
  return <Workspace sessionId={id} versionNumber={versionNumber} />;
}
