import type { Metadata } from "next";

import { ObjectiveForm } from "@/components/intake/ObjectiveForm";
import { Eyebrow } from "@/components/ui/primitives";

export const metadata: Metadata = {
  title: "New research — ScrapR",
  description:
    "Ask a question and get an evidence-backed report with a citation behind every claim.",
};

/**
 * Intake (`REQ-INPUT-001..007`).
 *
 * One column, left-aligned, with most of the viewport empty. The page is asking
 * a question, so it should look like a question rather than a dashboard: the
 * form is the only thing on it, and the examples below it are the only help
 * offered.
 */
export default function NewResearchPage() {
  return (
    <div className="shell py-20 md:py-28">
      <div className="max-w-[46rem]">
        <Eyebrow>New research</Eyebrow>
        <h1 className="display-l mt-6 max-w-[16ch]">What do you need to know?</h1>
        <p className="measure mt-6 text-lead text-ink-soft">
          ScrapR reads the sources, pulls out what they actually say, and writes
          it up with a citation behind every claim. The parts it could not
          confirm are marked as unconfirmed rather than smoothed over.
        </p>

        <ObjectiveForm />
      </div>
    </div>
  );
}
