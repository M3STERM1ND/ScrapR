import Link from "next/link";

import { Logo } from "@/components/ui/Logo";
import { ArrowRight } from "@/components/ui/primitives";

/**
 * A closing invitation, then the smallest possible footer.
 *
 * The call to action gets the last full band of the page; the links underneath
 * are kept deliberately plain, because a footer stuffed with columns is a
 * footer nobody reads.
 */

const LINKS = [
  { label: "How it works", href: "#how-it-works" },
  { label: "Evidence", href: "#evidence" },
  { label: "Workspace", href: "#workspace" },
  { label: "FAQ", href: "#faq" },
];

export function Footer() {
  return (
    <footer id="start" className="border-t border-line">
      <div className="shell py-20 md:py-28">
        <div className="grid gap-10 lg:grid-cols-12 lg:items-end lg:gap-20">
          <div className="lg:col-span-7">
            <h2 className="display-l max-w-[15ch] text-ink">
              Ask the question you actually have.
            </h2>
            <p className="measure mt-6 text-lead text-ink-soft">
              No account, no setup. Type it, and read what comes back.
            </p>
          </div>

          <div className="lg:col-span-5 lg:flex lg:justify-end">
            <Link
              href="/research/new"
              className="group inline-flex h-13 items-center justify-center gap-2 rounded-sm bg-ink px-7 text-small font-medium text-paper shadow-soft transition-[background-color,box-shadow,transform] duration-200 ease-out hover:bg-ochre-deep hover:shadow-lift active:translate-y-px"
            >
              Start researching
              <ArrowRight />
            </Link>
          </div>
        </div>

        <div className="mt-20 hairline md:mt-28" />

        <div className="mt-8 flex flex-col gap-8 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <Link href="/" aria-label="ScrapR home" className="inline-block rounded-sm">
              <Logo />
            </Link>
            <p className="mt-3 text-micro text-ink-muted">
              Research that shows its sources.
            </p>
          </div>

          <nav aria-label="Footer">
            <ul className="flex flex-wrap gap-x-8 gap-y-3">
              {LINKS.map((link) => (
                <li key={link.href}>
                  <a
                    href={link.href}
                    className="text-small text-ink-muted transition-colors duration-200 hover:text-ink"
                  >
                    {link.label}
                  </a>
                </li>
              ))}
            </ul>
          </nav>
        </div>
      </div>
    </footer>
  );
}
