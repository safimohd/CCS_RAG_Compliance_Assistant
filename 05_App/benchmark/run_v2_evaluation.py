"""
V2 Benchmark Evaluation Script
================================
Reads from Benchmark_RERA_V2_UserJourney_50Q.xlsx (1_Benchmark sheet),
runs all 50 questions through three systems, writes answers back into
the 3_Responses sheet of the SAME file, and auto-judges with LLM-as-judge.

Systems
-------
  A  Base LLM        gpt-5-nano, no retrieval context
  B  Plain RAG       gpt-5-nano + ChromaDB (improved per-doc cap + markers)
  C  Agentic RAG     gpt-5-nano + query decomposition + multi-hop reflection

Key design choices (different from V1)
---------------------------------------
  * Standalone — does NOT import from run_benchmark.py
  * Reads from and writes back to the V2 benchmark xlsx directly
  * Incremental save: writes each row to 3_Responses as soon as it completes
  * Resume: skips question_ids already present in 3_Responses
  * Systems B/C use a HYBRID system prompt: retrieve first, fill gaps
    with training knowledge and flag clearly (addresses Prof's suggestion
    that strict "never fill from knowledge" penalised honest uncertainty)
  * System A uses a pure-LLM prompt (no retrieval context at all)
  * LLM-as-judge runs immediately after all 50 answers are collected
  * Final output: 3_Responses sheet fully populated + 4_Scores sheet added
  * JUDGE_MODEL is separate from LLM_MODEL so a different model/provider
    can score the answers (avoids grading-own-homework bias)
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
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

# ── dotenv (optional) ─────────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
    load_dotenv(Path(__file__).parent.parent / ".env")  # also try 05_App/.env
except ImportError:
    pass

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

Answer the question from your training knowledge. Be specific:
- Cite exact section numbers and act names where you know them.
- Quote figures (percentages, timelines, penalties) with their source provision.
- If a premise in the question is false or misleading, correct it before answering.
- Flag genuine uncertainty honestly rather than guessing.

Structure every answer:
1. Direct answer (one clear statement).
2. Legal provision (Act / Section / Rule).
3. Key compliance takeaway for the stakeholder asking.
"""

SYSTEM_B_PROMPT = """\
You are an Indian regulatory compliance assistant for RERA and related regulations.

You are given retrieved document chunks from a regulatory corpus. Your job:
1. Answer the question primarily from the retrieved sources.
2. If the retrieved sources do not fully settle the question, draw on your
   training knowledge to fill the gap — but FLAG every such inference clearly
   with "(inferred from training, not in retrieved sources)".
3. If a premise in the question is false, correct it before answering.
4. Never invent section numbers, figures or citations not found in the sources
   or in your verified training knowledge.

Structure every answer:
1. Direct answer.
2. Sources relied on — cited by document TITLE (never a filename).
3. Regulatory provision — section, rule, clause.
4. Compliance takeaway.
"""

SYSTEM_C_PROMPT = SYSTEM_B_PROMPT  # Agentic RAG uses the same hybrid prompt

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


# ── System B — Plain RAG ───────────────────────────────────────────────────────

def answer_system_b(client, store, question: str, domain: str = "RERA",
                    retries: int = 3) -> str:
    # First try the proper rag_query.query_rag if available
    try:
        from rag_query import query_rag
        result = query_rag(question, domain=domain, store=store, client=client)
        return result.get("answer", str(result))
    except Exception:
        pass  # Fall through to inline implementation

    # Inline implementation with the hybrid prompt
    passages = retrieve_passages(store, question, domain)
    context  = passages_to_context(passages)

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


def answer_system_c(client, store, question: str, domain: str = "RERA") -> dict:
    """
    Agentic RAG:
      1. Decompose question into sub-queries
      2. Retrieve for each sub-query (de-duplicated)
      3. Generate first-pass answer
      4. Reflect — if insufficient, re-retrieve with refined query
      5. Return final answer + trace
    """
    steps: list[str] = []

    # Step 1: decompose
    try:
        sub_queries = decompose_query(client, question)
    except Exception as exc:
        sub_queries = [question]
        steps.append(f"Decompose failed ({exc}), using original query")

    steps.append(f"Decomposed into {len(sub_queries)} sub-quer{'y' if len(sub_queries)==1 else 'ies'}: {sub_queries}")

    # Step 2: retrieve for each sub-query
    seen_keys: set[str] = set()
    all_passages: list[dict] = []
    for sq in sub_queries:
        for p in retrieve_passages(store, sq, domain):
            key = p["content"][:100]
            if key not in seen_keys:
                seen_keys.add(key)
                all_passages.append(p)

    steps.append(f"Retrieved {len(all_passages)} unique passages across {len(sub_queries)} sub-queries")

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

    # Step 4: reflect
    try:
        reflect_raw  = _llm_call(client, REFLECT_SYSTEM,
                                  f"Original question: {question}\n\nDraft answer:\n{draft}")
        reflection   = json.loads(reflect_raw)
    except Exception:
        reflection = {"sufficient": True}

    final_answer = draft

    if not reflection.get("sufficient", True):
        refined = reflection.get("refined_query", question)
        steps.append(f"Reflection: insufficient. Re-querying: '{refined}'")

        for p in retrieve_passages(store, refined, domain):
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
            steps.append("Generated final answer after re-retrieval")
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

    import re
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
    args = parser.parse_args()

    bench_path = args.benchmark.resolve()
    if not bench_path.exists():
        sys.exit(f"ERROR: Benchmark file not found: {bench_path}")

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

    print(f"V2 Evaluation starting — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Benchmark:   {bench_path}")
    print(f"Answer model: {LLM_MODEL}")
    print(f"Judge model:  {JUDGE_MODEL} (Together AI)")

    # Load questions
    questions = load_benchmark(bench_path)
    if args.tiers:
        questions = [q for q in questions
                     if int(q.get("tier", 0)) in args.tiers]
    if args.limit:
        questions = questions[:args.limit]
    print(f"Questions: {len(questions)}")

    # Load ChromaDB
    print("Loading ChromaDB index ...")
    store = load_chromadb()
    print("  ChromaDB loaded.")

    # Resume: load already-done question IDs
    done_ids: set[str] = set()
    if args.resume:
        done_ids = load_done_ids(bench_path)
        print(f"Resume mode: {len(done_ids)} question(s) already done, skipping them.")
    else:
        # Reinitialise the response sheet (clears any old data)
        print("Initialising 3_Responses sheet ...")
        initialise_responses_sheet(bench_path)

    # Main loop
    total = len(questions)
    all_response_rows: list[dict] = []

    for idx, q in enumerate(questions, 1):
        qid  = str(q.get("question_id", ""))
        tier = q.get("tier", "")
        tier_name = q.get("tier_name", "")
        text = str(q.get("question_text", ""))
        expected = str(q.get("expected_answer", ""))

        if qid in done_ids:
            print(f"[{idx:3}/{total}] {qid} — SKIP (already done)")
            continue

        print(f"[{idx:3}/{total}] {qid} T{tier} ...", end=" ", flush=True)
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
            c_res = answer_system_c(client, store, text)
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
            append_response_row(bench_path, row)
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
        wb = openpyxl.load_workbook(str(bench_path), data_only=True)
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
                print(f"  [{call_n:4}/{total_calls}] {qid} System {sys_label} ...",
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

        write_scores_sheet(bench_path, score_rows)
        write_summary_sheet(bench_path, score_rows)
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

    print(f"\nDone. Benchmark file updated: {bench_path}")


if __name__ == "__main__":
    main()
