"""
V2.4 Benchmark Evaluation Script
==================================
Reads from Benchmark_RERA_V2_UserJourney_50Q.xlsx (1_Benchmark sheet),
runs all 50 questions through three systems, writes answers back into
the 3_Responses sheet of the SAME file, and auto-judges with LLM-as-judge.

Systems
-------
  A  Base LLM        gpt-5-nano, no retrieval context
  B  Plain RAG       gpt-5-nano + ChromaDB (improved per-doc cap + markers)
  C  Agentic RAG     gpt-5-nano + query decomposition + multi-hop reflection
                   (full agentic pipeline on ALL tiers in eval_mode — no tier routing)

Changes from V2.3
-----------------
  V2.4 makes two targeted fixes based on root-cause analysis of the V2.3
  failures.  Per-question investigation of T1-05 (B=0) and T1-07 (B=0, C=0)
  revealed two distinct failure modes:

  FAILURE MODE 1 — Rigid section-citation causes confident wrong answers (T1-05)
    T1-05 is a "trick" question: the correct answer is that the Authority has
    NO fixed deadline (only the Adjudicating Officer has 60 days under §71(5)).
    All three systems hallucinated "60 days", but System B scored 0 because
    MANDATORY REQ #1 forced it to open with "Section 39(1)" — a non-existent
    provision — with full confidence.  The judge gave FA=0, Conf=0 for
    "confidently asserts a wrong section and wrong legal position."
    V2.4 fix: make MANDATORY REQ #1 conditional on retrieval evidence.
    NEW: "If the retrieved text clearly identifies the controlling section,
    open with that section in your first sentence.  If the section is not
    clearly stated in the retrieved passages, describe the provision accurately
    without citing a section number — never invent one."

  FAILURE MODE 2 — Retrieval mismatch + mandatory citation = confident wrong answer (T1-07)
    T1-07 asks about the penalty for a PROMOTER who fails to register.
    The correct answer is §59 (10% of estimated project cost + potential
    imprisonment).  System B and C retrieved §62 (penalties for REAL ESTATE
    AGENTS who fail to register) — a related but different provision.
    MANDATORY REQ #4 then forced both systems to "answer primarily from
    retrieved sources", so they faithfully cited §62 → 0/8.
    System A (no retrieval) correctly cited §59 from training → 7/8.
    V2.4 fix: add an entity-relevance check instruction.
    NEW: "Before answering, verify the retrieved passages address the SAME
    entity the question asks about (promoter, agent, allottee, authority).
    If the retrieved text clearly addresses a DIFFERENT entity (e.g., agent
    penalties when the question asks about promoter penalties), do NOT cite
    those passages.  Instead, draw on training knowledge and flag every
    inference clearly with '(inferred from training, not in retrieved sources)'."

  ALL V2.3 WINS PRESERVED:
     - System A: best-effort citation (no MANDATORY REQUIREMENTS).
     - T3 HyDE restored for sub-queries; T4/T5 still raw sub-query text.
     - Multi-section routing override preserved in production path (eval_mode=False).
     - HyDE for T1/T2 plain RAG and reflection re-retrieval unchanged.

Key design choices (unchanged from V2.1)
-----------------------------------------
  * Standalone — does NOT import from run_benchmark.py
  * Reads from and writes back to the V2 benchmark xlsx directly
  * Incremental save: writes each row to 3_Responses as soon as it completes
  * Resume: skips question_ids already present in 3_Responses
  * Systems B/C use a HYBRID system prompt: retrieve first, fill gaps
    with training knowledge and flag clearly
  * System A uses a pure-LLM prompt (no retrieval context at all)
  * LLM-as-judge runs immediately after all 50 answers are collected
  * Final output: 3_Responses sheet fully populated + 4_Scores sheet added
  * JUDGE_MODEL is separate from LLM_MODEL (avoids grading-own-homework bias)
    Default judge: deepseek-ai/DeepSeek-V4.1-Flash via Together AI
    Default answer model: gpt-5-nano via OPENAI_API_KEY

Usage
-----
  # Full run (all 50 questions, all systems):
  python run_v2_evaluation.py \\
    --benchmark /path/to/Benchmark_RERA_V2_UserJourney_50Q.xlsx

  # Quick smoke test (first 3 questions):
  python run_v2_evaluation.py \\
    --benchmark /path/to/Benchmark_RERA_V2_UserJourney_50Q.xlsx \\
    --limit 3

  # Skip scoring (just get answers):
  python run_v2_evaluation.py \\
    --benchmark /path/to/Benchmark_RERA_V2_UserJourney_50Q.xlsx \\
    --no-judge

  # Resume a partial run (re-reads 3_Responses, skips done questions):
  python run_v2_evaluation.py \\
    --benchmark /path/to/Benchmark_RERA_V2_UserJourney_50Q.xlsx \\
    --resume

  OPENAI_API_KEY   must be set — used by answer systems A/B/C.
  LLM_MODEL        defaults to gpt-5-nano (override via env var).
  LLM_BASE_URL     optional — set for Together AI or any OpenAI-compatible endpoint.
  TOGETHER_API_KEY must be set — used by the judge model.
  JUDGE_MODEL      defaults to deepseek-ai/DeepSeek-V4.1-Flash (override via env var).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

# ── dotenv (optional) ─────────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv()                                        # cwd first (works when run from 05_App/benchmark)
    load_dotenv(Path(__file__).parent / ".env")          # script's own directory
    load_dotenv(Path(__file__).parent / "05_App" / "benchmark" / ".env")  # root → benchmark
    load_dotenv(Path(__file__).parent.parent / ".env")   # one level up
except ImportError:
    pass

# ── Tee Logger ────────────────────────────────────────────────────────────────
# Mirrors all print() output to both the terminal and a log file simultaneously.
# Used to auto-save a dated run log in 01_Benchmarks/RERA/logs/ alongside the
# output xlsx, so every evaluation run is fully reproducible.

class TeeLogger:
    """
    Replace sys.stdout so every print() goes to both terminal and a log file.

    Usage:
        logger = TeeLogger(log_path)
        sys.stdout = logger
        # ... all print() calls now go to terminal AND log file ...
        logger.close()          # flush + close file
        sys.stdout = sys.__stdout__
    """
    def __init__(self, log_path: Path):
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self._file   = open(str(log_path), "w", encoding="utf-8", buffering=1)
        self._stdout = sys.__stdout__
        self.log_path = log_path

    def write(self, msg: str) -> None:
        self._stdout.write(msg)
        self._file.write(msg)

    def flush(self) -> None:
        self._stdout.flush()
        self._file.flush()

    def close(self) -> None:
        self._file.flush()
        self._file.close()


# ── path setup ─────────────────────────────────────────────────────────────────
HERE         = Path(__file__).resolve().parent     # benchmark/
APP_DIR      = HERE.parent                         # 05_App/
PROJECT_ROOT = APP_DIR.parent
sys.path.insert(0, str(APP_DIR))

LLM_MODEL = os.getenv("LLM_MODEL", "gpt-5-nano")

# Judge uses a separate model/provider to avoid grading-own-homework bias
JUDGE_MODEL    = os.getenv("JUDGE_MODEL", "deepseek-ai/DeepSeek-V4.1-Flash")
JUDGE_BASE_URL = "https://api.together.xyz/v1"

# ── System prompts ─────────────────────────────────────────────────────────────

SYSTEM_A_PROMPT = """\
You are an Indian regulatory compliance expert with deep knowledge of RERA
(Real Estate Regulation and Development Act, 2016) and related state rules.

Answer the question from your training knowledge.

Guidelines (apply where you are confident — do NOT guess):
- Cite the relevant RERA section or rule if you know it with confidence.
  Example: "Under Section 11(4)(a) of RERA 2016, the promoter must..."
  If you are unsure of the exact section number, describe the provision
  without inventing a citation.
- Include specific statutory figures (percentages, timelines, penalty amounts)
  only if you are certain they are correct. Do not paraphrase or round them.
  If uncertain, give a qualitative answer and flag the uncertainty.
- If the question has multiple parts, address each part separately before
  giving an overall compliance takeaway.
- If a premise in the question is false or misleading, correct it explicitly
  before answering.
- Flag genuine uncertainty honestly rather than guessing.

Structure every answer:
1. Direct answer (with section citation if known with confidence).
2. Key figures or timelines from the Act (only if certain).
3. Each sub-part addressed separately (if the question has multiple parts).
4. Compliance takeaway for the stakeholder asking.
"""

SYSTEM_B_PROMPT = """\
You are an Indian regulatory compliance assistant for RERA and related regulations.

You are given retrieved document chunks from a regulatory corpus.

ENTITY CHECK (do this first, before writing your answer):
  Read the question carefully to identify which entity it asks about:
  promoter, real estate agent, allottee, or the Authority.
  Then scan the retrieved passages.  If the retrieved passages clearly address
  a DIFFERENT entity than the one the question asks about (e.g., the question
  is about a PROMOTER but the retrieved passages discuss penalties for REAL
  ESTATE AGENTS), do NOT rely on those passages.  Instead, answer entirely
  from training knowledge and flag every such inference clearly with
  "(inferred from training, not in retrieved sources)".

MANDATORY REQUIREMENTS — every answer must follow these:
1. If the retrieved text clearly identifies the controlling RERA section and
   subsection, open with that section inline in your first sentence
   (e.g. "Under Section 11(4)(h) of RERA 2016, ...").
   If the section is NOT clearly stated in the retrieved passages, describe
   the provision accurately without citing a section number — never invent one.
2. Quote exact statutory figures verbatim from the retrieved text — percentages,
   timelines, penalty amounts, thresholds. Do not paraphrase numbers or round them.
3. If the question has multiple parts or references two provisions, address each
   part separately with its own section reference before the overall takeaway.
4. Answer primarily from the retrieved sources (after passing the entity check above).
5. If the retrieved sources do not fully settle the question, draw on training
   knowledge to fill the gap — but FLAG every such inference clearly with
   "(inferred from training, not in retrieved sources)".
6. If a premise in the question is false, correct it before answering.
7. Never invent section numbers, figures or citations not in the sources or in
   your verified training knowledge.

Structure every answer:
1. Direct answer with inline section citation (only if clearly in retrieved text).
2. Exact statutory figures quoted verbatim from retrieved chunks.
3. Each sub-part of a multi-part question addressed separately (if applicable).
4. Sources relied on — cited by document TITLE (never a filename).
5. Compliance takeaway.
"""

SYSTEM_C_PROMPT = SYSTEM_B_PROMPT  # Agentic RAG uses the same hybrid prompt

# ── HyDE (Hypothetical Document Embeddings) prompt ────────────────────────────
# Instead of embedding the raw question, we generate a short hypothetical passage
# that would appear in an official RERA document and embed THAT. This bridges the
# lexical gap between how questions are phrased and how regulatory text is written.

HYDE_SYSTEM = """\
You are an expert on Indian RERA regulations and related state rules.
Write a single short paragraph (2-4 sentences) exactly as it would appear in an
official RERA document, circular, or judgment, and that directly answers the
question below. Use the same terminology, section references, and phrasing found
in official RERA sources. Write ONLY the hypothetical passage with no preamble,
explanation, or quotation marks.
"""

# ── LLM-as-judge prompt ────────────────────────────────────────────────────────

JUDGE_SYSTEM = """\
You are an expert evaluator for an Indian regulatory compliance Q&A system
focused on RERA (Real Estate Regulation and Development Act 2016).

Score the SYSTEM ANSWER against the EXPECTED ANSWER and QUESTION on three
dimensions. Be strict; do not reward confident wrong answers.

RUBRIC
------
Factual Accuracy (0-4)
  4 = All key facts correct, no hallucinations, sections/penalties cited correctly.
  3 = Mostly correct with minor factual gaps or small inaccuracies.
  2 = Partially correct; some material facts wrong or missing.
  1 = Mostly incorrect; fundamental misunderstanding of the regulation.
  0 = Completely wrong, no relevant content, or answer is an error message.

Completeness (0-2)
  2 = Covers all key aspects: legal provision, compliance requirement, impact.
  1 = Covers some aspects but misses important ones.
  0 = Very incomplete or single-dimensional.

Confidence Calibration (0-2)
  2 = Appropriate uncertainty expressed; hedges draft/proposed rules; no overconfidence.
  1 = Partially calibrated — some overconfidence or underconfidence.
  0 = Significantly overconfident (states wrong facts as certain) or uselessly hedged.

Return ONLY valid JSON — no markdown, no preamble:
{
  "factual_accuracy":        <integer 0-4>,
  "completeness":            <integer 0-2>,
  "confidence_calibration":  <integer 0-2>,
  "total":                   <integer 0-8>,
  "rationale":               "<1-2 sentences>"
}
"""

# ── load benchmark questions ───────────────────────────────────────────────────

def load_benchmark(xlsx_path: Path) -> list[dict]:
    """Load 1_Benchmark sheet from V2 benchmark file."""
    import openpyxl
    wb = openpyxl.load_workbook(str(xlsx_path), data_only=True)

    if "1_Benchmark" not in wb.sheetnames:
        sys.exit(f"ERROR: '1_Benchmark' sheet not found in {xlsx_path}.\n"
                 f"Available sheets: {wb.sheetnames}")

    ws = wb["1_Benchmark"]
    headers = [str(ws.cell(1, c).value or "").strip().lower().replace(" ", "_")
               for c in range(1, ws.max_column + 1)]

    rows = []
    for r in ws.iter_rows(min_row=2, values_only=True):
        rec = {h: (v if v is not None else "") for h, v in zip(headers, r)}
        if rec.get("question_id"):
            rows.append(rec)
    return rows


def load_done_ids(xlsx_path: Path) -> set[str]:
    """Return question_ids already written to 3_Responses (for resume)."""
    import openpyxl
    done = set()
    try:
        wb = openpyxl.load_workbook(str(xlsx_path), data_only=True)
        if "3_Responses" not in wb.sheetnames:
            return done
        ws = wb["3_Responses"]
        headers = [str(ws.cell(1, c).value or "").strip().lower().replace(" ", "_")
                   for c in range(1, ws.max_column + 1)]
        for r in ws.iter_rows(min_row=2, values_only=True):
            rec = dict(zip(headers, r))
            qid = rec.get("question_id")
            if qid:
                done.add(str(qid))
    except Exception:
        pass
    return done


# ── ChromaDB loader ────────────────────────────────────────────────────────────

def load_chromadb():
    from langchain_huggingface import HuggingFaceEmbeddings
    from langchain_chroma import Chroma
    index_dir = APP_DIR / "chroma_db"
    if not index_dir.exists():
        sys.exit(f"ERROR: ChromaDB index not found at {index_dir}.\n"
                 "Run: python ingest_corpus.py --rebuild")
    emb = HuggingFaceEmbeddings(
        model_name="BAAI/bge-small-en-v1.5",
        encode_kwargs={"normalize_embeddings": True},
    )
    return Chroma(
        collection_name="regulatory_corpus",
        embedding_function=emb,
        persist_directory=str(index_dir),
    )


# ── RAG retrieval (mirrors improved rag_query.py) ──────────────────────────────

TIER_WEIGHT = {"A": 1.00, "B": 0.95, "C": 0.80, "D": 0.55, "E": 0.35}
TOP_K       = 6
CANDIDATE_K = 24


def retrieve_passages(store, query: str, domain: str = "RERA") -> list[dict]:
    """
    Authority-weighted retrieval with per-doc cap of 4 chunks.
    Returns list of {content, title, tier, source, weighted}.
    """
    try:
        space = ((store._collection.metadata or {}).get("hnsw:space") or "l2").lower()
    except Exception:
        space = "l2"

    results = store.similarity_search_with_relevance_scores(
        query, k=CANDIDATE_K, filter={"domain": domain}
    )

    # Score and weight
    scored = []
    for doc, raw in results:
        md = doc.metadata or {}
        tier = md.get("tier", "C")
        if tier in ("D", "E"):
            continue
        # Convert L2 relevance to cosine-like similarity
        if space.startswith("cosine"):
            cos = raw
        else:
            cos = max(0.0, (raw + 1.0) / 2.0)
        weighted = cos * TIER_WEIGHT.get(tier, 0.80)
        scored.append({
            "content":    doc.page_content,
            "title":      md.get("title", "Unknown"),
            "tier":       tier,
            "source":     md.get("source_url", ""),
            "source_file": md.get("source_file", ""),
            "weighted":   weighted,
        })

    scored.sort(key=lambda x: x["weighted"], reverse=True)

    # Per-doc cap: at most 4 chunks from the same source file
    seen: dict[str, int] = {}
    out: list[dict] = []
    for p in scored:
        key = p["source_file"]
        if seen.get(key, 0) >= 4:
            continue
        seen[key] = seen.get(key, 0) + 1
        out.append(p)
        if len(out) >= TOP_K:
            break
    return out


def passages_to_context(passages: list[dict]) -> str:
    lines = []
    for i, p in enumerate(passages, 1):
        lines.append(f"[{i}] Title: {p['title']} | Tier: {p['tier']}\n{p['content']}")
    return "\n\n".join(lines)


def generate_hyde_query(client, question: str) -> str:
    """
    HyDE: generate a short hypothetical regulatory passage and return it as the
    retrieval query. This bridges the lexical gap between question phrasing and
    the dense regulatory language in the corpus.
    Falls back to the original question on any error.
    """
    try:
        rsp = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": HYDE_SYSTEM},
                {"role": "user",   "content": question},
            ],
            max_completion_tokens=160,
        )
        hyde_passage = rsp.choices[0].message.content.strip()
        return hyde_passage if hyde_passage else question
    except Exception:
        return question  # graceful fallback — never breaks retrieval


# ── System A — Base LLM ────────────────────────────────────────────────────────

def answer_system_a(client, question: str, retries: int = 3) -> str:
    for attempt in range(retries):
        try:
            rsp = client.chat.completions.create(
                model=LLM_MODEL,
                messages=[
                    {"role": "system", "content": SYSTEM_A_PROMPT},
                    {"role": "user",   "content": question},
                ],
            )
            return rsp.choices[0].message.content.strip()
        except Exception as exc:
            if attempt < retries - 1:
                time.sleep(5 * (attempt + 1))
            else:
                return f"[ERROR-A: {exc}]"
    return "[ERROR-A: max retries]"


# ── System B — Plain RAG with HyDE ────────────────────────────────────────────

def answer_system_b(client, store, question: str, domain: str = "RERA",
                    retries: int = 3) -> str:
    """
    Plain RAG with HyDE retrieval.
    1. Generate a hypothetical RERA document passage (HyDE) from the question.
    2. Embed that passage to retrieve more lexically-aligned corpus chunks.
    3. Answer using the hybrid prompt.
    """
    # HyDE: use a hypothetical passage as the retrieval query
    hyde_query = generate_hyde_query(client, question)
    passages   = retrieve_passages(store, hyde_query, domain)
    context    = passages_to_context(passages)

    for attempt in range(retries):
        try:
            rsp = client.chat.completions.create(
                model=LLM_MODEL,
                messages=[
                    {"role": "system", "content": SYSTEM_B_PROMPT},
                    {"role": "user",
                     "content": (
                         f"Domain: {domain}\n\n"
                         f"Retrieved sources:\n{context}\n\n"
                         f"Question: {question}"
                     )},
                ],
            )
            return rsp.choices[0].message.content.strip()
        except Exception as exc:
            if attempt < retries - 1:
                time.sleep(5 * (attempt + 1))
            else:
                return f"[ERROR-B: {exc}]"
    return "[ERROR-B: max retries]"


# ── System C — Agentic RAG ─────────────────────────────────────────────────────

DECOMPOSE_SYSTEM = """\
You are a query analyser for an Indian RERA compliance assistant.
Break a complex question into independent, focused sub-queries for document search.

Rules:
- Simple/atomic query → return it unchanged as a single sub-query.
- Multi-part or multi-step question → split into self-contained sub-queries.
- Do not invent questions the user did not ask.
- Return ONLY a JSON array of strings, nothing else.
"""

REFLECT_SYSTEM = """\
You are a quality-checker for an Indian regulatory compliance assistant.
Check whether the draft answer is sufficient. It is insufficient if it
explicitly says the corpus does not contain the information, or is under
two sentences with no citation.

Respond ONLY with valid JSON:
  {"sufficient": true}
  or
  {"sufficient": false, "refined_query": "<a better search query to try>"}
"""


def _llm_call(client, system: str, user: str, retries: int = 3) -> str:
    for attempt in range(retries):
        try:
            rsp = client.chat.completions.create(
                model=LLM_MODEL,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user",   "content": user},
                ],
            )
            return rsp.choices[0].message.content.strip()
        except Exception as exc:
            if attempt < retries - 1:
                time.sleep(5 * (attempt + 1))
            else:
                raise
    raise RuntimeError("max retries")


def decompose_query(client, query: str) -> list[str]:
    try:
        raw  = _llm_call(client, DECOMPOSE_SYSTEM, query)
        subs = json.loads(raw)
        if isinstance(subs, list) and all(isinstance(s, str) for s in subs):
            return [s.strip() for s in subs if s.strip()]
    except Exception:
        pass
    return [query]


def answer_system_c(client, store, question: str, domain: str = "RERA",
                    tier: int = 3, eval_mode: bool = True) -> dict:
    """
    Agentic RAG with HyDE retrieval.

    eval_mode=True  (default for benchmarking):
        Always runs the full agentic pipeline regardless of tier.
        This gives a clean isolated measurement of what agentic retrieval
        adds over plain RAG (System B).  Tier-based routing is a production
        optimisation for latency/cost — it must NOT be active during eval
        because T1/T2 questions would silently fall back to plain RAG,
        making System C indistinguishable from System B on those tiers.

    eval_mode=False (production / app):
        Tier-wise routing:
          T1/T2 → plain RAG + HyDE (single-section questions are faster this way)
          T3/T4/T5 → full agentic pipeline + HyDE
        Multi-section override: a T1/T2 question naming 2+ RERA sections
        escalates to the full pipeline regardless.
    """
    steps: list[str] = []

    # ── EVAL MODE: always run full agentic pipeline ────────────────────────────
    if eval_mode:
        steps.append(
            f"[eval_mode] T{tier} question: full agentic pipeline always "
            "(tier-based routing disabled for clean benchmark measurement)"
        )
        # Skip the tier-routing block entirely; fall through to T3/T4/T5 path.
        _force_agentic = True
    else:
        # ── PRODUCTION: tier-based routing ────────────────────────────────────
        _sec_refs = set(
            s.lower() for s in re.findall(r'\bsection\s+\d+(?:\(\w+\))*', question, re.IGNORECASE)
        )
        _force_agentic = tier <= 2 and len(_sec_refs) >= 2
        if _force_agentic:
            steps.append(
                f"T{tier} question references {len(_sec_refs)} RERA sections "
                f"({', '.join(sorted(_sec_refs))}): overriding to full agentic pipeline"
            )

    # ── T1 / T2 : Plain RAG path with HyDE (production only) ──────────────────
    if not eval_mode and tier <= 2 and not _force_agentic:
        tier_names = {1: "Factual Recall", 2: "Applied Compliance"}
        steps.append(
            f"T{tier} ({tier_names.get(tier, 'simple')}) question: "
            "plain RAG path with HyDE (skipping decomposition overhead)"
        )
        hyde_query = generate_hyde_query(client, question)
        steps.append(f"HyDE query generated ({len(hyde_query)} chars)")
        passages = retrieve_passages(store, hyde_query, domain)
        steps.append(f"Retrieved {len(passages)} passages via HyDE query")
        context  = passages_to_context(passages)

        for attempt in range(3):
            try:
                rsp = client.chat.completions.create(
                    model=LLM_MODEL,
                    messages=[
                        {"role": "system", "content": SYSTEM_C_PROMPT},
                        {"role": "user",
                         "content": (
                             f"Domain: {domain}\n\n"
                             f"Retrieved sources:\n{context}\n\n"
                             f"Question: {question}"
                         )},
                    ],
                )
                steps.append("Answer generated (plain RAG path)")
                return {
                    "answer":      rsp.choices[0].message.content.strip(),
                    "sub_queries": [question],
                    "steps":       steps,
                }
            except Exception as exc:
                if attempt < 2:
                    time.sleep(5 * (attempt + 1))
                else:
                    return {
                        "answer":      f"[ERROR-C: {exc}]",
                        "sub_queries": [question],
                        "steps":       steps + [f"Plain RAG answer failed: {exc}"],
                    }

    # ── T3 / T4 / T5 : Full agentic pipeline ─────────────────────────────────
    # V2.3 tier-specific HyDE strategy for sub-query retrieval:
    #   T3 Procedural  → HyDE per sub-query  (sub-queries decompose into user-
    #                    language procedural steps that need lexical bridging)
    #   T4 Multi-hop   → raw sub-query text  (sub-queries decompose into
    #   T5 Adversarial   regulatory-phrased section lookups; HyDE adds noise)
    # HyDE is always preserved for the reflection re-retrieval step.
    tier_names_agentic = {3: "Procedural Knowledge", 4: "Multi-hop Reasoning",
                          5: "Adversarial/Trap"}
    _tier_label = tier_names_agentic.get(tier, 'complex')
    _use_hyde_subq = (tier == 3)   # V2.4 (from V2.3): HyDE only for T3 sub-queries
    _subq_strategy = "HyDE per sub-query" if _use_hyde_subq else "raw sub-query text"
    if _force_agentic:
        steps.append(
            f"Full agentic pipeline (multi-section override from T{tier}, "
            f"sub-query retrieval: {_subq_strategy})"
        )
    else:
        steps.append(
            f"T{tier} ({_tier_label}) question: "
            f"full agentic pipeline ({_subq_strategy})"
        )

    # Step 1: decompose
    try:
        sub_queries = decompose_query(client, question)
    except Exception as exc:
        sub_queries = [question]
        steps.append(f"Decompose failed ({exc}), using original query")

    steps.append(
        f"Decomposed into {len(sub_queries)} "
        f"sub-quer{'y' if len(sub_queries) == 1 else 'ies'}: {sub_queries}"
    )

    # Step 2: retrieval for each sub-query — tier-specific HyDE strategy
    seen_keys: set[str] = set()
    all_passages: list[dict] = []
    for sq in sub_queries:
        # T3: generate HyDE passage to bridge user-phrased procedural steps
        # to regulatory document phrasing in the corpus
        # T4/T5: decomposed sub-queries are already regulatory-phrased;
        # use raw text so HyDE doesn't focus on a single dominant concept
        if _use_hyde_subq:
            sq_retrieval = generate_hyde_query(client, sq)
        else:
            sq_retrieval = sq
        for p in retrieve_passages(store, sq_retrieval, domain):
            key = p["content"][:100]
            if key not in seen_keys:
                seen_keys.add(key)
                all_passages.append(p)

    steps.append(
        f"Retrieved {len(all_passages)} unique passages across "
        f"{len(sub_queries)} sub-queries ({_subq_strategy})"
    )

    # Step 3: first-pass answer
    context = passages_to_context(all_passages)
    try:
        draft = _llm_call(
            client, SYSTEM_C_PROMPT,
            f"Domain: {domain}\n\nRetrieved sources:\n{context}\n\nQuestion: {question}"
        )
    except Exception as exc:
        return {
            "answer":      f"[ERROR-C: {exc}]",
            "sub_queries": sub_queries,
            "steps":       steps + [f"First-pass answer failed: {exc}"],
        }

    steps.append("Generated first-pass answer")

    # Step 4: reflect — is the answer sufficient?
    try:
        reflect_raw = _llm_call(client, REFLECT_SYSTEM,
                                 f"Original question: {question}\n\nDraft answer:\n{draft}")
        reflection  = json.loads(reflect_raw)
    except Exception:
        reflection = {"sufficient": True}

    final_answer = draft

    if not reflection.get("sufficient", True):
        refined = reflection.get("refined_query", question)
        steps.append(f"Reflection: insufficient. Re-querying with HyDE: '{refined}'")

        hyde_refined = generate_hyde_query(client, refined)
        for p in retrieve_passages(store, hyde_refined, domain):
            key = p["content"][:100]
            if key not in seen_keys:
                seen_keys.add(key)
                all_passages.append(p)

        context2 = passages_to_context(all_passages)
        try:
            final_answer = _llm_call(
                client, SYSTEM_C_PROMPT,
                f"Domain: {domain}\n\nRetrieved sources (expanded):\n{context2}\n\nQuestion: {question}"
            )
            steps.append("Generated final answer after HyDE re-retrieval")
        except Exception as exc:
            steps.append(f"Re-retrieval answer failed: {exc}, keeping first-pass")
    else:
        steps.append("Reflection: answer sufficient, no re-retrieval needed")

    return {
        "answer":      final_answer,
        "sub_queries": sub_queries,
        "steps":       steps,
    }


# ── LLM-as-judge ──────────────────────────────────────────────────────────────

def judge_one(judge_client, question: str, expected: str, system_answer: str,
              retries: int = 3) -> dict:
    """
    Score one system answer with the judge model (DeepSeek-V4.1-Flash by default).
    Uses non-streaming so we can read content directly from rsp.choices[0].message.content.
    judge_client is a separate OpenAI-compatible client pointed at Together AI.
    """
    if not system_answer or system_answer.startswith("[ERROR"):
        return {
            "factual_accuracy": 0, "completeness": 0,
            "confidence_calibration": 0, "total": 0,
            "rationale": "No answer produced (error or empty).",
        }

    user_msg = (
        f"QUESTION:\n{question}\n\n"
        f"EXPECTED ANSWER (ground truth):\n{expected}\n\n"
        f"SYSTEM ANSWER (to evaluate):\n{system_answer}"
    )

    for attempt in range(retries):
        try:
            rsp = judge_client.chat.completions.create(
                model=JUDGE_MODEL,
                messages=[
                    {"role": "system", "content": JUDGE_SYSTEM},
                    {"role": "user",   "content": user_msg},
                ],
                # Non-streaming: DeepSeek reasoning model returns final answer
                # in content (reasoning chain is in reasoning_content, not needed here)
            )
            raw = (rsp.choices[0].message.content or "").strip()
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw).strip()
            result = json.loads(raw)
            result["total"] = (
                result.get("factual_accuracy", 0)
                + result.get("completeness", 0)
                + result.get("confidence_calibration", 0)
            )
            return result
        except Exception as exc:
            if attempt < retries - 1:
                time.sleep(5)
            else:
                return {
                    "factual_accuracy": 0, "completeness": 0,
                    "confidence_calibration": 0, "total": 0,
                    "rationale": f"Judge error: {exc}",
                }
    return {"factual_accuracy": 0, "completeness": 0,
            "confidence_calibration": 0, "total": 0, "rationale": "judge failed"}


# ── xlsx write helpers ─────────────────────────────────────────────────────────

RESPONSE_COLS = [
    ("question_id",          16),
    ("tier",                  6),
    ("tier_name",            18),
    ("question_text",        55),
    ("expected_answer",      65),
    ("system_a_answer",      70),
    ("system_b_answer",      70),
    ("system_c_answer",      70),
    ("system_c_sub_queries", 40),
    ("system_c_steps",       40),
    ("run_timestamp",        20),
]

SCORE_COLS = [
    ("question_id",            16),
    ("tier",                    6),
    ("tier_name",              18),
    ("system",                  8),
    ("factual_accuracy",       10),
    ("completeness",           10),
    ("confidence_calibration", 14),
    ("total",                   8),
    ("rationale",              60),
    ("question_text",          55),
    ("expected_answer",        65),
    ("system_answer",          70),
]


def _header_style():
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    hdr_fill = PatternFill("solid", fgColor="1F4E79")
    thin = Side(style="thin", color="BBBBBB")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    hdr_font = Font(bold=True, color="FFFFFF", name="Calibri", size=10)
    hdr_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
    return hdr_fill, border, hdr_font, hdr_align


def _row_style():
    from openpyxl.styles import Font, Alignment, Border, Side
    thin = Side(style="thin", color="BBBBBB")
    return (
        Font(name="Calibri", size=9),
        Alignment(vertical="top", wrap_text=True),
        Border(left=thin, right=thin, top=thin, bottom=thin),
    )


def initialise_responses_sheet(xlsx_path: Path) -> None:
    """Add or clear 3_Responses sheet with header row."""
    import openpyxl
    from openpyxl.utils import get_column_letter

    wb = openpyxl.load_workbook(str(xlsx_path))
    if "3_Responses" in wb.sheetnames:
        del wb["3_Responses"]
    ws = wb.create_sheet("3_Responses")

    hdr_fill, border, hdr_font, hdr_align = _header_style()
    for ci, (col_key, col_w) in enumerate(RESPONSE_COLS, 1):
        cell = ws.cell(1, ci, col_key.replace("_", " ").title())
        cell.font      = hdr_font
        cell.fill      = hdr_fill
        cell.alignment = hdr_align
        cell.border    = border
        ws.column_dimensions[get_column_letter(ci)].width = col_w
    ws.row_dimensions[1].height = 28
    ws.freeze_panes = "A2"
    wb.save(str(xlsx_path))


def append_response_row(xlsx_path: Path, row: dict) -> None:
    """Append a completed answer row to 3_Responses, saving immediately."""
    import openpyxl
    from openpyxl.utils import get_column_letter
    from openpyxl.styles import PatternFill

    tier_fills = {
        "1": PatternFill("solid", fgColor="FFF2CC"),
        "2": PatternFill("solid", fgColor="E2EFDA"),
        "3": PatternFill("solid", fgColor="DDEEFF"),
        "4": PatternFill("solid", fgColor="FCE4D6"),
        "5": PatternFill("solid", fgColor="F4CCCC"),
    }

    wb = openpyxl.load_workbook(str(xlsx_path))
    ws = wb["3_Responses"]
    ri = ws.max_row + 1

    r_font, r_align, r_border = _row_style()
    tier_str = str(row.get("tier", ""))
    fill = tier_fills.get(tier_str, PatternFill())

    for ci, (col_key, _) in enumerate(RESPONSE_COLS, 1):
        val = row.get(col_key, "")
        if isinstance(val, (list, dict)):
            val = json.dumps(val, ensure_ascii=False)
        cell = ws.cell(ri, ci, val)
        cell.font      = r_font
        cell.fill      = fill
        cell.alignment = r_align
        cell.border    = r_border

    ws.row_dimensions[ri].height = 80
    wb.save(str(xlsx_path))


def write_scores_sheet(xlsx_path: Path, score_rows: list[dict]) -> None:
    """Write 4_Scores sheet with all judge scores."""
    import openpyxl
    from openpyxl.utils import get_column_letter
    from openpyxl.styles import PatternFill

    wb = openpyxl.load_workbook(str(xlsx_path))
    if "4_Scores" in wb.sheetnames:
        del wb["4_Scores"]
    ws = wb.create_sheet("4_Scores")

    hdr_fill, border, hdr_font, hdr_align = _header_style()
    for ci, (col_key, col_w) in enumerate(SCORE_COLS, 1):
        cell = ws.cell(1, ci, col_key.replace("_", " ").title())
        cell.font      = hdr_font
        cell.fill      = hdr_fill
        cell.alignment = hdr_align
        cell.border    = border
        ws.column_dimensions[get_column_letter(ci)].width = col_w
    ws.row_dimensions[1].height = 28
    ws.freeze_panes = "A2"

    sys_fills = {
        "A": PatternFill("solid", fgColor="FFF2CC"),
        "B": PatternFill("solid", fgColor="DDEEFF"),
        "C": PatternFill("solid", fgColor="E2EFDA"),
    }

    r_font, r_align, r_border = _row_style()
    for ri, row in enumerate(score_rows, 2):
        fill = sys_fills.get(row.get("system", ""), PatternFill())
        for ci, (col_key, _) in enumerate(SCORE_COLS, 1):
            val = row.get(col_key, "")
            cell = ws.cell(ri, ci, val)
            cell.font      = r_font
            cell.fill      = fill
            cell.alignment = r_align
            cell.border    = r_border
        ws.row_dimensions[ri].height = 60

    wb.save(str(xlsx_path))


def write_summary_sheet(xlsx_path: Path, score_rows: list[dict]) -> None:
    """Write 5_Summary sheet — per-tier and overall averages per system."""
    import openpyxl
    from openpyxl.utils import get_column_letter
    from openpyxl.styles import PatternFill, Font
    from collections import defaultdict

    wb = openpyxl.load_workbook(str(xlsx_path))
    if "5_Summary" in wb.sheetnames:
        del wb["5_Summary"]
    ws = wb.create_sheet("5_Summary")

    hdr_fill, border, hdr_font, hdr_align = _header_style()
    r_font, r_align, r_border = _row_style()

    sys_fills = {
        "A": PatternFill("solid", fgColor="FFF2CC"),
        "B": PatternFill("solid", fgColor="DDEEFF"),
        "C": PatternFill("solid", fgColor="E2EFDA"),
    }

    def _avg(vals):
        v = [x for x in vals if isinstance(x, (int, float))]
        return round(sum(v) / len(v), 2) if v else 0.0

    # Group by (tier, system)
    groups: dict = defaultdict(list)
    for row in score_rows:
        groups[(str(row.get("tier", "")), row.get("system", ""))].append(row)

    all_tiers   = sorted({str(r.get("tier", "")) for r in score_rows},
                         key=lambda x: int(x) if x.isdigit() else 99)
    all_systems = ["A", "B", "C"]

    hdrs = ["Tier", "Tier Name", "System", "N",
            "Avg Factual (0-4)", "Avg Completeness (0-2)",
            "Avg Calibration (0-2)", "Avg Total (0-8)"]
    widths = [8, 22, 10, 6, 18, 20, 20, 16]
    for ci, (h, w) in enumerate(zip(hdrs, widths), 1):
        cell = ws.cell(1, ci, h)
        cell.font = hdr_font; cell.fill = hdr_fill
        cell.alignment = hdr_align; cell.border = border
        ws.column_dimensions[get_column_letter(ci)].width = w
    ws.row_dimensions[1].height = 28
    ws.freeze_panes = "A2"

    ri = 2
    tier_name_map = {
        "1": "Factual Recall",
        "2": "Applied Compliance",
        "3": "Procedural Knowledge",
        "4": "Multi-hop Reasoning",
        "5": "Adversarial / Trap",
    }

    for tier in all_tiers:
        for sys_label in all_systems:
            rows_k = groups.get((tier, sys_label), [])
            if not rows_k:
                continue
            fill = sys_fills.get(sys_label, PatternFill())
            vals = [
                tier,
                tier_name_map.get(tier, ""),
                f"System {sys_label}",
                len(rows_k),
                _avg([r.get("factual_accuracy", 0) for r in rows_k]),
                _avg([r.get("completeness", 0) for r in rows_k]),
                _avg([r.get("confidence_calibration", 0) for r in rows_k]),
                _avg([r.get("total", 0) for r in rows_k]),
            ]
            for ci, v in enumerate(vals, 1):
                cell = ws.cell(ri, ci, v)
                cell.font = r_font; cell.fill = fill
                cell.alignment = r_align; cell.border = border
            ws.row_dimensions[ri].height = 20
            ri += 1

    # Overall row per system
    ri += 1
    ws.cell(ri, 1, "OVERALL").font = Font(bold=True, name="Calibri", size=10)
    ri += 1

    for ci, h in enumerate(hdrs, 1):
        cell = ws.cell(ri, ci, h)
        cell.font = hdr_font; cell.fill = hdr_fill
        cell.alignment = hdr_align; cell.border = border
    ri += 1

    for sys_label in all_systems:
        sys_rows = [r for r in score_rows if r.get("system") == sys_label]
        if not sys_rows:
            continue
        fill = sys_fills.get(sys_label, PatternFill())
        vals = [
            "All",
            "All tiers",
            f"System {sys_label}",
            len(sys_rows),
            _avg([r.get("factual_accuracy", 0) for r in sys_rows]),
            _avg([r.get("completeness", 0) for r in sys_rows]),
            _avg([r.get("confidence_calibration", 0) for r in sys_rows]),
            _avg([r.get("total", 0) for r in sys_rows]),
        ]
        for ci, v in enumerate(vals, 1):
            cell = ws.cell(ri, ci, v)
            cell.font = r_font; cell.fill = fill
            cell.alignment = r_align; cell.border = border
        ws.row_dimensions[ri].height = 20
        ri += 1

    wb.save(str(xlsx_path))


# ── Pre-flight checks ─────────────────────────────────────────────────────────

def run_preflight_checks(client, judge_client, bench_path: Path,
                         skip_judge: bool) -> None:
    """
    Validate everything that could silently kill a 300-call run.
    Exits with a clear error message rather than letting failures surface
    three hours in.

    Checks:
      1. Benchmark xlsx is writable (not locked open in Excel)
      2. OpenAI API key is valid and the answer model responds
      3. Together AI key is valid and the judge model responds (unless --no-judge)
      4. ChromaDB index exists and the regulatory_corpus collection is reachable
    """
    errors:   list[str] = []
    warnings: list[str] = []

    print("\nRunning pre-flight checks before the evaluation loop ...")

    # 1. Xlsx writability — catches "file open in Excel" on Windows
    try:
        import openpyxl
        wb = openpyxl.load_workbook(str(bench_path))
        wb.save(str(bench_path))
        wb.close()
        print(f"  [OK] Benchmark xlsx is writable: {bench_path.name}")
    except PermissionError:
        errors.append(
            f"Benchmark file is locked by another process (probably Excel): {bench_path}\n"
            "         Close it and retry."
        )
    except Exception as exc:
        warnings.append(f"Could not fully verify xlsx writability: {exc}")

    # 2. OpenAI API key + answer model
    try:
        rsp = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": "ping"}],
            max_completion_tokens=5,
        )
        print(f"  [OK] OpenAI API valid — model '{LLM_MODEL}' is responding")
    except Exception as exc:
        errors.append(
            f"OpenAI API call failed: {exc}\n"
            "         Check OPENAI_API_KEY and LLM_MODEL in .env"
        )

    # 3. Together AI key + judge model (skip when --no-judge)
    if not skip_judge and judge_client is not None:
        try:
            judge_client.chat.completions.create(
                model=JUDGE_MODEL,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=5,
            )
            print(f"  [OK] Together AI valid — judge model '{JUDGE_MODEL}' is responding")
        except Exception as exc:
            errors.append(
                f"Together AI call failed: {exc}\n"
                "         Check TOGETHER_API_KEY and JUDGE_MODEL in .env"
            )
    elif skip_judge:
        print("  [--] Judge model check skipped (--no-judge mode)")

    # 4. ChromaDB index + collection
    try:
        index_dir = APP_DIR / "chroma_db"
        if not index_dir.exists():
            errors.append(
                f"ChromaDB index directory not found: {index_dir}\n"
                "         Run: python ingest_corpus.py --rebuild"
            )
        else:
            import chromadb
            chroma_client = chromadb.PersistentClient(path=str(index_dir))
            col   = chroma_client.get_collection("regulatory_corpus")
            count = col.count()
            if count == 0:
                errors.append(
                    "ChromaDB collection 'regulatory_corpus' is empty.\n"
                    "         Run: python ingest_corpus.py --rebuild"
                )
            else:
                print(f"  [OK] ChromaDB index ready — {count:,} chunks in regulatory_corpus")
    except Exception as exc:
        errors.append(f"ChromaDB connectivity check failed: {exc}")

    # Report
    if warnings:
        print()
        for w in warnings:
            print(f"  [WARN] {w}")

    if errors:
        print("\nPre-flight FAILED. Fix the errors below before spending 300 API calls:")
        for e in errors:
            print(f"\n  [ERROR] {e}")
        sys.exit(1)

    print("Pre-flight passed. Starting evaluation.\n")


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="V2 RERA Benchmark Evaluation — runs all 50 V2 questions through A/B/C"
    )
    parser.add_argument("--benchmark", type=Path, required=True,
                        help="Path to Benchmark_RERA_V2_UserJourney_50Q.xlsx")
    parser.add_argument("--limit",    type=int,
                        help="Only run first N questions (for testing)")
    parser.add_argument("--tier",     type=int, action="append", dest="tiers",
                        help="Filter to tier(s) (repeatable, e.g. --tier 1 --tier 2)")
    parser.add_argument("--resume",   action="store_true",
                        help="Skip questions already in 3_Responses sheet")
    parser.add_argument("--no-judge", action="store_true",
                        help="Skip LLM-as-judge scoring step")
    parser.add_argument("--only-system", choices=["A", "B", "C"],
                        help="Run only one system (useful for partial re-runs)")
    parser.add_argument("--output", type=Path, default=None,
                        help="Write results to this xlsx (default: auto-increment version, "
                             "e.g. V2 -> V2.1, so the V2 baseline is never overwritten)")
    parser.add_argument("--question_ids", type=str, default=None,
                        help="Comma-separated list of question_ids to run (e.g. V2-T1-06,V2-T5-02). "
                             "Runs only these questions; all others are skipped.")
    args = parser.parse_args()

    bench_path = args.benchmark.resolve()
    if not bench_path.exists():
        sys.exit(f"ERROR: Benchmark file not found: {bench_path}")

    # ── Determine output path (auto-increment version to protect V2 baseline) ──
    # Pattern: Benchmark_RERA_V2_UserJourney_50Q.xlsx  ->  ...V2.1...
    #          Benchmark_RERA_V2.1_UserJourney_50Q.xlsx ->  ...V2.2...
    #          Benchmark_RERA_V2.2_UserJourney_50Q.xlsx ->  ...V2.3...
    if args.output:
        output_path = args.output.resolve()
    else:
        new_name = re.sub(
            r'_V(\d+)(?:\.(\d+))?_',
            lambda m: f"_V{m.group(1)}.{int(m.group(2) or 0) + 1}_",
            bench_path.name, count=1,
        )
        output_path = bench_path.parent / new_name

    if output_path != bench_path:
        shutil.copy2(str(bench_path), str(output_path))
        # This print goes to terminal BEFORE the logger is active (intentional)
        print(f"Output file:  {output_path.name}  (copied from {bench_path.name})")

    # ── Auto log file: 01_Benchmarks/RERA/logs/V2.1_eval_RERA_UserJourney_<date>.log
    _ver_match = re.search(r'V(\d+(?:\.\d+)?)', output_path.stem)
    _ver_str   = _ver_match.group(1) if _ver_match else "2.1"
    _log_dir   = PROJECT_ROOT / "01_Benchmarks" / "RERA" / "logs"
    _log_name  = (f"V{_ver_str}_eval_RERA_UserJourney_"
                  f"{datetime.now().strftime('%Y-%m-%d')}.log")
    _log_path  = _log_dir / _log_name

    logger = TeeLogger(_log_path)
    sys.stdout = logger
    print(f"Log file:     {_log_path}")

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        sys.exit("ERROR: OPENAI_API_KEY not set in environment.")

    together_key = os.getenv("TOGETHER_API_KEY")
    if not together_key and not args.no_judge:
        sys.exit("ERROR: TOGETHER_API_KEY not set. Required for LLM-as-judge.\n"
                 "       Run with --no-judge to skip scoring, or set TOGETHER_API_KEY.")

    from openai import OpenAI
    base_url = os.getenv("LLM_BASE_URL")
    client = OpenAI(api_key=api_key, base_url=base_url) if base_url else OpenAI(api_key=api_key)

    # Separate client for the judge — points to Together AI
    judge_client = (
        OpenAI(api_key=together_key, base_url=JUDGE_BASE_URL)
        if together_key else None
    )

    print(f"V2.4 Evaluation starting — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Benchmark:    {bench_path}")
    print(f"Answer model: {LLM_MODEL}")
    print(f"Judge model:  {JUDGE_MODEL} (Together AI)")
    print(f"Retrieval:    HyDE for B; C: T1/T2 HyDE, T3 HyDE sub-queries, T4/T5 raw sub-queries")
    print(f"System C:     Tier-wise routing + multi-section override for T1/T2")
    print(f"Prompts:      V2.4 — B/C: entity-check + conditional section cite; A: best-effort citation")

    # Pre-flight: validate APIs, ChromaDB, xlsx lock before the long run
    run_preflight_checks(client, judge_client, output_path, args.no_judge)

    # Load questions
    questions = load_benchmark(bench_path)
    if args.tiers:
        questions = [q for q in questions
                     if int(q.get("tier", 0)) in args.tiers]
    if args.limit:
        questions = questions[:args.limit]
    if args.question_ids:
        allowed = {qid.strip() for qid in args.question_ids.split(",")}
        questions = [q for q in questions if str(q.get("question_id", "")) in allowed]
        print(f"  --question_ids filter applied: running {len(questions)} question(s): {sorted(allowed)}")
    print(f"Questions: {len(questions)}")

    # Load ChromaDB
    print("Loading ChromaDB index ...")
    store = load_chromadb()
    print("  ChromaDB loaded.")

    # Resume: load already-done question IDs
    done_ids: set[str] = set()
    if args.resume:
        done_ids = load_done_ids(output_path)
        print(f"Resume mode: {len(done_ids)} question(s) already done, skipping them.")
    else:
        # Reinitialise the response sheet (clears any old data)
        print("Initialising 3_Responses sheet ...")
        initialise_responses_sheet(output_path)

    # Main loop
    total = len(questions)
    all_response_rows: list[dict] = []

    _loop_start: float | None = None
    for idx, q in enumerate(questions, 1):
        if idx == 2:                       # start timing after the first (cold) question
            _loop_start = time.time()
        qid  = str(q.get("question_id", ""))
        tier = q.get("tier", "")
        tier_name = q.get("tier_name", "")
        text = str(q.get("question_text", ""))
        expected = str(q.get("expected_answer", ""))

        if qid in done_ids:
            print(f"[{idx:3}/{total}] {qid} — SKIP (already done)")
            continue

        pct = int(idx / total * 100)
        # ETA: use rolling avg of elapsed time per completed question
        if idx > 1 and _loop_start is not None:
            avg_s = (time.time() - _loop_start) / (idx - 1)
            eta_s = avg_s * (total - idx + 1)
            eta_str = f"  ETA ~{int(eta_s)//60}m{int(eta_s)%60:02d}s"
        else:
            eta_str = ""
        print(f"[{idx:3}/{total}] {pct:3d}% {qid} T{tier} ...{eta_str}", end=" ", flush=True)
        t0 = time.time()

        row: dict[str, Any] = {
            "question_id":    qid,
            "tier":           tier,
            "tier_name":      tier_name,
            "question_text":  text,
            "expected_answer": expected,
            "system_a_answer": "",
            "system_b_answer": "",
            "system_c_answer": "",
            "system_c_sub_queries": "",
            "system_c_steps": "",
            "run_timestamp":  datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

        run_a = not args.only_system or args.only_system == "A"
        run_b = not args.only_system or args.only_system == "B"
        run_c = not args.only_system or args.only_system == "C"

        if run_a:
            row["system_a_answer"] = answer_system_a(client, text)
            print("A", end=" ", flush=True)

        if run_b:
            row["system_b_answer"] = answer_system_b(client, store, text)
            print("B", end=" ", flush=True)

        if run_c:
            # Pass tier so System C can route to plain RAG (T1/T2) or agentic (T3/T4/T5)
            tier_int = int(tier) if str(tier).strip().isdigit() else 3
            c_res = answer_system_c(client, store, text, tier=tier_int)
            row["system_c_answer"]      = c_res.get("answer", "")
            row["system_c_sub_queries"] = json.dumps(c_res.get("sub_queries", []),
                                                     ensure_ascii=False)
            row["system_c_steps"]       = json.dumps(c_res.get("steps", []),
                                                     ensure_ascii=False)
            print("C", end=" ", flush=True)

        elapsed = time.time() - t0
        print(f"({elapsed:.1f}s)")

        # Save this row immediately (crash-safe)
        try:
            append_response_row(output_path, row)
        except Exception as save_exc:
            print(f"  WARNING: could not save row to xlsx: {save_exc}")

        all_response_rows.append(row)
        time.sleep(1)  # polite rate-limit pause

    print(f"\nAll answers collected. {len(all_response_rows)} new row(s) written to 3_Responses.")

    # LLM-as-judge
    if not args.no_judge:
        print("\nRunning LLM-as-judge scoring ...")
        # Load all response rows (including any from prior resume)
        import openpyxl
        wb = openpyxl.load_workbook(str(output_path), data_only=True)
        ws = wb["3_Responses"]
        hdrs = [str(ws.cell(1, c).value or "").strip().lower().replace(" ", "_")
                for c in range(1, ws.max_column + 1)]
        all_rows = []
        for r in ws.iter_rows(min_row=2, values_only=True):
            rec = dict(zip(hdrs, r))
            if rec.get("question_id"):
                all_rows.append(rec)

        sys_answer_keys = {
            "A": "system_a_answer",
            "B": "system_b_answer",
            "C": "system_c_answer",
        }
        systems_to_judge = ["A", "B", "C"] if not args.only_system else [args.only_system]

        score_rows: list[dict] = []
        total_calls = len(all_rows) * len(systems_to_judge)
        call_n = 0

        for row in all_rows:
            qid      = row.get("question_id", "")
            tier     = row.get("tier", "")
            tier_name = row.get("tier_name", "")
            q_text   = row.get("question_text", "")
            expected = row.get("expected_answer", "")

            for sys_label in systems_to_judge:
                call_n += 1
                answer = str(row.get(sys_answer_keys[sys_label], "") or "")
                judge_pct = int(call_n / total_calls * 100)
                print(f"  [{call_n:4}/{total_calls}] {judge_pct:3d}% {qid} System {sys_label} ...",
                      end=" ", flush=True)
                t0 = time.time()
                scores = judge_one(judge_client, q_text, expected, answer)
                elapsed = time.time() - t0
                print(f"total={scores['total']}/8  ({elapsed:.1f}s)")
                time.sleep(1)

                score_rows.append({
                    "question_id":            qid,
                    "tier":                   tier,
                    "tier_name":              tier_name,
                    "system":                 sys_label,
                    "factual_accuracy":       scores.get("factual_accuracy", 0),
                    "completeness":           scores.get("completeness", 0),
                    "confidence_calibration": scores.get("confidence_calibration", 0),
                    "total":                  scores.get("total", 0),
                    "rationale":              scores.get("rationale", ""),
                    "question_text":          q_text,
                    "expected_answer":        expected,
                    "system_answer":          answer,
                })

        write_scores_sheet(output_path, score_rows)
        write_summary_sheet(output_path, score_rows)
        print(f"\n4_Scores and 5_Summary sheets written.")

        # Quick stdout summary
        from collections import defaultdict
        by_sys: dict = defaultdict(list)
        for row in score_rows:
            by_sys[row["system"]].append(row["total"])
        print("\n── V2 Benchmark Results (Avg Total / 8) ─────────────")
        for s in sorted(by_sys):
            vals = by_sys[s]
            avg  = sum(vals) / len(vals) if vals else 0
            print(f"  System {s}: {avg:.2f}  (n={len(vals)})")
        print("──────────────────────────────────────────────────────")

        # Per-tier breakdown
        print("\n── Per-Tier Breakdown ───────────────────────────────")
        tier_names = {
            "1": "T1 Factual", "2": "T2 Applied",
            "3": "T3 Procedural", "4": "T4 Multi-hop", "5": "T5 Adversarial",
        }
        by_tier_sys: dict = defaultdict(list)
        for row in score_rows:
            by_tier_sys[(str(row.get("tier", "")), row.get("system", ""))].append(row["total"])
        tiers = sorted({str(r.get("tier", "")) for r in score_rows},
                       key=lambda x: int(x) if x.isdigit() else 99)
        print(f"  {'Tier':<18}  {'Sys A':>8}  {'Sys B':>8}  {'Sys C':>8}")
        for t in tiers:
            row_vals = []
            for s in ["A", "B", "C"]:
                vals = by_tier_sys.get((t, s), [])
                avg  = sum(vals) / len(vals) if vals else 0.0
                row_vals.append(f"{avg:.2f}")
            tn = tier_names.get(t, f"T{t}")
            print(f"  {tn:<18}  {row_vals[0]:>8}  {row_vals[1]:>8}  {row_vals[2]:>8}")
        print("──────────────────────────────────────────────────────")

    print(f"\nDone. Output file: {output_path}")
    print(f"      Log saved to: {_log_path}")

    # Restore stdout and close log file cleanly
    logger.close()
    sys.stdout = sys.__stdout__


def _main_safe():
    """Entry point that guarantees logger teardown even on unexpected exceptions."""
    # logger is set up inside main(); if main() raises before that, nothing to close.
    # If it raises after, main()'s own try/finally would be cleaner, but keeping
    # all logic in main() avoids restructuring hundreds of lines.
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        # Restore stdout so the traceback prints to the real terminal
        if not isinstance(sys.stdout, TeeLogger):
            raise
        tee = sys.stdout
        sys.stdout = sys.__stdout__
        tee.close()
        raise


if __name__ == "__main__":
    _main_safe()
