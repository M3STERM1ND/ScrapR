import type { ReactNode } from "react";

import { DocumentIcon } from "@/components/ui/icons";

export function Eyebrow({
  children,
  tone = "light",
}: {
  children: ReactNode;
  tone?: "light" | "dark";
}) {
  return (
    <p
      className={`flex items-center gap-3 text-micro font-medium ${
        tone === "dark" ? "text-ochre-light" : "text-ochre-deep"
      }`}
    >
      <span
        aria-hidden
        className={`inline-block h-px w-8 ${tone === "dark" ? "bg-ochre/60" : "bg-ochre/50"}`}
      />
      {children}
    </p>
  );
}

export function SectionIntro({
  eyebrow,
  title,
  body,
  tone = "light",
  align = "left",
  size = "l",
  className = "",
}: {
  eyebrow: string;
  title: ReactNode;
  body?: ReactNode;
  tone?: "light" | "dark";
  align?: "left" | "center";
  /** `m` for a heading that sits in a narrow column, where the full display
   * size breaks into four cramped lines instead of reading as a statement. */
  size?: "l" | "m";
  className?: string;
}) {
  return (
    <div
      className={`${align === "center" ? "mx-auto flex flex-col items-center text-center" : ""} ${className}`}
    >
      <Eyebrow tone={tone}>{eyebrow}</Eyebrow>
      <h2
        className={`${size === "l" ? "display-l" : "display-m"} mt-6 max-w-[20ch] ${
          tone === "dark" ? "text-surface" : "text-ink"
        }`}
      >
        {title}
      </h2>
      {body ? (
        <p
          className={`mt-6 measure text-lead ${tone === "dark" ? "text-line-strong" : "text-ink-soft"}`}
        >
          {body}
        </p>
      ) : null}
    </div>
  );
}

type ButtonProps = {
  children: ReactNode;
  href: string;
  variant?: "primary" | "ghost" | "onDark";
  className?: string;
};

export function ButtonLink({
  children,
  href,
  variant = "primary",
  className = "",
}: ButtonProps) {
  const base =
    "group inline-flex h-12 items-center justify-center gap-2 rounded-sm px-6 text-small font-medium transition-[background-color,color,border-color,box-shadow,transform] duration-200 ease-out will-change-transform";

  const variants = {
    primary:
      "bg-ink text-paper shadow-soft hover:bg-ochre-deep hover:shadow-lift active:translate-y-px",
    ghost:
      "border border-line-strong bg-transparent text-ink hover:border-ink hover:bg-surface active:translate-y-px",
    onDark:
      "bg-surface text-ink hover:bg-ochre hover:text-surface active:translate-y-px",
  } as const;

  return (
    <a href={href} className={`${base} ${variants[variant]} ${className}`}>
      {children}
    </a>
  );
}

/**
 * The mark on a citation to something the reader uploaded (`REQ-DOC-008 AC-1`).
 *
 * "Your document", not "Document": the distinction the requirement is drawing
 * is between material the reader supplied and material ScrapR went and found,
 * and the possessive is what says that in two words.
 */
export function DocumentMark({ className = "" }: { className?: string }) {
  return (
    <span className={`document-mark ${className}`}>
      <DocumentIcon />
      Your document
    </span>
  );
}
