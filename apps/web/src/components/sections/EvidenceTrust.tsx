"use client";

import { motion } from "motion/react";
import { useState } from "react";

import { Reveal } from "@/components/ui/Reveal";
import { SectionIntro } from "@/components/ui/primitives";

/**
 * The signature hover: a citation chip that opens to show its source.
 *
 * This is moment three of the three the design system allows, and it is spent
 * here on purpose. The product's claim is that you can check the evidence, so
 * the one interaction the landing page teaches should be the act of checking.
 *
 * It also works on focus, not only hover, because a keyboard user has the same
 * question a mouse user does.
 */

const SOURCE = {
  name: "Form 10-K, filed 26 February",
  tier: "Primary source",
  read: "Read 6 February, 09:12",
  quote:
    "Data center revenue for fiscal 2025 was $115.2 billion, up 142% from a year ago.",
};

function Citation() {
  const [open, setOpen] = useState(false);

  return (
    // Not `relative`: the panel anchors to the paragraph instead, which is what
    // keeps a 24rem panel opening from a chip near the right edge inside the
    // card rather than widening the whole document.
    <span
      className="inline-block"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      <button
        type="button"
        aria-expanded={open}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onClick={() => setOpen((value) => !value)}
        className="rounded-sm text-ochre-deep underline decoration-line-strong decoration-1 underline-offset-4 transition-colors duration-200 hover:decoration-ochre-deep"
      >
        Form 10-K
      </button>

      <motion.span
        // Height is animated rather than display toggled, so the panel grows
        // out of the chip instead of appearing on top of the paragraph. Under
        // reduced motion the landing page's `MotionConfig` drops the slide and
        // keeps the fade, without the markup depending on the setting.
        initial={false}
        animate={{ opacity: open ? 1 : 0, y: open ? 0 : -6 }}
        transition={{ duration: 0.24, ease: [0.16, 1, 0.3, 1] }}
        aria-hidden={!open}
        className={`absolute left-0 top-[calc(100%+1rem)] z-20 w-full max-w-[26rem] rounded-md border border-line bg-surface p-5 text-left shadow-lift ${
          open ? "pointer-events-auto" : "pointer-events-none"
        }`}
      >
        <span className="block text-small font-medium text-ink">{SOURCE.name}</span>
        <span className="mt-1 block text-micro text-ink-muted">
          {SOURCE.tier} · {SOURCE.read}
        </span>
        <span className="mt-4 block border-l-2 border-line-strong pl-4 text-small italic text-ink-soft">
          {SOURCE.quote}
        </span>
      </motion.span>
    </span>
  );
}

export function EvidenceTrust() {
  return (
    <section id="evidence" className="section-y">
      <div className="shell">
        <div className="grid gap-14 lg:grid-cols-12 lg:gap-20">
          <Reveal className="lg:col-span-5">
            <SectionIntro
              size="m"
              eyebrow="Evidence and trust"
              title="Every fact points at where it came from."
              body="A research tool that cannot show its work is asking you to take its word for it. ScrapR does not ask."
            />

            <ul className="mt-10 flex flex-col">
              {[
                {
                  title: "Facts are separated from analysis",
                  body: "What a source said, and what ScrapR concluded from it, are never presented as the same kind of thing.",
                },
                {
                  title: "Sources are ranked, not counted",
                  body: "A filing outweighs a blog post that quotes the filing. Ten repeats of one story are still one story.",
                },
                {
                  title: "Disagreement is shown, not smoothed",
                  body: "When two sources conflict, you see both and the reason they differ, or that the reason is unknown.",
                },
                {
                  title: "Gaps are stated out loud",
                  body: "If the evidence runs out, the report says so instead of filling the hole with fluent prose.",
                },
              ].map((item) => (
                <li key={item.title} className="border-b border-line py-5 first:pt-0">
                  <p className="text-body font-medium text-ink">{item.title}</p>
                  <p className="mt-2 max-w-[46ch] text-small text-ink-soft">
                    {item.body}
                  </p>
                </li>
              ))}
            </ul>
          </Reveal>

          <Reveal delay={0.1} className="lg:col-span-7">
            <div className="rounded-lg border border-line bg-surface p-7 shadow-soft sm:p-10">
              <p className="claim-label">From the report</p>

              <p className="measure relative mt-5 text-lead text-ink">
                Data centre revenue reached $115.2bn in FY2025, according to the{" "}
                <Citation />, up 142% year over year.
              </p>

              <p className="mt-6 text-small text-ink-muted">
                Hover the citation, or tab to it. That is the whole
                interaction, and it is the same one inside the product.
              </p>

              <div className="mt-10 hairline" />

              <div className="mt-8 grid gap-6 sm:grid-cols-3">
                {[
                  { label: "Primary", body: "Filings, official data, company disclosure" },
                  { label: "Established", body: "Reuters, the trade press, analyst notes" },
                  { label: "Lower", body: "Forums, aggregators, anonymous posts" },
                ].map((tier, index) => (
                  <div key={tier.label}>
                    <div
                      aria-hidden
                      className="h-1 w-full rounded-full"
                      style={{
                        background:
                          index === 0
                            ? "var(--color-ink)"
                            : index === 1
                              ? "var(--color-line-strong)"
                              : "var(--color-line)",
                      }}
                    />
                    <p className="mt-3 text-small font-medium text-ink">{tier.label}</p>
                    <p className="mt-1 text-micro text-ink-muted">{tier.body}</p>
                  </div>
                ))}
              </div>
            </div>
          </Reveal>
        </div>
      </div>
    </section>
  );
}
