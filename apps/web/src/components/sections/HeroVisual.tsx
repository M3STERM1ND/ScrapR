"use client";

import { motion, useReducedMotion } from "motion/react";

const STEPS = [
  { label: "Read the latest filings", state: "done" },
  { label: "Pull five years of revenue", state: "done" },
  { label: "Compare against four rivals", state: "done" },
  { label: "Reconcile two conflicting figures", state: "active" },
  { label: "Write the report", state: "queued" },
] as const;

const SOURCES = [
  { name: "SEC 10-Q filing", kind: "Primary", strength: 0.96 },
  { name: "NVIDIA investor relations", kind: "Primary", strength: 0.92 },
  { name: "Reuters", kind: "Established press", strength: 0.81 },
  { name: "Bureau of Labor Statistics", kind: "Official data", strength: 0.88 },
];

function StepMark({ state }: { state: (typeof STEPS)[number]["state"] }) {
  if (state === "done") {
    return (
      <svg viewBox="0 0 16 16" fill="none" aria-hidden className="h-4 w-4 text-ink">
        <path
          d="M3.5 8.5l3 3 6-7"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    );
  }
  if (state === "active") {
    return (
      <span aria-hidden className="flex h-4 w-4 items-center justify-center">
        <span className="h-2 w-2 rounded-full bg-ochre" />
      </span>
    );
  }
  return (
    <span aria-hidden className="flex h-4 w-4 items-center justify-center">
      <span className="h-1.5 w-1.5 rounded-full bg-line-strong" />
    </span>
  );
}

export function HeroVisual() {
  const reduced = useReducedMotion();

  return (
    <div className="relative">
      <div className="overflow-hidden rounded-lg border border-line bg-surface shadow-lift">
        {/* Objective bar */}
        <div className="flex flex-col gap-4 px-6 py-6 sm:px-9 sm:py-8 md:flex-row md:items-start md:justify-between md:gap-10">
          <div>
            <p className="text-micro text-ink-muted">Research objective</p>
            <p className="mt-2 max-w-[46ch] text-lead text-ink">
              Analyze NVIDIA as a company, an investment, and a place to work.
            </p>
          </div>
          <span className="inline-flex shrink-0 items-center gap-2 self-start rounded-full border border-line bg-paper px-3 py-1.5 text-micro text-ink-muted tnum">
            <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-ochre" />
            42 sources reviewed
          </span>
        </div>

        <div className="hairline" />

        <div className="grid gap-y-10 md:grid-cols-12">
          {/* Activity trace */}
          <div className="flex min-w-0 flex-col px-6 py-7 sm:px-9 md:col-span-5 md:py-9">
            <p className="text-micro text-ink-muted">Working</p>
            <ul className="mt-5 space-y-4">
              {STEPS.map((step, i) => (
                <motion.li
                  key={step.label}
                  className="flex items-center gap-3"
                  initial={reduced ? false : { opacity: 0, x: -6 }}
                  animate={reduced ? undefined : { opacity: 1, x: 0 }}
                  transition={{ delay: 0.35 + i * 0.09, duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
                >
                  <StepMark state={step.state} />
                  <span
                    className={`text-small ${
                      step.state === "queued" ? "text-ink-muted" : "text-ink-soft"
                    }`}
                  >
                    {step.label}
                  </span>
                </motion.li>
              ))}
            </ul>

            <div className="mt-8 flex items-center justify-between border-t border-line pt-5 md:mt-auto">
              <span className="text-micro text-ink-muted">Elapsed</span>
              <span className="text-micro text-ink-soft tnum">2 min 14 sec</span>
            </div>
          </div>

          {/* Moment 1: the source stack fans out */}
          <div className="relative min-w-0 border-t border-line px-6 pb-9 pt-7 sm:px-9 md:col-span-7 md:border-l md:border-t-0 md:py-9">
            <p className="text-micro text-ink-muted">Evidence collected</p>
            <ul className="mt-5 space-y-3">
              {SOURCES.map((source, i) => (
                <motion.li
                  key={source.name}
                  initial={reduced ? false : { opacity: 0, y: 14, rotate: -1.2, scale: 0.985 }}
                  animate={reduced ? undefined : { opacity: 1, y: 0, rotate: 0, scale: 1 }}
                  transition={{
                    delay: 0.5 + i * 0.11,
                    duration: 0.62,
                    ease: [0.16, 1, 0.3, 1],
                  }}
                  className="flex items-center justify-between gap-4 rounded-md border border-line bg-paper px-4 py-3.5"
                >
                  <span className="min-w-0">
                    <span className="block truncate text-small font-medium text-ink">
                      {source.name}
                    </span>
                    <span className="mt-0.5 block text-micro text-ink-muted">{source.kind}</span>
                  </span>
                  <span className="flex shrink-0 items-center gap-2.5">
                    <span aria-hidden className="relative block h-1 w-12 rounded-full bg-line sm:w-16">
                      <span
                        className="absolute inset-y-0 left-0 rounded-full bg-ochre"
                        style={{ width: `${source.strength * 100}%` }}
                      />
                    </span>
                    <span className="w-8 text-right text-micro text-ink-muted tnum">
                      {Math.round(source.strength * 100)}
                    </span>
                  </span>
                </motion.li>
              ))}
            </ul>
          </div>
        </div>
      </div>
    </div>
  );
}
