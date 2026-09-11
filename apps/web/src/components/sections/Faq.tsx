"use client";

import { useState } from "react";

import { Reveal } from "@/components/ui/Reveal";
import { SectionIntro } from "@/components/ui/primitives";

/**
 * Questions people actually ask, answered without hedging.
 *
 * Hairline-separated rows rather than boxed cards, and native `button` +
 * `aria-expanded` rather than a details/summary hack, so the keyboard and
 * screen-reader behaviour is the standard one people already know.
 */

const QUESTIONS = [
  {
    q: "How is this different from asking a chatbot?",
    a: "A chatbot answers from what it remembers. ScrapR goes and reads current sources, keeps the link to each one, and separates what a source said from what it concluded. When the evidence is thin it says so rather than writing around the gap.",
  },
  {
    q: "How long does a report take?",
    a: "Minutes, not seconds. It depends on how much there is to read. You watch the work happen rather than staring at a spinner, and the report appears when it holds together.",
  },
  {
    q: "Where do the sources come from?",
    a: "Company filings, financial data, official statistics, the trade press and the open web. Filings and official data outrank commentary, and a source that only repeats another one does not count twice.",
  },
  {
    q: "What happens when sources disagree?",
    a: "You see both, along with the reason they differ when there is one: a different period, a different definition, an estimate against a reported figure. When the reason is not clear, the disagreement is shown unresolved rather than averaged away.",
  },
  {
    q: "Can I use my own documents?",
    a: "Yes. Upload them and they are searched alongside everything else, marked clearly as yours so you can tell your numbers from the public ones.",
  },
  {
    q: "Do I need an account?",
    a: "Not to start. Your first report runs without one. Create an account when you want to keep your research and come back to it.",
  },
];

function Row({ question, answer }: { question: string; answer: string }) {
  const [open, setOpen] = useState(false);
  const id = question.replace(/\W+/g, "-").toLowerCase();

  return (
    <div className="border-b border-line">
      <h3>
        <button
          type="button"
          aria-expanded={open}
          aria-controls={id}
          onClick={() => setOpen((value) => !value)}
          className="group flex w-full items-baseline justify-between gap-8 py-6 text-left"
        >
          <span className="text-lead text-ink transition-colors duration-200 group-hover:text-ochre-deep">
            {question}
          </span>
          <span
            aria-hidden
            className="relative mt-2 h-3 w-3 shrink-0 text-ink-muted"
          >
            <span className="absolute left-0 top-1/2 h-px w-3 bg-current" />
            <span
              className={`absolute left-1/2 top-0 h-3 w-px bg-current transition-transform duration-300 ease-out ${
                open ? "scale-y-0" : "scale-y-100"
              }`}
            />
          </span>
        </button>
      </h3>

      {open ? (
        <div id={id} className="pb-7">
          <p className="measure text-body text-ink-soft">{answer}</p>
        </div>
      ) : null}
    </div>
  );
}

export function Faq() {
  return (
    <section id="faq" className="section-y">
      <div className="shell">
        <div className="grid gap-12 lg:grid-cols-12 lg:gap-20">
          <Reveal className="lg:col-span-4">
            <SectionIntro
              size="m"
              eyebrow="Questions"
              title="The things people ask first."
            />
          </Reveal>

          <Reveal delay={0.08} className="lg:col-span-8">
            <div className="border-t border-line">
              {QUESTIONS.map((item) => (
                <Row key={item.q} question={item.q} answer={item.a} />
              ))}
            </div>
          </Reveal>
        </div>
      </div>
    </section>
  );
}
