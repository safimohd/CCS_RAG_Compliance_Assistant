# Indian Regulatory Compliance Intelligence Assistant
**CCS Project — IIM Bangalore, Term 5, PGPBA 2026-28**
**Team:** Safi (Mohammad Safi Ur Rahman) and Sairaj
**Faculty Advisor:** Prof. Subhabrata Majumdar ("Subho")
**GitHub:** https://github.com/safimohd/CCS_RAG_Compliance_Assistant
**Last updated:** November 11, 2026

---

## What This Project Is

A Retrieval-Augmented Generation (RAG) compliance assistant that answers Indian regulatory questions across two domains: Healthcare and Real Estate (RERA). The project spans corpus assembly, a live Streamlit app, a rigorous benchmark comparing three system designs, and an LLM-as-judge evaluation pipeline.

The core research question: does retrieval-augmented generation meaningfully improve answer quality over a raw LLM on domain-specific regulatory questions?

---

## Deployed Apps

| Domain | URL |
|--------|-----|
| Healthcare | https://ccsragcomplianceassistant-6hug2utprjrpwtyijwjanr.streamlit.app |
| RERA | https://ccsragcomplianceassistant-cisyv46xzq7kte95ywkmhg.streamlit.app |

Both are on Streamlit Community Cloud. Auto-deploy on every push to `main`. Both must remain public so they stay warm (private apps cannot be pinged anonymously).

Admin dashboard: append `?admin=1` to either URL. Password: `iimb2024`.

---

## Folder Structure

```
RAG_Project/
  README.md                  <- this file
  PROJECT_OVERVIEW.md        <- Oct 29 snapshot of system design
  requirements.txt           <- top-level Python deps
  .gitignore / .gitattributes

  01_Benchmarks/
      RERA/                  <- 12 benchmark xlsx files (V1 through V5)
      Healthcare/            <- Healthcare 50Q benchmark

  02_Corpus/
      RERA/                  <- 91 PDFs, tiered A/C/E
          01_Central_RERA/   <- RERA Act 2016 (1 doc)
          02_Karnataka_RERA/ <- K-RERA rules, circulars, penalty orders (41)
          03_Supporting_Guidance/ <- FAQs, model forms (12)
          04_Review/         <- unverified, excluded by default (37)
      Healthcare/            <- 267 PDFs, tiered A-E
          01_Core_Regulatory/     (54)
          02_Core_PMJAY_Insurance/(39)
          03_Medical_Regulation_Education/ (32)
          04_Supporting_Guidance/ (48)
          05_Draft_Proposed/      (11)
          06_Review/              (83)
      _ocr_text/             <- Tesseract sidecar files for image-only PDFs
      _new_sources/          <- incoming documents not yet classified

  03_Metadata/
      RERA/                  <- krera_metadata.csv, rera_corpus_mapping.csv, rera_master_index.csv
      Healthcare/            <- healthcare_corpus_mapping.csv and all source CSVs
      ingestion_report.csv   <- 350-row log of every ingested document
      corrupt_pdf_report.csv
      _organize_manifest.csv

  04_Scripts/
      RERA/
          run_v2_evaluation_v24.py   <- final RERA evaluation script
          create_v2_benchmark.py     <- benchmark question builder
          update_expiry_dates.py
          scraper/                   <- K-RERA website scraper
          download/
          classification/
      Healthcare/
          download/
          classification/
      Utilities/
          keep_warm.py               <- app keep-warm pinger
          run_awake.ps1              <- holds laptop awake during long runs
          run_both_awake.ps1
          run_git_push.bat
          fix-keepwarm.ps1
          combine_healthcare_files.ps1

  05_App/
      app.py                 <- main Streamlit UI (entry: combined, 4-tab UI)
      app_healthcare.py      <- entry point for Healthcare deployment (execs structured_chat.py)
      app_rera.py            <- entry point for RERA deployment (execs user_flow_rera.py)
      user_flow_rera.py      <- RERA standalone app (828 lines, completely separate from Healthcare)
      structured_chat.py     <- Healthcare chat pipeline
      rag_query.py           <- retrieve(), index_titles(), to_cosine(), FACILITY_MARKERS
      ingest_corpus.py       <- PDF ingestion into ChromaDB
      admin.py               <- admin dashboard (774 lines)
      requirements.txt       <- Streamlit Cloud deps (includes plotly, pysqlite3-binary)
      .env / .env.example    <- API keys (NEVER committed)
      chroma_db/             <- local vector store (gitignored)
      benchmark/             <- evaluation pipeline
          run_benchmark.py        <- runs all systems on benchmark questions
          evaluate_benchmark.py   <- LLM-as-judge scoring
          agentic_rag.py          <- System C multi-step logic
          system_d_cvrag.py       <- System D claim-verification logic
          questions_rera.py       <- 50 RERA benchmark questions with expected answers
          questions_healthcare.py <- 50 Healthcare questions
          run_v2_evaluation.py    <- V2 evaluation runner
          run_system_d_benchmark.py

  06_Evaluation/
      Results/RERA/          <- 10 timestamped raw run xlsx files
      Logs/RERA/             <- benchmark build logs
      Raw_Responses/
      Scoring/
      scope_baseline.json    <- regression baseline for measure_scope.py

  07_Documentation/
      HANDOVER.md            <- Oct 11 full session handover
      HANDOVER_11OCT.md      <- Oct 11 architecture notes (scope fix, bugs)
      PROJECT_DOCUMENTATION.txt <- full narrative from Aug 24 to Nov 5
      feedback_prof_soudeep_deb_RERA_4nov.txt <- Prof's Nov 4 feedback
      DEPLOY.md              <- deployment checklist
      GO_LIVE.md             <- two-app deployment procedure
      DEMO_PLAN.md
      MARKET_POSITIONING.md
      SHARING.md
      Meeting_Notes/
      Project_Proposal/
      Reports/
      Research/

  08_Archive/
      Superseded_Collections/   <- original Healthcare collections (440 PDFs), old project clone
      Raw_Downloads/UP_RERA_Reference/ <- UP-RERA reference PDFs (out of scope)
      Failed_or_Unused/         <- 20+ PDFs that could not be used
      chroma_db_root_backup/    <- old root-level chroma duplicate
      app_working_27sep.py      <- Sept 27 snapshot before scope refactor
      chroma_db.tar.gz          <- old index archive

  09_Deliverables/              <- final report PDF and presentation deck go here
```

---

## Project Timeline

### August 24, 2026 — Kickoff

First meeting: Safi, Sairaj, Prof. Majumdar. Agreed deliverables: a benchmark dataset and a cloud-deployed AI compliance assistant. Domains confirmed: Healthcare and Real Estate. Deployment target at this stage was Vercel. Three-month plan: Month 1 Build, Month 2 Demo, Month 3 Build and Demo.

### September 2026 — Corpus Assembly and First Build

Assembled the RERA corpus from the K-RERA website. Discovery: 88 of 350 PDFs had no text layer. Karnataka RERA was approximately 87% image-only. Without OCR, retrieval would have silently failed on nearly every RERA query.

**Pivot 1 (OCR):** Ran Tesseract on all image-only PDFs. Sidecar `.txt` files stored in `02_Corpus/_ocr_text/`. `ingest_corpus.py` checks for sidecars and uses them when a PDF yields fewer than 300 characters.

Built the RAG pipeline on LangChain and ChromaDB:
- Embedding model: `BAAI/bge-small-en-v1.5` (384d, normalized)
- Chunking: `RecursiveCharacterTextSplitter`, chunk 1000, overlap 150
- Generation: `gpt-5-nano` via OpenAI (Prof's API key)
- `TOP_K=6`, `CANDIDATE_K=24`, max 2 chunks per source document
- Authority tiers A through E with retrieval weights 1.00 / 0.95 / 0.80 / 0.55 / 0.35

**Bug 1 found (sidecar collision):** Two K-RERA circulars were character-identical apart from the financial year. Their OCR sidecar files were swapped during ingestion. The system gave confident, authoritative, wrong penalty figures under the correct title and correct tier. Fixed by renaming sidecars and rebuilding the index.

### October 2026 — Retrieval Bugs, Scope Fix, Benchmark

**Bug 2 found (scope post-filtering):** The dropdown scope filter was retrieving 24 chunks and then discarding any that did not match the selection. When nothing matched, it returned the unscoped 24 chunks silently. A physiotherapy question returned a Yoga Standards DRAFT because 20 near-identical CEA facility templates were filling the entire candidate window and the physiotherapy document never entered it. The fallback returned whatever was first.

**Pivot 2 (pre-filtering):** Moved all filters (domain, tier, scope) inside the ChromaDB `where` clause before any chunks are fetched. This also fixed tier starvation: previously, excluded-tier chunks were filling the 24-slot window and being discarded, leaving zero candidates for some questions.

**Bug 3 found (two mis-tiered drafts):** Yoga Standards and Siddha Standards had `DRAFT` in their titles but were tiered C (retrieved by default). Ten other draft documents were correctly tiered D. `fix_draft_tiers.py` re-tiered them: C to D in ChromaDB (194 chunks) and in `ingestion_report.csv` (2 rows).

**Bug 4 found (L2 vs cosine):** ChromaDB defaults to L2 distance. LangChain reports it as `1 - L2/sqrt(2)`, which goes negative. When multiplied by tier weight, a tier-A chunk at cosine -0.30 scores -0.30 while a tier-C chunk at the same cosine scores -0.24. Authority inverts exactly when relevance is weakest. `to_cosine()` in `rag_query.py` corrects this: `cos = 1 - (1 - score)^2`. Relevance is then `(cos + 1) / 2`, always non-negative.

Switched deployment from Vercel to Streamlit Community Cloud. The ChromaDB index exceeds GitHub's 100MB file limit, so it is packaged as `chroma_db.tar.gz` and uploaded as a GitHub Release asset (`index-v1`). The deployed apps download it on first boot via an `INDEX_URL` secret.

**October 28 — V1 Benchmark Run**

Ran 600 questions (200 per system) with Qwen3-8B-Flash as the judge via Together AI. Credits ran out at row 537. 542 of 600 rows scored.

V1 scores (out of 8):

| System | Score |
|--------|-------|
| A — Base LLM (no retrieval) | 5.07 |
| C — Agentic RAG | 4.19 |
| B — Plain RAG | 4.04 |

Base LLM outperformed both RAG systems. The three compounding reasons: retrieval precision failures, tier starvation eating the candidate window, and the 2-chunks-per-document cap cutting useful passages from authoritative sources.

**October 29 — Evaluation fix and re-run**

Switched judge to `DeepSeek-V4.1-Flash`. Fixed the judge to use `response_format={"type": "json_object"}` and robust regex fence-stripping. Completed the 58 failed rows.

**Human correction rule:** If `|Human Score - DeepSeek Score| > 1.5`, final score is the average of both. Otherwise, DeepSeek score stands.

**November 2026 — System D and V5 Final**

Added System D: CV-RAG (Claim Verification RAG). System D generates a candidate answer, extracts discrete claims from it, verifies each claim against ChromaDB independently, then synthesises a corrected answer. Implemented in `system_d_cvrag.py`.

**V5.1 Final RERA scores (out of 8, n=50):**

| System | Overall | T1 Factual | T2 Applied | T3 Procedural | T4 Multi-hop | T5 Adversarial |
|--------|---------|------------|------------|---------------|--------------|----------------|
| A — Vanilla RAG | 4.02 | 3.20 | 4.30 | 3.40 | 4.50 | 4.70 |
| B — RAG + BM25 | 4.62 | 4.70 | 4.10 | 4.90 | 4.50 | 4.90 |
| C — Sub-query + Reflection | 4.90 | 5.60 | 5.10 | 4.80 | 4.40 | 4.60 |
| D — CV-RAG | pending (run in progress) | | | | | |

System C is the current best. All three systems plateau on T4 Multi-hop and T5 Adversarial (4.4-4.7 range); the gap is primarily in T1 Factual retrieval precision.

**Why System D underperformed on RERA:** The K-RERA ChromaDB contains only circulars from 2019 to 2026, not the RERA Act 2016 base text. Claims about foundational provisions (Section 3, Section 11, etc.) cannot be verified against the corpus and get incorrectly flagged as unsupported. This is a corpus coverage issue, not a system design flaw.

**Cross-validation (V2.1):** Independent re-scoring of a stratified 50-row sample found five DeepSeek bias patterns:
1. CO underscoring on retrieval-heavy answers
2. FA ceiling applied to corpus-grounded answers even when complete
3. Missed hallucination detection on Section 3(1) fabrications
4. Over-credit for vague answers without specific citations
5. CC under-credit for explicit uncertainty signalling

86% agreement between human and DeepSeek on the sample. 7 flagged rows.

**November 10 — Blank Response Fix (commit `0d96909`)**

`05_App/user_flow.py` line 355: `resp.choices[0].message.content` returned `None` silently on Streamlit Cloud (openai==3.48.0 installed there). The try block succeeded with no exception, so the error fallback never triggered and the assistant bubble rendered blank. Fix: check for `None` and surface `finish_reason` in the UI when content is empty. Committed and deployed.

**System D benchmark — in progress**

Running System D (CV-RAG) on `Benchmark_RERA_V5.1_Final.xlsx`. Pipeline: generate candidate answer, extract discrete claims, verify each claim against ChromaDB (25,955 chunks), synthesise corrected answer. Judge: DeepSeek-V4.1-Flash via Together AI. Expected runtime ~90 minutes.

```powershell
python run_v2_evaluation.py --benchmark "..\..\01_Benchmarks\RERA\Benchmark_RERA_V5.1_Final.xlsx" --only-system D
```

**November 3 — Prof. Majumdar UI Feedback**

Three requests:
1. Query history visible in the sidebar
2. Central vs State RERA toggle for the RERA bot (the Healthcare bot already has domain filtering)
3. Minimum clicks — map the full user journey and remove unnecessary steps

Priority agreed: complete UI changes first, then return to evaluation.

**November 2026 — Investment Signals Tab**

A new Investment Signals tab was added to `app.py` with live 2026 price data sourced from NoBroker, 99acres, and MagicBricks (anchored to November 2026). The tab covers 20 Bangalore localities across 5 zones (Central, East, North, South, West) and contains three sub-tabs:

1. **Fair Price Checker** — compares a quoted price per sq ft against the locality band (±15% tolerance). Returns FAIR / PREMIUM / BELOW MARKET verdict with reasoning.
2. **Buy vs Rent vs Invest** — calculates monthly EMI at 8.5% interest over 20 years, compares against comparable rental yield, and returns break-even years. Helps a buyer decide whether to purchase or rent.
3. **Locality Explorer** — table view of all 20 localities with zone, average PSF, trend indicator, CAGR estimate, and risk rating (Low/Medium/High).

Each locality record stores: `avg_psf`, `trend` (Rising/Stable/Cooling), `zone`, CAGR scaling factor, and risk rating. Sources cited inline per locality.

**November 2026 — Domain Separation (Sairaj Feedback)**

Sairaj reviewed the app and flagged that the shared codebase between RERA and Healthcare was confusing and created merge risk. The two domains are now fully separate:

| Domain | Entry file | App file | Deployment |
|--------|-----------|----------|------------|
| RERA | `app_rera.py` | `user_flow_rera.py` | https://ccsragcomplianceassistant-cisyv46xzq7kte95ywkmhg.streamlit.app |
| Healthcare | `app_healthcare.py` | `structured_chat.py` | https://ccsragcomplianceassistant-6hug2utprjrpwtyijwjanr.streamlit.app |

`app_rera.py` sets `APP_DOMAIN="RERA"` and exec's `user_flow_rera.py`. Nothing is shared between the two codebases at runtime. `user_flow_rera.py` is 828 lines and fully self-contained: it includes its own `_secret()` helper, all LLM calls, the K-RERA Supabase lookup, the investment calculator, and the report card renderer.

**November 2026 — RERA Chat UX Overhaul (Sairaj Feedback)**

Sairaj also flagged that the RERA app's UX was confusing: uploading a document caused the chat to disappear. The UX was rebuilt in `user_flow_rera.py`:

- **Document upload moved to the sidebar.** Users can upload multiple PDFs and paste URLs for context in a persistent sidebar panel. A "Load into chat" button triggers extraction.
- **Chat is always visible.** The report card is stored in `st.session_state.uf_report_data` and rendered above the chat; removing the blocking `return` after the report means the chat never disappears after a document is loaded.
- **Multi-document context.** Text from all uploaded PDFs and URLs is combined into a single context block injected into the system prompt at chat time.

**November 2026 — Bug Fix: Empty Response from gpt-5-nano**

`gpt-5-nano` on Streamlit Cloud returned `None` for `resp.choices[0].message.content` when `max_completion_tokens=1500` was set and the system prompt was large. The model silently truncated its response to zero tokens. Fix in `user_flow_rera.py`: the `max_completion_tokens` parameter was removed entirely from all `client.chat.completions.create()` calls. Debug output showing `finish_reason`, message count, and `system_chars` is logged when content is empty so future silent failures surface immediately.

Note: `structured_chat.py` and the older `user_flow.py` still carry the `max_completion_tokens` bug. Only `user_flow_rera.py` is fixed.

---

## System Architecture

### Two Separate Deployments

```
RERA deployment
  app_rera.py  (sets APP_DOMAIN="RERA", execs user_flow_rera.py)
    └─ user_flow_rera.py  (828 lines, fully self-contained)
         ├─ Sidebar: multi-PDF upload + URL paste
         ├─ K-RERA live lookup via Supabase REST (10,000+ projects)
         ├─ Investment Signals: Fair Price, Buy vs Rent, Locality Explorer
         ├─ Report card: RERA status, price verdict, break-even
         └─ Always-visible chat with injected document context

Healthcare deployment
  app_healthcare.py  (sets APP_DOMAIN="Healthcare", imports structured_chat.py)
    └─ structured_chat.py  (Healthcare chat pipeline, ChromaDB RAG)
```

### RAG Pipeline (both domains)

```
User query
  -> embed with BAAI/bge-small-en-v1.5
  -> ChromaDB query (where: domain + tier + scope filters PRE-applied)
  -> fetch CANDIDATE_K=24 chunks
  -> to_cosine() correction + tier weight re-ranking
  -> top TOP_K=6 chunks to LLM
  -> gpt-5-nano generates answer with citation instructions
  -> display with document titles (cleaned), tier, source
```

### Three Benchmarked Systems

| System | Strategy |
|--------|----------|
| A | No RAG — raw gpt-5-nano from training knowledge |
| B | Standard RAG — top-k vector retrieval from ChromaDB |
| C | Agentic RAG — sub-query decomposition, multi-hop retrieval, reflection |
| D | CV-RAG — generate, extract claims, verify each claim, synthesise |

### Scoring Rubric (max 8 per question)

| Dimension | Max |
|-----------|-----|
| Factual Accuracy (FA) | 4 |
| Completeness (CO) | 2 |
| Confidence Calibration (CC) | 2 |

### Benchmark Structure

50 questions per domain, 5 tiers of complexity (10 questions each):

| Tier | Type |
|------|------|
| T1 | Factual recall |
| T2 | Multi-provision |
| T3 | Scenario-based |
| T4 | Edge cases |
| T5 | Refusal / out-of-scope |

---

## Authority Tiers

Every corpus document is assigned a tier that controls whether it reaches the LLM.

| Tier | Bucket | Meaning | Retrieved by default |
|------|--------|---------|---------------------|
| A | 01_Central_RERA, 02_Karnataka_RERA, 01_Core_Regulatory, 02_Core_PMJAY_Insurance | Binding law, gazette notifications, binding circulars | Yes |
| B | 03_Medical_Regulation_Education | NMC regulations, medical education standards | Yes |
| C | 03_Supporting_Guidance (both domains), 04_Supporting_Guidance | FAQs, guidelines, model forms | Yes |
| D | 05_Draft_Proposed | Drafts, proposed amendments | No (excluded) |
| E | 06_Review, 04_Review | Binding status unverified, admin/recruitment material | No (excluded) |

Tiers D and E are excluded from the default `where` clause. Including them requires an explicit opt-in. 38% of Healthcare chunks and 29% of RERA chunks are in excluded tiers.

---

## Key Technical Decisions (do not relitigate)

1. **Filters go inside the ChromaDB `where` clause, not after retrieval.** Post-filtering created silent failures and fallbacks that returned wrong documents.

2. **`clear_index()` runs at the top of `main()` in `ingest_corpus.py`, before extraction.** `shutil.rmtree` hit a Windows PermissionError (OneDrive lock) partway through deletion, leaving a silently unusable index after 35 minutes of extraction. Delete first, then extract.

3. **`to_cosine()` must be applied before tier-weighted ranking.** Raw L2-derived scores go negative and invert authority ordering. The fix: `cos = 1 - (1 - score)^2`, then `rel = (cos + 1) / 2`.

4. **01_Benchmarks/ is never indexed.** Indexing the expected answers would let the system retrieve its own answer key and invalidate the base-vs-RAG comparison.

5. **Both deployed apps must be public.** Private apps cannot be pinged anonymously, so they cannot be kept warm. Rate limiting is handled inside the app via `DAILY_QUESTION_LIMIT`.

6. **`APP_DOMAIN` is set in the entry file, not in Secrets.** Streamlit's `_secret()` reads environment variables first. A stale Secrets value cannot flip an app to the wrong domain.

7. **No credentials ever committed to git.** API keys live in `.env` (gitignored) locally and in Streamlit Secrets on deployment. This is a hard rule.

8. **RERA and Healthcare are entirely separate codebases at runtime.** `user_flow_rera.py` has no imports from `structured_chat.py` and vice versa. Changes to one domain cannot silently break the other. Do not introduce shared modules between them.

9. **`max_completion_tokens` must NOT be set for gpt-5-nano on Streamlit Cloud.** The model silently returns `None` content when the token cap is reached on a large system prompt. Remove this parameter entirely; the model's own context limit is the effective ceiling.

10. **Investment PSF data is hardcoded to November 2026 and must be updated each review cycle.** The `LOCALITIES` dict in `user_flow_rera.py` holds `avg_psf` values anchored to Nov 2026. Source columns are cited per locality. Update when NoBroker/99acres/MagicBricks data is refreshed; do not let it drift more than 6 months without a refresh pass.

---

## Corpus Accounting

| | PDFs |
|---|---|
| Total in project | 803 |
| Production corpus (02_Corpus) | 358 |
| Archived (08_Archive) | 445 |
| Missing | 0 |

Nothing has been deleted. Every PDF is in exactly one of: production corpus, review, or archive.

Measured index (read from chroma.sqlite3, Nov 2026):

| | Healthcare | RERA |
|---|---|---|
| Chunks | 24,586 | 1,369 |
| Documents | 258 | 86 |
| Retrievable (tier A/B/C) | 161 | 53 |

---

## Known Issues and Accepted Trade-offs

| Issue | Status |
|-------|--------|
| One Kannada tier-A RERA document could not be processed (transfer failure) | Documented, accepted loss |
| K-RERA corpus has no RERA Act 2016 base text, only circulars from 2019-2026 | Explains System D underperformance on foundational provisions |
| `02_Corpus/Healthcare/06_Review/` (83 docs) and `02_Corpus/RERA/04_Review/` (37 docs) have unverified binding status | Awaiting human review before promotion or drop |
| Apps sleep after 12 hours of inactivity; cold boot takes several minutes (index download + model load) | Mitigation: GitHub Actions keep-warm workflow (see GO_LIVE.md) |
| `.usage.json` is not in .gitignore but is per-deployment runtime state | Should be added post-submission |
| `structured_chat.py` and `user_flow.py` still have `max_completion_tokens=1500` bug | Only `user_flow_rera.py` is fixed; Healthcare deployment may produce blank responses on large prompts |
| `structured_chat_healthcare.py` not yet created | Healthcare domain still running from `structured_chat.py` which contains 53 RERA/Karnataka references |

---

## Git Workflow

```powershell
git add <specific files>   # never git add -A
git status --short         # verify .env, chroma_db/, *.tar.gz, 02_Corpus/**/*.pdf are absent
git commit -m "message"
git push
# Streamlit Cloud auto-redeploys within 60-90 seconds
```

Do not use `git add -A` or `git add .`. Sensitive files must not appear in the staged list.

---

## Running the Benchmark

```powershell
# From 05_App/benchmark/
$env:OPENAI_API_KEY = "sk-..."
python run_benchmark.py
# Output: results/benchmark_answers_YYYYMMDD_HHMMSS.xlsx
# Time: approximately 4-5 hours for 50 questions
```

---

## Running the Evaluation (LLM Judge)

```powershell
# DeepSeek judge via Together AI
$env:TOGETHER_API_KEY = "..."
python run_v2_evaluation_v24.py results/benchmark_answers_YYYYMMDD.xlsx
# Output: results/benchmark_evaluation_YYYYMMDD.xlsx
```

---

## What Remains

| Task | Status |
|------|--------|
| Blank response fix in user_flow.py (None content from gpt-5-nano) | Done — commit `0d96909`, Nov 10 |
| Domain separation: `user_flow_rera.py` created as standalone RERA codebase | Done — commit `b4d0fbc`, Nov 11 |
| RERA chat UX overhaul: sidebar upload, multi-PDF + URL, always-visible chat | Done — in `user_flow_rera.py` |
| Investment Signals tab: Fair Price, Buy vs Rent, Locality Explorer | Done — in `app.py` |
| 2026-anchored PSF data for 20 Bangalore localities (NoBroker, 99acres, MagicBricks) | Done — in `app.py` |
| Fix `max_completion_tokens` bug causing silent blank responses | Done — in `user_flow_rera.py` only |
| System D benchmark run (CV-RAG, RERA V5.1) | In progress (~90 min) |
| UI changes from Nov 3 prof feedback (query history, RERA toggle, journey) | In progress |
| Create `structured_chat_healthcare.py` (clean copy, strip 53 RERA references) | Pending |
| Update `app_healthcare.py` to use `structured_chat_healthcare.py` | Pending |
| Fix `max_completion_tokens` bug in `structured_chat.py` | Pending |
| System D RERA score with RERA Act 2016 added to corpus | Pending |
| Complaint/delay data layer in K-RERA Project Lookup tab | Pending |
| Final report (PDF, max 10 pages) | Pending |
| 10-minute presentation deck with live demo | Pending |
| Repackage and re-upload chroma_db.tar.gz to GitHub Release index-v1 | Pending |
| Move scope_baseline.json rebaseline commit | Pending |

Final report and deck go in `09_Deliverables/`.

---

## People

| Person | Role |
|--------|------|
| Safi (Mohammad Safi Ur Rahman) | Developer, project lead |
| Sairaj | Teammate |
| Prof. Subhabrata Majumdar | Faculty advisor, provided API keys |
| Prof. Arnab | Healthcare domain expert (connected by Prof. Majumdar) |
