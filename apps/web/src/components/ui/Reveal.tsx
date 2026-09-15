"use client";

import { motion } from "motion/react";
import type { ReactNode } from "react";

type RevealProps = {
  children: ReactNode;
  delay?: number;
  className?: string;
  as?: "div" | "li" | "section" | "header" | "article";
};

/**
 * Moment 2 of 3: sections rise a little as they enter, once, then stay put.
 *
 * The same markup whatever the reader's motion setting. The server cannot see
 * that setting, so branching on it here rendered a hidden starting frame that
 * the browser's first render then disagreed with, and the content never
 * appeared. Reduced motion is handled by the `MotionConfig` around the landing
 * page instead: the rise is dropped and only the fade remains.
 */
export function Reveal({ children, delay = 0, className, as = "div" }: RevealProps) {
  const Tag = motion[as];

  return (
    <Tag
      className={className}
      initial={{ opacity: 0, y: 16 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: "-12% 0px -8% 0px" }}
      transition={{ duration: 0.56, delay, ease: [0.16, 1, 0.3, 1] }}
    >
      {children}
    </Tag>
  );
}
