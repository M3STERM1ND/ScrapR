"use client";

import { useEffect, useState } from "react";
import { Logo } from "@/components/ui/Logo";

const LINKS = [
  { label: "How it works", href: "#how-it-works" },
  { label: "Evidence", href: "#evidence" },
  { label: "Workspace", href: "#workspace" },
  { label: "FAQ", href: "#faq" },
];

export function Navbar() {
  const [scrolled, setScrolled] = useState(false);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    let frame = 0;
    const onScroll = () => {
      if (frame) return;
      frame = window.requestAnimationFrame(() => {
        setScrolled(window.scrollY > 12);
        frame = 0;
      });
    };
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      window.removeEventListener("scroll", onScroll);
      if (frame) window.cancelAnimationFrame(frame);
    };
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <header
      className={`fixed inset-x-0 top-0 z-50 transition-[background-color,border-color,backdrop-filter] duration-300 ease-out ${
        scrolled || open
          ? "border-b border-line bg-paper/85 backdrop-blur-md"
          : "border-b border-transparent bg-transparent"
      }`}
    >
      <nav aria-label="Main" className="shell flex h-18 items-center justify-between">
        <a href="#top" className="rounded-sm" aria-label="ScrapR home">
          <Logo />
        </a>

        <ul className="hidden items-center gap-9 lg:flex">
          {LINKS.map((link) => (
            <li key={link.href}>
              <a
                href={link.href}
                className="relative text-small text-ink-muted transition-colors duration-200 hover:text-ink"
              >
                {link.label}
              </a>
            </li>
          ))}
        </ul>

        <div className="hidden items-center gap-2 md:flex">
          <a
            href="#start"
            className="rounded-sm px-4 py-2 text-small text-ink-muted transition-colors duration-200 hover:text-ink"
          >
            Sign in
          </a>
          <a
            href="#start"
            className="inline-flex h-10 items-center rounded-sm bg-ink px-5 text-small font-medium text-paper transition-colors duration-200 hover:bg-ochre-deep"
          >
            Start researching
          </a>
        </div>

        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          aria-controls="mobile-nav"
          aria-label={open ? "Close menu" : "Open menu"}
          className="-mr-2 flex h-11 w-11 items-center justify-center rounded-sm text-ink md:hidden"
        >
          <svg viewBox="0 0 20 20" fill="none" aria-hidden className="h-5 w-5">
            {open ? (
              <path
                d="M5 5l10 10M15 5L5 15"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
              />
            ) : (
              <path
                d="M3 6h14M3 13h14"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
              />
            )}
          </svg>
        </button>
      </nav>

      <div
        id="mobile-nav"
        hidden={!open}
        className="border-t border-line bg-paper md:hidden"
      >
        <ul className="shell flex flex-col py-4">
          {LINKS.map((link) => (
            <li key={link.href}>
              <a
                href={link.href}
                onClick={() => setOpen(false)}
                className="block py-3 text-body text-ink-soft"
              >
                {link.label}
              </a>
            </li>
          ))}
          <li className="mt-3">
            <a
              href="#start"
              onClick={() => setOpen(false)}
              className="inline-flex h-12 w-full items-center justify-center rounded-sm bg-ink text-small font-medium text-paper"
            >
              Start researching
            </a>
          </li>
        </ul>
      </div>
    </header>
  );
}
