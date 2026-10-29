"""
rerun_missing_answers.py

Finds every (question_id, system) pair in the answers file where the system
answer is an [ERROR ...], regenerates those answers, scores them with the
LLM judge, and writes two updated output files:

  benchmark/results/benchmark_answers_<ts>_fixed.xlsx     (full answers, errors replaced)
  benchmark/results/benchmark_evaluation_<ts>_fixed.xlsx  (full evaluation, zeros replaced)

Run from 05_App/benchmark/:
    python rerun_missing_answers.py

OPENAI_API_KEY must be in the environment (loaded from benchmark/.env or 05_App/.env).
ChromaDB index must exist at 05_App/chroma_db/.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

HERE    = Path(__file__).resolve().parent   # benchmark/
APP_DIR = HERE.parent                        # 05_App/
sys.path.insert(0, str(APP_DIR))

try:
    from dotenv import load_dotenv
    load_dotenv(HERE / ".env")
    load_dotenv(APP_DIR / ".env", override=True)
except ImportError:
    pass

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

RESULTS_DIR = HERE / "results"
RESULTS_DIR.mkdir(exist_ok=True)

LLM_MODEL = os.getenv("LLM_MODEL", "gpt-5-nano")

ANSWERS_FILE = sorted(RESULTS_DIR.glob("benchmark_answers_*.xlsx"))[-1]
EVAL_FILE    = sorted(RESULTS_DIR.glob("benchmark_evaluation_*.xlsx"))[-1]


# ── Judge prompt (same as evaluate_benchmark.py) ──────────────────────────────
JUDGE_SYSTEM = """\
You are an expert evaluator for an Indian regulatory compliance Q&A system.
Score the SYSTEM ANSWER against the EXPECTED ANSWER and the QUESTION on three
dimensions using the rubric below. Be strict and objective.

RUBRIC
------
Factual Accuracy (0-4)
  4 = All key facts correct, no hallucinations, sections/penalties cited correctly.
  3 = Mostly correct with minor factual gaps or small inaccuracies.
  2 = Partially correct; some material facts wrong or missing.
  1 = Mostly incorrect; fundamental misunderstanding of the regulation.
  0 = Completely wrong or no relevant content.

Completeness (0-2)
  2 = Covers all key aspects: legal provision, compliance requirement, practical impact.
  1 = Covers some aspects but misses important ones.
  0 = Very incomplete or single-dimensional.

Confidence Calibration (0-2)
  2 = Uncertainty expressed appropriately; no overconfidence where knowledge is thin;
      hedges on draft/proposed rules; does not claim certainty on unclear areas.
  1 = Partially calibrated — some overconfidence or underconfidence.
  0 = Significantly overconfident (states wrong facts as certain) or so hedged
      as to be useless.

Return ONLY valid JSON — no markdown, no preamble:
{
  "factual_accuracy":        <integer 0-4>,
  "completeness":            <integer 0-2>,
  "confidence_calibration":  <integer 0-2>,
  "total":                   <integer 0-8>,
  "rationale":               "<1-2 sentences>"
}
"""


# ── Helpers ───────────────────────────────────────────────────────────────────
def load_sheet(path: Path) -> tuple[list[str], list[dict]]:
    wb = openpyxl.load_workbook(str(path), data_only=True)
    ws = wb.active
    headers = [str(ws.cell(1, c).value or "") for c in range(1, ws.max_column + 1)]
    rows = []
    for r in ws.iter_rows(min_row=2, values_only=True):
        rows.append(dict(zip(headers, r)))
    return headers, rows


def save_answers(headers: list[str], rows: list[dict], out_path: Path) -> None:
    thin = Side(style="thin", color="AAAAAA")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    hdr_fill = PatternFill("solid", fgColor="1F4E79")
    domain_fills = {
        "Healthcare": PatternFill("solid", fgColor="E2EFDA"),
        "RERA":       PatternFill("solid", fgColor="DDEEFF"),
    }
    col_widths = {
        "Question Id": 14, "Domain": 12, "Tier": 6,
        "Question Text": 55, "Expected Answer": 70,
        "System A Answer": 70, "System B Answer": 70, "System C Answer": 70,
        "System C Sub Queries": 40, "System C Steps": 40,
    }
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Results"
    for ci, h in enumerate(headers, 1):
        cell = ws.cell(1, ci, h)
        cell.font = Font(bold=True, color="FFFFFF", name="Calibri", size=10)
        cell.fill = hdr_fill
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = border
        ws.column_dimensions[get_column_letter(ci)].width = col_widths.get(h, 20)
    ws.row_dimensions[1].height = 25
    for ri, row in enumerate(rows, 2):
        fill = domain_fills.get(str(row.get("Domain", "")), PatternFill())
        for ci, h in enumerate(headers, 1):
            val = row.get(h, "")
            cell = ws.cell(ri, ci, val)
            cell.font = Font(name="Calibri", size=9)
            cell.fill = fill
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            cell.border = border
        ws.row_dimensions[ri].height = 80
    ws.freeze_panes = "A2"
    wb.save(str(out_path))
    print(f"Saved answers: {out_path}")


def save_eval(headers: list[str], rows: list[dict], out_path: Path) -> None:
    thin = Side(style="thin", color="BBBBBB")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    hdr_fill = PatternFill("solid", fgColor="1F4E79")
    sys_fills = {
        "A": PatternFill("solid", fgColor="FFF2CC"),
        "B": PatternFill("solid", fgColor="DDEEFF"),
        "C": PatternFill("solid", fgColor="E2EFDA"),
    }
    col_widths = {
        "Question Id": 14, "Domain": 12, "Tier": 6, "System": 8,
        "Factual Accuracy": 10, "Completeness": 10, "Confidence Calibration": 14,
        "Total": 8, "Rationale": 60, "Question Text": 50,
        "Expected Answer": 55, "System Answer": 55,
    }
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Scores"
    for ci, h in enumerate(headers, 1):
        cell = ws.cell(1, ci, h)
        cell.font = Font(bold=True, color="FFFFFF", name="Calibri", size=10)
        cell.fill = hdr_fill
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = border
        ws.column_dimensions[get_column_letter(ci)].width = col_widths.get(h, 14)
    ws.row_dimensions[1].height = 28
    for ri, row in enumerate(rows, 2):
        fill = sys_fills.get(str(row.get("System", "")), PatternFill())
        for ci, h in enumerate(headers, 1):
            val = row.get(h, "")
            cell = ws.cell(ri, ci, val)
            cell.font = Font(name="Calibri", size=9)
            cell.fill = fill
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            cell.border = border
        ws.row_dimensions[ri].height = 60
    ws.freeze_panes = "A2"
    wb.save(str(out_path))
    print(f"Saved evaluation: {out_path}")


def judge_one(client, question: str, expected: str, system_answer: str) -> dict:
    if not system_answer or system_answer.startswith("[ERROR"):
        return {
            "factual_accuracy": 0, "completeness": 0,
            "confidence_calibration": 0, "total": 0,
            "rationale": "No answer produced (error or skipped).",
        }
    user_msg = (
        f"QUESTION:\n{question}\n\n"
        f"EXPECTED ANSWER (ground truth):\n{expected}\n\n"
        f"SYSTEM ANSWER (to evaluate):\n{system_answer}"
    )
    try:
        stream = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": JUDGE_SYSTEM},
                {"role": "user",   "content": user_msg},
            ],
            stream=True,
        )
        raw = ""
        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                raw += chunk.choices[0].delta.content
        raw = re.sub(r"^```(?:json)?\s*", "", raw.strip())
        raw = re.sub(r"\s*```$", "", raw).strip()
        result = json.loads(raw)
        result["total"] = (
            result.get("factual_accuracy", 0)
            + result.get("completeness", 0)
            + result.get("confidence_calibration", 0)
        )
        return result
    except Exception as exc:
        return {
            "factual_accuracy": 0, "completeness": 0,
            "confidence_calibration": 0, "total": 0,
            "rationale": f"Judge error: {exc}",
        }


def load_chromadb():
    from langchain_huggingface import HuggingFaceEmbeddings
    from langchain_chroma import Chroma
    index_dir = APP_DIR / "chroma_db"
    if not index_dir.exists():
        sys.exit(f"ERROR: ChromaDB not found at {index_dir}. Run ingest_corpus.py first.")
    emb = HuggingFaceEmbeddings(
        model_name="BAAI/bge-small-en-v1.5",
        encode_kwargs={"normalize_embeddings": True},
    )
    return Chroma(
        collection_name="regulatory_corpus",
        embedding_function=emb,
        persist_directory=str(index_dir),
    )


# ── Answer generators (copied from run_benchmark.py) ─────────────────────────
SYSTEM_A_PROMPT = """\
You are an Indian regulatory compliance expert. Answer the question using your
general knowledge of Indian laws and regulations. Be specific, cite relevant
sections where known, and flag any uncertainty. Structure your answer as:
1. Direct answer
2. Relevant legal provision (Act / Section / Rule)
3. Key compliance takeaway
"""


def get_system_a(client, question_text: str) -> str:
    try:
        rsp = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_A_PROMPT},
                {"role": "user",   "content": question_text},
            ],
        )
        return rsp.choices[0].message.content.strip()
    except Exception as exc:
        return f"[ERROR: {exc}]"


def get_system_b(question_text: str, domain: str, store, client) -> str:
    try:
        from rag_query import query_rag
        result = query_rag(question_text, domain=domain, store=store, client=client)
        return result.get("answer", str(result))
    except Exception as exc:
        return f"[ERROR: {exc}]"


def get_system_c(question_text: str, domain: str, store, client) -> str:
    try:
        from agentic_rag import agentic_answer
        result = agentic_answer(question_text, domain, store=store, client=client)
        return result.get("answer", f"[ERROR: no answer key]")
    except Exception as exc:
        return f"[ERROR: {exc}]"


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        sys.exit("ERROR: OPENAI_API_KEY not set.")

    from openai import OpenAI
    client = OpenAI(api_key=api_key)

    print(f"Answers file : {ANSWERS_FILE}")
    print(f"Eval file    : {EVAL_FILE}")

    ans_headers, ans_rows = load_sheet(ANSWERS_FILE)
    eval_headers, eval_rows = load_sheet(EVAL_FILE)

    # Build index of answer rows by question_id
    ans_index = {str(r.get("Question Id", "")): i for i, r in enumerate(ans_rows)}

    # Find which (question_id, system) pairs need new answers
    sys_col = {
        "A": "System A Answer",
        "B": "System B Answer",
        "C": "System C Answer",
    }
    to_rerun: list[tuple[str, str]] = []   # (question_id, system)
    for r in ans_rows:
        qid = str(r.get("Question Id", ""))
        for sys_label, col in sys_col.items():
            val = str(r.get(col, "") or "")
            if val.startswith("[ERROR"):
                to_rerun.append((qid, sys_label))

    print(f"\nFound {len(to_rerun)} (question, system) pairs to regenerate.")
    if not to_rerun:
        print("Nothing to do.")
        return

    # Load ChromaDB once (needed for B and C)
    needs_store = any(s in ("B", "C") for _, s in to_rerun)
    store = load_chromadb() if needs_store else None
    if store:
        print("ChromaDB loaded.")

    # Regenerate answers
    print("\n── Step 1: Regenerating answers ──────────────────────────────────")
    for n, (qid, sys_label) in enumerate(to_rerun, 1):
        idx = ans_index[qid]
        row = ans_rows[idx]
        q_text = str(row.get("Question Text", ""))
        domain = str(row.get("Domain", ""))

        print(f"[{n:3}/{len(to_rerun)}] {qid} System {sys_label} ...", end=" ", flush=True)
        t0 = time.time()

        if sys_label == "A":
            answer = get_system_a(client, q_text)
        elif sys_label == "B":
            answer = get_system_b(q_text, domain, store, client)
        else:
            answer = get_system_c(q_text, domain, store, client)

        elapsed = time.time() - t0
        status = "OK" if not answer.startswith("[ERROR") else "FAIL"
        print(f"{status}  ({elapsed:.1f}s)")
        ans_rows[idx][sys_col[sys_label]] = answer
        time.sleep(1)

    # Save updated answers file
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    new_answers_path = RESULTS_DIR / f"benchmark_answers_{ts}_fixed.xlsx"
    save_answers(ans_headers, ans_rows, new_answers_path)

    # Score the newly regenerated pairs
    print("\n── Step 2: Scoring regenerated answers ───────────────────────────")
    eval_index = {
        (str(r.get("Question Id", "")), str(r.get("System", ""))): i
        for i, r in enumerate(eval_rows)
    }
    eval_sys_col = {"A": "System A Answer", "B": "System B Answer", "C": "System C Answer"}

    for n, (qid, sys_label) in enumerate(to_rerun, 1):
        idx = ans_index[qid]
        ans_row = ans_rows[idx]
        answer = str(ans_row.get(eval_sys_col[sys_label], ""))
        q_text = str(ans_row.get("Question Text", ""))
        expected = str(ans_row.get("Expected Answer", ""))

        print(f"[{n:3}/{len(to_rerun)}] {qid} System {sys_label} ...", end=" ", flush=True)
        t0 = time.time()
        scores = judge_one(client, q_text, expected, answer)
        elapsed = time.time() - t0
        print(f"total={scores['total']}/8  ({elapsed:.1f}s)")
        time.sleep(1)

        eval_key = (qid, sys_label)
        if eval_key in eval_index:
            ei = eval_index[eval_key]
            eval_rows[ei]["Factual Accuracy"]       = scores.get("factual_accuracy", 0)
            eval_rows[ei]["Completeness"]           = scores.get("completeness", 0)
            eval_rows[ei]["Confidence Calibration"] = scores.get("confidence_calibration", 0)
            eval_rows[ei]["Total"]                  = scores.get("total", 0)
            eval_rows[ei]["Rationale"]              = scores.get("rationale", "")
            eval_rows[ei]["System Answer"]          = answer
        else:
            print(f"  WARNING: {qid} System {sys_label} not found in eval file — skipping merge.")

    # Save updated eval file
    new_eval_path = RESULTS_DIR / f"benchmark_evaluation_{ts}_fixed.xlsx"
    save_eval(eval_headers, eval_rows, new_eval_path)

    # Summary
    from collections import defaultdict
    by_sys: dict = defaultdict(list)
    for r in eval_rows:
        by_sys[str(r.get("System", ""))].append(r.get("Total") or 0)

    remaining_errors = sum(
        1 for r in eval_rows
        if "Judge error" in str(r.get("Rationale", ""))
        or "No answer produced" in str(r.get("Rationale", ""))
    )

    print(f"\nDone. Rows still with errors/no-answer: {remaining_errors}")
    print("\n── Final Scores (Avg Total / 8) ──────────────────────────────────")
    for s in sorted(by_sys):
        vals = by_sys[s]
        avg = sum(vals) / len(vals) if vals else 0
        print(f"  System {s}: {avg:.2f}  (n={len(vals)})")
    print("──────────────────────────────────────────────────────────────────")


if __name__ == "__main__":
    main()
