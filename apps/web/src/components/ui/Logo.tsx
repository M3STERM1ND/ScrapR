import { sparkPath } from "@/components/ui/icons";

/**
 * The ScrapR wordmark: the mark *is* the S, so it reads "[S]crapR".
 *
 * The mark is traced from the app icon (`src/app/icon.png`) at 25 px to the
 * unit — the open bracket, the S inside it and the spark in the bracket's gap —
 * so the header and the browser tab carry the same drawing. Vector rather than
 * the PNG itself, because the icon's tile and shadow belong to a home screen,
 * not to a letter in a line of type.
 *
 * The visible "crapR" is hidden from assistive technology and the whole name
 * is given once, so a screen reader says "ScrapR" rather than "crapR".
 */
export function Logo({ tone = "light" }: { tone?: "light" | "dark" }) {
  const ink = tone === "dark" ? "#FFFEFC" : "#171614";
  return (
    <span className="inline-flex items-center">
      <span className="sr-only">ScrapR</span>
      <svg
        viewBox="0.75 0.75 23.25 22.75"
        fill="none"
        aria-hidden
        focusable="false"
        className="h-[1.375rem] w-[1.375rem] shrink-0"
      >
        <path
          d="M19.78 8.28V5.36a3.4 3.4 0 0 0-3.4-3.4H5.42a3.4 3.4 0 0 0-3.4 3.4v13.5a3.4 3.4 0 0 0 3.4 3.4h10.96a3.4 3.4 0 0 0 3.29-2.54"
          stroke={ink}
          strokeWidth="2"
          strokeLinecap="round"
        />
        <path
          d="M14.48 8.2C13.7 6.9 12.4 6.2 11 6.2 8.9 6.2 6.72 7.5 6.72 9.6c0 1.7 1.88 2.2 3.98 2.5 2.2.3 4.06.8 4.06 2.5 0 1.6-1.86 2.64-4.28 2.64-1.78 0-3.18-.74-4-1.76"
          stroke={ink}
          strokeWidth="2"
          strokeLinecap="round"
        />
        <path d={sparkPath(20.18, 14.26, 3.54, 3.9)} fill="var(--color-spark)" />
      </svg>
      <span
        aria-hidden
        className="ml-[0.0625rem] text-[1.0625rem] font-semibold tracking-[-0.02em]"
        style={{ fontFamily: "var(--font-display)", color: ink }}
      >
        crapR
      </span>
    </span>
  );
}
