import { MotionConfig } from "motion/react";
import type { Metadata } from "next";

import { EvidenceTrust } from "@/components/sections/EvidenceTrust";
import { Faq } from "@/components/sections/Faq";
import { Features } from "@/components/sections/Features";
import { Footer } from "@/components/sections/Footer";
import { Hero } from "@/components/sections/Hero";
import { HowItWorks } from "@/components/sections/HowItWorks";
import { Navbar } from "@/components/sections/Navbar";
import { RecentNews } from "@/components/sections/RecentNews";
import { ResearchPreview } from "@/components/sections/ResearchPreview";
import { SearchHistory } from "@/components/sections/SearchHistory";
import { WorkspaceSection } from "@/components/sections/WorkspaceSection";

export const metadata: Metadata = {
  title: "AI Research",
};

export default function Home() {
  return (
    // Reduced motion belongs to Motion, not to the sections. With "user", each
    // animated element reads the reader's setting when it mounts in the browser
    // and drops movement (position, scale, rotation) while keeping fades. The
    // sections render identical markup either way, so the server's starting
    // frame always matches the browser's first render and always animates away.
    <MotionConfig reducedMotion="user">
      <Navbar />
      <main id="main">
        <Hero />
        <HowItWorks />
        <ResearchPreview />
        <EvidenceTrust />
        <Features />
        <SearchHistory />
        <RecentNews />
        <WorkspaceSection />
        <Faq />
      </main>
      <Footer />
    </MotionConfig>
  );
}
