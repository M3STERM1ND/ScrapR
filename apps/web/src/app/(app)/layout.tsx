import Link from "next/link";
import type { ReactNode } from "react";

import { AccountMenu } from "@/components/account/AccountMenu";
import { Logo } from "@/components/ui/Logo";

/**
 * Chrome for the product surface.
 *
 * Deliberately quieter than the marketing navbar: no anchor links, no
 * scroll-reactive background, nothing that competes with the report. The
 * landing page's job is to persuade; this page's job is to get out of the way
 * of the evidence. The account corner is the one addition, and it offers an
 * account rather than asking for one (`REQ-AUTH-001 AC-3`).
 */
export default function AppLayout({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-dvh bg-paper">
      <header className="sticky top-0 z-50 border-b border-line bg-paper/85 backdrop-blur-md">
        <div className="shell flex h-16 items-center justify-between">
          {/* Not prefetched: the landing page carries the animation library,
              ~50 KB of script a reader in the middle of research rarely needs
              and the Lighthouse script budget counts. */}
          <Link href="/" prefetch={false} className="rounded-sm" aria-label="ScrapR home">
            <Logo />
          </Link>
          <AccountMenu />
        </div>
      </header>
      <main id="main">{children}</main>
    </div>
  );
}
