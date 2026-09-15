"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";

import { AccountDropdown } from "@/components/account/AccountAvatar";
import { useAccount } from "@/lib/useAccount";

/**
 * The account corner of the product header (`REQ-AUTH-001 AC-3`).
 *
 * Offered, never forced: a visitor who has not signed in sees two quiet links,
 * and nothing about the page they are on changes. A signed-in reader sees where
 * their saved research is, and an avatar that holds who they are and how to
 * leave — the email itself stays out of the header.
 *
 * Asks the API rather than reading a cookie, because the session cookie is
 * HttpOnly and no script can see it — which is the point of it.
 */
export function AccountMenu() {
  const router = useRouter();
  const account = useAccount();

  const link =
    "text-small text-ink-muted transition-colors duration-200 hover:text-ink";

  // Reserve the space while asking, so the header does not jump.
  if (account === undefined) {
    return <span className="inline-block h-5 w-40" aria-hidden />;
  }

  if (account === null) {
    return (
      <nav aria-label="Account" className="flex items-center gap-6">
        <Link href="/research/new" className={link}>
          New research
        </Link>
        <Link href="/signin" className={link}>
          Sign in
        </Link>
      </nav>
    );
  }

  return (
    <nav aria-label="Account" className="flex items-center gap-6">
      <Link href="/research/new" className={link}>
        New research
      </Link>
      <Link href="/history" className={link}>
        Saved research
      </Link>
      <AccountDropdown account={account} onSignedOut={() => router.push("/research/new")} />
    </nav>
  );
}
