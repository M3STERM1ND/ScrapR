import { Reveal } from "@/components/ui/Reveal";
import { SectionIntro } from "@/components/ui/primitives";

/**
 * A real page of a real report, not a screenshot-shaped rectangle.
 *
 * The chart is hand-authored inline SVG on the design system's own palette.
 * That is a deliberate choice the implementation plan notes (§11.3): it renders
 * identically in a browser and in a headless PDF pipeline, it adds no runtime
 * dependency, and it keeps the marketing page honest about what an exported
 * report will actually look like.
 */

const REVENUE = [
  { period: "FY21", value: 16.7 },
  { period: "FY22", value: 26.9 },
  { period: "FY23", value: 27.0 },
  { period: "FY24", value: 60.9 },
  { period: "FY25", value: 130.5 },
];

const MAX = 140;
const CHART_W = 560;
const CHART_H = 200;
const BAR_GAP = 26;

function RevenueChart() {
  const barWidth = (CHART_W - BAR_GAP * (REVENUE.length - 1)) / REVENUE.length;

  return (
    <figure className="mt-7">
      <figcaption className="flex items-baseline justify-between gap-6">
        <span className="text-small font-medium text-ink">
          Revenue, reported, by fiscal year
        </span>
        <span className="text-micro text-ink-muted">$bn</span>
      </figcaption>

      <svg
        // Negative y origin: the tallest bar's value label sits above the bar,
        // and without headroom it is clipped by the viewBox edge.
        viewBox={`0 -18 ${CHART_W} ${CHART_H + 52}`}
        className="mt-4 w-full"
        role="img"
        aria-label="Revenue by fiscal year: 16.7, 26.9, 27.0, 60.9 and 130.5 billion dollars from FY21 to FY25."
      >
        {[0, 0.5, 1].map((fraction) => (
          <line
            key={fraction}
            x1="0"
            x2={CHART_W}
            y1={CHART_H - fraction * CHART_H}
            y2={CHART_H - fraction * CHART_H}
            stroke="var(--color-line)"
            strokeWidth="1"
          />
        ))}

        {REVENUE.map((point, index) => {
          const height = (point.value / MAX) * CHART_H;
          const x = index * (barWidth + BAR_GAP);
          const isLatest = index === REVENUE.length - 1;
          return (
            <g key={point.period}>
              <rect
                x={x}
                y={CHART_H - height}
                width={barWidth}
                height={height}
                rx="3"
                fill={isLatest ? "var(--color-ochre)" : "var(--color-line-strong)"}
              />
              <text
                x={x + barWidth / 2}
                y={CHART_H - height - 9}
                textAnchor="middle"
                fontSize="12"
                fill={isLatest ? "var(--color-ochre-deep)" : "var(--color-ink-muted)"}
                style={{ fontVariantNumeric: "tabular-nums" }}
              >
                {point.value}
              </text>
              <text
                x={x + barWidth / 2}
                y={CHART_H + 22}
                textAnchor="middle"
                fontSize="12"
                fill="var(--color-ink-muted)"
              >
                {point.period}
              </text>
            </g>
          );
        })}
      </svg>

      <p className="mt-3 text-micro text-ink-muted">
        Built from five annual reports. Every figure traces back to the filing it
        came from.
      </p>
    </figure>
  );
}

export function ResearchPreview() {
  return (
    <section id="preview" className="section-y">
      <div className="shell">
        <Reveal>
          <SectionIntro
            eyebrow="What you get back"
            title="A report, not a wall of links."
            body="Sections you can read top to bottom, charts built from the numbers in the sources, and a citation on every factual line."
          />
        </Reveal>

        <Reveal delay={0.1} className="mt-14 md:mt-20">
          <article className="overflow-hidden rounded-lg border border-line bg-surface shadow-lift">
            <header className="flex flex-wrap items-baseline justify-between gap-4 border-b border-line px-7 py-6 sm:px-10 sm:py-8">
              <div>
                <p className="text-micro text-ink-muted">Report · version 2</p>
                <h3 className="mt-2 text-lead text-ink">
                  How is NVIDIA positioned against its competitors?
                </h3>
              </div>
              <p className="text-micro text-ink-muted tnum">
                41 sources · updated 6 Feb
              </p>
            </header>

            <div className="grid gap-10 px-7 py-9 sm:px-10 sm:py-12 lg:grid-cols-[minmax(0,1fr)_minmax(0,21rem)] lg:gap-16">
              <div>
                <h4 className="display-m text-ink">Revenue and share</h4>

                <div className="mt-7 flex flex-col gap-7">
                  <div className="claim claim-fact">
                    <p className="claim-label">Fact</p>
                    <p className="measure mt-2 text-body">
                      Data centre revenue reached $130.5bn in FY2025, up 114% year
                      over year.
                    </p>
                    <p className="mt-3 text-micro text-ink-muted">
                      <span className="text-ochre-deep underline decoration-line-strong underline-offset-4">
                        Form 10-K, filed 26 Feb
                      </span>{" "}
                      · read 6 Feb
                    </p>
                  </div>

                  <div className="claim claim-analysis">
                    <p className="claim-label">Analysis</p>
                    <p className="measure mt-2 text-body">
                      Growth is concentrated in a small number of hyperscale
                      buyers, so the revenue line is less diversified than the
                      total suggests.
                    </p>
                    <p className="mt-3 text-micro text-ink-muted">
                      Drawn from four sources. ScrapR&rsquo;s reading, not a quote.
                    </p>
                  </div>

                  <div className="claim claim-uncertainty">
                    <p className="claim-label">Uncertain</p>
                    <p className="measure mt-2 text-body">
                      Second-half pricing is not disclosed. Two press estimates
                      disagree by 18%, and neither is primary.
                    </p>
                  </div>
                </div>

                <RevenueChart />
              </div>

              <aside className="border-t border-line pt-8 lg:border-l lg:border-t-0 lg:pl-10 lg:pt-0">
                <p className="claim-label">Sources behind this section</p>
                <ul className="mt-5 flex flex-col">
                  {[
                    { name: "Form 10-K", tier: "Primary" },
                    { name: "Q4 earnings call", tier: "Primary" },
                    { name: "Reuters", tier: "Established press" },
                    { name: "Bureau of Labor Statistics", tier: "Official data" },
                  ].map((source) => (
                    <li
                      key={source.name}
                      className="flex items-baseline justify-between gap-4 border-b border-line py-3 last:border-b-0"
                    >
                      <span className="text-small text-ink-soft">{source.name}</span>
                      <span className="text-micro text-ink-muted">{source.tier}</span>
                    </li>
                  ))}
                </ul>
                <p className="mt-6 text-micro text-ink-muted">
                  Every source keeps the time it was read, so a figure from
                  February is never quietly presented as today&rsquo;s.
                </p>
              </aside>
            </div>
          </article>
        </Reveal>
      </div>
    </section>
  );
}
