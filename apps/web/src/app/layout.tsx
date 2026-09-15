import type { Metadata } from "next";
import { Plus_Jakarta_Sans } from "next/font/google";
import "./globals.css";

const plusJakarta = Plus_Jakarta_Sans({
  variable: "--font-plus-jakarta",
  subsets: ["latin"],
  weight: ["500", "600", "700"],
  display: "swap",
});

/**
 * The name is "ScrapR" in text everywhere, even though the wordmark draws its S
 * as the mark: a tab, a bookmark and a search result cannot show a drawing.
 * Pages give only their own part, and the template puts the name first.
 *
 * Icons come from the file conventions beside this file — `favicon.ico`,
 * `icon.png`, `apple-icon.png` and `manifest.ts` — all cut from the one app icon.
 */
export const metadata: Metadata = {
  title: {
    default: "ScrapR",
    template: "ScrapR | %s",
  },
  applicationName: "ScrapR",
  description:
    "Ask one question and get a full report back: findings, charts, and a citation behind every claim, with the shaky parts marked as shaky.",
  metadataBase: new URL("https://scrapr.app"),
  appleWebApp: { title: "ScrapR" },
  openGraph: {
    title: "ScrapR | AI Research",
    siteName: "ScrapR",
    description:
      "Ask one question and get a full report back: findings, charts, and a citation behind every claim.",
    type: "website",
  },
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className={plusJakarta.variable}>
      <head>
        <link
          rel="preload"
          href="/fonts/Satoshi-Regular.woff2"
          as="font"
          type="font/woff2"
          crossOrigin="anonymous"
        />
      </head>
      <body className="antialiased">
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:absolute focus:left-6 focus:top-6 focus:z-[100] focus:rounded-sm focus:bg-ink focus:px-4 focus:py-2 focus:text-surface"
        >
          Skip to content
        </a>
        {children}
      </body>
    </html>
  );
}
