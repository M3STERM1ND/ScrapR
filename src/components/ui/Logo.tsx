export function Logo({ tone = "light" }: { tone?: "light" | "dark" }) {
  const ink = tone === "dark" ? "#FFFEFC" : "#171614";
  return (
    <span className="inline-flex items-center gap-2.5">
      <svg viewBox="0 0 24 24" aria-hidden className="h-6 w-6">
        <rect
          x="1.75"
          y="1.75"
          width="20.5"
          height="20.5"
          rx="6"
          stroke={ink}
          strokeWidth="1.5"
          fill="none"
        />
        <path
          d="M7.5 15.25c1.9 1.6 4.1 1.9 5.6.9 2-1.35.2-3.3-1.9-3.9-2.1-.6-3.4-2.1-1.9-3.6 1.3-1.3 3.4-1.1 5 .1"
          stroke="#C2761B"
          strokeWidth="1.6"
          strokeLinecap="round"
          fill="none"
        />
      </svg>
      <span
        className="text-[1.0625rem] font-semibold tracking-[-0.02em]"
        style={{ fontFamily: "var(--font-display)", color: ink }}
      >
        ScrapR
      </span>
    </span>
  );
}
