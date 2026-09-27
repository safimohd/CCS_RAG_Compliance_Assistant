# Indian Regulatory Compliance Intelligence Assistant

IIM Bangalore — Contemporary Consulting Studies (CCS) project
Faculty: Prof. Subhabrata Majumdar

An AI compliance assistant that (a) benchmarks LLMs on Indian regulatory knowledge and
(b) uses retrieval-augmented generation over an official-source corpus to improve those answers.

**Domains (two, deliberately narrowed):**

| Domain | Corpus root |
|---|---|
| Healthcare | `02_Corpus/Healthcare/` |
| Real Estate / RERA | `02_Corpus/RERA/` |

Banking/RBI, SEBI and GST are explicitly **out of scope** for this corpus.

---

## 1. Repository layout

```
RAG_Project/
├── README.md
├── 01_Benchmarks/          Benchmark Q&A sets — NOT retrieval material
│   ├── Healthcare/
│   └── RERA/
├── 02_Corpus/              Production retrieval corpus (358 PDFs)
│   ├── Healthcare/
│   │   ├── 01_Core_Regulatory/              54
│   │   ├── 02_Core_PMJAY_Insurance/         39
│   │   ├── 03_Medical_Regulation_Education/ 32
│   │   ├── 04_Supporting_Guidance/          48
│   │   ├── 05_Draft_Proposed/               11
│   │   └── 06_Review/                       83
│   └── RERA/
│       ├── 01_Central_RERA/                  1
│       ├── 02_Karnataka_RERA/                41
│       ├── 03_Supporting_Guidance/           12
│       └── 04_Review/                        37
├── 03_Metadata/            Classification + provenance CSVs (no PDFs)
│   ├── Healthcare/
│   └── RERA/
├── 04_Scripts/             Collection and classification scripts
│   ├── Healthcare/{download,classification}/
│   ├── RERA/{download,classification}/
│   └── Utilities/
├── 05_App/                 RAG application (to be built)
├── 06_Evaluation/          {Raw_Responses,Scoring,Results}/
├── 07_Documentation/       {Project_Proposal,Meeting_Notes,Research,Reports}/
└── 08_Archive/             Nothing here is deleted, only superseded
    ├── Raw_Downloads/          UP_RERA_Reference (5 PDFs, out-of-scope state)
    ├── Superseded_Collections/ original healthcare collections (440 PDFs)
    └── Failed_or_Unused/
```

## 2. Authority tiers — read this before building retrieval

The bucket a document sits in **is** its authority level. This is the single most
important property of the corpus and the retriever must respect it.

| Tier | Buckets | What it means | Citable as binding? |
|---|---|---|---|
| **A — Core regulatory** | `01_Core_Regulatory`, `02_Core_PMJAY_Insurance`, `01_Central_RERA`, `02_Karnataka_RERA` | Acts, rules, regulations, gazette notifications, binding circulars and orders | **Yes** |
| **B — Sector regulation** | `03_Medical_Regulation_Education` | NMC regulations and medical-education standards | Yes, within their sector |
| **C — Supporting guidance** | `04_Supporting_Guidance` (Healthcare), `03_Supporting_Guidance` (RERA) | Guidelines, FAQs, manuals, SOPs, model forms, clinical/treatment guidance | Explanatory only — **not** binding law |
| **D — Draft / proposed** | `05_Draft_Proposed` | Drafts, proposed amendments, material circulated for comment | **No — never present as binding** |
| **E — Review / unverified** | `06_Review` (Healthcare), `04_Review` (RERA) | Binding status unverified, or administrative/recruitment/press material | **No** |

Hard rules for the assistant:

1. **Draft/proposed material is never presented as binding regulation.** Anything retrieved
   from `05_Draft_Proposed` must be labelled as a draft or proposal in the answer.
2. **Tier E is not evidence.** Prefer excluding it from default retrieval, or label it clearly
   as unverified. It exists so nothing was thrown away, not so it can be cited.
3. **Prefer Tier A/B over C over D/E** when ranking. Where a Tier C document conflicts with a
   Tier A document, the Tier A document governs.
4. **If the corpus lacks sufficient evidence, say so.** Do not fill the gap from model priors.
5. **Always cite** the document title and authority, not just the filename — several filenames
   are opaque hashes or portal IDs. Titles live in `03_Metadata/*/*_corpus_mapping.csv`.

## 3. How the Healthcare classification was derived

Healthcare documents were **not** classified by filename. The 267 unique PDFs were routed
using the project's own prior classification work, in this precedence order:

1. `healthcare_content_classification.csv` — PDF-content classification (123 files, authoritative)
2. `healthcare_manual_review_classified.csv` — where it resolved a file to a definite `KEEP` verdict (2 files)
3. `healthcare_document_catalog.csv` — first-pass category for the remaining 142 files

Category → bucket mapping used for step 3:

| First-pass category | Bucket |
|---|---|
| Clinical Establishments · Law / Regulation / Policy · Digital Health / Data & Privacy | `01_Core_Regulatory` |
| PM-JAY / Ayushman Bharat | `02_Core_PMJAY_Insurance` |
| Medical Regulation / NMC · Medical Education | `03_Medical_Regulation_Education` |
| Clinical / Treatment Guidelines | `04_Supporting_Guidance` |
| Other / Review | `06_Review` |

A **draft-safety override** runs last: any document whose name marks it as a draft or proposal
is forced into `05_Draft_Proposed` even when its category would have placed it in a KEEP
bucket. Overrides can only move a document *away* from binding status, never toward it.

`healthcare_other_review.csv` and `healthcare_high_priority_review.csv` are retained as
supporting metadata and carried into the mapping file, but on their own they only ever
indicated *"needs review"* — so those files sit in `06_Review` awaiting a human decision.

## 4. How the RERA classification was derived

KRERA filenames are unreliable (hashes, portal IDs, `KRERA_CORE_RETRY_nn.pdf`), so all 89
Karnataka documents were routed by their **document title** from `krera_metadata.csv` and
`krera_core_retry_results.csv`, decided document by document:

- **`02_Karnataka_RERA`** — rules, regulations, tribunal regulations, bank-account directions,
  fee and penalty circulars, audit/QPR filing obligations, extension and force-majeure orders,
  promoter obligations.
- **`03_Supporting_Guidance`** — guidelines, FAQs (incl. Kannada), procedures, model forms,
  the National Building Code guide.
- **`04_Review`** — recruitment and empanelment notices, press releases, Lok Adalat notices,
  office-logistics circulars, land-revenue recovery letters and defaulter lists, a GST circular
  (out of domain).

`01_Central_RERA` holds the central Real Estate (Regulation and Development) Act, 2016.
**Note:** that file is named `Karnataka Real_Estate_Act.pdf`, which is misleading — its
contents were verified to be the central Act. The name was left unchanged; see the mapping CSV.

UP-RERA material is **not** part of the Karnataka corpus. It is preserved in
`08_Archive/Raw_Downloads/UP_RERA_Reference/` and must not be retrieved for
Karnataka compliance questions.

## 5. Metadata

`03_Metadata/` holds no PDFs — only provenance and classification records.

**Healthcare:** `healthcare_corpus_mapping.csv` (the file to load — final path, original
filename, bucket, authority, year, document title, which CSV decided it, SHA256),
plus the source records: `healthcare_file_inventory.csv` (440 encountered → 267 unique
→ 173 duplicates, with hashes), `healthcare_document_catalog.csv`,
`healthcare_content_classification.csv`, `healthcare_manual_review_classified.csv`,
`healthcare_other_review.csv`, `healthcare_high_priority_review.csv`, and the
collection indexes (`healthcare_master_index`, `clinical_establishments_index`,
`abdm_master_index`, `remaining_master_index`, `document_index`).

**RERA:** `rera_corpus_mapping.csv` (final path, bucket, document title, doc type, source URL),
`krera_metadata.csv`, `krera_core_retry_results.csv`, `rera_master_index.csv`.

Ten filenames were shortened to stay inside the Windows 260-character path limit. Each keeps
its original content-hash suffix, and `OriginalFileName` in the mapping CSVs preserves the
full name. No other file was renamed.

## 6. Benchmarks are not corpus

`01_Benchmarks/` holds the evaluation question set. It is **never** indexed for retrieval —
indexing it would let the system retrieve its own answer key and invalidate the comparison
between baseline LLM and RAG performance. Baseline and RAG responses belong in
`06_Evaluation/Raw_Responses/`, scoring in `06_Evaluation/Scoring/`, analysis in `Results/`.

## 7. How a RAG application should consume this

1. **Index `02_Corpus/<domain>/` only.** Never `01_Benchmarks/`, never `08_Archive/`.
2. **Carry the bucket as chunk metadata** (`domain`, `bucket`, `tier`) at ingestion. Retrieval
   filters on `domain` from the UI selector; ranking boosts on `tier`.
3. **Join to the mapping CSV on filename** to recover document title, authority, year and
   source URL, so citations show a real title instead of `1807_75c389383b.pdf`.
4. **Default retrieval set:** tiers A–C. Include D only when the question is explicitly about
   proposed/upcoming rules, and label it. Exclude E unless the user opts in.
5. **Answer shape:** direct answer → cited sources with titles → regulatory basis →
   concise compliance takeaway → explicit statement when evidence is insufficient.

## 8. Corpus accounting

| | PDFs |
|---|---|
| Total in project | 803 |
| Production corpus (`02_Corpus`) | 358 |
| Archive (`08_Archive`) | 445 |
| Missing / unaccounted | **0** |

The 445 archived PDFs are the 440 original healthcare source files that were consolidated
into the 267 unique documents (173 of them exact duplicates), plus 5 UP-RERA reference PDFs.
Nothing was deleted. Every PDF is in exactly one of: production corpus, review, or archive.

## 9. Status

Organization complete. Corpus is ready to hand to the RAG application build.

Still open, and deliberately not invented here:
- `01_Benchmarks/` is empty — the benchmark question set has not been added yet.
- `07_Documentation/` is empty apart from `Research/RAG_Project_structure.txt`; the CCS one-pager
  and meeting notes were not present in the project folder.
- `02_Corpus/Healthcare/06_Review/` (83) and `02_Corpus/RERA/04_Review/` (37) hold documents
  whose binding status a human should confirm before they are promoted or dropped.
