# What compliance tools actually do — and where this project fits

Research note, 27 Sep 2026. Feeds report §5 (business recommendation) and the
product roadmap.

---

## 1. What the market ships

### Regulatory obligation management (Nimonik, LogicGate, Resolver, VComply)
The consistent feature set across vendors:

| Capability | What it means |
|---|---|
| **Obligations register** | One place listing every applicable obligation, its regulatory source, owner, frequency and deadline |
| **Applicability management** | Deciding *which* requirements apply to this business unit, facility or jurisdiction |
| **Ownership and accountability** | A named responsible person per obligation, with escalation paths |
| **Recurring workflow automation** | Auto-generated quarterly reviews, annual certifications, permit renewals |
| **Evidence management** | Supporting documents attached to the obligation, not filed separately |
| **Status monitoring** | Overdue items, exceptions, real-time deadline tracking |
| **Corrective actions** | Recording remediation when an obligation is missed |
| **Regulatory change workflow** | Detecting and assessing new or amended requirements |
| **Audit trail** | Full lifecycle history of every obligation |

### RERA-specific (BUILDX / Quantbit)
- RERA Project Master; Registration and Validity Register
- Quarterly Progress Evidence; **Quarterly Action Scheduler**
- Approval / pending-approval register with role-based workflow
- **State-RERA Requirement Matrix** — mapping how requirements differ by state
- Submission Evidence Pack; configurable validations that surface missing data
  *before* filing

### Enterprise legal AI (Harvey, Lexis+ AI, Westlaw AI, CoCounsel)
- Ground answers in real legal databases rather than model memory
- Citation hallucination is *reduced, not eliminated* — complex queries still err
- **The workflow assumes mandatory human verification of every citation**
- Notably, published reviews find these tools **do not transparently expose
  retrieval confidence or provenance strength to the user**

---

## 2. Where this project already stands out

Two things here are genuinely ahead of what the research describes.

**Authority tiering on the face of the answer.** Commercial legal AI grounds in a
database but treats all retrieved material as equally citable. This system
distinguishes binding regulation from explanatory guidance from draft material,
shows it as a badge on every source, and *re-ranks* on it. A guidance FAQ cannot
outrank the Act.

**Visible evidence strength.** The reviews specifically note that the commercial
tools do not show users how strongly a given answer is supported. This one shows
the percentage of distinct source documents that are binding, and counts
documents rather than chunks so six passages from one circular cannot masquerade
as six sources.

Both are defensible claims in report §5 and worth stating plainly.

---

## 3. What is missing, and what is realistic to add

The hard constraint: most commercial features need a **tenant database of the
customer's own projects, facilities, owners and deadlines**. This project has a
document corpus and no customer data. Building fake project records would be
dishonest and adds nothing. So the question is which market capabilities can be
delivered *from a corpus alone*.

### Tier 1 — high value, corpus-only, realistic

**A. Applicability profile ("what applies to me")**
The single most consistent market capability, and the natural extension of the
scope selectors already built. The user states who they are — establishment type,
or project cost band and state — and gets the obligations that bind *them*,
rather than everything the corpus contains. Already half-built.

**B. Supersession and timeline tracking**
The most valuable thing this corpus can do that generic tools cannot. The
Karnataka annual-audit circulars form an explicit chain: 2022-23 extended to
31-12-2023, 2023-24 to 31-12-2024 then to 31-03-2026, 2024-25 to 31-12-2025.
Presenting that chain — and warning when a retrieved circular has been superseded
— directly addresses the "regulatory change workflow" capability *and* the year
confusion measured on 27 Sep. Nothing else on this list is as strong.

**C. Obligations register generated from the corpus**
One offline LLM pass over tier-A documents extracting every deadline-bearing
obligation with its source title, provision and frequency, written to a CSV and
rendered as a filterable table. This is the market's core artefact, built from
documents rather than customer data. Moderate effort, high credibility.

**D. Central vs state comparison**
`source_domain` already tags central and karnataka. A side-by-side view answering
"what does the central Act say, and what does Karnataka add?" mirrors BUILDX's
State-RERA Requirement Matrix and is unique to a two-jurisdiction corpus.

### Tier 2 — useful, cheaper

**E. Compliance memo export** — any answer plus its citations, dated, as a file
for the compliance record. The market treats evidence and audit trail as core.

**F. Corpus coverage transparency** — what the corpus does and does not contain,
so silence is never mistaken for absence. Directly supports the confidence
calibration limb of the rubric.

**G. Clause check** — paste a contract or policy clause, ask whether it conforms.
Maps to "contract analysis" in the legal AI tools.

### Tier 3 — deliberately out of scope, and say so
Workflow, task assignment, owners, corrective actions, multi-entity dashboards,
filing integration. All require customer data and a persistence layer. Naming
these as *conscious* exclusions in report §5 is stronger than ignoring them.

---

## 4. Recommended sequence

1. **B — supersession timeline.** Strongest story, uses a measured failure,
   unique to this corpus.
2. **A — applicability profile.** Extends what exists; matches the market's most
   consistent capability.
3. **D — central vs state comparison.** Cheap, and no other cohort group covering
   one jurisdiction can build it.
4. **C — obligations register.** Highest effort, highest credibility as a
   deliverable artefact.
5. **E, F, G** as time allows.

---

## 5. For report §5 — the honest recommendation

Deploy as a **research and drafting assistant for a compliance professional, not
as a filing or decision system.** The evidence for that boundary is in this
project's own measurements: dense retrieval could not distinguish four circulars
differing only by financial year; near-identical facility templates crowded out
the authoritative instrument; and the model attached one year's penalty schedule
to another year's question until reasoning effort was lowered.

The guardrails that follow are: authority tiering with draft material excluded by
default; mandatory human verification of every citation, which is what the
commercial legal AI tools also assume; scope selection so the user asserts which
regime applies rather than leaving it to a similarity score; and visible evidence
strength so a thin answer looks thin.

---

## Sources

- [Regulatory Obligation Management Software: Features & Buyer Guide — VComply](https://www.v-comply.com/blog/regulatory-obligation-management-software-complete-guide-for-2026/)
- [Regulatory Compliance Management Software — Nimonik](https://nimonik.com/software/)
- [Regulatory Compliance Management — LogicGate Risk Cloud](https://www.logicgate.com/solutions/regulatory-compliance-management/)
- [7 Essential Compliance Management Software Benefits — Resolver](https://www.resolver.com/blog/compliance-management-software-benefits/)
- [RERA Compliance Software for Builders India — BUILDX / Quantbit](https://quantbit.io/solutions/buildx/rera-compliance-software-builders-india)
- [Best RERA Filing and Compliance Software in India — SoftwareSuggest](https://www.softwaresuggest.com/rera-filing-compliance-software)
- [Project Quarterly Compliances in RERA — RERA Filing](https://rerafiling.com/project-quarterly-complainces.php)
- [Responsibilities of Builders & Promoters under RERA — IndiaFilings](https://www.indiafilings.com/learn/responsibilities-of-builders-promoter-rera/)
- [AI Tools for Legal Research 2026: Harvey, Westlaw AI, Lexis+ AI, and Why Citation Hallucination Still Matters — Sonomos](https://sonomos.ai/blog/ai-legal-research-harvey-westlaw-casetext-2026/)
- [Hospital and Healthcare Establishment Legal Compliance India: 2026 Guide — Altacit](https://www.altacit.com/hospital-and-healthcare-establishment-legal-compliance-in-india-2026/)
- [Best Healthcare Compliance Software 2026 — ComplyAssistant](https://www.complyassistant.com/resources/tips/best-healthcare-compliance-software/)
