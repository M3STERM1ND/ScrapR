"use client";

import Link from "next/link";
import { useEffect, useId, useRef, useState } from "react";

import { AccountIcon, SignOutIcon, WorkspaceIcon } from "@/components/ui/icons";
import { signOutAndAnnounce } from "@/lib/account";
import type { Account } from "@/lib/api/client";

/**
 * The signed-in reader, as a small circle rather than an email address.
 *
 * The family's account icon, not a photo: accounts are email and password
 * (`DEC-16`) and carry no profile image, so there is nothing real to show and
 * nothing is invented.
 */
export function AvatarGlyph({ className = "" }: { className?: string }) {
  return (
    <span
      aria-hidden
      className={`inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-line-strong bg-surface text-ink-muted ${className}`}
    >
      <AccountIcon />
    </span>
  );
}

interface AccountDropdownProps {
  account: Account;
  /** Where to go once signed out. Omitted, the reader stays on the page. */
  onSignedOut?: () => void;
}

/**
 * The account corner, shared by the landing navbar and the product header so
 * both say the same thing about who is signed in.
 *
 * A disclosure, not an ARIA menu: two plain items do not need arrow-key roving,
 * and a button that opens a panel of ordinary links is what screen readers
 * already understand.
 */
export function AccountDropdown({ account, onSignedOut }: AccountDropdownProps) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const panelId = useId();

  useEffect(() => {
    if (!open) return;
    const onPointer = (e: PointerEvent) => {
      if (!root.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      setOpen(false);
      trigger.current?.focus();
    };
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  async function onSignOut() {
    setOpen(false);
    await signOutAndAnnounce();
    onSignedOut?.();
  }

  const item =
    "flex w-full items-center gap-2.5 px-4 py-2.5 text-left text-small text-ink-soft transition-colors duration-200 hover:bg-paper hover:text-ink";

  return (
    <div ref={root} className="relative">
      <button
        ref={trigger}
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-controls={panelId}
        aria-label="Account"
        className="flex h-10 w-10 items-center justify-center rounded-full transition-opacity duration-200 hover:opacity-80"
      >
        <AvatarGlyph className={open ? "border-ink text-ink" : ""} />
      </button>

      <div
        id={panelId}
        hidden={!open}
        className="absolute right-0 top-full z-50 mt-2 w-60 overflow-hidden rounded-sm border border-line bg-surface shadow-lift"
      >
        <div className="border-b border-line px-4 py-3">
          <p className="text-micro text-ink-muted">Signed in as</p>
          <p className="mt-0.5 truncate text-small text-ink" title={account.email}>
            {account.email}
          </p>
        </div>
        <Link href="/history" onClick={() => setOpen(false)} className={item}>
          <WorkspaceIcon className="h-4 w-4 text-ink-muted" />
          Saved research
        </Link>
        <button type="button" onClick={onSignOut} className={`${item} border-t border-line`}>
          <SignOutIcon className="h-4 w-4 text-ink-muted" />
          Sign out
        </button>
      </div>
    </div>
  );
}
