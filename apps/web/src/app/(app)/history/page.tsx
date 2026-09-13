import type { Metadata } from "next";

import { HistoryList } from "@/components/history/HistoryList";
import { Eyebrow } from "@/components/ui/primitives";

export const metadata: Metadata = {
  title: "Saved research — ScrapR",
  robots: { index: false, follow: false },
};

/**
 * Research history (`REQ-AUTH-005`, Flow F F-2).
 *
 * A server component that renders the frame and hands the list to the client:
 * the account cookie belongs to the browser, and fetching here would mean
 * forwarding it through the server for a page nobody else may see.
 */
export default function HistoryPage() {
  return (
    <div className="shell py-20 md:py-28">
      <div className="max-w-[52rem]">
        <Eyebrow>Saved research</Eyebrow>
        <h1 className="display-l mt-6 max-w-[16ch]">Your research</h1>
        <HistoryList />
      </div>
    </div>
  );
}
