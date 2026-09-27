# HANDOVER — Indian Regulatory Compliance Intelligence Assistant

Attach this file to the new chat as the first message. It is written to be the only
context a fresh session needs.

- **Course:** IIM Bangalore, Term 5, CCS — Prof. Subhabrata Majumdar
- **Project:** Indian Regulatory Compliance Intelligence Assistant (authority-aware RAG)
- **Domains:** Healthcare, and Real Estate / RERA (Central + Karnataka)
- **Date of handover:** 27 Sep 2026, 12:10 IST
- **Project root:** `C:\Users\rahma\OneDrive - Indian Institute of Management\Desktop\Term 5\CCS\RAG_Project`
- **Second connected folder:** `...\Term 5\CCS\all_benchmarks` (the cohort's benchmark files)
- **Status last updated:** 27 Sep 2026, 13:40 IST

---

## 0. Status board — where the project actually stands

| # | Step | Status | Blocked on |
|---|---|---|---|
| 1 | Corpus organised, 803 PDFs reconciled | **Done** | — |
| 2 | Benchmark: 100 questions, 2 workbooks | **Done** | — |
| 3 | OCR of image-only PDFs (104 sidecars) | **Done** | — |
| 4 | Sidecar collision bug fixed | **Done** | — |
| 5 | Index rebuild `--rebuild` | **Attempt 2 running** since 13:31 — attempt 1 crashed, see §0b | ~35 min |
| 6 | Retrieval verification (`--retrieve-only`) | Not started | step 5 |
| 7 | `05_App/.env` with the two API keys | **Done** 12:24 — but see step 7b | — |
| 7b | Together key works | **FAILING — 401 Unauthorized** | **you** |
| 8 | Streamlit UI smoke test | Not started | step 5 |
| 9 | `run_evaluation.py` written and preflight-tested | **Done** — 27 Sep, in `05_App/` | — |
| 10 | Evaluation run | Not started — **2 of 4 conditions** until 7b is fixed | step 5 |
| 11 | Manual scoring, 3 scorers × 400 | Not started | step 10, **people** |
| 12 | Report (PDF, ≤10 pages) | Not started | step 11 |
| 13 | Presentation, 10 min + live demo | Not started | steps 8, 11 |
| 14 | Git repo and push | Not started | — (can be done any time) |

**The critical path is 5 → 10 → 11 → 12.** Step 11 is manual and cannot be
compressed: three people scoring 400 responses each. Start it the moment step 10
finishes, and in parallel with steps 12–14.

### The one thing only you can do right now
**Fix the Together API key.** `OPENAI_API_KEY` works — both `gpt-5-nano`
conditions smoke-tested clean at 13:31. `TOGETHER_API_KEY` is present in `.env`
but the server returns **401 Unauthorized**, so `gpt-oss-20b` and `gemma-3n-E4B`
were dropped and the run falls back to 2 conditions.

401 is authentication, not routing. Likely causes, in order: quotes or a stray
character in the value; the word `Bearer` pasted into it; not actually a Together
key; or a Together account that was never email-verified. Test it without
printing it:

```powershell
$k = ((Get-Content .env | Select-String '^TOGETHER_API_KEY=') -split '=',2)[1].Trim()
"length: $($k.Length)"      # expect 64 hex chars
curl.exe -s -o NUL -w "HTTP %{http_code}`n" -H "Authorization: Bearer $k" https://api.together.xyz/v1/models
```

**This does not block the run.** The checkpoint is keyed on
`(domain, question_id, condition)`, so running now with 2 conditions and
re-running the same command after the key is fixed generates only the missing
two and skips what is on disk. Nothing is wasted.

### Verified state as of 13:35 IST, 27 Sep
- `05_App/.env` exists (created 12:24). `openai` 3.19.2 installed — it was
  missing, which is what made the first preflight fail.
- `chroma_db` **deleted by hand** at 13:28 after the attempt-1 crash left it
  corrupt. Rebuild attempt 2 started 13:31; expect the new
  `03_Metadata/ingestion_report.csv` around 14:05. Judge completion by that
  timestamp, not by the folder existing.
- OneDrive sync **paused** from ~13:20 for 2 hours. It expires ~15:20 — re-pause
  it if the evaluation run is still going, and never let it sync `chroma_db`
  mid-write.
- `06_Evaluation/` exists with `Raw_Responses/`, `Results/`, `Scoring/`, all empty.
- Both workbooks: 9 sheets, **2030 formulas**, `3_Responses` pre-scaffolded with
  200 rows each (50 questions × 4 conditions), `response_text` empty. The `model`
  column already carries the exact four condition labels the runner writes against:
  `gpt-5-nano (base)`, `gpt-oss-20b (base)`, `gemma-3n-E4B (base)`,
  `RAG (corpus-augmented)`. Verified: writing all 200 rows leaves all 2030
  formulas intact.

---

## 0b. Incident, 27 Sep 12:16–13:31 — the rebuild that died on cleanup

**What happened.** `ingest_corpus.py --rebuild` extracted all 350 documents,
wrote the ingestion report at 12:51, built 25,947 chunks, and then crashed:

```
PermissionError: [WinError 5] Access is denied:
  ...\05_App\chroma_db\bdc5c9a5-92ea-4326-b9bc-535679f58d9b
```

`shutil.rmtree` was called *after* the 35-minute extraction. Something held a
handle on the index folder — almost certainly OneDrive, since `chroma_db` is
231 MB of churning binary inside the OneDrive tree. `rmtree` deletes as it walks,
so it had already emptied the vector-segment directory before raising: the index
was left with intact sqlite metadata and **no vectors**, which is worse than
either a clean index or an untouched one. The old index was no longer a fallback.

**Fixed in code.** `clear_index()` now runs at the top of `main()`, before
extraction, with 3 retries and an explicit diagnosis on failure. A locked folder
now costs 5 seconds instead of 35 minutes. The post-extraction removal is kept as
a safety net. **Do not move that call back down.**

**Structural fix, after submission:** move `chroma_db` out of the OneDrive tree.
Two constants, `INDEX_DIR` in `ingest_corpus.py` and `rag_query.py`. OneDrive
syncing a large binary index will keep causing this.

### The extraction results were good — attempt 1's numbers
- 344 extracted / 6 skipped / **25,947 chunks** (previous build: 24,555)
- **RERA tier A: 25 → 40 documents.** The OCR work paid off.
- The three FY penalty circulars resolved distinctly — 2022-23 via OCR sidecar,
  2023-24 via OCR sidecar, 2024-25 via embedded PDF text. **The collision fix
  holds.**

### But the §7 Step 1 criterion was not met: 2 tier-A skips
Four of the six skips were tier E (ignorable). Two were **tier A**:

1. `Imposing penalty ... Quarterly Progress Reports for the Financial Year 202_1210f057.pdf`
   — **FIXED.** OCR'd 27 Sep, 5,443 chars, sidecar
   `Imposing penalty for Non-Submission and Delay in Submission _a177758e.txt`
   written to `02_Corpus/_ocr_text/`. Digest verified against `ocr_sidecar_for()`.
   It is the K-RERA circular of 20-01-2026 on penalties for late quarterly
   progress reports FY2025-26, citing Section 11(1) of the Act with Rule 15(1)(D)
   of the Karnataka Rules. It was entirely invisible to retrieval before.
2. `ತ್ರೈಮಾಸಿಕ ಹಾಗೂ ವಾರ್ಷಿಕ ಲೆಕ್ಕ ಪರಿಶೋಧನಾ ತಃಖ್ತೆ ... _024fd150.pdf`
   — **still skipped, accepted.** The file would not transfer over the
   desktop-app bridge; three attempts each reported success and delivered
   nothing, so it is something about the Kannada filename in the bridge. Judged
   not worth more time: it is Kannada text against an English-only embedding
   model, so it would retrieve poorly even if indexed, and it reads as the Kannada
   edition of the English audit-penalty circulars that *are* indexed. **Record it
   in the report's limitations section.** If it ever matters, OCR it locally
   rather than through the bridge.

**Expected on attempt 2:** 345 indexed, **5 skipped, all tier E**, RERA tier A
holding at 40.

### Report material — a third failure worth writing up
§9 of the brief asks for failures. This is a good one, alongside the image-only
PDFs and the sidecar collision: **a pipeline that does its expensive work before
its destructive work will throw away the expensive work.** The index was left in
a state that looked present and was silently unusable — the same failure shape as
the empty-text PDFs and the collided sidecars. All three are cases where the
system reported success and the artefact was wrong.

---

## 1. Hard rules — do not violate these

1. **The API key lives in `05_App/.env`, which is gitignored.** Never paste a key into
   chat, never commit one, never ask the user to send one. The code reads
   `OPENAI_API_KEY` from `.env`. A third party in the user's project group once relayed
   "use the API key you used for the PGAI endterm" — that was declined, correctly.
2. **English only.** All benchmark questions, answers and generated material are in
   English. The user supplied a mixed Kannada/English source file only because no pure
   English version existed; that does not license Kannada output.
3. **Never delete source material.** Files move or get copied; nothing is removed.
4. **The benchmark is never indexed.** `01_Benchmarks/` holds expected answers. Indexing
   it would let the system retrieve its own answer key and invalidate the base-vs-RAG
   comparison. `ingest_corpus.py` asserts against this. Same for `08_Archive` and
   `_new_sources`.
5. **Draft / proposed material is never presented as binding law.**
6. **Cite documents by title, never by filename.** Titles come from the mapping CSVs in
   `03_Metadata/`.
7. **The classification CSVs are the source of truth.** Never re-derive a document's
   classification from its filename.
8. **Don't make the user wait on your polling.** Long jobs run detached; report once at
   the end. See §8.

---

## 2. Authority tiers — the core architecture

Every corpus document carries a tier, derived from its folder, and retrieval re-ranks so
authority outranks raw similarity.

| Tier | Meaning | Binding? |
|---|---|---|
| A | Core regulatory — Acts, Rules, Regulations, gazette notifications, binding circulars, PM-JAY instruments | Yes |
| B | Sector regulation — NMC regulations, medical-education standards | Within sector |
| C | Supporting guidance — guidelines, FAQs, manuals, SOPs, model forms | No, explanatory only |
| D | Draft / proposed | **Never** |
| E | Review — binding status unverified, or administrative correspondence | Unknown; excluded by default |

Folder → tier map, as coded in `ingest_corpus.py`:

```python
TIER = {
    "01_Core_Regulatory": "A", "02_Core_PMJAY_Insurance": "A",
    "01_Central_RERA": "A",    "02_Karnataka_RERA": "A",
    "03_Medical_Regulation_Education": "B",
    "04_Supporting_Guidance": "C",   # Healthcare
    "03_Supporting_Guidance": "C",   # RERA
    "05_Draft_Proposed": "D",
    "06_Review": "E",                # Healthcare
    "04_Review": "E",                # RERA
}
```

Retrieval weights: `A 1.00, B 0.95, C 0.80, D 0.55, E 0.35`. D and E are excluded unless
the user opts in via the sidebar toggles, and the UI warns when either appears.

---

## 3. Stack, as built and agreed

Chosen deliberately to match the cohort's setup so results are comparable.

- LangChain + ChromaDB
- Embeddings: `BAAI/bge-small-en-v1.5`, 384d, `normalize_embeddings=True`
- Splitter: `RecursiveCharacterTextSplitter`, chunk 1000, overlap 150
- Retrieval: `TOP_K=6` (cohort setting), `CANDIDATE_K=24` over-fetch then tier re-rank,
  max 2 chunks per source document so one long PDF cannot crowd out the rest
- LLM: `gpt-5-nano` (group's instruction), via `OPENAI_API_KEY` from `.env`
- UI: Streamlit

---

## 4. Files that exist

### `05_App/`
| File | What it is |
|---|---|
| `ingest_corpus.py` | Builds the Chroma index over `02_Corpus/` only. Flags: `--dry-run`, `--rebuild`. Writes `03_Metadata/ingestion_report.csv`. |
| `rag_query.py` | Retriever + generation. `SYSTEM_PROMPT` encodes the rules above as hard constraints. `--retrieve-only` works with no API key — use it to test retrieval. |
| `app.py` | Streamlit chat UI: domain radio, coloured tier badges, expandable sources with title/similarity/URL, warning banners for tier D/E, streaming answers. 201 lines. |
| `run_evaluation.py` | **New, 27 Sep.** The evaluation runner — 4 conditions × 100 questions, per-provider config, runtime Together slug resolution, preflight smoke tests, per-generation checkpointing, writes into `3_Responses` in place. Flags: `--preflight`, `--limit N`, `--domain`, `--write-only`. See §7 Step 4. |
| `requirements.txt`, `.env.example` | `.env.example` documents both `OPENAI_API_KEY` and `TOGETHER_API_KEY`. |
| `.env` | **Does not exist yet.** The blocker for steps 3, 4 and the whole evaluation. |
| `chroma_db/` | The persisted index (gitignored). |

### Project root
- `README.md` — handover doc defining the tier table and the hard rules
- `.gitignore` — excludes `.env`, `chroma_db/`, `02_Corpus/**/*.pdf`, `08_Archive/`
- `_organize_manifest.csv` (396 rows), `organize_rag_project.ps1` — the corpus organiser
- `01_Benchmarks/RERA/Benchmark_RERA_50Q.xlsx`
- `01_Benchmarks/Healthcare/Benchmark_Healthcare_50Q.xlsx`
- `03_Metadata/healthcare_corpus_mapping.csv` (272 rows), `rera_corpus_mapping.csv`
- `02_Corpus/_ocr_text/` — **104 OCR sidecars**

### Benchmark workbooks
9 sheets each: `README`, `1_Benchmark`, `2_Source_Register`, `3_Responses`,
`4_Scorer_A/B/C`, `5_Reconciled`, `6_Analysis`. 2030 formulas, 0 errors on recalc.

- 50 questions per domain, 100 total, written from scratch in English
- Difficulty tiers: 1 Factual Recall, 2 Conceptual, 3 Scenario Application,
  4 Multi-step Reasoning, 5 Adversarial/Trap
- RERA split: ~20 Central / ~30 Karnataka
- Rubric: Factual Accuracy 0–4 + Completeness 0–2 + Confidence Calibration 0–2 = **max 8**
- Difficulty calibrated against cohort Group 5 (Banking)

---

## 5. Corpus state

- 803 source PDFs reconciled exactly: **358 corpus + 445 archive, 0 unaccounted**
- 350 PDFs live under `02_Corpus/`
- 104 OCR sidecars in `02_Corpus/_ocr_text/`

Sidecar naming (this matters — see §6):

```python
def ocr_sidecar_for(pdf):
    digest = hashlib.sha1(pdf.stem.encode("utf-8")).hexdigest()[:8]
    return OCR_TEXT / f"{pdf.stem[:60]}_{digest}.txt"
```

`MIN_TEXT_CHARS = 300`: below that, the PDF is treated as image-only and the sidecar is
used instead.

### Cohort context (settles a question the user raised twice)
- **Group 6 (RERA)** covers Central + **Uttar Pradesh**. 23 of their 50 questions need UP
  material; **zero** touch Karnataka.
- **Group 12 (Healthcare)** built their benchmark from only **13 source documents**.
- The user worried they were at "62% coverage." They are not under-collected — they are
  heavily over-collected relative to the cohort. That worry is resolved; don't reopen it.
- The group promised a "combined set of benchmarks" file. **It never arrived.** Ask for it
  if benchmark alignment comes up.

---

## 6. What was done in the last session (27 Sep, morning)

The first index build reported **262 indexed, 88 skipped**. Two problems behind that:

### The collision bug — a correctness defect, now fixed
`ocr_sidecar_for()` previously returned `pdf.stem[:70] + ".txt"` with no disambiguator.
Three tier-A Karnataka circulars are character-identical to position 70:

```
Imposing penalty ... Annual Audit Report for the Financial Year 2022-23
Imposing penalty ... Annual Audit Report for the Financial Year 2023-24
Imposing penalty ... Annual Audit Report for the Financial Year 2024-25
```

Two of them resolved to the same sidecar, so one was indexed carrying the other year's
penalty figures — under a correct title and a correct authority tag. The tier system
cannot catch that, because only the text was wrong.

Fixed by hashing the full stem. Verified after the fix: FY2022-23 → sidecar `30f07d3b`,
FY2023-24 → sidecar `5810ec93`, FY2024-25 → its own embedded PDF text. Three distinct
resolutions. **If you touch sidecar naming, preserve the hash suffix.**

### OCR of the skipped documents
Most Karnataka RERA PDFs are scans — photographs of paper with no text layer. Recovered:

| Document | Recovered |
|---|---|
| Karnataka RERA Rules 2017 | 126,506 chars / 40p |
| RERA Bank Account Directions, 2020 | 69,524 chars / 52p |
| CBME Guideline 2023 | 126,797 chars / 98p |
| NHA Data Privacy Policy v3.0 | 52,446 chars |
| Appellate Tribunal Regulations 2019, Clinical Establishments Rules 2012, CBME Corrigendum, + ~95 more | |

Method: `pdftoppm -r 200 -gray -png` → `tesseract --psm 1`. Kannada-titled files used
`-l kan+eng`, everything else `-l eng`.

**Scope note, honestly:** ~57 of the 88 were tier E (Lok Adalat notices, recruitment ads,
press releases, promoter arrears lists) which retrieval excludes by default. OCRing them
consumed roughly two-thirds of the runtime for no retrieval value. The 31 tier A/B/C
documents were the ones that mattered. Don't repeat that pattern — check the tier before
spending time on a document.

---

## 7. Immediate next steps

### Step 1 — rebuild — **attempt 2 RUNNING since 13:31 IST.** Attempt 1 crashed; see §0b
```
cd 05_App
python ingest_corpus.py --rebuild
```
~35 min unattended. Extraction is the slow part; Chroma writes come after it, so
`chroma_db` does not exist for most of the run. That is normal.

**How to know it finished:** `03_Metadata/ingestion_report.csv` gets a fresh
timestamp (12:51 was attempt 1). Check the tail of the console output for the
indexed / skipped counts, and confirm **every remaining skip is tier E**.

**Expected:** ~350 documents found, ~348 indexed, **0 skipped in tiers A–C**. RERA tier A
should rise from 25 documents to ~42. Previous run had 24,555 chunks; expect noticeably
more.

If tier A–C skips are still non-zero, list them before doing anything else — a skip there
means a document is invisible to retrieval.

### Step 2 — verify retrieval, no API key needed
```
python rag_query.py --retrieve-only --domain RERA "penalty for late annual audit report submission"
python rag_query.py --retrieve-only --domain RERA "promoter bank account 70 percent withdrawal"
python rag_query.py --retrieve-only --domain Healthcare "how long must indoor patient records be kept"
```
Check that the FY2023-24 penalty query returns FY2023-24 figures, not another year's.

### Step 3 — key in and test the UI
User copies `.env.example` to `.env` and puts the key there. Then:
```
streamlit run app.py
```
Ask 3–4 benchmark questions across both domains. Confirm citations show titles, tier
badges render, and tier D/E warnings fire when those toggles are on.

### Step 4 — the evaluation runner (this is the actual deliverable)

**WRITTEN — `05_App/run_evaluation.py`, 27 Sep 2026.** Everything below is now a
description of what it does, not a spec to build.

**How to run it**, once `.env` has both keys and step 1 has finished:

```
cd 05_App
python run_evaluation.py --preflight           # keys, Together slugs, 3 smoke calls
python run_evaluation.py --limit 2             # 16 generations, sanity check
python run_evaluation.py > eval_log.txt 2>&1   # the full run, detached
```

Then open either workbook and confirm `3_Responses` column E is populated.

**What was verified offline** (no API key needed, and already done):
- Reads 50 questions from each workbook's `1_Benchmark`.
- Writes all 200 rows per workbook and leaves **2030 formulas intact** in both —
  the scorer, reconciliation and analysis sheets are untouched.
- Adds two columns to the right of `3_Responses`, `retrieved_sources` and
  `retrieved_tiers`, filled for RAG rows only. Nothing references those columns,
  so nothing breaks.
- Resumes from a checkpoint truncated mid-write by a crash.
- Refuses to run, with a clear message, if the RAG condition fails preflight.

**Not yet verified** — needs the keys: the three live model calls, the Together
slug lookup, and end-to-end throughput.

**Other flags:** `--domain RERA` (repeatable), `--write-only` to rebuild the
workbooks from the checkpoint without generating anything.

**Design notes that matter:**
- Per-provider config lives in a `CONDITIONS` dict in the runner. `rag_query.py`'s
  single-model CLI is untouched — the runner imports from it rather than changing it.
- The checkpoint is `06_Evaluation/Raw_Responses/responses.jsonl`, appended and
  fsynced after every single generation. Re-running skips what is already there, so
  a killed terminal or a rate-limit wall costs nothing.
- `06_Evaluation/Raw_Responses/run_manifest.json` records which conditions actually
  ran and which were dropped. **That file is the evidence for the report** if the
  Together models fall over.
- Retrieval runs on the main thread (Chroma is not guaranteed thread-safe); only the
  four generations per question fan out, on a 4-thread pool.
- Unsupported request parameters are dropped and retried rather than failing the run;
  other errors get exponential backoff, 4 attempts.

Original spec, for reference:

- Reads the 100 questions from the two benchmark workbooks, sheet `1_Benchmark`
**The three models are fixed by the professor. Do not substitute.** All on free tier:

| Condition | Model | Provider | Key | Role |
|---|---|---|---|---|
| RAG | `gpt-5-nano` | OpenAI | `OPENAI_API_KEY` | **Main model** — this is the system being evaluated |
| Base 1 | `gpt-5-nano` | OpenAI | `OPENAI_API_KEY` | Same model, no retrieval — isolates RAG's contribution |
| Base 2 | gpt-oss-20b | Together | `TOGETHER_API_KEY` | |
| Base 3 | Google Gemma 3N E4B Instruct | Together | `TOGETHER_API_KEY` | |

`gpt-5-nano` is the main model per the professor. Base 1 exists so the comparison is
same-model-with-and-without-retrieval; the two Together models are the cross-model
baseline.

**Two keys are needed in `05_App/.env`** — `OPENAI_API_KEY` and `TOGETHER_API_KEY`. The
user says both are already available to them. Same rules as always: they go in `.env`,
never into chat, never into git.

**Together routing:** base URL `https://api.together.xyz/v1`, OpenAI-compatible, so the
same `openai` client works with `base_url` set. Exact model slugs must be **verified at
runtime, not hardcoded from memory** — Together's identifiers change. Hit
`GET https://api.together.xyz/v1/models` and match on `gpt-oss-20b` and `gemma-3n`. The
likely slugs are `openai/gpt-oss-20b` and `google/gemma-3n-E4B-it`, but confirm before
launching 400 generations.

**Smoke-test all three models with one cheap prompt each before the full run.** The user's
instruction: if the two Together models don't work, **fall back to gpt-5-nano alone** (RAG
vs no-RAG) rather than blocking. Record in the workbook which conditions actually ran.

Note that `rag_query.py` currently has a single `LLM_BASE_URL` env var, which is not enough
for two providers. The runner needs per-model provider config — either a small dict of
`{model: (base_url, key_env)}` in the runner, or extend `rag_query.py`. Don't break
`rag_query.py`'s existing single-model CLI behaviour.

- For each question, generates four answers, one per row of the table above. Only the RAG
  condition retrieves; the three base conditions get the question with no corpus context
  and no `SYSTEM_PROMPT` grounding rules (that asymmetry is the point of the experiment).
- Writes results into sheet `3_Responses` of each workbook, one column per condition,
  preserving the existing formulas — **do not rebuild the workbooks, write into them**
- Records retrieved source titles and tiers per RAG answer, for the confidence-calibration
  part of the rubric
- Checkpoints to disk after every question so a crash doesn't lose the run
- 100 questions × 4 conditions = 400 generations. Run detached; 30–45 min.

### Step 5 — scoring
**Manual only.** The brief (§4.4) requires each response to be scored independently by
**three human group members** and averaged, with both the original scores and the final
average recorded. Do not offer or perform model-assisted scoring — it violates the brief.
The `4_Scorer_A/B/C` and `5_Reconciled` sheets already implement this structure.

### Step 6 — git repo and push
The group explicitly asked for code to be pushed. `.gitignore` is already correct.
**Confirm `.env` is not staged before the first commit.**

---

## 7b. Compliance with the official project brief

The brief (`Endterm_project_brief.pdf`, "Building an Indian Regulatory Compliance AI:
Benchmark, Build, and Break", 35% of course grade) was checked against this build on
27 Sep. Findings below.

### Already compliant — claim these explicitly in the report
- **Four conditions per question.** The brief's "3 base LLMs + RAG system = 200 responses"
  for 50 questions confirms the design in §7 Step 4.
- **Five difficulty tiers**, 8-point rubric (Factual Accuracy 4 + Completeness 2 +
  Confidence Calibration 2).
- **Workbook columns.** All seven required fields present: `question_id, tier,
  question_text, expected_answer, source_citation, source_url, known_trap`, plus
  `tier_name` and `source_domain` as extras. IDs follow `DOMAIN-TIER-NUM`
  (`RERA-T1-01`, `HEALTHCARE-T1-01`).
- **"Do not game the benchmark" (§9).** `ingest_corpus.py` *asserts* that `01_Benchmarks/`
  is never indexed. This is the rule enforced in code — say so in report §3.
- **"No third-party sources" (§5.2).** Corpus is primary/quasi-primary government sources
  only; the tier system documents the distinction.
- **No system prompt for base models (§4.3).** "Do not use system prompts or custom
  instructions: test the models as a first-time user encounters them." The three base
  conditions must get the bare question. Only the RAG condition uses `SYSTEM_PROMPT`.
  Use an identical prompt template across all three base models.

### Settled
**100 questions — 50 Healthcare + 50 RERA.** Confirmed by the user on 27 Sep. The brief's
"exactly 50" applies per domain, and this group covers two (Healthcare = domain 12,
RERA = domain 6). Do not re-ask this.

Consequences: **400 model responses** (100 × 4 conditions), and **400 manual scores per
scorer** across three scorers. Both benchmark workbooks are submitted. Size the evaluation
run and the scoring plan against these numbers, not the brief's 200.

### Open question — resolve with the group/professor
1. **The RAG template (§5.3).** The brief says to use `https://github.com/shubhobm/rag-skeleton`
   ("RAG Parameter Lab"): ChromaDB + sentence-transformers (MiniLM / MPNet / **BGE-small**),
   FastAPI + Gradio, configurable chunk size, embedding model, top_k, and a hybrid
   embedding/BM25 weight. Its LLM options are exactly the three assigned models —
   confirming the Together slug **`gemma-3n-E4B-it`**.
   This project built its own LangChain + Streamlit system instead. The retrieval stack
   matches the template's configuration space; the framework and UI differ, and the
   template's BM25 hybrid is absent here. Three ways forward, for the group to choose:
   keep ours and justify it in report §3 (authority-tier re-ranking is a genuine addition
   the template lacks); port the corpus and settings into the skeleton; or run both and
   report the comparison. Worth asking whether "use the template" is a hard requirement.

### Recommended addition — BM25 hybrid retrieval
Regulatory questions hinge on exact tokens ("Section 4(2)(l)(D)", "FY2023-24", rule
numbers) and pure dense retrieval handles those poorly. The template offers a hybrid
weight; adding one here is cheap and likely to improve tier-1 and tier-4 results. Also
brings us closer to the prescribed template.

### Deliverables not yet planned
- **A. Benchmark dataset** — questions, raw model responses, individual scorer sheets,
  reconciled scores. The workbooks hold all of this; they need filling.
- **B. Report** — PDF, max 10 pages excluding appendices, covering: (1) domain overview,
  (2) benchmark design rationale and difficulty calibration, (3) RAG architecture
  decisions and trade-offs **plus links to source documents**, (4) results and analysis —
  base vs RAG, failure patterns, statistical breakdown by tier, (5) business
  recommendation with guardrails and limitations.
- **C. Presentation** — 10 min + 2 min Q&A, pitched as an internal tiger team to
  leadership, **with a live demo**. `app.py` is the demo.
- **Working links to all source documents** must be submitted with the report. The
  `source_url` columns in `03_Metadata/*_corpus_mapping.csv` and the workbooks'
  `2_Source_Register` sheets are the raw material.

### Analysis questions the report must answer (§5.4)
1. On which tiers did RAG improve performance most? Least?
2. Were there questions where RAG made performance *worse* (e.g. irrelevant retrieved
   chunks confusing the LLM)?
3. What types of regulatory questions suit RAG, and which need capabilities beyond
   retrieval?
4. Would you recommend deploying this system? Under what conditions and guardrails?

### Report material already in hand
§9 of the brief: *"Document your failures. A well-analyzed failure is worth more than a
success."* Two findings from this project are strong material:
- **Image-only PDFs.** 88 of 350 documents had no text layer. Silently indexed as empty,
  they produce confident "not in the corpus" answers that are indistinguishable from a
  correct refusal. Karnataka RERA was ~87% invisible before OCR.
- **The sidecar collision.** Two tier-A circulars served each other's penalty figures
  under correct titles and correct authority tags. A wrong number behind a right citation
  is the failure mode a compliance RAG cannot survive, and no scorer would have caught it
  from the output alone. This is a genuine contribution to the "where does RAG fail"
  analysis.

---

## 8. Traps and known issues

**Environment**
- The user is on **Windows**, running commands in their own terminal (Cursor/PowerShell).
  Their laptop is reachable through the desktop-app bridge, which **drops in and out** —
  if file tools vanish mid-task, the app was closed or the machine slept. Say so plainly
  and carry on with container-side work.
- **The cloud container can restart without warning** and kill detached jobs. Disk under
  `/root/work` survives. Make long jobs idempotent and cache-checked so a restart resumes
  rather than restarting. Do not trust `pgrep -c -f` to tell you a detached job is alive —
  check `ps` output properly, or check whether its log is still advancing.
- **Never poll a long job while narrating the wait.** The user lost most of an hour to
  that. Launch detached, do other useful work, report once.
- Container needs `pip install fonttools` or pypdf floods thousands of
  "fontTools is required" lines that bury all output.
- Tesseract language data: fetch from **`raw.githubusercontent.com`**, which works.
  `github.com/.../raw/...` is **blocked** in this container and returns a 378-byte error
  page that looks like a successful download.
- The container has **2 cores**. OCR with 2 workers; more doesn't help.
- Windows `MAX_PATH` is 260. Filename shortening needs collision-safe suffixing — a
  previous pass at LIM=240 silently collapsed three "Model Tender Document Vol I/II/III"
  files into one name. Caught by an assert; would have destroyed two documents.

**Corpus gotchas**
- **`Karnataka Real_Estate_Act.pdf` is the CENTRAL Act**, not a Karnataka instrument.
  Verified by reading page 1. Filename left unchanged, flagged in the mapping. Do not
  "fix" it or treat it as state law.
- **NMC:** Act No. 30 of 2019 (assented 8 Aug 2019) is the genuine Act and is in the
  corpus. Bill No. 185 of 2019 is numbered in *clauses*, is **not** the enacted Act, and
  sits in `05_Draft_Proposed` with an explicit filename saying so. The Act+Bill pair
  enables a trap question no other cohort group can build — optional, unbuilt.
- **`2.2.Competency Based Medical Education (CBME) Guideline ... _79584888.pdf` is
  corrupt** — a partial download with 178KB of null bytes after `%%EOF`. Every PDF tool
  chokes on it. Its OCR sidecar exists, so the RAG can read it; the file itself still
  can't be opened. Repairable by truncating at `%%EOF`+5. The user was offered the repair
  and had not yet answered.
- 7 other files fail the same way. All are nmc.org.in HTML pages saved with a `.pdf`
  extension; all are already archived and **none are in the corpus**. Ignore them.
- 18 of 360 "PDFs" were originally HTML pages from nmc.org.in's document viewer. Real URLs
  were recovered from the `file=` parameter inside viewer links in the user's own index
  CSVs. 9 re-downloaded, 8 verified. A `%PDF-` 5-byte header check guards against this
  class now (`is_real_pdf()`).
- **Kannada documents now have OCR text, but it is Kannada**, and the index embeds with an
  English-only model, so they will retrieve poorly. Most appear to be Kannada editions of
  English circulars already indexed — likely duplicate coverage, not a gap. Low priority;
  confirm before spending time on it.
- The empty `Real Estate` folder in the project root is file-locked and won't delete.
  Cosmetic. **Ignore it** — several attempts already failed.
- OneDrive was paused at one point and may still be catching up.

**Previous mistakes worth not repeating**
- I once reported the Ethics Regulations 2002 as missing. It was already in the corpus as
  `Click Here To View This Regulation_0e21152e.pdf` (SHA256 `44de73c3017fc95e…cd6005b`).
  My coverage check searched *titles* for "ethics" and that document's recorded title was
  webpage link text. **Search content and hashes, not just titles, before declaring a gap.**
- A related "38% coverage gap" figure I gave was an upper bound from that same flawed
  method, not a measurement. Don't cite it.

---

## 9. Budget context

The user is cost-conscious and time-pressed. At handover: ~42% of the session limit
consumed, renewing 15:30 IST. Prefer unattended jobs, batch tool calls, and avoid
re-reading large files. State estimates in both *their* attention and *wall clock*.

**Update, 12:35 IST.** The bridge to the laptop is connected and both folders are
granted, but **there is no shell on the user's machine** this session — file read,
stage and write only. Every command still runs in the user's own terminal. Write
files to their machine through the bridge; do not offer to run anything there.
