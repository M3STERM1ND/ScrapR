# Autonomous AI Research Agent --- V1 Master Plan

## 1. App Overview & Objectives

### Product vision

Build a web-based autonomous AI research agent that turns a simple
research objective into a comprehensive, source-backed research
workspace.

A user should be able to enter something as simple as:

> "Analyze NVIDIA as a company, investment, and potential employer."

The agent determines what research is needed, gathers information from
the web and specialized data sources, evaluates source quality,
identifies conflicting information, synthesizes findings, creates useful
visualizations, and presents the results in an interactive workspace.

### Core value proposition

**Turn hours of fragmented research into a structured, evidence-backed
analysis in minutes.**

### V1 success criteria

-   A user can research a company/business/website without configuring a
    complicated research workflow.
-   The agent autonomously determines research scope and depth.
-   Important claims have traceable citations.
-   Conflicting information is surfaced rather than silently resolved.
-   Facts, AI analysis, forecasts, and uncertainty are clearly
    distinguished.
-   Users can ask follow-up questions while retaining research context.
-   Users can upload documents and have them analyzed alongside external
    research.
-   Users can update an existing research project and see what changed.
-   Users can export the finished research as PDF and PowerPoint.
-   Saved research is private by default.

------------------------------------------------------------------------

## 2. Target Audience

Primary audiences: - Business owners - Marketing professionals - Job
seekers - People researching stocks and companies - Anyone who wants to
save substantial research time

The audiences have different use cases, but share the same fundamental
need: **quickly understand a company, market, business, opportunity, or
organization using reliable evidence.**

------------------------------------------------------------------------

## 3. Core Product Experience

### Basic flow

1.  User opens the web app.
2.  User enters a research objective.
3.  User can optionally provide additional instructions.
4.  User can optionally provide a URL, company identifier, or uploaded
    documents.
5.  Agent determines the research plan and depth automatically.
6.  Agent researches using appropriate tools.
7.  Agent evaluates source quality.
8.  Agent extracts and normalizes evidence.
9.  Agent detects conflicting information.
10. Agent synthesizes the evidence.
11. Agent generates appropriate charts/tables.
12. Agent produces an interactive research workspace.
13. User asks follow-up questions or modifies the research.
14. User can export PDF/PowerPoint.
15. User can create an account to save the research.
16. Later, user can select **Update Research** to produce a new version
    and see what changed.

### Design principle

The user should specify **what they want to know**, not **how the agent
should research it**.

------------------------------------------------------------------------

## 4. Core Features

### 4.1 Autonomous research agent

The agent should autonomously determine: - What questions need to be
answered - Which sources are appropriate - Which tools/data sources to
use - How deeply each area needs to be researched - Whether more
evidence is required - Which information is important enough to
include - Which visualizations improve understanding

Research depth should adapt to complexity rather than forcing users to
choose a "quick/standard/deep" mode.

### 4.2 Research activity

While researching, the interface should show useful progress such as: -
Understanding objective - Identifying research areas - Searching
financial information - Investigating competitors - Reviewing recent
developments - Evaluating sources - Checking conflicting evidence -
Building the report

Avoid exposing every raw internal search query. The activity display
should communicate meaningful progress without overwhelming the user.

### 4.3 Research domains

Depending on the request, the agent can investigate: - Company/business
overview - Products and services - Financials - Stock information -
Marketing strategy - Competitors - Market position/share - Industry
trends - Recent news - Customer/consumer sentiment - Growth
opportunities - Risks - SWOT - Career opportunities - Job roles -
Salaries when reliable information is available - Required skills and
qualifications - Other relevant areas discovered by the agent

The agent should not force every section into every report. It should
include sections that are relevant to the user's objective.

------------------------------------------------------------------------

## 5. Evidence, Sources & Trust

This is one of the most important parts of the product.

### Evidence hierarchy

The system should generally prioritize:

**Primary/authoritative sources** - Government databases - SEC filings
and regulatory filings - Official company financial statements -
Official company websites - Official statistics - Official job postings

**High-quality secondary sources** - Established financial/news
organizations - Reputable research organizations - Industry publications

**Lower-confidence sources** - Blogs - Aggregators - Unverified
websites - User-generated sources

The hierarchy should influence confidence, but should not prevent useful
lower-tier sources from being shown when they provide relevant evidence.

### Claim-level citations

Important claims should be directly traceable to their sources.

A claim should conceptually retain: - Source - Source type - Retrieval
time - Relevant evidence - Confidence - Reporting period/date when
applicable

Users should be able to inspect where important claims came from.

### Fact vs. interpretation

The report should clearly distinguish:

**Fact** \> Revenue was X according to the company's filing.

**Analysis** \> This suggests improving operating efficiency.

**Forecast** \> Growth could remain strong if assumptions A, B, and C
hold.

**Uncertainty** \> Available evidence is insufficient to determine X
with high confidence.

------------------------------------------------------------------------

## 6. Conflicting Information

Conflicting information should be a visible feature rather than a hidden
problem.

Example:

> Revenue: \$10B\
> Confidence: High\
> Primary source: Company filing
>
> Conflicting estimate: \$11.2B from a third-party source.\
> Possible explanation: different reporting period or methodology.

The agent should attempt to explain why numbers differ when enough
evidence exists.

Possible causes: - Different reporting periods - Different definitions -
Different currencies - Estimated vs. reported data - Different
methodologies - Stale information

If the conflict cannot be resolved, the report should say so.

------------------------------------------------------------------------

## 7. Research Source Strategy

V1 should use a **hybrid source architecture**.

### General web research

Useful for: - Broad company context - Competitors - Marketing - News -
Industry trends - Public discussion - General research

### Specialized sources/APIs

Useful for: - Financial data - Stock information - Regulatory filings -
Jobs - Structured company data - Other high-value datasets

The system should be designed around a modular tool layer so new
research capabilities can be added later without rebuilding the agent.

Future tools could include: - Social media analysis - Google Trends -
Patent databases - Academic research - Government datasets -
Industry-specific databases - Additional financial datasets

------------------------------------------------------------------------

## 8. Agent Architecture

A practical V1 should avoid an unnecessarily complicated multi-agent
swarm.

Recommended conceptual architecture:

### Central research orchestrator

Responsible for: - Understanding the objective - Planning research -
Selecting tools - Coordinating research tasks - Determining when enough
evidence has been collected - Sending evidence to synthesis

### Tool layer

Provides standardized interfaces for: - Web search - Web page
retrieval - Financial data - Regulatory filings - Jobs - News - Document
retrieval - Future specialized sources

### Evidence layer

Responsible for: - Extracting evidence - Normalizing information -
Tracking provenance - Deduplicating sources - Comparing evidence -
Detecting conflicts - Assigning confidence

### Synthesis/report layer

Responsible for: - Creating conclusions - Separating fact from
analysis - Building sections - Selecting charts/tables - Mapping
citations - Producing report-ready content

This architecture gives the team a clear separation between
**researching**, **understanding evidence**, and **communicating
conclusions**.

------------------------------------------------------------------------

## 9. Conceptual Data Model

The database should conceptually contain entities such as:

### User

-   Account identity
-   Authentication information
-   Preferences
-   Usage metadata

### Research Session

-   User objective
-   Creation date
-   Status
-   Current version
-   Input context
-   Research configuration

### Research Version

-   Version number
-   Creation/update time
-   Research snapshot
-   Changes from previous version

### Source

-   URL/identifier
-   Source name
-   Source category
-   Authority level
-   Retrieval timestamp
-   Accessibility status

### Evidence

-   Extracted information
-   Source relationship
-   Relevant date/reporting period
-   Confidence/provenance

### Claim

-   Claim text
-   Claim type
-   Supporting evidence
-   Confidence
-   Conflicting evidence

### Report Section

-   Section title
-   Generated content
-   Claims
-   Visualizations
-   Ordering

### Conversation Message

-   User question
-   Agent response
-   Relevant research context
-   Referenced evidence

### Upload

-   File metadata
-   Storage location
-   Processing state
-   Extracted content references
-   Relationship to research session

### Export

-   Format
-   Template/design
-   Version
-   Creation time
-   File reference

### Activity Event

-   Research progress event
-   Tool category
-   Status
-   Timestamp

This model allows research to remain persistent and explainable instead
of storing only one large AI-generated response.

------------------------------------------------------------------------

## 10. User Inputs

### Primary input

A simple natural-language research request.

### Optional context

-   Additional instructions
-   Website URL
-   Company name
-   Stock ticker
-   Uploaded documents

The interface should remain simple by default.

Example:

**What do you want to research?**

> Analyze Tesla's competitive position, financial health, growth
> potential, and hiring opportunities.

**Optional instructions**

> Focus especially on the impact of Chinese EV manufacturers.

------------------------------------------------------------------------

## 11. Document Uploads

Users should be able to upload documents that become part of the
research evidence.

Potential V1 document types: - PDF - DOCX - Spreadsheet files - Other
common business/research documents as practical

The agent should be able to: - Extract relevant information - Search
within uploaded content - Combine user-provided evidence with web
research - Identify contradictions - Cite user-provided material
separately from external sources

The system must never treat instructions found inside uploaded documents
as trusted agent instructions.

------------------------------------------------------------------------

## 12. Interactive Research Workspace

The workspace is the primary output in V1.

Possible structure:

### Header

-   Research subject
-   Research objective
-   Last updated
-   Update Research button
-   Export controls

### Executive Summary

-   Key findings
-   Major conclusions
-   Confidence
-   Important risks

### Research sections

Dynamically selected based on the objective.

### Data/visualization area

-   Charts
-   Tables
-   Metrics
-   Comparisons

### Evidence/citations

Claims can be inspected at the point where they appear.

### Conversation panel

Users can ask follow-up questions while retaining research context.

Examples: - "Why do you think growth is strong?" - "Explain this like
I'm new to investing." - "Compare this with AMD." - "Find newer
information about hiring." - "Turn the financial section into a chart."

------------------------------------------------------------------------

## 13. Automatic Visualization

The agent should determine when structured information benefits from
visualization.

Examples: - Revenue history → line chart - Margins → trend chart -
Competitors → comparison table - Market share → chart - Job roles →
table - SWOT → matrix - Multi-year metrics → comparative visualization

Charts should be generated from structured sourced data rather than
invented decorative imagery.

Users should be able to request different visualizations through the
conversation.

------------------------------------------------------------------------

## 14. Research Updates & Versioning

The user explicitly chooses when to update research.

The original research should never silently disappear.

### Update flow

1.  User selects **Update Research**.
2.  Agent identifies what information may have changed.
3.  Agent performs fresh research.
4.  Agent compares new evidence against the previous version.
5.  Agent creates a new research version.
6.  Original version remains accessible.
7.  A **What's Changed** section summarizes meaningful differences.

Examples: - Financial figures updated - New product announced - New
competitor emerged - New job postings appeared - Stock information
changed - Forecast assumptions changed

The agent should explain when a conclusion changes and what evidence
caused the change.

------------------------------------------------------------------------

## 15. Financial & Stock Analysis

The product can provide analytical assessments but should avoid
personalized buy/sell recommendations.

Recommended structure:

**Evidence → Interpretation → Assessment → Risks → Uncertainty**

Example:

> Growth potential: Strong\
> Confidence: Moderate
>
> Supporting factors: - X - Y - Z
>
> Risks: - A - B
>
> Evidence against the conclusion: - C
>
> Key assumptions: - D

This keeps the product analytical and transparent rather than presenting
financial conclusions as certainty.

------------------------------------------------------------------------

## 16. PDF & PowerPoint Exports

V1 should support: - PDF - PowerPoint

The interactive workspace remains the source of truth; exports are
generated from the research content.

### Design strategy

Use a **hybrid design system**.

Users choose from predefined themes, such as: - Professional/business -
Investor/financial - Modern/creative - Corporate - Minimal -
Dark/technology

The AI adapts the content to the selected design rather than generating
an entirely unique visual design from scratch every time.

This reduces token/processing costs while still allowing customization.

Future versions can offer advanced AI-generated layouts.

------------------------------------------------------------------------

## 17. Authentication

The core research experience should not require an account.

### Anonymous flow

1.  Open app
2.  Research
3.  Interact
4.  Export
5.  Optionally create an account to save

### Account benefits

-   Save research
-   Access history
-   Preserve versions
-   Reopen conversations
-   Manage uploads
-   Eventually organize workspaces

Authentication is primarily a persistence feature in V1, not a barrier
to trying the product.

------------------------------------------------------------------------

## 18. Privacy & Security

Research should be **private by default**.

### V1 security principles

-   Strong user/account isolation
-   Encryption in transit
-   Encryption at rest where appropriate
-   Secure file storage
-   Least-privilege access
-   Secure secret/API-key management
-   User-controlled deletion
-   Protection against unauthorized report access
-   Rate limiting and abuse controls
-   Secure background processing

### Prompt-injection defense

Web pages and uploaded documents must be treated as **untrusted data**.

The system should maintain a strict conceptual separation:

**Trusted** - System policies - Agent instructions - Tool permissions -
Application logic

**Untrusted** - Web pages - Uploaded documents - Search results -
Third-party text

The agent should never follow instructions found in research material
merely because the text tells it to do something.

### Future sharing

V1 remains private.

Later versions can add: - Shareable report links - Expiration -
Read-only access - Permissions - Team collaboration

------------------------------------------------------------------------

## 19. Recommended Technical Stack

The stack should favor technologies that let two developers move quickly
while preserving room to grow.

### Frontend

**React-based web application**, preferably using a modern full-stack
React framework where practical.

Responsibilities: - Research input - Activity display - Interactive
report - Charts/tables - Conversation - Authentication UI - Export
controls

### Backend

**Python-based API/service layer** is a strong choice for: - AI
orchestration - Research workflows - Data processing - Document
processing - API integrations

### Database

**PostgreSQL**

Use it for: - Users - Research sessions - Versions - Claims - Sources -
Evidence - Conversations - Exports - Usage metadata

A vector-search capability can be added through PostgreSQL extensions
rather than immediately introducing another database if that meets V1
requirements.

### Object storage

Use object storage for: - Uploaded documents - Generated PDFs -
PowerPoint files - Other large artifacts

### Background processing

Research and export jobs should run asynchronously.

A queue/worker architecture prevents long research tasks from blocking
normal web requests.

### AI layer

Keep the AI provider behind an abstraction so the product isn't
permanently tied to one model provider.

### External data layer

Create standardized tool interfaces for: - Web search - Financial data -
Filings - Jobs - News - Other specialized research tools

### Observability

Track: - Research failures - Tool failures - Latency - Token/API usage -
Cost - Source failures - Agent workflow errors

------------------------------------------------------------------------

## 20. V1 Architecture at a Conceptual Level

``` text
                    ┌──────────────────┐
                    │    Web Client    │
                    │ React-based UI   │
                    └────────┬─────────┘
                             │
                             ▼
                    ┌──────────────────┐
                    │   Application    │
                    │      API         │
                    └────────┬─────────┘
                             │
                             ▼
                 ┌───────────────────────┐
                 │ Research Orchestrator │
                 │                       │
                 │ Plan → Research →     │
                 │ Evaluate → Synthesize │
                 └───────────┬───────────┘
                             │
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
        ┌──────────┐   ┌──────────┐   ┌──────────┐
        │ Web Tool │   │ Financial│   │ Document │
        │          │   │ / Data   │   │  Tools   │
        └──────────┘   └──────────┘   └──────────┘
              │              │              │
              └──────────────┼──────────────┘
                             ▼
                    ┌──────────────────┐
                    │ Evidence Layer   │
                    │                  │
                    │ Sources          │
                    │ Claims           │
                    │ Conflicts        │
                    │ Confidence       │
                    └────────┬─────────┘
                             │
                             ▼
                    ┌──────────────────┐
                    │ Report Generator │
                    │                  │
                    │ Text             │
                    │ Charts           │
                    │ Tables           │
                    │ Citations        │
                    └────────┬─────────┘
                             │
                 ┌───────────┴───────────┐
                 ▼                       ▼
          ┌─────────────┐         ┌─────────────┐
          │ Interactive │         │ PDF / PPTX  │
          │ Workspace   │         │   Export    │
          └─────────────┘         └─────────────┘
```

------------------------------------------------------------------------

## 21. Development Phases

### Phase 1 --- Research Engine Foundation

Goal: prove the core research loop.

Build: - Basic web research - Research objective input - Agent
orchestration - Source collection - Evidence extraction - Basic
citations - Basic report generation

Success condition: \> One query can reliably become a useful
source-backed report.

### Phase 2 --- Evidence & Trust

Build: - Source hierarchy - Confidence - Claim/evidence relationships -
Conflict detection - Retrieval timestamps - Better provenance

Success condition: \> The agent can explain where important information
came from and identify disagreement.

### Phase 3 --- Interactive Workspace

Build: - Research dashboard - Sections - Charts/tables - Research
activity - Conversation - Context-aware follow-ups

Success condition: \> Users can explore and interrogate the research
rather than just reading a static answer.

### Phase 4 --- Documents

Build: - Uploads - Document extraction - Document search/retrieval -
Combined external + user evidence - Document citations

Success condition: \> A user can provide a document and have the agent
incorporate it into research.

### Phase 5 --- Accounts & Persistence

Build: - Authentication - Saved research - Research history - Version
storage - Private access controls

Success condition: \> Users can leave and return to their research
securely.

### Phase 6 --- Updates

Build: - Update Research - Fresh-source retrieval - Version comparison -
What's Changed - Changed-conclusion explanations

Success condition: \> Users can keep research current without losing
previous versions.

### Phase 7 --- Exports

Build: - PDF generation - PowerPoint generation - Predefined design
themes - Export version tracking

Success condition: \> Users can take their research outside the
application.

### Phase 8 --- Hardening

Build: - Security improvements - Prompt-injection defenses - Error
handling - Rate limiting - Monitoring - Performance improvements - Cost
tracking

Success condition: \> The product is reliable enough for real users.

------------------------------------------------------------------------

## 22. Suggested Two-Person Team Split

The split should be flexible, but a useful starting point is:

### Developer A --- AI/research backend

Focus: - Agent orchestration - Research tools - Evidence pipeline -
Source evaluation - Conflict detection - Document processing - Data
model

### Developer B --- Product/frontend

Focus: - Web application - Research activity UI - Interactive report -
Charts/tables - Conversation interface - Authentication - Export
experience

Both developers should understand the complete system so neither becomes
a single point of failure.

------------------------------------------------------------------------

## 23. Major Challenges & Solutions

### Challenge: Hallucinations

**Solution:** Require evidence-backed claims, citations, structured
evidence, and explicit uncertainty.

### Challenge: Conflicting data

**Solution:** Preserve competing evidence, evaluate source authority,
explain possible causes, and show unresolved conflicts.

### Challenge: Stale information

**Solution:** Store retrieval timestamps and provide explicit Update
Research functionality.

### Challenge: Paywalls/blocked sources

**Solution:** Continue with accessible sources, identify inaccessible
sources, and never claim to have read inaccessible content.

### Challenge: Prompt injection

**Solution:** Treat external content as untrusted data and isolate it
from trusted agent instructions and tool permissions.

### Challenge: High research costs

**Solution:** Adaptive research depth, caching, reusable evidence,
asynchronous processing, and usage tracking.

### Challenge: Long research times

**Solution:** Background jobs and visible research activity.

### Challenge: Poor report layouts

**Solution:** Separate content generation from presentation and use
predefined design systems.

### Challenge: Overly complex V1

**Solution:** Start with one research workspace and modular tools. Delay
collaboration, monitoring, and advanced workspaces.

------------------------------------------------------------------------

## 24. What NOT to Build in V1

Avoid spending development time on:

-   Team collaboration
-   Complex sharing permissions
-   Automatic monitoring
-   Scheduled research
-   Advanced workspace management
-   Huge numbers of integrations
-   Fully AI-generated visual themes
-   Complex monetization
-   Native mobile applications
-   Enterprise administration
-   Public report marketplace
-   Large-scale social features

These can become future expansion once the core research engine proves
valuable.

------------------------------------------------------------------------

## 25. Future Expansion

### V2

-   Folders/workspaces
-   Cross-company comparison
-   Shareable reports
-   More research tools
-   More export formats
-   Custom report templates

### V3

-   Persistent company/industry monitoring
-   Alerts
-   Scheduled updates
-   Team collaboration
-   Permissions
-   Comments
-   Shared workspaces

### Longer-term possibilities

-   Industry-specific research agents
-   Browser extension
-   API access
-   Custom data connectors
-   Advanced AI-designed reports
-   Research automation
-   Enterprise version
-   Personalized research workflows

------------------------------------------------------------------------

## 26. Product North Star

The product should feel less like:

> "Ask an AI a question."

and more like:

> **"Give an AI a research objective, and it will do the research for
> you."**

The user's job is to define the goal.

The agent's job is to figure out: - What needs to be investigated -
Where to look - Which evidence to trust - What conflicts exist - What
conclusions are justified - How the information should be presented -
What needs updating later

That distinction is the core identity of the product.

------------------------------------------------------------------------

## 27. V1 Definition of Done

V1 is successful when a new user can:

1.  Open the web app without creating an account.
2.  Enter a natural-language research objective.
3.  Optionally provide instructions or documents.
4.  Start autonomous research.
5.  Watch meaningful research activity.
6.  Receive a comprehensive interactive report.
7.  Inspect citations attached to important claims.
8.  See conflicting information when it exists.
9.  Understand the difference between evidence and AI analysis.
10. View useful automatically generated charts/tables.
11. Ask follow-up questions with research context preserved.
12. Export the research to PDF or PowerPoint.
13. Create an account and save the research.
14. Return later and view the original research.
15. Run Update Research.
16. Receive a new version with a clear What's Changed summary.
17. Keep all saved research private by default.

------------------------------------------------------------------------

## 28. Guiding Principle for the Team

**Do not optimize for how impressive the architecture looks. Optimize
for how reliably the user can go from a question to trustworthy
research.**

A smaller system that produces accurate, transparent, useful research is
a better V1 than a massive agent architecture that is difficult for two
developers to maintain.

Build the research loop first.

Then make it trustworthy.

Then make it beautiful.

Then make it scalable.
