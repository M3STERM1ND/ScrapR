import type { Metadata } from "next";
import Link from "next/link";

import { Logo } from "@/components/ui/Logo";
import { ButtonLink, Eyebrow } from "@/components/ui/primitives";

export const metadata: Metadata = {
  title: "Page not found — ScrapR",
  robots: { index: false, follow: false },
};

/**
 * Any address nothing answers, and any `notFound()` a page raises.
 *
 * Research that belongs to someone else is also "not found" rather than
 * "forbidden" (`REQ-SEC-002`), so the copy does not guess which it was.
 */
export default function NotFound() {
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
          <Eyebrow>Not found</Eyebrow>
          <h1 className="display-l mt-6 max-w-[16ch]">There is nothing at this address</h1>
          <p className="mt-6 text-body text-ink-soft">
            The link may be mistyped, or the research it pointed to may have been deleted.
          </p>
          <div className="mt-10 flex flex-wrap gap-3">
            <ButtonLink href="/research/new">Start new research</ButtonLink>
            <ButtonLink href="/history" variant="ghost">
              Saved research
            </ButtonLink>
          </div>
        </div>
      </main>
    </div>
  );
}
