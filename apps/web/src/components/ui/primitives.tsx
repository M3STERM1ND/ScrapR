import type { ReactNode } from "react";

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
        tone === "dark" ? "text-ochre" : "text-ochre-deep"
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
  className = "",
}: {
  eyebrow: string;
  title: ReactNode;
  body?: ReactNode;
  tone?: "light" | "dark";
  align?: "left" | "center";
  className?: string;
}) {
  return (
    <div
      className={`${align === "center" ? "mx-auto flex flex-col items-center text-center" : ""} ${className}`}
    >
      <Eyebrow tone={tone}>{eyebrow}</Eyebrow>
      <h2
        className={`display-l mt-6 max-w-[20ch] ${tone === "dark" ? "text-surface" : "text-ink"}`}
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

export function ArrowRight({ className = "" }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 16 16"
      fill="none"
      aria-hidden
      className={`h-4 w-4 transition-transform duration-200 ease-out group-hover:translate-x-0.5 ${className}`}
    >
      <path
        d="M2.5 8h11m0 0L9.5 4m4 4-4 4"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
