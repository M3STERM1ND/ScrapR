import { Reveal } from "@/components/ui/Reveal";
import { Eyebrow } from "@/components/ui/primitives";

/**
 * The one inverted section on the page.
 *
 * Night ground, used once, to break the rhythm of warm paper at roughly the
 * two-thirds mark and to let the recency story feel like a different register.
 * The design system allows an inverted section; spending it twice would make
 * neither one land.
 */

const ITEMS = [
  {
    when: "2 hours ago",
    source: "Reuters",
    headline: "Chip supplier raises full-year guidance on data centre demand",
    tag: "Established press",
  },
  {
    when: "Yesterday",
    source: "SEC EDGAR",
    headline: "8-K filed: executive transition at a top-five customer",
    tag: "Primary",
  },
  {
    when: "3 days ago",
    source: "Bureau of Labor Statistics",
    headline: "Semiconductor employment up 4.1% year over year",
    tag: "Official data",
  },
];

export function RecentNews() {
  return (
    <section id="news" className="bg-night">
      <div className="shell section-y">
        <div className="grid gap-12 lg:grid-cols-12 lg:gap-20">
          <Reveal className="lg:col-span-5">
            <Eyebrow tone="dark">Recent developments</Eyebrow>
            <h2 className="display-m mt-6 max-w-[18ch] text-surface">
              Research that knows what happened this week.
            </h2>
            <p className="measure mt-6 text-lead text-line-strong">
              A report built on last quarter is a history lesson. ScrapR pulls
              current filings and press alongside the background, and tells you
              when each one was published.
            </p>
          </Reveal>

          <Reveal delay={0.08} className="lg:col-span-7">
            <ul className="border-t border-night-line">
              {ITEMS.map((item) => (
                <li
                  key={item.headline}
                  className="border-b border-night-line py-6"
                >
                  <div className="flex items-baseline justify-between gap-6">
                    <span className="text-micro text-line-strong tnum">
                      {item.when}
                    </span>
                    <span className="text-micro text-line-strong/80">{item.tag}</span>
                  </div>
                  <p className="mt-3 max-w-[44ch] text-body text-surface">
                    {item.headline}
                  </p>
                  <p className="mt-2 text-micro text-ochre-light">{item.source}</p>
                </li>
              ))}
            </ul>

            <p className="mt-8 text-small text-line-strong">
              Published dates and retrieval dates are kept apart, so a story from
              last March is never dressed up as news.
            </p>
          </Reveal>
        </div>
      </div>
    </section>
  );
}
