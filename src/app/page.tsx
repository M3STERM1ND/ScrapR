import { Navbar } from "@/components/sections/Navbar";
import { Hero } from "@/components/sections/Hero";

export default function Home() {
  return (
    <>
      <Navbar />
      <main id="main">
        <Hero />
      </main>
    </>
  );
}
