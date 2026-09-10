# Graph Report - ScrapR  (2026-09-09)

## Corpus Check
- Corpus is ~22,047 words - fits in a single context window. You may not need a graph.

## Summary
- 239 nodes · 349 edges · 25 communities (14 shown, 11 thin omitted)
- Extraction: 98% EXTRACTED · 2% INFERRED · 0% AMBIGUOUS · INFERRED: 6 edges (avg confidence: 0.73)
- Token cost: 201,497 input · 0 output

## Community Hubs (Navigation)
- Evidence and Conflict Core
- Stack and Open Decisions
- Data Model Entities
- Orchestration and Tools
- Documents and Security
- Workspace and Conversation
- Performance, Cost, Activity
- Export and Themes
- Versioning and Updates
- Intake and Research Foundation
- Auth and Anonymous Access
- Synthesis and Report Content
- V1 Scope Discipline
- Target Audiences
- Team Principles
- Core Product Flow
- Development Phases
- Future Expansion
- Product North Star
- Product Vision
- Team Split
- Architecture Diagram
- V1 Success Criteria
- Core Value Proposition
- TBD Numeric Values

## God Nodes (most connected - your core abstractions)
1. `Requirement Namespace: OPEN (Unresolved Decisions)` - 26 edges
2. `Requirement Namespace: REQ-EVID (Sources, Evidence, Claims, Confidence, Conflicts)` - 14 edges
3. `Requirement Namespace: REQ-DATA (Persistent Data Model)` - 12 edges
4. `Evidence, Sources & Trust` - 11 edges
5. `Requirement Namespace: REQ-SEC (Privacy, Security, Prompt-Injection Defense)` - 11 edges
6. `Recommended Technical Stack` - 9 edges
7. `Requirement Namespace: REQ-AGENT (Orchestration, Planning, Adaptive Depth)` - 9 edges
8. `PRD V1 Definition of Done (17 items)` - 8 edges
9. `REQ-SYNTH-001: Claim Type Classification` - 8 edges
10. `Privacy & Security` - 7 edges

## Surprising Connections (you probably didn't know these)
- `Risk: Stale Information (mitigated by retrieval timestamps and explicit Update Research)` --semantically_similar_to--> `REQ-TOOL-013: Caching and Evidence Reuse`  [INFERRED] [semantically similar]
  masterplan.md → PRD.md
- `NFR-USE-002: Claim-Type/Confidence Distinctions Not Color-Only` --cites--> `Evidence, Sources & Trust`  [EXTRACTED]
  PRD.md → masterplan.md
- `REQ-SYNTH-002: Visible Distinction of Claim Types` --cites--> `Evidence, Sources & Trust`  [EXTRACTED]
  PRD.md → masterplan.md
- `REQ-TOOL-012: Provenance Metadata on Every Result` --cites--> `Evidence, Sources & Trust`  [EXTRACTED]
  PRD.md → masterplan.md
- `REQ-SYNTH-003: Executive Summary` --cites--> `Interactive Research Workspace`  [EXTRACTED]
  PRD.md → masterplan.md

## Hyperedges (group relationships)
- **Evidence Trust Core (Sources, Claims, Confidence, Conflicts)** — prd_ns_evid, masterplan_evidence_sources_trust, masterplan_evidence_layer, prd_req_evid_012, prd_req_evid_017 [EXTRACTED 1.00]
- **Four-Layer Agent Architecture (Orchestrator, Tool, Evidence, Synthesis)** — masterplan_research_orchestrator, masterplan_tool_layer, masterplan_evidence_layer, masterplan_synthesis_report_layer [EXTRACTED 1.00]
- **Anonymous Access, Ownership, and Rate-Limiting Concern Group** — prd_req_auth_001, prd_req_auth_002, prd_req_sec_009, prd_open_17, prd_open_18 [EXTRACTED 0.90]

## Communities (25 total, 11 thin omitted)

### Community 0 - "Evidence and Conflict Core"
Cohesion: 0.09
Nodes (32): Claim Type: Analysis, Claim Type: Fact, Claim Type: Forecast, Claim Type: Uncertainty, Conflicting Information Handling, Evidence Layer, Evidence, Sources & Trust, Phase 2: Evidence & Trust (+24 more)

### Community 1 - "Stack and Open Decisions"
Cohesion: 0.09
Nodes (30): Recommended Technical Stack, DEC-01: Frontend Framework Confirmed as Next.js, DEC-02: Backend Framework Confirmed as FastAPI, DEC-03: Deployment Platform Confirmed as Vercel, Requirement Namespace: REQ-OBS (Observability), Requirement Namespace: OPEN (Unresolved Decisions), Requirement Namespace: REQ-TECH (Technical Stack Constraints), OPEN-03: Queue/Worker Technology and Execution Location (+22 more)

### Community 2 - "Data Model Entities"
Cohesion: 0.08
Nodes (25): Conceptual Data Model, Entity: Activity Event, Entity: Claim, Entity: Conversation Message, Entity: Evidence, Entity: Export, Entity: Report Section, Entity: Research Session (+17 more)

### Community 3 - "Orchestration and Tools"
Cohesion: 0.13
Nodes (22): Autonomous Research Agent, Major Challenges & Solutions, Central Research Orchestrator, Risk: High Research Costs (mitigated by adaptive depth, caching, reusable evidence, async processing, usage tracking), Risk: Paywalls/Blocked Sources (mitigated by continuing with accessible sources and never claiming to have read inaccessible content), Tool Layer, NFR-REL-002: Partial Failure Must Not Produce False-Complete Report, Requirement Namespace: REQ-AGENT (Orchestration, Planning, Adaptive Depth) (+14 more)

### Community 4 - "Documents and Security"
Cohesion: 0.15
Nodes (21): Document Uploads, Phase 4: Documents, Phase 5: Accounts & Persistence, Privacy & Security, Risk: Prompt Injection (mitigated by treating external content as untrusted and isolating it from trusted instructions), Flow C: Document-Augmented Research, Requirement Namespace: REQ-DOC (Document Uploads), Requirement Namespace: REQ-SEC (Privacy, Security, Prompt-Injection Defense) (+13 more)

### Community 5 - "Workspace and Conversation"
Cohesion: 0.22
Nodes (14): Automatic Visualization, Interactive Research Workspace, Phase 3: Interactive Workspace, Flow B: Follow-up Conversation, Requirement Namespace: REQ-CONV (Follow-up Conversation), Requirement Namespace: REQ-VIZ (Automatic Visualization), Requirement Namespace: REQ-WORK (Interactive Research Workspace), REQ-CONV-001: Context-Preserving Follow-Up (+6 more)

### Community 6 - "Performance, Cost, Activity"
Cohesion: 0.18
Nodes (14): Phase 8: Hardening, Research Activity Display, Risk: Long Research Times (mitigated by background jobs and visible research activity), NFR-COST-001: Maximum AI/API Cost per Research Run, NFR-PERF-001: Research Run Bounded Completion Time (p50), NFR-PERF-002: Hard Ceiling on Single Research Run, NFR-REL-001: Research Run Survives Worker Restart, NFR-USE-002: Claim-Type/Confidence Distinctions Not Color-Only (+6 more)

### Community 7 - "Export and Themes"
Cohesion: 0.26
Nodes (13): PDF & PowerPoint Exports, Phase 7: Exports, Risk: Poor Report Layouts (mitigated by separating content generation from presentation and using predefined design systems), Flow D: Export, Requirement Namespace: REQ-EXP (PDF and PowerPoint Export), OPEN-21: PDF/PowerPoint Generation Approach, OPEN-22: Visual Specifications for the Six Export Themes, REQ-EXP-001: PDF Export (+5 more)

### Community 8 - "Versioning and Updates"
Cohesion: 0.27
Nodes (13): Phase 6: Updates, Research Updates & Versioning, Risk: Stale Information (mitigated by retrieval timestamps and explicit Update Research), V1 Definition of Done, Flow F: Return to Saved Research, Flow G: Update Research, Requirement Namespace: REQ-VER (Versioning and Update Research), REQ-VER-001: User-Initiated Updates Only (+5 more)

### Community 9 - "Intake and Research Foundation"
Cohesion: 0.20
Nodes (11): Agent Architecture (Single Orchestrator, No Swarm), Design Principle: What, Not How, Phase 1: Research Engine Foundation, User Inputs, Flow A: First Research, Anonymous, Governing Design Principle (What, Not How), Requirement Namespace: REQ-INPUT (Research Request Intake), REQ-AGENT-001: Objective Interpretation (+3 more)

### Community 10 - "Auth and Anonymous Access"
Cohesion: 0.36
Nodes (10): Authentication, Flow E: Account Creation and Saving Research, Requirement Namespace: REQ-AUTH (Anonymous Access, Accounts, Persistence), OPEN-17: Anonymous Research Ownership and Claiming Mechanism, REQ-AUTH-001: No Account Required for Core Experience, REQ-AUTH-002: Anonymous Session, REQ-AUTH-003: Account Creation, REQ-AUTH-004: Claiming Anonymous Research (+2 more)

### Community 11 - "Synthesis and Report Content"
Cohesion: 0.22
Nodes (9): Financial & Stock Analysis Structure, Research Domains, Synthesis/Report Layer, Requirement Namespace: REQ-SYNTH (Synthesis, Report Content), REQ-AGENT-006: Objective-Driven Section Selection, REQ-SYNTH-002: Visible Distinction of Claim Types, REQ-SYNTH-003: Executive Summary, REQ-SYNTH-004: Dynamic Section Generation (+1 more)

### Community 12 - "V1 Scope Discipline"
Cohesion: 0.38
Nodes (7): Hybrid Research Source Strategy, Risk: Overly Complex V1 (mitigated by one research workspace, modular tools, delaying collaboration and monitoring), What NOT to Build in V1, REQ-SEC-016: No Sharing in V1, REQ-TOOL-009: Extensibility Without Orchestrator Change, REQ-VER-009: No Scheduled or Automatic Updates in V1, V1 Scope Boundary (In/Out of Scope)

### Community 13 - "Target Audiences"
Cohesion: 0.33
Nodes (6): Audience: Business Owners, Audience: General Time-Constrained Researchers, Audience: Job Seekers, Audience: Marketing Professionals, Audience: Stock/Company Researchers, Target Audience

## Knowledge Gaps
- **48 isolated node(s):** `Product Vision`, `Core Value Proposition`, `V1 Success Criteria`, `Core Product Experience (Basic Flow)`, `Hybrid Research Source Strategy` (+43 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 54 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **11 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Requirement Namespace: OPEN (Unresolved Decisions)` connect `Stack and Open Decisions` to `Evidence and Conflict Core`, `Orchestration and Tools`, `Documents and Security`, `Export and Themes`, `Auth and Anonymous Access`?**
  _High betweenness centrality (0.268) - this node is a cross-community bridge._
- **Why does `PRD V1 Definition of Done (17 items)` connect `Versioning and Updates` to `Documents and Security`, `Workspace and Conversation`, `Export and Themes`, `Intake and Research Foundation`, `Auth and Anonymous Access`?**
  _High betweenness centrality (0.200) - this node is a cross-community bridge._
- **Why does `Flow A: First Research, Anonymous` connect `Intake and Research Foundation` to `Versioning and Updates`, `Stack and Open Decisions`?**
  _High betweenness centrality (0.162) - this node is a cross-community bridge._
- **What connects `Product Vision`, `Core Value Proposition`, `V1 Success Criteria` to the rest of the system?**
  _48 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Evidence and Conflict Core` be split into smaller, more focused modules?**
  _Cohesion score 0.09475806451612903 - nodes in this community are weakly interconnected._
- **Should `Stack and Open Decisions` be split into smaller, more focused modules?**
  _Cohesion score 0.0896551724137931 - nodes in this community are weakly interconnected._
- **Should `Data Model Entities` be split into smaller, more focused modules?**
  _Cohesion score 0.08 - nodes in this community are weakly interconnected._