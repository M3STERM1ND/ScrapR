import {
  AiPoweredIcon,
  InsightsIcon,
  ResearchIcon,
  TrustedSourcesIcon,
} from "@/components/ui/icons";
import { Reveal } from "@/components/ui/Reveal";
import { SectionIntro } from "@/components/ui/primitives";

/**
 * Four capabilities, four different shapes.
 *
 * Not a row of identical cards: each panel is sized to what it has to show, and
 * each carries a small purpose-built demonstration rather than an icon. A grid
 * of equal boxes would say all four are the same weight, and they are not.
 * The small icon beside each label names the capability; the demo still does
 * the showing.
 */

function DepthDemo() {
  const areas = [
    { label: "Financial position", sources: 14 },
    { label: "Competitive landscape", sources: 11 },
    { label: "Hiring signals", sources: 7 },
    { label: "Recent developments", sources: 9 },
  ];
  const max = 14;

  return (
    <ul className="mt-8 flex flex-col gap-4">
      {areas.map((area) => (
        // Wraps on a phone: the label takes its own line and the bar and count
        // share the next one, rather than forcing a 376px row into 360px.
        <li
          key={area.label}
          className="flex flex-wrap items-center gap-x-5 gap-y-2"
        >
          <span className="w-full shrink-0 text-small text-ink-soft sm:w-40">
            {area.label}
          </span>
          <span aria-hidden className="h-1.5 min-w-0 flex-1 rounded-full bg-sunk">
            <span
              className="block h-full rounded-full bg-ink/70"
              style={{ width: `${(area.sources / max) * 100}%` }}
            />
          </span>
          <span className="tnum w-20 shrink-0 whitespace-nowrap text-right text-micro text-ink-muted">
            {area.sources} sources
          </span>
        </li>
      ))}
    </ul>
  );
}

function CitationDemo() {
  return (
    <div className="mt-7 rounded-md border border-line bg-paper p-5">
      <p className="text-small text-ink">
        Gross margin held at 75% through the year.
      </p>
      <div className="mt-4 flex flex-wrap gap-2">
        {["10-K", "Q3 call", "Reuters"].map((chip) => (
          <span
            key={chip}
            className="rounded-sm border border-line-strong px-2.5 py-1 text-micro text-ink-muted"
          >
            {chip}
          </span>
        ))}
      </div>
      <p className="mt-4 border-t border-line pt-4 text-micro text-ink-muted">
        Three sources agree. The two that only quote the filing are not counted
        twice.
      </p>
    </div>
  );
}

function VizDemo() {
  const points = [18, 26, 24, 38, 52, 49, 71];
  const width = 240;
  const height = 76;
  const step = width / (points.length - 1);
  const max = Math.max(...points);
  const path = points
    .map((value, index) => {
      const x = index * step;
      const y = height - (value / max) * height;
      return `${index === 0 ? "M" : "L"}${x.toFixed(1)} ${y.toFixed(1)}`;
    })
    .join(" ");

  return (
    <svg
      viewBox={`0 0 ${width} ${height + 6}`}
      className="mt-7 w-full"
      role="img"
      aria-label="A line rising over seven periods."
    >
      <path d={path} fill="none" stroke="var(--color-ochre)" strokeWidth="2" />
      <circle
        cx={width}
        cy={height - (points[points.length - 1] / max) * height}
        r="3.5"
        fill="var(--color-ochre)"
      />
    </svg>
  );
}

function ConversationDemo() {
  return (
    <div className="mt-7 flex flex-col gap-4">
      <p className="ml-auto max-w-[85%] rounded-md rounded-br-sm bg-ink px-4 py-3 text-small text-paper">
        Which of those competitors is closest on price?
      </p>
      <div className="max-w-[90%] rounded-md rounded-bl-sm border border-line bg-paper px-4 py-3">
        <p className="text-small text-ink-soft">
          AMD, on list price. The gap is under 8% on comparable parts, though
          only two of the four configurations are public.
        </p>
        <p className="mt-3 text-micro text-ink-muted">
          Answered from the evidence already gathered. No new search needed.
        </p>
      </div>
    </div>
  );
}

export function Features() {
  return (
    <section id="features" className="section-y">
      <div className="shell">
        <Reveal>
          <SectionIntro
            eyebrow="What it does"
            title="Four things, done properly."
            body="Not a feature list. These are the parts that decide whether a report is worth reading."
          />
        </Reveal>

        <div className="mt-16 grid gap-6 md:mt-24 lg:grid-cols-12">
          <Reveal className="lg:col-span-7">
            <div className="h-full rounded-lg border border-line bg-surface p-8 shadow-soft sm:p-10">
              <p className="claim-label flex items-center gap-2">
                <ResearchIcon className="h-4 w-4 text-ink" />
                Deep research
              </p>
              <h3 className="display-m mt-4 max-w-[18ch] text-ink">
                It keeps going until the question is answered.
              </h3>
              <p className="measure mt-5 text-body text-ink-soft">
                ScrapR breaks your question into the areas it actually depends on,
                then works each one until the evidence is enough. Not one search
                and a summary.
              </p>
              <DepthDemo />
            </div>
          </Reveal>

          <Reveal delay={0.06} className="lg:col-span-5">
            <div className="h-full rounded-lg border border-line bg-surface p-8 shadow-soft sm:p-10">
              <p className="claim-label flex items-center gap-2">
                <TrustedSourcesIcon className="h-4 w-4 text-ink" />
                Citations
              </p>
              <h3 className="mt-4 text-lead font-medium text-ink">
                Every factual line carries its source
              </h3>
              <p className="mt-4 text-small text-ink-soft">
                With the time it was read, so you can tell fresh from stale at a
                glance.
              </p>
              <CitationDemo />
            </div>
          </Reveal>

          <Reveal delay={0.12} className="lg:col-span-5">
            <div className="h-full rounded-lg border border-line bg-surface p-8 shadow-soft sm:p-10">
              <p className="claim-label flex items-center gap-2">
                <InsightsIcon className="h-4 w-4 text-ink" />
                Visualizations
              </p>
              <h3 className="mt-4 text-lead font-medium text-ink">
                Charts built from sourced numbers
              </h3>
              <p className="mt-4 text-small text-ink-soft">
                A chart only appears when there is real data behind it. Nothing
                decorative, nothing invented.
              </p>
              <VizDemo />
            </div>
          </Reveal>

          <Reveal delay={0.18} className="lg:col-span-7">
            <div className="h-full rounded-lg border border-line bg-surface p-8 shadow-soft sm:p-10">
              <p className="claim-label flex items-center gap-2">
                <AiPoweredIcon className="h-4 w-4 text-ink" />
                Follow-up
              </p>
              <h3 className="mt-4 text-lead font-medium text-ink">
                Ask about what you just read
              </h3>
              <p className="measure mt-4 text-small text-ink-soft">
                Point at a claim and ask why. The answer comes from the same
                evidence, with the same citations, and ScrapR goes looking again
                only when it needs to.
              </p>
              <ConversationDemo />
            </div>
          </Reveal>
        </div>
      </div>
    </section>
  );
}
