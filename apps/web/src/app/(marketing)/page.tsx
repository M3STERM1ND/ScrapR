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

export default function Home() {
  return (
    <>
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
    </>
  );
}
