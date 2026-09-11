Read the masterplan.md file before starting this.
Build a minimal, premium landing page for our AI research agent that turns a simple question into a comprehensive, evidence-backed report with sources, analysis, charts, and insights.
THE ONE FEELING: Expensive, calm, and vibrant. Every choice serves this. If something does not, cut it. When in doubt, remove, do not add.
STACK
Frontend: Next.js + React
Backend: Python + FastAPI
Database: PostgreSQL
AI: LLM APIs
Research: Web search + specialized data APIs + News APIs
Storage: S3-compatible object storage
Background jobs: Redis + workers
Deployment: Vercel
NOTE ADDED 2026-09-09 (resolves N-05 in the implementation plan): this STACK block is orientation for whoever builds the landing page. It is not an architecture decision record. Next.js, FastAPI, PostgreSQL and Vercel are confirmed elsewhere with provenance (DEC-01, DEC-02, REQ-TECH-003, DEC-03). The other four lines are category placeholders, not vendor choices, and the real decisions are still open: AI provider is OPEN-04, the research sources are OPEN-05 through OPEN-09, object storage provider is OPEN-10, and queue plus worker execution location is OPEN-03. Do not treat "S3-compatible" or "Redis" as settled. See PRD.md section 13 and implementation-plan.md section 14.2.
Use the UI/UX Pro Max skill to define the design system
Use Superpowers to plan, scaffold, run, and inspect the project
DEFAULT TEXT (follow this exactly, it is where these builds usually fail)
Two fonts total: one display font for headlines, one clean readable font for everything else. Not Inter, Roboto, or Arial.
Plus Jakarta Sans  and  Satoshi 
Headlines in sentence case or normal case. Do NOT force all-caps and do NOT use small-caps. Never set a serif font in all-caps. It looks dated and stiff.
Body text: 16 to 18px, line height around 1.6, max line length about 65 characters. Never let a paragraph run the full page width.
High contrast. Body text must pass WCAG AA against its background. No gray-on-background mush.
DESIGN SYSTEM (lock before building sections)
One dominant color, one accent, used sparingly. No third color.
Color: Hurricane Grey 
Accent: Orche
Lock a spacing scale and stick to it. Extreme negative space. Add room, not content.
Every section fills the width with balance. Never strand all the content in one fourth with an empty half.
SECTIONS (keep it lean, in order)
Sticky Navbar — Simple navigation with the ScrapR logo, sign-in, and a clear CTA.
Hero — Communicate ScrapR’s core value immediately with a strong headline, short description, CTA, and subtle product visual.
How It Works — Show the simple process: Ask → Research → Understand.
Research Preview — Give users a realistic glimpse of the comprehensive reports ScrapR creates, including insights, charts, and citations.
Evidence & Trust — Show how ScrapR connects claims to reliable sources and distinguishes facts from AI analysis.
Features — Highlight deep research, citations, visualizations, and follow-up conversations through varied visual demonstrations rather than generic cards.
Search History — Show how users can save and return to their previous research.
Recent Business News — Demonstrate ScrapR’s ability to incorporate current information and developments.
Research Workspace — Show the full interactive experience where users can explore their report, sources, charts, and continue the conversation and export all the findings into a comprehensive pdf report. 
FAQ — Answer the most important questions concisely.
Footer — Keep it minimal with ScrapR branding, a short tagline, and essential links.
MOTION (less is more)
Pick 2 to 3 high-impact moments and choreograph them well: a stack of money staggering out, gentle scroll reveals as sections enter (whileInView, once: true), and one signature hover.
Skip animation everywhere else. Scattered motion reads cheap. Respect prefers-reduced-motion.
CHECK YOUR OWN WORK (after every section, before showing me)
Run the site, open localhost:3000, and screenshot the section you just built.
Critique the screenshot honestly: Is the layout balanced or is one side empty? Anything crowded, overlapping, or misaligned? Is the text readable? Does the type look intentional?
Fix every problem, screenshot again, and only show me once it actually looks right.
Confirm there are no runtime or build errors before moving on. A broken build means the page is not rendering what you think it is.
QUALITY BAR
One idea per section. Generous space between every block. No two sections overlap or bleed together.
Desktop-first. Design for a wide screen, then make sure it still holds up smaller.
Lighthouse 90+ on performance.
Copy sounds human. No em-dashes or anything that sounds like it’s written by ai. Make everything sound humanized. 
PROCESS
Lock the design system and the default text rules first.
Build one section at a time. Screenshot, self-critique, fix, then show me. Move on only when it looks right.
