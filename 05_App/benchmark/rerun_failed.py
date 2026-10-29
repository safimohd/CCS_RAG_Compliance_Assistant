"""
rerun_failed.py — re-scores rows where Rationale contains 'Judge error'.

Loads OPENAI_API_KEY from 05_App/.env (parent of benchmark/).
Uses gpt-5-nano via OpenAI directly (LLM_BASE_URL not set).
Merges fixed rows back into the partial evaluation file and saves
a new benchmark_evaluation_<timestamp>.xlsx alongside the original.

Usage:
    python rerun_failed.py <partial_eval.xlsx> <answers.xlsx>
"""

from __future__ import annotations

import json, os, re, sys, time
from datetime import datetime
from pathlib import Path

# Load OpenAI key from the parent app .env (not benchmark/.env)
try:
    from dotenv import load_dotenv
    HERE = Path(__file__).resolve().parent
    load_dotenv(HERE / ".env")          # benchmark/.env  (Together keys — may override)
    load_dotenv(HERE.parent / ".env", override=True)  # 05_App/.env  (OpenAI key wins)
except ImportError:
    pass

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter


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


def judge_one(client, model: str, question: str, expected: str, system_answer: str) -> dict:
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
            model=model,
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
        raw = raw.strip()
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
        return {
            "factual_accuracy": 0, "completeness": 0,
            "confidence_calibration": 0, "total": 0,
            "rationale": f"Judge error: {exc}",
        }


def load_sheet(path: Path) -> tuple[list[str], list[dict]]:
    wb = openpyxl.load_workbook(str(path), data_only=True)
    ws = wb.active
    headers = [str(ws.cell(1, c).value or "") for c in range(1, ws.max_column + 1)]
    rows = []
    for r in ws.iter_rows(min_row=2, values_only=True):
        rows.append(dict(zip(headers, r)))
    return headers, rows


def save_sheet(headers: list[str], rows: list[dict], out_path: Path) -> None:
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
    print(f"Saved: {out_path}")


def main():
    if len(sys.argv) < 3:
        print("Usage: python rerun_failed.py <partial_eval.xlsx> <answers.xlsx>")
        sys.exit(1)

    eval_path    = Path(sys.argv[1])
    answers_path = Path(sys.argv[2])

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        sys.exit("ERROR: OPENAI_API_KEY not set.")

    # Use OpenAI directly — no base_url override
    from openai import OpenAI
    client = OpenAI(api_key=api_key)
    model = "gpt-5-nano"

    print(f"Loading partial evaluation: {eval_path}")
    headers, eval_rows = load_sheet(eval_path)
    print(f"  {len(eval_rows)} rows loaded.")

    print(f"Loading answers: {answers_path}")
    _, answer_rows = load_sheet(answers_path)
    # Build lookup: (question_id, system) -> answer row
    ans_lookup: dict[tuple, dict] = {}
    for ar in answer_rows:
        qid = str(ar.get("Question Id") or ar.get("question_id") or "")
        for sys_label in ["A", "B", "C"]:
            ans_key = f"System {sys_label} Answer" if f"System {sys_label} Answer" in ar else f"system_{sys_label.lower()}_answer"
            ans_lookup[(qid, sys_label)] = str(ar.get(ans_key, "") or "")

    # Find failed rows
    failed_idx = [
        i for i, row in enumerate(eval_rows)
        if "Judge error" in str(row.get("Rationale", ""))
    ]
    print(f"Found {len(failed_idx)} failed rows to rerun.")

    if not failed_idx:
        print("Nothing to rerun. Exiting.")
        return

    for n, idx in enumerate(failed_idx, 1):
        row = eval_rows[idx]
        qid = str(row.get("Question Id", ""))
        sys_label = str(row.get("System", ""))
        q_text = str(row.get("Question Text", ""))
        expected = str(row.get("Expected Answer", ""))
        system_answer = str(row.get("System Answer", ""))

        print(f"[{n}/{len(failed_idx)}] {qid} System {sys_label} ...", end=" ", flush=True)
        t0 = time.time()
        scores = judge_one(client, model, q_text, expected, system_answer)
        elapsed = time.time() - t0
        print(f"total={scores['total']}/8  ({elapsed:.1f}s)")
        time.sleep(1)

        eval_rows[idx]["Factual Accuracy"]       = scores.get("factual_accuracy", 0)
        eval_rows[idx]["Completeness"]           = scores.get("completeness", 0)
        eval_rows[idx]["Confidence Calibration"] = scores.get("confidence_calibration", 0)
        eval_rows[idx]["Total"]                  = scores.get("total", 0)
        eval_rows[idx]["Rationale"]              = scores.get("rationale", "")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = eval_path.parent / f"benchmark_evaluation_{ts}.xlsx"
    save_sheet(headers, eval_rows, out_path)
    print(f"\nDone. {len(failed_idx)} rows fixed. Output: {out_path}")


if __name__ == "__main__":
    main()
