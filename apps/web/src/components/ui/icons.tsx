import type { ReactNode, SVGProps } from "react";

/**
 * The ScrapR icon family.
 *
 * One module, so there is one place an icon comes from. MASTER.md asks for
 * inline SVG at a 1.5px stroke; every icon here draws on a 24-unit grid with a
 * 1.75-unit stroke, which is 1.5px at 20px. The stroke scales with the icon,
 * so a 16px one beside a caption keeps the same proportions as a 24px one
 * rather than filling in — a fixed pixel stroke turns the brain and the gear
 * into blots at caption size.
 *
 * Linework is `currentColor` and follows the text it sits beside. The warm
 * accents — the spark, and the faint wash inside a shield, folder or bulb — are
 * the one colour the family adds, taken from the app icon.
 *
 * **Decorative by default.** Most icons here sit next to a word that already
 * says what they mean, and announcing both reads the label twice. Pass `label`
 * only when the icon stands alone.
 */

export interface IconProps extends Omit<SVGProps<SVGSVGElement>, "children"> {
  /** Accessible name. Omitted, the icon is hidden from assistive technology. */
  label?: string;
  className?: string;
}

const SPARK = "var(--color-spark)";

/** Stroke attributes every line in the family shares. */
const LINE = {
  stroke: "currentColor",
  strokeWidth: 1.75,
  strokeLinecap: "round",
  strokeLinejoin: "round",
} as const;

/** The faint warm fill inside a closed shape. */
const WASH = { fill: SPARK, fillOpacity: 0.25 } as const;

function Icon({
  label,
  className = "h-4 w-4",
  children,
  ...rest
}: IconProps & { children: ReactNode }) {
  const a11y = label
    ? { role: "img", "aria-label": label }
    : { "aria-hidden": true, focusable: false };
  return (
    <svg viewBox="0 0 24 24" fill="none" className={`shrink-0 ${className}`} {...a11y} {...rest}>
      {children}
    </svg>
  );
}

/**
 * A four-point spark, concave-sided like the one in the ScrapR mark.
 * Shared with the logo so the two are the same shape.
 */
export function sparkPath(cx: number, cy: number, rx: number, ry = rx): string {
  const kx = rx * 0.2;
  const ky = ry * 0.2;
  return [
    `M${cx} ${cy - ry}`,
    `Q${cx + kx} ${cy - ky} ${cx + rx} ${cy}`,
    `Q${cx + kx} ${cy + ky} ${cx} ${cy + ry}`,
    `Q${cx - kx} ${cy + ky} ${cx - rx} ${cy}`,
    `Q${cx - kx} ${cy - ky} ${cx} ${cy - ry}Z`,
  ].join("");
}

/* ---- The ScrapR set -------------------------------------------------- */

/** Research: a lens with a spark inside it. */
export function ResearchIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <circle cx="10.5" cy="10.5" r="6.75" {...LINE} />
      <path d="M15.5 15.5 20 20" {...LINE} />
      <path d={sparkPath(10.5, 10.5, 3)} fill={SPARK} />
    </Icon>
  );
}

/** Generate: a document, with a spark where it is being made. */
export function GenerateIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path
        d="M17.25 11V5.5a1.75 1.75 0 0 0-1.75-1.75h-9A1.75 1.75 0 0 0 4.75 5.5v13a1.75 1.75 0 0 0 1.75 1.75H12"
        {...LINE}
      />
      <path d="M8.25 8h5.5M8.25 11.5h5.5M8.25 15h2.5" {...LINE} />
      <path d={sparkPath(17.5, 17.25, 3.5)} fill={SPARK} />
    </Icon>
  );
}

/** Trusted sources: a shield with a check. */
export function TrustedSourcesIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path
        d="M12 3.5 5.25 6v5.25c0 4.2 2.85 7.6 6.75 9.25 3.9-1.65 6.75-5.05 6.75-9.25V6L12 3.5Z"
        {...WASH}
        {...LINE}
      />
      <path d="m9 12.25 2.1 2.1 3.9-4.1" {...LINE} />
    </Icon>
  );
}

/** Insights: three rising bars. */
export function InsightsIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <rect x="4.25" y="13.25" width="3.5" height="6.5" rx="1" {...LINE} />
      <rect x="10.25" y="8.75" width="3.5" height="11" rx="1" {...WASH} {...LINE} />
      <rect x="16.25" y="4.25" width="3.5" height="15.5" rx="1" {...WASH} {...LINE} />
    </Icon>
  );
}

/** AI powered: two hemispheres and their folds. */
export function AiPoweredIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path
        d="M12 5.5C11 4 8.4 4.1 7.9 6.3 5.8 6.4 4.6 8.5 5.5 10.2 3.9 11.2 4 13.9 5.8 14.7 5.3 16.8 7 18.6 9 18.2 9.7 20 11.4 20.2 12 19"
        {...LINE}
      />
      <path
        d="M12 5.5C13 4 15.6 4.1 16.1 6.3 18.2 6.4 19.4 8.5 18.5 10.2 20.1 11.2 20 13.9 18.2 14.7 18.7 16.8 17 18.6 15 18.2 14.3 20 12.6 20.2 12 19"
        {...LINE}
      />
      <path d="M12 5.5V19" {...LINE} />
      <path d="M12 9.75c-1.4 0-2.5-.8-2.9-2M12 14.25c-1.5 0-2.8.8-3 2.1" {...LINE} />
      <path d="M12 9.75c1.4 0 2.5-.8 2.9-2M12 14.25c1.5 0 2.8.8 3 2.1" {...LINE} />
    </Icon>
  );
}

/** Workspace: a folder. */
export function WorkspaceIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path
        d="M3.75 6.5c0-.97.78-1.75 1.75-1.75h3.8l2 2.25h7.2c.97 0 1.75.78 1.75 1.75v8.75c0 .97-.78 1.75-1.75 1.75h-13c-.97 0-1.75-.78-1.75-1.75V6.5Z"
        {...WASH}
        {...LINE}
      />
    </Icon>
  );
}

/** Upload: a cloud with an arrow rising out of it. */
export function UploadIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path
        d="M8.25 18.25H7a4 4 0 0 1-.55-7.96 5.75 5.75 0 0 1 11.1-.9A4.25 4.25 0 0 1 17 18.25h-1.25"
        {...LINE}
      />
      <path d="M12 20.25v-8.5M9 14.5l3-3 3 3" {...LINE} />
    </Icon>
  );
}

/** Download: an arrow landing in a tray. */
export function DownloadIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M12 4v10.5M7.75 10.5 12 14.75l4.25-4.25" {...LINE} />
      <path d="M4.75 15.25v3c0 .83.67 1.5 1.5 1.5h11.5c.83 0 1.5-.67 1.5-1.5v-3" {...LINE} />
    </Icon>
  );
}

/** Account: a head and shoulders. */
export function AccountIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <circle cx="12" cy="8.25" r="3.75" {...LINE} />
      <path d="M4.75 20c.9-3.65 3.75-5.75 7.25-5.75S18.35 16.35 19.25 20" {...LINE} />
    </Icon>
  );
}

/** Sign out: an arrow leaving through a door. */
export function SignOutIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M10 4.75H6.5c-.97 0-1.75.78-1.75 1.75v11c0 .97.78 1.75 1.75 1.75H10" {...LINE} />
      <path d="M10.5 12h9M16 8.5l3.5 3.5-3.5 3.5" {...LINE} />
    </Icon>
  );
}

/** Ideas: a lit bulb. */
export function IdeasIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path
        d="M9.4 17c0-1.75-.85-2.6-1.8-3.65a5.25 5.25 0 1 1 8.8 0c-.95 1.05-1.8 1.9-1.8 3.65H9.4Z"
        {...WASH}
        {...LINE}
      />
      <path d="M10 20.25h4M12 2.75V4M5.6 5.35l.9.9M18.4 5.35l-.9.9M3.5 11h1.25M19.25 11h1.25" {...LINE} />
    </Icon>
  );
}

/** Settings: a gear. */
export function SettingsIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path
        d="M9.81 5.61 10.26 3.42h3.48l.45 2.19a6.75 6.75 0 0 1 .78.33l1.86-1.23 2.46 2.46-1.23 1.86a6.75 6.75 0 0 1 .33.78l2.19.45v3.48l-2.19.45a6.75 6.75 0 0 1-.33.78l1.23 1.86-2.46 2.46-1.86-1.23a6.75 6.75 0 0 1-.78.33l-.45 2.19h-3.48l-.45-2.19a6.75 6.75 0 0 1-.78-.33l-1.86 1.23-2.46-2.46 1.23-1.86a6.75 6.75 0 0 1-.33-.78l-2.19-.45v-3.48l2.19-.45a6.75 6.75 0 0 1 .33-.78L4.71 7.17l2.46-2.46 1.86 1.23a6.75 6.75 0 0 1 .78-.33Z"
        {...LINE}
      />
      <circle cx="12" cy="12" r="2.75" {...LINE} />
    </Icon>
  );
}

/* ---- Interface marks -------------------------------------------------- */
/* Drawn on a 16-unit grid, where they have always lived: an arrow and a page
   are simple enough that the finer grid only moves their points off-pixel. */

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

/** A document, drawn rather than typed. */
export function DocumentIcon({ className = "" }: { className?: string }) {
  return (
    <svg viewBox="0 0 16 16" fill="none" aria-hidden className={`h-3.5 w-3.5 ${className}`}>
      <path
        d="M9 1.5H4.5A1.5 1.5 0 0 0 3 3v10a1.5 1.5 0 0 0 1.5 1.5h7A1.5 1.5 0 0 0 13 13V5.5L9 1.5Z"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinejoin="round"
      />
      <path d="M9 1.5V5.5H13" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" />
    </svg>
  );
}
