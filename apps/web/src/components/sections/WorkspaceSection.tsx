"use client";

import { useState } from "react";

import { Reveal } from "@/components/ui/Reveal";
import { SectionIntro } from "@/components/ui/primitives";

/**
 * The workspace, shown as a workspace.
 *
 * The tabs work. That is the point of putting a real control here rather than a
 * picture of one: the page is claiming the report is something you explore, and
 * a static image would be asking you to take that on faith too.
 */

const TABS = ["Report", "Sources", "Charts", "Conversation"] as const;
type Tab = (typeof TABS)[number];

function Panel({ tab }: { tab: Tab }) {
  if (tab === "Sources") {
    return (
      <div className="flex flex-col">
        {[
          { name: "Form 10-K, filed 26 Feb", tier: "Primary", read: "6 Feb" },
          { name: "Q4 FY25 earnings call", tier: "Primary", read: "6 Feb" },
          { name: "Reuters, 14 Jan", tier: "Established press", read: "6 Feb" },
          { name: "Bureau of Labor Statistics", tier: "Official data", read: "5 Feb" },
        ].map((source) => (
          <div
            key={source.name}
            className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1 border-b border-line py-4 last:border-b-0"
          >
            <span className="text-small text-ink">{source.name}</span>
            <span className="text-micro text-ink-muted">
              {source.tier} · read {source.read}
            </span>
          </div>
        ))}
      </div>
    );
  }

  if (tab === "Charts") {
    const bars = [
      { label: "Q1", value: 42 },
      { label: "Q2", value: 58 },
      { label: "Q3", value: 51 },
      { label: "Q4", value: 96 },
    ];
    return (
      <div>
        <p className="claim-label">Revenue by quarter, $bn</p>

        {/* Bars and labels are separate rows on purpose: a percentage height
            inside an auto-height column resolves to zero, which renders an
            empty chart rather than a broken one. */}
        <div className="mt-6 flex h-40 items-end gap-4">
          {bars.map((bar, index) => (
            <div
              key={bar.label}
              className="flex-1 rounded-sm"
              style={{
                height: `${bar.value}%`,
                background:
                  index === bars.length - 1
                    ? "var(--color-ochre)"
                    : "var(--color-line-strong)",
              }}
            />
          ))}
        </div>
        <div className="mt-3 flex gap-4">
          {bars.map((bar) => (
            <span
              key={bar.label}
              className="tnum flex-1 text-center text-micro text-ink-muted"
            >
              {bar.label}
            </span>
          ))}
        </div>
        <p className="mt-5 text-micro text-ink-muted">
          Every bar traces to the filing it came from. Export keeps the chart and
          the citation together.
        </p>
      </div>
    );
  }

  if (tab === "Conversation") {
    return (
      <div className="flex flex-col gap-4">
        <p className="ml-auto max-w-[80%] rounded-md rounded-br-sm bg-ink px-4 py-3 text-small text-paper">
          Why is the second chart flat while revenue climbs?
        </p>
        <div className="max-w-[85%] rounded-md rounded-bl-sm border border-line bg-paper px-4 py-3 text-small text-ink-soft">
          Volume grew and average price fell by about the same amount. Both
          figures are in the Q3 and Q4 calls, and they are cited on the claim
          above the chart.
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-7">
      <div className="claim claim-fact">
        <p className="claim-label">Fact</p>
        <p className="measure mt-2 text-body">
          Data centre revenue reached $130.5bn in FY2025.
        </p>
      </div>
      <div className="claim claim-forecast">
        <p className="claim-label">Forecast</p>
        <p className="measure mt-2 text-body">
          Consensus has growth slowing to roughly 40% next year.
        </p>
        <p className="mt-3 text-micro text-ink-muted">
          Assumes supply constraints ease in H2 and no change to export rules.
        </p>
      </div>
    </div>
  );
}

export function WorkspaceSection() {
  const [tab, setTab] = useState<Tab>("Report");

  return (
    <section id="workspace" className="section-y">
      <div className="shell">
        <Reveal>
          <SectionIntro
            eyebrow="The workspace"
            title="Read it, poke at it, take it with you."
            body="The report is the beginning. Open any claim to see what is behind it, ask what you still want to know, and export the whole thing when you are done."
          />
        </Reveal>

        <Reveal delay={0.1} className="mt-14 md:mt-20">
          <div className="overflow-hidden rounded-lg border border-line bg-surface shadow-lift">
            <div className="flex flex-wrap items-center justify-between gap-4 border-b border-line px-6 py-4 sm:px-8">
              <div
                role="tablist"
                aria-label="Workspace"
                className="flex flex-wrap gap-1"
              >
                {TABS.map((name) => (
                  <button
                    key={name}
                    role="tab"
                    type="button"
                    aria-selected={tab === name}
                    onClick={() => setTab(name)}
                    className={`rounded-sm px-3.5 py-2 text-small transition-colors duration-200 ${
                      tab === name
                        ? "bg-sunk font-medium text-ink"
                        : "text-ink-muted hover:text-ink"
                    }`}
                  >
                    {name}
                  </button>
                ))}
              </div>

              <div className="flex items-center gap-2">
                <span className="rounded-sm border border-line-strong px-3 py-1.5 text-micro text-ink-muted">
                  Export PDF
                </span>
                <span className="rounded-sm border border-line-strong px-3 py-1.5 text-micro text-ink-muted">
                  Export PPTX
                </span>
              </div>
            </div>

            <div className="px-6 py-9 sm:px-10 sm:py-12">
              <Panel tab={tab} />
            </div>
          </div>
        </Reveal>
      </div>
    </section>
  );
}
