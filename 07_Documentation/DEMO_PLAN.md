# Demo plan — first code review with Prof. Majumdar

Indian Regulatory Compliance Intelligence Assistant · prepared 27 Sep 2026

---

## 1. Five minutes before the meeting

```powershell
cd "C:\Users\rahma\OneDrive - Indian Institute of Management\Desktop\Term 5\CCS\RAG_Project\05_App"
python -m pip install -r requirements.txt      # once; openpyxl was missing
python rag_query.py --retrieve-only --domain RERA "promoter separate bank account seventy percent"
streamlit run app.py
```

Checklist:
- [ ] OneDrive **paused** — it has corrupted the index once already
- [ ] Retrieval returns tier-A hits before launching the UI
- [ ] Ask one question and get a real answer before he arrives
- [ ] Both sidebar toggles **off** to start (drafts and unverified excluded)
- [ ] `.env` is **not** on screen and not in the repo

If generation fails, retrieval still works. `--retrieve-only` needs no API key and
is a valid demo on its own: it shows the tier re-ranking, which is the part of
this build that is ours rather than the template's.

---

## 2. The demo sequence

Six questions, roughly eight minutes. Each one shows a different property.

### Q1 — a binding answer, cleanly cited  · domain RERA
> What percentage of the amounts realised from allottees must a promoter deposit
> in a separate account, and what may it be used for?

**Watch for:** answer names seventy per cent; cites the RERD Act, 2016 by
**title, not filename**; source carries a green **Core regulatory (binding)**
badge; the answer states the provision — Section 4(2)(l)(D).

**Say:** citations resolve through the mapping CSVs in `03_Metadata/`, so a
document is never cited by its filename. The tier badge comes from the corpus
folder, not from anything the model inferred.

### Q2 — the regression test for our worst bug  · domain RERA
> What penalty applies for non-submission of the annual audit report for the
> financial year 2023-24?

**Watch for:** the figures must be **FY2023-24's**, not another year's.

**Say — this is the strongest thing we have.** Three Karnataka circulars are
character-identical up to position 70 of their filenames, differing only in the
year. Our OCR sidecars were named on a 70-character truncation, so two circulars
resolved to the same sidecar and one was indexed carrying **another year's
penalty figures, under a correct title and a correct authority tag.** The tier
system cannot catch that, because only the text was wrong. No human scorer would
have caught it from the output either. We fixed it by hashing the full filename
stem, and this question is the regression test.

A wrong number behind a right citation is the failure mode a compliance RAG
cannot survive. That is our §9 "document your failures" material.

### Q3 — a document that was invisible until yesterday  · domain RERA
> What is the penalty for delay in submitting quarterly progress reports for
> 2025-26?

**Watch for:** a K-RERA circular dated 20-01-2026, citing Section 11(1) of the
Act with Rule 15(1)(D) of the Karnataka Rules.

**Say:** 88 of 350 corpus PDFs were photographs of paper with no text layer.
Karnataka RERA was roughly 87% invisible to retrieval. Silently indexed as empty,
they produce confident "not in the corpus" answers that are indistinguishable
from a correct refusal. We OCR'd them; this specific circular was the last tier-A
gap and was recovered on 27 Sep.

### Q4 — honest refusal  · domain RERA
> What is the stamp duty payable on a flat purchase in Bengaluru?

**Watch for:** it should say the corpus does not settle this, and **stop** — not
answer from the model's own knowledge. Stamp duty is state revenue law and is
deliberately outside our corpus.

**Say:** the system prompt makes "the corpus does not settle this" a *correct*
answer, and the rubric's confidence-calibration points reward it. This is the
behaviour a compliance tool needs and the one base LLMs fail.

### Q5 — draft material is never passed off as law  · domain Healthcare
Tick **Include draft / proposed material**, then:
> What does the NMC Bill of 2019 say about the composition of the Commission?

**Watch for:** an orange **DRAFT / PROPOSED** badge, a warning banner above the
answer, and the answer saying plainly that it has no binding force.

**Say:** we hold both NMC Act No. 30 of 2019 — the enacted law — and Bill No. 185
of 2019, which is numbered in clauses and was never enacted. Tier D is excluded by
default and labelled when opted into. The Act-and-Bill pair also enables a trap
question no other group can build.

### Q6 — authority outranking similarity  · domain Healthcare
> How long must a physician retain medical records of an indoor patient?

**Watch for:** the Ethics Regulations (tier A/B) ranked above any guideline or FAQ
that mentions record-keeping, even where the guideline's wording is a closer
lexical match.

**Say:** this is the re-ranking, and it is the one thing the prescribed
`rag-skeleton` template does not have. We over-fetch 24 candidates, weight by
authority tier (A 1.00, B 0.95, C 0.80, D 0.55, E 0.35), and cap each source
document at 2 chunks so one long PDF cannot crowd out the rest.

---

## 3. Architecture points, if he asks

- **Stack chosen to match the cohort** so results are comparable: ChromaDB,
  `BAAI/bge-small-en-v1.5` at 384d, `RecursiveCharacterTextSplitter` 1000/150,
  `TOP_K=6`. LangChain and Streamlit rather than the template's FastAPI + Gradio.
- **Corpus:** 803 source PDFs reconciled exactly — 358 corpus, 445 archive, zero
  unaccounted. 350 under `02_Corpus/`, 345 indexed, 25,955 chunks.
- **The benchmark is never indexed.** `ingest_corpus.py` *asserts* that
  `01_Benchmarks/` is not on the path. Brief §9 says do not game the benchmark;
  ours is enforced in code, not by convention.
- **Primary sources only** (brief §5.2). The tier system documents the
  binding-status distinction rather than treating all sources as equal.
- **Base models get no system prompt** (brief §4.3) — bare question, identical
  template across all three. Only the RAG condition gets grounding rules. That
  asymmetry is the experiment.

---

## 4. Two more failures worth telling him about

He asked for failures analysed, not successes. Beyond the OCR and collision
findings above, two came out of 27 Sep:

1. **Pipeline ordering.** `--rebuild` did 35 minutes of extraction and *then*
   deleted the old index. The delete failed on a Windows file lock, but
   `shutil.rmtree` deletes as it walks — so it had already destroyed the vector
   segment before raising. The index was left with intact metadata and no
   vectors: present on disk, silently unusable. Expensive work before destructive
   work means the expensive work gets thrown away. Now cleared before extraction.

2. **A metric mismatch that inverted our own authority ranking.** Embeddings are
   unit-normalised for cosine, but the Chroma collection was created without
   `collection_metadata`, so it used Chroma's default **L2** space. Ranking is
   unaffected for unit vectors, but LangChain then reports relevance as
   `1 - L2/√2`, which runs down to −0.414. Multiplying a *negative* score by a
   tier weight inverts the order: −0.30 × 1.00 for tier A sorts below
   −0.30 × 0.80 for tier C. Authority inverted exactly when matches were weak —
   when the tier system matters most. Fixed by recovering true cosine
   (`cos = 1 − (1 − score)²`, exact for unit vectors) and weighting a
   non-negative relevance.

The common shape across all four: **the system reported success and the artefact
was wrong.** That is the thesis for the report's failure section.

---

## 5. Ask him these

1. **Is the `shubhobm/rag-skeleton` template a hard requirement (§5.3)?** We built
   our own LangChain + Streamlit system. The retrieval configuration space
   matches the template's; the framework, the UI and the BM25 hybrid differ.
   Three ways forward: keep ours and justify it in report §3 (authority-tier
   re-ranking is a genuine addition the template lacks), port the corpus into the
   skeleton, or run both and report the comparison. **This is the biggest open
   question and the meeting is the place to settle it.**
2. **Should we add BM25 hybrid retrieval?** Regulatory questions turn on exact
   tokens — "Section 4(2)(l)(D)", "FY2023-24", rule numbers — and dense retrieval
   handles those poorly. The template offers a hybrid weight. Cheap to add and
   likely to lift tier 1 and tier 4 results.
3. **The Together API key returns 401.** `gpt-5-nano` works, so we can run RAG vs
   no-RAG on the same model, but `gpt-oss-20b` and `gemma-3n-E4B` are currently
   unavailable to us. Is a documented 2-condition fallback acceptable, or can the
   course supply a working key?
4. **Confirm 100 questions across two domains** — 50 Healthcare + 50 RERA, both
   workbooks submitted, 400 responses, 400 manual scores per scorer.
5. **One tier-A Kannada circular stays unindexed** — Kannada text against an
   English-only embedding model, and it appears to duplicate an English circular
   already in the corpus. Is documenting it in limitations sufficient?

---

## 6. What is not ready, and say so first

Being straight about this is better than being caught by it.

- **No results yet.** The evaluation run has not started; the index finished only
  this evening. `3_Responses` in both workbooks is empty.
- **Scoring not started.** It is manual by design — three group members, 400
  responses each, per brief §4.4. No model-assisted scoring.
- **Report and presentation not started.**
- The prototype is the chatbot and the benchmark instrument. The measurement is
  the next phase.
