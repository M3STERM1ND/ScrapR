import { Reveal } from "@/components/ui/Reveal";
import { SectionIntro } from "@/components/ui/primitives";

/**
 * Ask → Research → Understand.
 *
 * Three steps on one baseline, separated by hairlines rather than boxed into
 * cards. The numerals carry the rhythm: they are the largest type in the
 * section and the only ochre in it, so the eye reads the sequence before it
 * reads a word.
 */

const STEPS = [
  {
    n: "01",
    title: "Ask",
    body: "Type the question the way you would ask a colleague. No keywords, no filters, no filling in a brief.",
    detail: "Add a company or a link if it helps. Most people do not need to.",
  },
  {
    n: "02",
    title: "Research",
    body: "ScrapR decides what to look into, reads the filings, the financials, the news and the open web, and keeps going until the question is actually answered.",
    detail: "You watch it work. Every step is named in plain language.",
  },
  {
    n: "03",
    title: "Understand",
    body: "A written report with charts where they earn their place, a source behind every fact, and the uncertain parts marked as uncertain.",
    detail: "Ask follow-ups. Come back next month and update it.",
  },
];

export function HowItWorks() {
  return (
    <section id="how-it-works" className="section-y">
      <div className="shell">
        <Reveal>
          <SectionIntro
            eyebrow="How it works"
            title="Three steps, and only one of them is yours."
            body="The work in the middle is the product. You ask, and you read what comes back."
          />
        </Reveal>

        <div className="mt-16 grid gap-px border-t border-line md:mt-24 md:grid-cols-3">
          {STEPS.map((step, index) => (
            <Reveal
              key={step.n}
              delay={index * 0.08}
              className="relative pt-8 md:pt-10 md:pr-10 md:[&:not(:last-child)]:border-r md:[&:not(:last-child)]:border-line"
            >
              <p className="display-m text-ochre-deep tnum">{step.n}</p>
              <h3 className="mt-5 text-lead font-medium text-ink">{step.title}</h3>
              <p className="mt-4 max-w-[34ch] text-body text-ink-soft">{step.body}</p>
              <p className="mt-4 max-w-[34ch] text-small text-ink-muted">
                {step.detail}
              </p>
            </Reveal>
          ))}
        </div>
      </div>
    </section>
  );
}
