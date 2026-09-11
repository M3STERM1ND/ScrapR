import { Reveal } from "@/components/ui/Reveal";
import { SectionIntro } from "@/components/ui/primitives";

/**
 * Saved research, and what makes returning to it worth anything: versions.
 *
 * The list is the visual. A history panel that looks like a history panel says
 * more than an illustration of one, and it lets the section make its real
 * point in the margin, where the version counts sit.
 */

const HISTORY = [
  {
    objective: "How is NVIDIA positioned against its competitors?",
    when: "6 February",
    versions: 3,
    note: "Updated after Q4 results",
  },
  {
    objective: "What is driving margin compression at Chipotle?",
    when: "28 January",
    versions: 1,
    note: "",
  },
  {
    objective: "Which companies are hiring hardest in humanoid robotics?",
    when: "14 January",
    versions: 2,
    note: "Updated after two new job posts appeared",
  },
  {
    objective: "Is the enterprise AI market consolidating?",
    when: "3 January",
    versions: 1,
    note: "",
  },
];

export function SearchHistory() {
  return (
    <section id="history" className="section-y">
      <div className="shell">
        <div className="grid gap-14 lg:grid-cols-12 lg:gap-20">
          <Reveal className="lg:col-span-4">
            <SectionIntro
              size="m"
              eyebrow="Saved research"
              title="It is still there next month."
              body="Every report you run is kept, exactly as it was. Come back and read it, or run it again and see what changed."
            />
            <p className="measure mt-8 text-small text-ink-muted">
              An update never overwrites the old version. The figures you read in
              January stay the figures you read in January, with the dates they
              were read.
            </p>
          </Reveal>

          <Reveal delay={0.08} className="lg:col-span-8">
            <ul className="border-t border-line">
              {HISTORY.map((item) => (
                <li key={item.objective} className="group border-b border-line">
                  <div className="flex flex-col gap-2 py-6 sm:flex-row sm:items-baseline sm:justify-between sm:gap-10">
                    <div>
                      <p className="text-body text-ink transition-colors duration-200 group-hover:text-ochre-deep">
                        {item.objective}
                      </p>
                      {item.note ? (
                        <p className="mt-1.5 text-micro text-ink-muted">{item.note}</p>
                      ) : null}
                    </div>
                    <div className="flex shrink-0 items-baseline gap-6">
                      <span className="tnum text-micro text-ink-muted">{item.when}</span>
                      <span className="tnum text-micro text-ink-soft">
                        {item.versions === 1
                          ? "1 version"
                          : `${item.versions} versions`}
                      </span>
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          </Reveal>
        </div>
      </div>
    </section>
  );
}
