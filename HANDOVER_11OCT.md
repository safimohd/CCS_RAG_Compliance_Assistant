# CCS RAG Compliance Assistant — handover, 11 Oct 2026

## Prompt

You are continuing work on my IIM Bangalore CCS course project. Read this whole
brief before replying. Do not re-plan the project and do not re-derive the
sequence — it is recorded in §7. Start at the step marked NEXT. The previous
session's work is all on disk and verified; what remains is mostly running
things and deploying.

---

## 1. Who and what

- Me: Safi. IIM Bangalore, Term 5, CCS course. Prof. Subhabrata Majumdar ("Subho").
- GitHub `safimohd`, repo `CCS_RAG_Compliance_Assistant`, email `rahmansafi2000@gmail.com`.
- Project: **Indian Regulatory Compliance Intelligence Assistant** — an
  authority-tiered RAG system over two domains: **Healthcare**, and
  **Real Estate / RERA** (Central + Karnataka).
- No submission deadline at present. Deliverables: report (PDF, ≤10 pages) +
  10-minute presentation with live demo.
- Prof has connected us to domain experts: **Prof. Arnab** (healthcare), real
  estate reviewer TBD. He forwards the app link **without telling me in
  advance** — this is why the apps must stay warm by themselves.
- Project root:
  `C:\Users\rahma\OneDrive - Indian Institute of Management\Desktop\Term 5\CCS\RAG_Project`

### How I work best with you

- Short, direct replies. Give me the command to run, not a lecture.
- Measure before arguing. Build the measurement script rather than defending a
  design.
- Keep the demo critical path separate from the project critical path.
- Own mistakes plainly when a tool reports success and the artefact is wrong.
  That has happened repeatedly and I'd rather hear it immediately.

---

## 2. Architecture as built

- LangChain + ChromaDB. Embeddings `BAAI/bge-small-en-v1.5` (384d,
  `normalize_embeddings=True`).
- `RecursiveCharacterTextSplitter`, chunk 1000 / overlap 150.
- `TOP_K=6`, `CANDIDATE_K=24`, max 2 chunks per source document.
- Generation: `gpt-5-nano` via OpenAI, on **the professor's API key**.
- UI: Streamlit, deployed on Streamlit Community Cloud.
- Authority tiers A–E. Retrieval weights: A 1.00, B 0.95, C 0.80, D 0.55, E 0.35.
  **D and E are excluded by default.**
- Index ships as a **GitHub Release asset** (`chroma_db.tar.gz`) because
  GitHub's hard file limit is 100MB; the app downloads it on first boot.
  `pysqlite3-binary` shim required at the top of `app.py` (Chroma needs
  SQLite ≥3.35).
- `APP_DOMAIN` locks one deployment to one domain and hides the selector. As of
  11 Oct it is set by the **entry file**, not by Secrets (see §5).

### Measured corpus facts (11 Oct, read directly from `chroma.sqlite3`)

| | Healthcare | RERA |
|---|---|---|
| chunks | 24,586 | 1,369 |
| documents | 258 | 86 |
| retrievable documents (tier A/B/C) | 161 | 53 |
| chunks in excluded tiers (D/E) | 38% | 29% |
| documents tied to one facility type / one year | 17 | 13 |
| documents that are general | 144 | 40 |

Total 25,955 embeddings. Document tiers: Healthcare A72 B20 C67 D15 E84;
RERA A41 C12 E33.

---

## 3. What the previous session changed — file by file

Everything below is **on disk and byte-verified** against the intended content.

### `05_App/rag_query.py`
- `index_titles(store, domain, tiers)` — distinct document titles read from the
  **collection**, not from `ingestion_report.csv`. The CSV is what ingestion
  intended; the collection is what retrieval matches on.
- `resolve_titles(universe, needles, is_narrow)` → `(specific, general)`.
- `FACILITY_MARKERS`, `_FY_RE`, `is_narrow_doc(domain, title)` — what
  "narrowing the search" means. Lives here, not in `app.py`, because it is a
  fact about the corpus; `measure_scope.py` and `audit_scope.py` read the same
  copy so they cannot drift.
- `retrieve(..., titles=None)` — **domain, tier and scope filters all now go
  INSIDE the Chroma `where` clause.** This is the core change.
- CLI: `--scope SUBSTRING` (repeatable) and `--list-titles`.

### `05_App/app.py`
- `scope_universe(domain)` — index titles, cached, CSV fallback.
- `resolve_scope(domain, needles)` → `(specific, general)`.
- `scope_count` returns a tuple; `scope_caption` rewritten in plain language.
- **`apply_scope()` deleted.** The offending line was
  `return (keep or results)[:limit], not keep` — `keep or results` is the
  silent widening. A comment block where it used to be records why.
- `clean_title(title, source_file)` + `_from_filename()` — display-only title
  tidying (§6).
- `import re` added.

### `05_App/measure_scope.py` — the regression test
- Tests the shipped behaviour; exits **1** and prints
  `REGRESSION FAILED — do not deploy this build` on failure.
- Baselines the **unscoped** column to `06_Evaluation/scope_baseline.json`.
- `--rebaseline` to accept an intended change.
- Tier-aware drift diagnosis: a document that left because its tier changed is
  reported as an intended change, not a failure.
- Asserts the control case reaches at least one always-applicable document.

### `05_App/test_scope.py` — NEW, runs anywhere
Seven sections, no index and no embedding model needed (drives the real
`retrieve()` against a fake Chroma that honours the `where` clause). Runs in
about a second. Section 6 asserts that a **rejected** design fails, so nobody
reintroduces it; section 7 reproduces tier starvation.

### `05_App/audit_scope.py` — NEW
Prints every retrievable title sorted into SPECIFIC / GENERAL / UNREACHABLE,
plus DEAD OPTIONS. Reads the dropdown constants out of `app.py` via its AST.

### `05_App/fix_draft_tiers.py` — NEW, **already applied**
Re-tiered the Yoga and Siddha drafts C→D in both the index (194 chunks) and
`03_Metadata/ingestion_report.csv` (2 rows). Verified: 350 CSV rows in, 350
out, 9 columns and the BOM preserved, only two cells changed.

### `05_App/app_healthcare.py`, `05_App/app_rera.py` — NEW
Five-line entry points, each setting `APP_DOMAIN` then running the shared
`app.py`.

### `07_Documentation/GO_LIVE.md` — NEW
The ordered deployment checklist.

### `05_App/run_evaluation.py`, `05_App/.env.example`
Rewired from Together to OpenRouter earlier in the day. **The evaluation is now
dropped** (§4), so this is parked, not live work.

---

## 4. Decisions taken, so they are not relitigated

1. **The evaluation is dropped entirely.** I will clarify scope with the prof.
   `run_evaluation.py` is wired and parked. Nothing depends on it.
2. **No human scoring.** Three people scoring 400 responses each (~5 hours per
   scorer) is not happening. Asked and answered.
3. **Scope = documents specific to the selection + documents that apply to
   every case.** Not strict exclusion, not an allowlist of general documents.
4. **Prototype first, then submit to prof for feedback.** Fix, deploy, show.

---

## 5. Streamlit deployment — the two things DEPLOY.md does not cover

`07_Documentation/DEPLOY.md` covers one app and the index Release. `GO_LIVE.md`
covers these.

**One script = one app.** Community Cloud identifies an app by repo + branch +
main file path; deploying the same script twice just redeploys the first app.
Hence two entry files:

| Main file path | Domain |
|---|---|
| `05_App/app_healthcare.py` | Healthcare |
| `05_App/app_rera.py` | RERA |

`APP_DOMAIN` is set in the entry file, not in Secrets, because `_secret()` reads
environment variables first — so a stale Secrets value cannot flip an app to the
wrong domain. Verified.

**Apps sleep after 12 hours** of inactivity (reduced from 7 days). Waking one
gives a fresh container, which re-downloads the ~150MB index and re-pulls the
BGE model before answering anything — minutes. A private app cannot be kept
warm at all: an anonymous request reaches the Google sign-in page, not the app.
So **both apps must be public**, and the spend risk is carried by
`DAILY_QUESTION_LIMIT` inside the app instead.

Secrets for both apps (no `APP_DOMAIN` needed):

```toml
OPENAI_API_KEY = "sk-proj-..."
INDEX_URL = "https://github.com/safimohd/CCS_RAG_Compliance_Assistant/releases/download/index-v1/chroma_db.tar.gz"
LLM_REASONING_EFFORT = "low"
DAILY_QUESTION_LIMIT = "100"
CONTACT_EMAIL = "rahmansafi2000@gmail.com"
```

Caveat to carry forward: Streamlit staff have **not** confirmed that HTTP
traffic resets the sleep timer — the only keep-alive they endorse is a
scheduled commit to the repo. The 10-minute ping is the cheaper thing to try
first. `GO_LIVE.md` has the commit-based fallback.

---

## 6. UI: technicalities removed

Prof's standing instruction is to minimise technical detail in the UI. Already
gone from view in earlier sessions: similarity scores, passage counts, tier
letters, reasoning-effort indicator, binding-evidence percentage,
retrieved-passage sliders, internal corpus folder names.

Added 11 Oct — document titles, which were the worst remaining offender.
68 of 214 displayed something a reviewer should not see:

| Before | After |
|---|---|
| `Hospital(Level 2) (832.09 KB )` | `Hospital(Level 2)` |
| `1 \| OPERATIONAL \| GUIDELINES FOR \| CLINICAL \| ESTABLISHMENTS ACT \| 2 \| Conte…` | `OPERATIONAL GUIDELINES FOR CLINICAL ESTABLISHMENTS ACT` |
| `(i) Management of Common Mental Disorders` | `Management of Common Mental Disorders` |
| `2794 GI/2018 (1) \| jftLVªh laö Mhö ,yö&33004@99 REGD…` | `Gazette of India notification 2794 GI/2018` |
| `1 \| Preamble \| Kala-azar (KA) or Visceral…` | `Road Map for Kala-Azar Elimination` |

12 titles carried a file size, 33 were pipe-fragmented by OCR. **Display only** —
retrieval, scoping and the tiers all still key on the real string, so the
regression needles in `measure_scope.py` match raw titles.

Sidebar caption also stopped quoting unusable numbers. Was "4 documents cover
this. 143 more apply throughout." Now "4 documents are specific to this. Plus
the rules that apply to every case."

---

## 7. What is left

1. **Run the two tests.** ← **NEXT**
   ```
   cd "C:\Users\rahma\OneDrive - Indian Institute of Management\Desktop\Term 5\CCS\RAG_Project\05_App"
   python test_scope.py          # expect: ALL CHECKS PASSED, 7 sections
   python measure_scope.py       # read the drift report
   python measure_scope.py --rebaseline
   ```
   The unscoped column **will** move, legitimately: fixing tier starvation
   changes what retrieval sees. The 11 Oct assertion may also fail, because
   that run predates the fix and was partly shaped by the defect — the test
   prints each document's tier and says so. Judge the new set on whether the
   documents answer the question, then rebaseline. Commit
   `06_Evaluation/scope_baseline.json`.

2. **Create `.github/workflows/keep-warm.yml`.** Content is in
   `GO_LIVE.md` §5 / the file I was sent. It could not be written remotely —
   `.github/` is a protected path for remote writes because workflow files
   execute code. Pings both apps every 10 minutes; skips with a warning until
   the two URLs are filled in.

3. **Repackage and re-upload the index.** Easy to forget and it matters: the
   re-tier changed `chroma.sqlite3`, so the deployed apps keep the old tiers
   until the Release asset is replaced.
   ```
   cd 05_App
   tar -czf chroma_db.tar.gz chroma_db
   cd ..
   gh release upload index-v1 "05_App\chroma_db.tar.gz" --clobber
   ```
   A backup of the pre-fix index is at `05_App/chroma_db/chroma.sqlite3.bak`.
   Do not commit either file — `.gitignore` covers `*.tar.gz`.

4. **Push.** Check the staged list with `git status --short` before committing;
   `.env`, `chroma_db/`, `chroma_db.tar.gz` and `02_Corpus/**/*.pdf` must not
   appear. Verify with `git check-ignore -v`, not by trusting `.gitignore`.

5. **Deploy both apps**, make both public, secrets on both, then fill the two
   URLs into the workflow. `GO_LIVE.md` is the checklist.

6. **Local test pass** — both domains, one scoped and one unscoped question
   each, the daily cap at its boundary.

7. **Report + deck**, built around the findings in §8.

---

## 8. The failure findings — report material

All the same shape: **the system reported success and the artefact was wrong.**

1. **Image-only PDFs.** 88 of 350 had no text layer. Karnataka RERA was ~87%
   invisible to retrieval before OCR.
2. **The sidecar collision.** Two tier-A circulars served each other's penalty
   figures — under correct titles and correct authority tags.
3. **The metric mismatch.** L2 scores weighted as cosine inverted authority
   ranking precisely when retrieval was weakest (§9).
4. **Pipeline ordering.** Expensive work placed before destructive work;
   35 minutes of extraction lost to a delete that failed halfway.
5. **The free tier that moved.** Gemma 3n aged out of free hosting
   mid-project. Measured 11 Oct: `openai/gpt-oss-20b:free` now also resolves
   with an **empty endpoints array** — the id is live, no provider serves it
   free — while `google/gemma-4-26b-a4b-it:free` has one live endpoint. The
   free tier moved under two models in opposite directions. A model id that
   resolves is not a model that runs.
6. **Two mis-tiered drafts.** Twelve documents say DRAFT in the title. Ten are
   correctly tier D and excluded from retrieval by default. The **Yoga** and
   **Siddha** drafts were tier **C**, which is retrieved — so they were being
   presented alongside binding law. They are exactly the two documents that
   polluted the measured results. The tier system worked for ten of twelve and
   failed silently for two, and the two failures were the ones a reader saw.
7. **Filtering after retrieval instead of inside it** — the biggest one, and it
   appeared twice in the same codebase:
   - **Scope.** The dropdown post-filtered the top 24 results by title and,
     when nothing matched, returned the unscoped list. Measured: a
     physiotherapy equipment question returned a Yoga standards DRAFT. The
     physiotherapy document exists but never enters the 24-**chunk** window,
     because ~20 near-identical CEA templates at several chunks each fill it
     first — and generic questions are exactly what the dropdown exists to
     serve.
   - **Tier.** The A/B/C filter also ran on the results, after 24 chunks had
     been fetched. 38% of Healthcare chunks and 29% of RERA chunks are in
     excluded tiers, so drafts and unverified material ate the candidate
     budget and were then discarded. Measured: "What equipment must be
     available?" returned **zero documents** from 161, and "What are the
     minimum staffing requirements?" returned one. The corpus answers both.
     This affected the **unscoped** path — the default for every visitor.

A note worth including on method: the strict scope filter was itself an
overcorrection, caught by the regression test. Scoping to FY 2023-24 and asking
what share of allottees' money goes into a separate account returned two
audit-penalty circulars; the answer is in s.4(2)(l)(D) of the Act and the 2020
bank-account directions, neither of which carries a year in its title. A scope
reached only 16 of 161 Healthcare documents and 13 of 53 RERA ones, hiding 90%
and 75% of the library. Three designs were tried:

1. post-filter with fallback — silent, wrong;
2. strict title filter — hid the standing law;
3. **exclude competing facilities and years, keep the general law** — shipped,
   and derivable because only 17 and 13 documents respectively are narrow.

An allowlist of general titles was tried in between and rejected as a 144-entry
maintenance burden. The complement rule ("general = matches no dropdown
option") is unsafe on its own, because yoga and siddha are not dropdown options
and their drafts would read as general — which is why `FACILITY_MARKERS`
includes facility types the dropdown does not offer.

---

## 9. Gotchas — do not relearn these

- `ingest_corpus.py`: `clear_index()` is called at the **TOP of `main()`**,
  before the 35-minute extraction, with 3 retries. **Do not move it back down.**
  `shutil.rmtree` hit `PermissionError [WinError 5]` (OneDrive lock) *after*
  deleting as it walked, leaving an index present on disk and silently unusable.
- **L2 vs cosine authority inversion.** The index was built with Chroma's
  default `l2`. LangChain reports `1 - L2/√2`, which goes negative.
  `to_cosine()` recovers true cosine exactly for unit vectors:
  `cos = 1 - (1 - score)**2`. Then rank on a **non-negative** relevance,
  `rel = (score + 1) / 2`, never a raw cosine — with raw cosines,
  `-0.30 × 1.00` (tier A) sorts below `-0.30 × 0.80` (tier C), so authority
  inverts exactly when matches are poor. Both fixed in `rag_query.py`; do not
  regress them.
- Streamlit **keeps the active tab across a re-run**. This caused the
  dead-button bug the professor saw. Tabs were removed entirely.
- **Verify git with git, not with a tool's success report.** A `.gitignore`
  write once reported success without reaching disk, a 156MB tar.gz got staged,
  and the push was rejected. Use `git check-ignore -v`.
- **A remote file write can report success and not land.** Happened on 11 Oct:
  a commit returned success with zero rejections, the mtime on disk advanced,
  and the content was the previous version. It survived a 20-second wait and a
  re-read, so it was not read caching. Writing under a fresh filename fixed it.
  **Byte-diff anything important after writing it.** If a patched script behaves
  like the old one, suspect the write before suspecting the code.
- `huggingface.co` and `openrouter.ai` are **blocked by the Claude container's
  egress proxy**. Anything that loads the embedding model must run **on my
  laptop**. Note the useful exception: reading document titles and tiers only
  needs `chroma.sqlite3` metadata, so a Claude session can audit the corpus by
  staging that one file (247MB, under the 400MB stage limit) and querying it
  with plain SQL — no model needed. That is how the corpus numbers in §2 were
  measured. The index cannot be written back, though: the commit limit is 30MB
  per file.
- PowerShell parses `0x80000001` as a signed Int32 and overflows. Use
  `[uint32]2147483649`. See `run_awake.ps1`, which holds the laptop awake for
  long jobs:
  `powershell -ExecutionPolicy Bypass -File ..\run_awake.ps1 "python run_evaluation.py"`
  (the quotes are required).
- Windows Security may block
  `_expected_mutual_info_fast.cp312-win_amd64.pyd` (scikit-learn, via
  sentence-transformers). Unsigned-binary reputation protection — reinstall
  scikit-learn, do not disable security.
- Known accepted loss: one Kannada tier-A document would not transfer and is
  not in the index. Documented, not a bug to chase.
- Post-submission cleanup, not now: move `chroma_db` out of OneDrive; add a
  `--skip-tier-e` flag; rebuild with
  `collection_metadata={"hnsw:space": "cosine"}`.

---

## 10. File inventory

```
05_App/
  app.py                   # UI. pysqlite3 shim, _secret(), APP_DOMAIN,
                           #   ensure_index(), scope_universe(), resolve_scope(),
                           #   scope_count(), scope_caption(), clean_title(),
                           #   basis_sentence(), daily_usage(), choose_effort()
  app_healthcare.py        # entry point, APP_DOMAIN=Healthcare      [NEW 11 Oct]
  app_rera.py              # entry point, APP_DOMAIN=RERA            [NEW 11 Oct]
  app_working_27sep.py     # fallback copy
  rag_query.py             # retrieve(), index_titles(), resolve_titles(),
                           #   is_narrow_doc(), FACILITY_MARKERS, to_cosine()
  ingest_corpus.py         # clear_index() at top of main()
  run_evaluation.py        # OpenRouter-wired, PARKED (evaluation dropped)
  measure_scope.py         # regression test, --rebaseline
  test_scope.py            # offline test suite, 7 sections           [NEW 11 Oct]
  audit_scope.py           # corpus/dropdown audit                    [NEW 11 Oct]
  fix_draft_tiers.py       # re-tier the 2 drafts, APPLIED            [NEW 11 Oct]
  chroma_db/               # the index (gitignored)
    chroma.sqlite3.bak     # pre-retier backup
  .env, .env.example       # OPENAI_API_KEY, OPENROUTER_API_KEY
  .streamlit/config.toml   # fileWatcherType = "none", theme
03_Metadata/ingestion_report.csv        # 2 tiers corrected 11 Oct
06_Evaluation/
  Raw_Responses/responses.jsonl         # evaluation checkpoint (unused)
  scope_baseline.json                   # regression baseline — NEEDS REBASELINE
07_Documentation/
  DEPLOY.md  GO_LIVE.md  DEMO_PLAN.md  MARKET_POSITIONING.md  SHARING.md
.github/workflows/keep-warm.yml         # TO BE CREATED BY HAND
run_awake.ps1
HANDOVER.md, HANDOVER_11OCT.md
.gitattributes, .gitignore
```

`.gitignore` already excludes `.env`, `chroma_db/`, `.answer_cache.json` and
`*.tar.gz`. `.usage.json` is not listed and probably should be — it is derived
per-deployment runtime state.
