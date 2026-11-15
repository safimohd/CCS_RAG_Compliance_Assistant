"""
Healthcare Benchmark Evaluation Script
=======================================
Reads from Healthcare_Benchmark_Questions_v1.xlsx (sheet: Healthcare Benchmark Questions),
runs all 50 questions through four systems, writes answers back into the SAME file,
then scores with DeepSeek-V4.1-Flash as LLM-as-judge.

Systems
-------
  A  Base LLM        gpt-5-nano, no retrieval context
  B  Plain RAG       gpt-5-nano + ChromaDB with HyDE retrieval
  C  Agentic RAG     gpt-5-nano + query decomposition + multi-hop reflection
  D  CV-RAG          generate candidate answer, extract discrete claims,
                     verify each claim against ChromaDB, synthesise corrected answer

Scoring rubric (8 points total)
---------------------------------
  Factual Accuracy      0-4
  Completeness          0-2
  Confidence Calibration 0-2
  Total                 0-8

Output files
------------
  Healthcare_Benchmark_Questions_v1.xlsx     <- answers written back (in-place)
  Healthcare_Benchmark_Evaluation_v1.xlsx    <- one row per (question, system)

Usage
-----
  python run_healthcare_evaluation.py \\
    --benchmark /path/to/Healthcare_Benchmark_Questions_v1.xlsx

  python run_healthcare_evaluation.py \\
    --benchmark /path/to/Healthcare_Benchmark_Questions_v1.xlsx --limit 3

  python run_healthcare_evaluation.py \\
    --benchmark /path/to/Healthcare_Benchmark_Questions_v1.xlsx --no-judge

  python run_healthcare_evaluation.py \\
    --benchmark /path/to/Healthcare_Benchmark_Questions_v1.xlsx --resume

  python run_healthcare_evaluation.py \\
    --benchmark /path/to/Healthcare_Benchmark_Questions_v1.xlsx --only-judge

Environment variables
---------------------
  OPENAI_API_KEY    gpt-5-nano answer model
  TOGETHER_API_KEY  DeepSeek judge model
  LLM_MODEL         default gpt-5-nano
  JUDGE_MODEL       default deepseek-ai/DeepSeek-V4.1-Flash
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

try:
    from dotenv import load_dotenv
    load_dotenv()
    load_dotenv(Path(__file__).parent / ".env")
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

HERE         = Path(__file__).resolve().parent
APP_DIR      = HERE.parent
PROJECT_ROOT = APP_DIR.parent
sys.path.insert(0, str(APP_DIR))

LLM_MODEL      = os.getenv("LLM_MODEL", "gpt-5-nano")
JUDGE_MODEL    = os.getenv("JUDGE_MODEL", "deepseek-ai/DeepSeek-V4.1-Flash")
JUDGE_BASE_URL = "https://api.together.xyz/v1"
DOMAIN         = "Healthcare"

# ── System prompts ─────────────────────────────────────────────────────────────

SYSTEM_A_PROMPT = """\
You are an Indian healthcare regulatory compliance expert with deep knowledge of:
- Ayushman Bharat PM-JAY and AB-ArK (Karnataka) health insurance schemes
- Clinical Establishments (Registration and Regulation) Act 2010 (CEA)
- Telemedicine Practice Guidelines 2020 (MCI / NMC)
- National Medical Commission (NMC) Act 2019 and Ethics / Registration Regulations
- Ayushman Bharat Digital Mission (ABDM) framework

Answer the question from your training knowledge.

Guidelines:
- Cite the relevant Act, section, or guideline if you know it with confidence.
  Example: "Under Section 11(2) of the CEA 2010, ..."
  If you are unsure of the exact provision, describe the rule accurately without
  inventing a citation.
- Include specific statutory figures (timelines, penalty amounts, coverage limits)
  only if you are certain. Flag uncertainty honestly.
- If a premise in the question is false or misleading, correct it explicitly
  before answering.
- Address each sub-part of a multi-part question separately.

Structure every answer:
1. Direct answer (with provision citation if known with confidence).
2. Key figures or timelines (only if certain).
3. Compliance or practical takeaway for the stakeholder.
"""

SYSTEM_B_PROMPT = """\
You are an Indian healthcare regulatory compliance assistant.
You are given retrieved document chunks from a healthcare regulatory corpus
covering PM-JAY, CEA 2010, Telemedicine Guidelines 2020, NMC Ethics, and ABDM.

ENTITY CHECK (do this first):
  Identify which entity the question asks about (patient/beneficiary, hospital,
  doctor/RMP, insurer, platform, government authority).
  If retrieved passages address a DIFFERENT entity than the question asks about,
  do NOT rely on those passages. Answer from training knowledge and flag with
  "(inferred from training, not in retrieved sources)".

MANDATORY REQUIREMENTS:
1. If the retrieved text clearly identifies the controlling provision (Act + section),
   open with it in your first sentence. If not clearly stated, describe the rule
   accurately without inventing a citation.
2. Quote exact statutory figures verbatim from retrieved text. Do not round numbers.
3. Answer primarily from retrieved sources (after the entity check).
4. If sources do not fully settle the question, fill gaps from training knowledge
   flagged clearly with "(inferred from training, not in retrieved sources)".
5. Never invent section numbers or figures not in the sources or verified training.
6. Correct any false premise in the question before answering.

Structure every answer:
1. Direct answer with inline provision citation (only if in retrieved text).
2. Exact statutory figures quoted verbatim.
3. Each sub-part addressed separately (if multi-part).
4. Sources relied on — cited by document TITLE, never by filename.
5. Compliance or practical takeaway.
"""

SYSTEM_C_PROMPT = SYSTEM_B_PROMPT

HYDE_SYSTEM = """\
You are an expert on Indian healthcare regulations including PM-JAY, CEA 2010,
Telemedicine Guidelines 2020, NMC Ethics, and ABDM.
Write a single short paragraph (2-4 sentences) exactly as it would appear in an
official Indian healthcare regulatory document, circular, or guideline, that
directly answers the question below. Use the same terminology, section references,
and phrasing found in official sources. Write ONLY the hypothetical passage with
no preamble, explanation, or quotation marks.
"""

JUDGE_SYSTEM = """\
You are an expert evaluator for an Indian healthcare regulatory compliance Q&A system
covering PM-JAY, CEA 2010, Telemedicine Guidelines 2020, NMC Ethics, and ABDM.

Score the SYSTEM ANSWER against the EXPECTED ANSWER and QUESTION on three dimensions.
Be strict; do not reward confident wrong answers.

RUBRIC
------
Factual Accuracy (0-4)
  4 = All key facts correct, no hallucinations, provisions cited correctly.
  3 = Mostly correct with minor factual gaps or small inaccuracies.
  2 = Partially correct; some material facts wrong or missing.
  1 = Mostly incorrect; fundamental misunderstanding of the regulation.
  0 = Completely wrong, no relevant content, or answer is an error message.

Completeness (0-2)
  2 = Covers all key aspects: legal provision, compliance requirement, practical impact.
  1 = Covers some aspects but misses important ones.
  0 = Very incomplete or single-dimensional.

Confidence Calibration (0-2)
  2 = Appropriate uncertainty expressed; hedges on draft/proposed rules; no overconfidence.
  1 = Partially calibrated: some overconfidence or underconfidence.
  0 = Significantly overconfident (states wrong facts as certain) or uselessly hedged.

Return ONLY valid JSON, no markdown, no preamble:
{
  "factual_accuracy":        <integer 0-4>,
  "completeness":            <integer 0-2>,
  "confidence_calibration":  <integer 0-2>,
  "total":                   <integer 0-8>,
  "rationale":               "<1-2 sentences>"
}
"""

# ── ChromaDB loader ────────────────────────────────────────────────────────────

def load_chromadb_healthcare():
    from langchain_huggingface import HuggingFaceEmbeddings
    from langchain_chroma import Chroma
    # Try healthcare-specific index first, fall back to shared index
    for index_name in ("chroma_db_healthcare", "chroma_db"):
        index_dir = APP_DIR / index_name
        if index_dir.exists():
            print(f"[ChromaDB] Loading index from {index_dir}")
            break
    else:
        sys.exit(
            "ERROR: No ChromaDB index found. "
            "Tried chroma_db_healthcare/ and chroma_db/ under 05_App/.\n"
            "Run: python ingest_corpus.py --domain healthcare"
        )
    emb = HuggingFaceEmbeddings(
        model_name="BAAI/bge-small-en-v1.5",
        encode_kwargs={"normalize_embeddings": True},
    )
    collection = "healthcare_corpus" if index_name == "chroma_db_healthcare" else "regulatory_corpus"
    return Chroma(
        collection_name=collection,
        embedding_function=emb,
        persist_directory=str(index_dir),
    )

# ── Retrieval ──────────────────────────────────────────────────────────────────

TIER_WEIGHT = {"A": 1.00, "B": 0.95, "C": 0.80, "D": 0.55, "E": 0.35}
TOP_K       = 6
CANDIDATE_K = 24


def retrieve_passages(store, query: str) -> list[dict]:
    """Authority-weighted retrieval with per-doc cap of 4 chunks."""
    try:
        space = ((store._collection.metadata or {}).get("hnsw:space") or "l2").lower()
    except Exception:
        space = "l2"

    # Try domain-filtered first; fall back to unfiltered if no results
    for filter_arg in [{"domain": DOMAIN}, None]:
        try:
            kw = {"k": CANDIDATE_K}
            if filter_arg:
                kw["filter"] = filter_arg
            results = store.similarity_search_with_relevance_scores(query, **kw)
            if results:
                break
        except Exception:
            results = []

    scored = []
    for doc, raw in results:
        md = doc.metadata or {}
        tier = md.get("tier", "C")
        if tier in ("D", "E"):
            continue
        cos = raw if space.startswith("cosine") else max(0.0, (raw + 1.0) / 2.0)
        weighted = cos * TIER_WEIGHT.get(tier, 0.80)
        scored.append({
            "content":     doc.page_content,
            "title":       md.get("title", "Unknown"),
            "tier":        tier,
            "source":      md.get("source_url", ""),
            "source_file": md.get("source_file", ""),
            "weighted":    weighted,
        })

    scored.sort(key=lambda x: x["weighted"], reverse=True)
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
    try:
        rsp = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": HYDE_SYSTEM},
                {"role": "user",   "content": question},
            ],
            max_completion_tokens=160,
        )
        h = rsp.choices[0].message.content.strip()
        return h if h else question
    except Exception:
        return question


# ── System A ──────────────────────────────────────────────────────────────────

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


# ── System B ──────────────────────────────────────────────────────────────────

def answer_system_b(client, store, question: str, retries: int = 3) -> str:
    hyde_query = generate_hyde_query(client, question)
    passages   = retrieve_passages(store, hyde_query)
    context    = passages_to_context(passages)
    for attempt in range(retries):
        try:
            rsp = client.chat.completions.create(
                model=LLM_MODEL,
                messages=[
                    {"role": "system", "content": SYSTEM_B_PROMPT},
                    {"role": "user",
                     "content": (
                         f"Domain: {DOMAIN}\n\n"
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


# ── System C ──────────────────────────────────────────────────────────────────

DECOMPOSE_SYSTEM = """\
You are a query analyser for an Indian healthcare regulatory compliance assistant.
Break a complex question into independent, focused sub-queries for document search.

Rules:
- Simple or atomic question: return it unchanged as a single sub-query.
- Multi-part or multi-step question: split into self-contained sub-queries.
- Do not invent questions the user did not ask.
- Return ONLY a JSON array of strings, nothing else.
"""

REFLECT_SYSTEM = """\
You are a quality-checker for an Indian healthcare regulatory compliance assistant.
Check whether the draft answer is sufficient. It is insufficient if it explicitly
says the corpus does not contain the information, or is under two sentences with
no citation.

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


def answer_system_c(client, store, question: str, tier: int = 3) -> dict:
    """
    Agentic RAG with HyDE sub-query retrieval + reflection.
    Always runs the full pipeline in eval mode (no tier routing).
    Returns dict with keys: answer, sub_queries, steps.
    """
    steps: list[str] = []

    # Step 1: decompose
    try:
        sub_queries = decompose_query(client, question)
    except Exception as exc:
        sub_queries = [question]
        steps.append(f"Decompose failed ({exc}), using original query")

    steps.append(f"Decomposed into {len(sub_queries)} sub-quer{'y' if len(sub_queries)==1 else 'ies'}: {sub_queries}")

    # Step 2: retrieve for each sub-query (HyDE for T1-T3, raw for T4-T5)
    use_hyde = tier <= 3
    seen_keys: set[str] = set()
    all_passages: list[dict] = []

    for sq in sub_queries:
        rq = generate_hyde_query(client, sq) if use_hyde else sq
        for p in retrieve_passages(store, rq):
            key = p["source_file"] + "||" + p["content"][:80]
            if key not in seen_keys:
                seen_keys.add(key)
                all_passages.append(p)

    steps.append(f"Retrieved {len(all_passages)} unique passages across all sub-queries")
    all_passages.sort(key=lambda x: x["weighted"], reverse=True)
    context = passages_to_context(all_passages[:TOP_K])

    # Step 3: draft answer
    try:
        draft = _llm_call(
            client, SYSTEM_C_PROMPT,
            f"Domain: {DOMAIN}\n\nRetrieved sources:\n{context}\n\nQuestion: {question}"
        )
    except Exception as exc:
        return {"answer": f"[ERROR-C: {exc}]", "sub_queries": sub_queries, "steps": steps}

    steps.append("Draft answer generated")

    # Step 4: reflect
    try:
        reflect_raw = _llm_call(client, REFLECT_SYSTEM,
                                f"Question: {question}\n\nDraft answer: {draft}")
        reflect = json.loads(reflect_raw)
    except Exception:
        reflect = {"sufficient": True}

    if not reflect.get("sufficient", True):
        refined = reflect.get("refined_query", question)
        steps.append(f"Reflection: insufficient. Refined query: {refined}")
        for p in retrieve_passages(store, refined):
            key = p["source_file"] + "||" + p["content"][:80]
            if key not in seen_keys:
                seen_keys.add(key)
                all_passages.append(p)
        all_passages.sort(key=lambda x: x["weighted"], reverse=True)
        context2 = passages_to_context(all_passages[:TOP_K])
        try:
            draft = _llm_call(
                client, SYSTEM_C_PROMPT,
                f"Domain: {DOMAIN}\n\nRetrieved sources:\n{context2}\n\nQuestion: {question}"
            )
            steps.append("Re-generated answer after reflection re-retrieval")
        except Exception as exc:
            steps.append(f"Re-generation failed: {exc}")
    else:
        steps.append("Reflection: answer sufficient, no re-retrieval needed")

    return {"answer": draft, "sub_queries": sub_queries, "steps": steps}


# ── System D: CV-RAG (Claim Verification RAG) ─────────────────────────────────

CLAIM_EXTRACT_SYSTEM = """\
You are a claim extractor for an Indian healthcare regulatory compliance assistant.

Given an answer to a healthcare regulatory question, extract every discrete factual
claim the answer makes. A claim is a single checkable assertion: a statutory figure,
a provision name, a right or obligation, a process step, a coverage rule.

Rules:
- One claim per line, plain text, no numbering.
- Keep each claim self-contained and verifiable without reading the rest of the answer.
- Omit meta-sentences like "I hope this helps" or "please consult a lawyer".
- Return ONLY the claims, one per line, nothing else.
"""

CLAIM_VERIFY_SYSTEM = """\
You are a claim verifier for an Indian healthcare regulatory compliance assistant.
You are given retrieved document chunks from a regulatory corpus and a single
factual claim from an answer.

Classify the claim as one of:
  SUPPORTED       - the retrieved chunks clearly support this claim
  CONTRADICTED    - the retrieved chunks clearly contradict this claim (state the correction)
  NOT_IN_CORPUS   - the claim cannot be verified from the retrieved chunks

Return ONLY valid JSON:
{
  "verdict": "SUPPORTED" | "CONTRADICTED" | "NOT_IN_CORPUS",
  "note": "<one sentence: what the corpus says, or why not found>"
}
"""

SYNTHESIS_SYSTEM = """\
You are an Indian healthcare regulatory compliance expert. You have:
  1. An original candidate answer to a question.
  2. A claim verification table showing which claims are SUPPORTED, CONTRADICTED,
     or NOT_IN_CORPUS from the regulatory document corpus.

Your task: write a corrected, final answer that:
  - Keeps and confidently states all SUPPORTED claims.
  - Removes or corrects all CONTRADICTED claims using the corpus-provided correction.
  - For NOT_IN_CORPUS claims, retains the content if it is well-established regulatory
    knowledge, but flags it with "(based on regulatory knowledge, not verified in corpus)".
  - Does NOT invent new information.
  - Remains helpful and complete.

Write only the final answer, no preamble.
"""


def answer_system_d(client, store, question: str, retries: int = 3) -> dict:
    """
    CV-RAG pipeline:
    1. Generate a candidate answer (System A style).
    2. Extract discrete claims from the candidate answer.
    3. Verify each claim independently against ChromaDB.
    4. Synthesise a corrected final answer.
    Returns dict: answer, claims_text, verdicts_text.
    """
    # Step 1: generate candidate answer
    candidate = answer_system_a(client, question)

    # Step 2: extract claims
    try:
        claims_raw = _llm_call(client, CLAIM_EXTRACT_SYSTEM,
                               f"Question: {question}\n\nAnswer: {candidate}")
        claims = [c.strip() for c in claims_raw.strip().splitlines() if c.strip()]
    except Exception as exc:
        claims = []

    if not claims:
        return {
            "answer":        candidate,
            "claims_text":   "[No claims extracted]",
            "verdicts_text": "[No verification performed]",
        }

    # Step 3: verify each claim against ChromaDB
    verdicts: list[dict] = []
    for claim in claims:
        passages = retrieve_passages(store, claim)
        context  = passages_to_context(passages) if passages else "(no passages retrieved)"
        try:
            verify_raw = _llm_call(
                client, CLAIM_VERIFY_SYSTEM,
                f"Retrieved sources:\n{context}\n\nClaim to verify: {claim}"
            )
            verdict = json.loads(verify_raw)
        except Exception:
            verdict = {"verdict": "NOT_IN_CORPUS", "note": "verification call failed"}
        verdicts.append({"claim": claim, **verdict})

    # Build human-readable claims and verdicts strings for the xlsx
    claims_text   = "\n".join(f"- {v['claim']}" for v in verdicts)
    verdicts_text = "\n".join(
        f"[{v['verdict']}] {v['claim']}: {v.get('note', '')}"
        for v in verdicts
    )

    # Step 4: synthesise corrected answer
    verification_table = "\n".join(
        f"Claim: {v['claim']}\nVerdict: {v['verdict']}\nNote: {v.get('note','')}"
        for v in verdicts
    )
    try:
        final_answer = _llm_call(
            client, SYNTHESIS_SYSTEM,
            f"Question: {question}\n\n"
            f"Candidate answer: {candidate}\n\n"
            f"Claim verification:\n{verification_table}"
        )
    except Exception as exc:
        final_answer = f"[ERROR-D synthesis: {exc}]\n\nCandidate:\n{candidate}"

    return {
        "answer":        final_answer,
        "claims_text":   claims_text,
        "verdicts_text": verdicts_text,
    }


# ── Load benchmark ─────────────────────────────────────────────────────────────

def load_benchmark(xlsx_path: Path) -> list[dict]:
    import openpyxl
    wb = openpyxl.load_workbook(str(xlsx_path), data_only=True)
    sheet_name = "Healthcare Benchmark Questions"
    if sheet_name not in wb.sheetnames:
        # Try first sheet as fallback
        sheet_name = wb.sheetnames[0]
        print(f"[warn] Expected sheet '{sheet_name}' not found. Using '{sheet_name}'.")
    ws = wb[sheet_name]
    headers = [str(ws.cell(1, c).value or "").strip() for c in range(1, ws.max_column + 1)]
    rows = []
    for r in ws.iter_rows(min_row=2, values_only=True):
        rec = {h: (v if v is not None else "") for h, v in zip(headers, r)}
        if rec.get("Question Id"):
            rows.append(rec)
    return rows


def already_answered(row: dict, system: str) -> bool:
    """Return True if this row already has a non-empty answer for the given system."""
    col = {
        "A": "System A Answer",
        "B": "System B Answer",
        "C": "System C Answer",
        "D": "System D Answer",
    }[system]
    val = str(row.get(col, "")).strip()
    return bool(val) and not val.startswith("[ERROR")


# ── Write answers back into xlsx ───────────────────────────────────────────────

COL_MAP = {
    "System A Answer":          7,
    "System B Answer":          8,
    "System C Answer":          9,
    "System C Sub Queries":     10,
    "System C Steps":           11,
    "System D Answer":          12,
    "System D Claims":          13,
    "System D Claim Verdicts":  14,
}


def write_answer_to_xlsx(xlsx_path: Path, row_idx: int, updates: dict[str, str]) -> None:
    """
    Writes answer fields for one row in-place. row_idx is 1-based data row
    (row 2 = first data row in the sheet).
    """
    import openpyxl
    from openpyxl.styles import Alignment
    wb = openpyxl.load_workbook(str(xlsx_path))
    ws = wb["Healthcare Benchmark Questions"]
    wrap = Alignment(wrap_text=True, vertical="top")
    sheet_row = row_idx + 1  # +1 for header
    for col_name, value in updates.items():
        col_idx = COL_MAP.get(col_name)
        if col_idx:
            cell = ws.cell(row=sheet_row, column=col_idx, value=str(value))
            cell.alignment = wrap
    wb.save(str(xlsx_path))


# ── LLM-as-judge scoring ──────────────────────────────────────────────────────

def judge_answer(judge_client, question: str, expected: str,
                 system_answer: str, retries: int = 4) -> dict:
    if not system_answer or system_answer.startswith("[ERROR"):
        return {
            "factual_accuracy": 0, "completeness": 0,
            "confidence_calibration": 0, "total": 0,
            "rationale": "Answer was an error or empty."
        }
    prompt = (
        f"QUESTION:\n{question}\n\n"
        f"EXPECTED ANSWER:\n{expected}\n\n"
        f"SYSTEM ANSWER:\n{system_answer}"
    )
    for attempt in range(retries):
        try:
            rsp = judge_client.chat.completions.create(
                model=JUDGE_MODEL,
                messages=[
                    {"role": "system", "content": JUDGE_SYSTEM},
                    {"role": "user",   "content": prompt},
                ],
                response_format={"type": "json_object"},
            )
            raw = rsp.choices[0].message.content.strip()
            # Strip markdown fences if present
            raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
            raw = re.sub(r"\s*```$", "", raw)
            data = json.loads(raw)
            fa  = int(data.get("factual_accuracy", 0))
            com = int(data.get("completeness", 0))
            cal = int(data.get("confidence_calibration", 0))
            return {
                "factual_accuracy": fa,
                "completeness": com,
                "confidence_calibration": cal,
                "total": fa + com + cal,
                "rationale": str(data.get("rationale", "")),
            }
        except Exception as exc:
            if attempt < retries - 1:
                time.sleep(8 * (attempt + 1))
            else:
                return {
                    "factual_accuracy": 0, "completeness": 0,
                    "confidence_calibration": 0, "total": 0,
                    "rationale": f"Judge call failed: {exc}",
                }
    return {"factual_accuracy": 0, "completeness": 0,
            "confidence_calibration": 0, "total": 0, "rationale": "judge error"}


# ── Build evaluation xlsx ─────────────────────────────────────────────────────

def build_evaluation_xlsx(rows: list[dict], scores: list[dict], out_path: Path) -> None:
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Scores"

    headers = [
        "Question Id", "Domain", "Tier", "Question Type", "System",
        "Factual Accuracy", "Completeness", "Confidence Calibration", "Total",
        "Rationale", "Question Text", "Expected Answer", "System Answer"
    ]

    hfont = Font(bold=True, color="FFFFFF")
    hfill = PatternFill("solid", fgColor="1F4E79")
    wrap  = Alignment(wrap_text=True, vertical="top")
    thin  = Side(style="thin", color="BFBFBF")
    bdr   = Border(left=thin, right=thin, top=thin, bottom=thin)

    sys_fills = {
        "A": PatternFill("solid", fgColor="DDEEFF"),
        "B": PatternFill("solid", fgColor="DDFFDD"),
        "C": PatternFill("solid", fgColor="FFFACC"),
        "D": PatternFill("solid", fgColor="FFE5CC"),
    }

    for ci, h in enumerate(headers, 1):
        c = ws.cell(row=1, column=ci, value=h)
        c.font = hfont; c.fill = hfill; c.alignment = wrap; c.border = bdr

    for ri, s in enumerate(scores, 2):
        vals = [
            s["question_id"], s["domain"], s["tier"], s["question_type"], s["system"],
            s["factual_accuracy"], s["completeness"], s["confidence_calibration"], s["total"],
            s["rationale"], s["question_text"], s["expected_answer"], s["system_answer"],
        ]
        for ci, v in enumerate(vals, 1):
            c = ws.cell(row=ri, column=ci, value=v)
            c.fill = sys_fills.get(s["system"], PatternFill())
            c.alignment = wrap; c.border = bdr

    col_widths = [14,12,6,14,8,16,12,22,8,50,55,55,55]
    for ci, w in enumerate(col_widths, 1):
        ws.column_dimensions[get_column_letter(ci)].width = w
    for ri in range(2, len(scores)+2):
        ws.row_dimensions[ri].height = 60
    ws.freeze_panes = "A2"

    # Summary sheet
    ws2 = wb.create_sheet("Summary")
    ws2["A1"] = "Healthcare Benchmark Summary"
    ws2["A1"].font = Font(bold=True, size=14)

    systems = ["A", "B", "C", "D"]
    tiers   = [1, 2, 3, 4, 5]

    ws2["A3"] = "System"
    ws2["B3"] = "Avg Factual Acc"
    ws2["C3"] = "Avg Completeness"
    ws2["D3"] = "Avg Conf Cal"
    ws2["E3"] = "Avg Total"
    ws2["F3"] = "N"

    for ri, sys in enumerate(systems, 4):
        sys_scores = [s for s in scores if s["system"] == sys and isinstance(s["total"], (int, float))]
        n = len(sys_scores)
        ws2.cell(ri, 1, sys)
        ws2.cell(ri, 2, round(sum(s["factual_accuracy"] for s in sys_scores)/n, 2) if n else "")
        ws2.cell(ri, 3, round(sum(s["completeness"] for s in sys_scores)/n, 2) if n else "")
        ws2.cell(ri, 4, round(sum(s["confidence_calibration"] for s in sys_scores)/n, 2) if n else "")
        ws2.cell(ri, 5, round(sum(s["total"] for s in sys_scores)/n, 2) if n else "")
        ws2.cell(ri, 6, n)

    ws2["A10"] = "By Tier"
    ws2["A11"] = "Tier"
    for ci, sys in enumerate(systems, 2):
        ws2.cell(11, ci, f"Sys {sys} Avg")

    for ti, t in enumerate(tiers, 12):
        ws2.cell(ti, 1, f"T{t}")
        for ci, sys in enumerate(systems, 2):
            t_scores = [s for s in scores if s["system"] == sys and s["tier"] == t and isinstance(s["total"], (int, float))]
            n = len(t_scores)
            ws2.cell(ti, ci, round(sum(s["total"] for s in t_scores)/n, 2) if n else "")

    for col in ["A","B","C","D","E","F"]:
        ws2.column_dimensions[col].width = 20

    wb.save(str(out_path))
    print(f"\n[Evaluation] Saved: {out_path}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Healthcare 4-system benchmark evaluation")
    parser.add_argument("--benchmark",  required=True, help="Path to Healthcare_Benchmark_Questions_v1.xlsx")
    parser.add_argument("--limit",      type=int, default=None, help="Only process first N questions (smoke test)")
    parser.add_argument("--no-judge",   action="store_true",  help="Skip scoring, only collect answers")
    parser.add_argument("--only-judge", action="store_true",  help="Skip answer collection, only run judge on existing answers")
    parser.add_argument("--resume",     action="store_true",  help="Skip questions that already have answers")
    parser.add_argument("--systems",    default="A,B,C,D",    help="Comma-separated systems to run (default: A,B,C,D)")
    args = parser.parse_args()

    xlsx_path = Path(args.benchmark)
    if not xlsx_path.exists():
        sys.exit(f"ERROR: benchmark file not found: {xlsx_path}")

    systems_to_run = [s.strip().upper() for s in args.systems.split(",")]
    print(f"[Config] Benchmark: {xlsx_path}")
    print(f"[Config] Systems:   {systems_to_run}")
    print(f"[Config] LLM model: {LLM_MODEL}")
    print(f"[Config] Judge:     {JUDGE_MODEL}")
    print(f"[Config] Resume:    {args.resume}")
    print(f"[Config] Limit:     {args.limit or 'all'}")

    # ── OpenAI client ──────────────────────────────────────────────────────────
    from openai import OpenAI
    openai_key = os.getenv("OPENAI_API_KEY")
    if not openai_key:
        sys.exit("ERROR: OPENAI_API_KEY not set.")
    openai_client = OpenAI(api_key=openai_key)

    # ── Load benchmark ─────────────────────────────────────────────────────────
    rows = load_benchmark(xlsx_path)
    if args.limit:
        rows = rows[:args.limit]
    print(f"[Benchmark] Loaded {len(rows)} questions")

    # ── ChromaDB (not needed for System A alone) ───────────────────────────────
    needs_store = any(s in systems_to_run for s in ["B", "C", "D"])
    store = None
    if needs_store and not args.only_judge:
        print("[ChromaDB] Loading Healthcare index...")
        store = load_chromadb_healthcare()
        print("[ChromaDB] Ready.")

    # ── Phase 1: collect answers ───────────────────────────────────────────────
    if not args.only_judge:
        for idx, row in enumerate(rows, 1):
            qid  = row["Question Id"]
            q    = row["Question Text"]
            tier = int(row.get("Tier", 3) or 3)
            print(f"\n[{idx}/{len(rows)}] {qid} | {row.get('Question Type','')} | T{tier}")
            print(f"  Q: {q[:90]}...")

            updates: dict[str, str] = {}

            if "A" in systems_to_run and (not args.resume or not already_answered(row, "A")):
                print("  [A] answering...")
                ans_a = answer_system_a(openai_client, q)
                updates["System A Answer"] = ans_a
                row["System A Answer"]     = ans_a

            if "B" in systems_to_run and (not args.resume or not already_answered(row, "B")):
                print("  [B] answering...")
                ans_b = answer_system_b(openai_client, store, q)
                updates["System B Answer"] = ans_b
                row["System B Answer"]     = ans_b

            if "C" in systems_to_run and (not args.resume or not already_answered(row, "C")):
                print("  [C] answering...")
                res_c = answer_system_c(openai_client, store, q, tier=tier)
                updates["System C Answer"]      = res_c["answer"]
                updates["System C Sub Queries"] = json.dumps(res_c["sub_queries"])
                updates["System C Steps"]       = "\n".join(res_c["steps"])
                row["System C Answer"]          = res_c["answer"]

            if "D" in systems_to_run and (not args.resume or not already_answered(row, "D")):
                print("  [D] answering (CV-RAG)...")
                res_d = answer_system_d(openai_client, store, q)
                updates["System D Answer"]          = res_d["answer"]
                updates["System D Claims"]          = res_d["claims_text"]
                updates["System D Claim Verdicts"]  = res_d["verdicts_text"]
                row["System D Answer"]              = res_d["answer"]

            if updates:
                write_answer_to_xlsx(xlsx_path, idx, updates)
                print(f"  [saved] row {idx} written back to xlsx")

        print(f"\n[Phase 1 complete] All answers written to {xlsx_path}")

    # ── Phase 2: judge scoring ─────────────────────────────────────────────────
    if args.no_judge:
        print("[Judge] Skipped (--no-judge flag).")
        return

    together_key = os.getenv("TOGETHER_API_KEY")
    if not together_key:
        print("[Judge] TOGETHER_API_KEY not set. Skipping scoring.")
        print("        Set it and re-run with --only-judge to score existing answers.")
        return

    from openai import OpenAI as _OAI
    judge_client = _OAI(api_key=together_key, base_url=JUDGE_BASE_URL)

    # Reload rows from file (may have been updated above)
    rows = load_benchmark(xlsx_path)
    if args.limit:
        rows = rows[:args.limit]

    scores: list[dict] = []
    sys_col = {
        "A": "System A Answer",
        "B": "System B Answer",
        "C": "System C Answer",
        "D": "System D Answer",
    }

    total_pairs = len(rows) * len(systems_to_run)
    done = 0
    for row in rows:
        qid      = row["Question Id"]
        q        = row["Question Text"]
        expected = row["Expected Answer"]
        tier_val = row.get("Tier", "")
        try:
            tier_int = int(str(tier_val).strip())
        except Exception:
            tier_int = 0

        for sys in systems_to_run:
            done += 1
            sys_ans = str(row.get(sys_col[sys], "") or "").strip()
            print(f"  [Judge {done}/{total_pairs}] {qid} | System {sys} ...", end=" ", flush=True)
            score = judge_answer(judge_client, q, expected, sys_ans)
            print(f"Total: {score['total']}/8")
            scores.append({
                "question_id":           qid,
                "domain":                row.get("Domain", DOMAIN),
                "tier":                  tier_int,
                "question_type":         row.get("Question Type", ""),
                "system":                sys,
                "factual_accuracy":      score["factual_accuracy"],
                "completeness":          score["completeness"],
                "confidence_calibration":score["confidence_calibration"],
                "total":                 score["total"],
                "rationale":             score["rationale"],
                "question_text":         q,
                "expected_answer":       expected,
                "system_answer":         sys_ans,
            })

    # Save evaluation file alongside benchmark file
    eval_path = xlsx_path.parent / "Healthcare_Benchmark_Evaluation_v1.xlsx"
    build_evaluation_xlsx(rows, scores, eval_path)

    # Print summary table
    print("\n── Score Summary ──────────────────────────────────────────")
    print(f"{'System':<8} {'Avg FA':>8} {'Avg Com':>9} {'Avg Cal':>9} {'Avg Total':>10} {'N':>5}")
    print("-" * 55)
    for sys in systems_to_run:
        ss = [s for s in scores if s["system"] == sys]
        n  = len(ss)
        if n:
            fa  = sum(s["factual_accuracy"] for s in ss) / n
            co  = sum(s["completeness"] for s in ss) / n
            ca  = sum(s["confidence_calibration"] for s in ss) / n
            tot = sum(s["total"] for s in ss) / n
            print(f"  {sys:<6} {fa:>8.2f} {co:>9.2f} {ca:>9.2f} {tot:>10.2f} {n:>5}")
    print(f"\n[Done] Evaluation file: {eval_path}")


if __name__ == "__main__":
    main()
