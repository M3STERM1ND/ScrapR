"use client";

import Link from "next/link";
import { useEffect } from "react";

import { Logo } from "@/components/ui/Logo";
import { Eyebrow } from "@/components/ui/primitives";

/**
 * The fallback when a page fails to render.
 *
 * Research itself is never lost to this: it lives in the database and the
 * page only reads it, so trying again is always safe. The error's message is
 * not shown; it can carry detail that is not the reader's (`REQ-SEC-010 AC-5`).
 */
export default function ErrorPage({
  error,
  retry,
}: {
  error: Error & { digest?: string };
  retry: () => void;
}) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <div className="min-h-dvh bg-paper">
      <header className="border-b border-line">
        <div className="shell flex h-16 items-center">
          <Link href="/" className="rounded-sm" aria-label="ScrapR home">
            <Logo />
          </Link>
        </div>
      </header>
      <main id="main" className="shell py-20 md:py-28">
        <div className="max-w-[40rem]">
          <Eyebrow>Something went wrong</Eyebrow>
          <h1 className="display-l mt-6 max-w-[16ch]">This page could not be shown</h1>
          <p className="mt-6 text-body text-ink-soft">
            Your research is saved. Try again, and if this keeps happening, come back in a few
            minutes.
          </p>
          <div className="mt-10 flex flex-wrap gap-3">
            <button
              type="button"
              onClick={() => retry()}
              className="inline-flex h-12 items-center justify-center rounded-sm bg-ink px-6 text-small font-medium text-paper shadow-soft transition-colors duration-200 hover:bg-ochre-deep"
            >
              Try again
            </button>
            <Link
              href="/"
              className="inline-flex h-12 items-center justify-center rounded-sm border border-line-strong px-6 text-small font-medium text-ink transition-colors duration-200 hover:border-ink hover:bg-surface"
            >
              Go to the start
            </Link>
          </div>
        </div>
      </main>
    </div>
  );
}
