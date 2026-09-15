"use client";

import { motion, useReducedMotion } from "motion/react";
import { ArrowRight } from "@/components/ui/icons";
import { ButtonLink, Eyebrow } from "@/components/ui/primitives";
import { HeroVisual } from "./HeroVisual";

const LINES = ["Ask one question.", "Get research you can check."];

export function Hero() {
  const reduced = useReducedMotion();
  const rise = (delay: number) =>
    reduced
      ? {}
      : {
          initial: { opacity: 0, y: 14 },
          animate: { opacity: 1, y: 0 },
          transition: { duration: 0.62, delay, ease: [0.16, 1, 0.3, 1] as const },
        };

  return (
    <section id="top" className="relative overflow-hidden pt-32 pb-20 md:pt-40 md:pb-28">
      {/* one quiet atmospheric wash, no gradient blobs */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-x-0 top-0 h-[560px]"
        style={{
          background:
            "radial-gradient(115% 62% at 12% 0%, rgba(194,118,27,0.075) 0%, rgba(194,118,27,0) 60%)",
        }}
      />

      <div className="shell relative">
        <motion.div {...rise(0)}>
          <Eyebrow>Autonomous research agent</Eyebrow>
        </motion.div>

        <h1 className="display-xl mt-8 text-ink">
          {LINES.map((line, i) => (
            <span key={line} className="block overflow-hidden pb-[0.06em]">
              <motion.span
                className="block"
                initial={reduced ? false : { y: "108%" }}
                animate={reduced ? undefined : { y: "0%" }}
                transition={{ duration: 0.82, delay: 0.08 + i * 0.1, ease: [0.16, 1, 0.3, 1] }}
              >
                {line}
              </motion.span>
            </span>
          ))}
        </h1>

        <motion.div className="mt-14 hairline md:mt-16" {...rise(0.38)} />

        <div className="grid gap-y-9 pt-9 md:grid-cols-12 md:items-start md:gap-x-16">
          <motion.p
            className="measure text-lead text-ink-soft md:col-span-6 lg:col-span-5"
            {...rise(0.44)}
          >
            ScrapR reads the filings, the news, and the rest of the open web, then hands back a full
            report. Charts where they help, a source behind every claim, and a straight answer when
            the evidence runs out.
          </motion.p>

          <motion.div
            className="md:col-span-6 md:flex md:flex-col md:items-end lg:col-span-7"
            {...rise(0.5)}
          >
            <div className="flex flex-wrap items-center gap-3">
              <ButtonLink href="/research/new">
                Start researching
                <ArrowRight />
              </ButtonLink>
              <ButtonLink href="#preview" variant="ghost">
                See a sample report
              </ButtonLink>
            </div>
            <p className="mt-5 text-micro text-ink-muted">
              No account needed for your first report.
            </p>
          </motion.div>
        </div>

        <motion.div
          className="mt-20 md:mt-28"
          initial={reduced ? false : { opacity: 0, y: 26 }}
          animate={reduced ? undefined : { opacity: 1, y: 0 }}
          transition={{ duration: 0.85, delay: 0.3, ease: [0.16, 1, 0.3, 1] }}
        >
          <HeroVisual />
        </motion.div>
      </div>
    </section>
  );
}
