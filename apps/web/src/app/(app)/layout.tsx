import Link from "next/link";
import type { ReactNode } from "react";

import { Logo } from "@/components/ui/Logo";

/**
 * Chrome for the product surface.
 *
 * Deliberately quieter than the marketing navbar: no anchor links, no
 * scroll-reactive background, nothing that competes with the report. The
 * landing page's job is to persuade; this page's job is to get out of the way
 * of the evidence.
 */
export default function AppLayout({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-dvh bg-paper">
      <header className="sticky top-0 z-50 border-b border-line bg-paper/85 backdrop-blur-md">
        <div className="shell flex h-16 items-center justify-between">
          <Link href="/" className="rounded-sm" aria-label="ScrapR home">
            <Logo />
          </Link>
          <Link
            href="/research/new"
            className="text-small text-ink-muted transition-colors duration-200 hover:text-ink"
          >
            New research
          </Link>
        </div>
      </header>
      <main id="main">{children}</main>
    </div>
  );
}
