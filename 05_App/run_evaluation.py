"""
run_evaluation.py — the benchmark evaluation runner.

Generates four answers per benchmark question and writes them into sheet
`3_Responses` of the two benchmark workbooks, in place, preserving formulas.

    Condition                 Model                Provider   Key
    ------------------------  -------------------  ---------  ------------------
    gpt-5-nano (base)         gpt-5-nano           OpenAI     OPENAI_API_KEY
    gpt-oss-20b (base)        openai/gpt-oss-20b   Together   TOGETHER_API_KEY
    gemma-3n-E4B (base)       google/gemma-3n-...  Together   TOGETHER_API_KEY
    RAG (corpus-augmented)    gpt-5-nano           OpenAI     OPENAI_API_KEY

The three base conditions receive the bare question: no retrieval, no system
prompt, no custom instructions (project brief §4.3 — "test the models as a
first-time user encounters them"). Only the RAG condition retrieves and only
the RAG condition uses rag_query.SYSTEM_PROMPT. That asymmetry is the
experiment.

Usage
    python run_evaluation.py --preflight      # verify keys, slugs, 3 smoke calls
    python run_evaluation.py                  # full run, resumes automatically
    python run_evaluation.py --domain RERA    # one domain only
    python run_evaluation.py --limit 3        # first 3 questions per domain
    python run_evaluation.py --write-only     # workbook write from checkpoint

Run detached; it checkpoints after every generation and resumes where it
stopped, so a crash or a closed terminal costs nothing:

    python run_evaluation.py > eval_log.txt 2>&1
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rag_query  # noqa: E402  (same directory)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BENCHMARKS = {
    "RERA":       PROJECT_ROOT / "01_Benchmarks" / "RERA" / "Benchmark_RERA_50Q.xlsx",
    "Healthcare": PROJECT_ROOT / "01_Benchmarks" / "Healthcare" / "Benchmark_Healthcare_50Q.xlsx",
}
OUT_DIR    = PROJECT_ROOT / "06_Evaluation" / "Raw_Responses"
CHECKPOINT = OUT_DIR / "responses.jsonl"
RUN_LOG    = OUT_DIR / "run_manifest.json"

OPENAI_BASE   = None                              # library default
TOGETHER_BASE = "https://api.together.xyz/v1"

# Condition label -> how to run it. Labels must match column C of 3_Responses
# exactly; they are the join key for the scorer sheets.
CONDITIONS = {
    "gpt-5-nano (base)":      {"model": "gpt-5-nano", "base_url": OPENAI_BASE,
                               "key_env": "OPENAI_API_KEY", "rag": False,
                               "match": None},
    "gpt-oss-20b (base)":     {"model": "openai/gpt-oss-20b", "base_url": TOGETHER_BASE,
                               "key_env": "TOGETHER_API_KEY", "rag": False,
                               "match": "gpt-oss-20b"},
    "gemma-3n-E4B (base)":    {"model": "google/gemma-3n-E4B-it", "base_url": TOGETHER_BASE,
                               "key_env": "TOGETHER_API_KEY", "rag": False,
                               "match": "gemma-3n"},
    "RAG (corpus-augmented)": {"model": "gpt-5-nano", "base_url": OPENAI_BASE,
                               "key_env": "OPENAI_API_KEY", "rag": True,
                               "match": None},
}
FALLBACK_CONDITIONS = ["gpt-5-nano (base)", "RAG (corpus-augmented)"]

MAX_RETRIES  = 4
RETRY_BASE_S = 4
_print_lock  = threading.Lock()


def log(msg: str) -> None:
    with _print_lock:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


# ── provider plumbing ────────────────────────────────────────────────────────
_clients: dict[tuple, object] = {}
_client_lock = threading.Lock()


def client_for(cfg) -> object:
    """One OpenAI-compatible client per (base_url, key) pair. Together speaks
    the same protocol, so the same library works with base_url swapped."""
    key = os.getenv(cfg["key_env"])
    if not key:
        raise RuntimeError(f"{cfg['key_env']} is not set in 05_App/.env")
    ident = (cfg["base_url"], cfg["key_env"])
    with _client_lock:
        if ident not in _clients:
            from openai import OpenAI
            _clients[ident] = (OpenAI(api_key=key, base_url=cfg["base_url"])
                               if cfg["base_url"] else OpenAI(api_key=key))
        return _clients[ident]


def resolve_together_slugs() -> dict[str, str | None]:
    """Together's model identifiers change. Verify them at runtime rather than
    trusting a hardcoded string, before launching 400 generations."""
    out: dict[str, str | None] = {}
    targets = {lbl: c["match"] for lbl, c in CONDITIONS.items() if c["match"]}
    try:
        cli = client_for(CONDITIONS["gpt-oss-20b (base)"])
        ids = [m.id for m in cli.models.list().data]
    except Exception as exc:                                    # noqa: BLE001
        log(f"  ! could not list Together models: {exc}")
        return {lbl: None for lbl in targets}

    for lbl, needle in targets.items():
        hits = [i for i in ids if needle.lower() in i.lower()]
        if not hits:
            log(f"  ! no Together model matching '{needle}'")
            out[lbl] = None
            continue
        want = CONDITIONS[lbl]["model"]
        pick = want if want in hits else sorted(hits, key=len)[0]
        if pick != want:
            log(f"  slug corrected: {want} -> {pick}")
        out[lbl] = pick
    return out


def chat(cfg, messages: list[dict]) -> str:
    """One completion, with retry. Parameter support differs between providers
    and between reasoning and non-reasoning models, so unsupported parameters
    are dropped rather than allowed to fail the run."""
    cli = client_for(cfg)
    kwargs: dict = {"model": cfg["model"], "messages": messages}
    last: Exception | None = None

    for attempt in range(MAX_RETRIES):
        try:
            resp = cli.chat.completions.create(**kwargs)
            return (resp.choices[0].message.content or "").strip()
        except Exception as exc:                                # noqa: BLE001
            last = exc
            text = str(exc).lower()
            dropped = False
            for param in ("temperature", "max_tokens", "max_completion_tokens", "top_p"):
                if param in text and param in kwargs and (
                        "unsupported" in text or "not supported" in text
                        or "unrecognized" in text or "invalid" in text):
                    kwargs.pop(param)
                    dropped = True
            if dropped:
                continue
            if attempt == MAX_RETRIES - 1:
                break
            wait = RETRY_BASE_S * (2 ** attempt)
            log(f"  retry in {wait}s ({type(exc).__name__}: {str(exc)[:110]})")
            time.sleep(wait)
    raise RuntimeError(f"generation failed after {MAX_RETRIES} attempts: {last}")


# ── checkpointing ────────────────────────────────────────────────────────────
_ck_lock = threading.Lock()


def load_checkpoint() -> dict[tuple[str, str, str], dict]:
    done: dict[tuple[str, str, str], dict] = {}
    if not CHECKPOINT.exists():
        return done
    for line in CHECKPOINT.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue                    # half-written final line after a crash
        if rec.get("response"):
            done[(rec["domain"], rec["question_id"], rec["condition"])] = rec
    return done


def append_checkpoint(rec: dict) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with _ck_lock, CHECKPOINT.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


# ── benchmark reading ────────────────────────────────────────────────────────
def read_questions(domain: str) -> list[dict]:
    import openpyxl
    wb = openpyxl.load_workbook(BENCHMARKS[domain], data_only=True)
    ws = wb["1_Benchmark"]
    head = [c.value for c in ws[1]]
    idx = {name: head.index(name) for name in
           ("question_id", "tier", "question_text") if name in head}
    rows = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        qid = row[idx["question_id"]]
        if not qid:
            continue
        rows.append({"domain": domain,
                     "question_id": str(qid).strip(),
                     "tier": row[idx["tier"]],
                     "question": str(row[idx["question_text"]]).strip()})
    wb.close()
    return rows


# ── generation ───────────────────────────────────────────────────────────────
def run_base(cfg, question: str) -> str:
    """Bare question. No system prompt, no context — brief §4.3. The template
    is identical across all three base models so the comparison is clean."""
    return chat(cfg, [{"role": "user", "content": question}])


def run_rag(cfg, question: str, domain: str, results) -> tuple[str, list[dict]]:
    if not results:
        return ("No sources retrieved from the corpus for this question. "
                "The corpus does not contain evidence to answer it."), []
    user = (f"Domain: {domain}\n\nQuestion: {question}\n\n"
            f"Retrieved sources:\n\n{rag_query.format_sources(results)}")
    answer = chat(cfg, [{"role": "system", "content": rag_query.SYSTEM_PROMPT},
                        {"role": "user", "content": user}])
    meta = [{"title": r["title"], "tier": r["tier"],
             "similarity": round(float(r["similarity"]), 4),
             "url": r["url"]} for r in results]
    return answer, meta


# ── workbook writing ─────────────────────────────────────────────────────────
def write_workbook(domain: str, done: dict) -> tuple[int, int]:
    """Write into the existing workbook. Never rebuild it: the 2030 formulas in
    the scorer, reconciliation and analysis sheets are the graded artefact."""
    import openpyxl
    path = BENCHMARKS[domain]
    wb = openpyxl.load_workbook(path)            # keep formulas, not values
    ws = wb["3_Responses"]
    head = [c.value for c in ws[1]]

    c_qid, c_model = head.index("question_id") + 1, head.index("model") + 1
    c_resp = head.index("response_text") + 1
    c_src, c_tier = c_resp + 1, c_resp + 2
    if len(head) < c_src or head[c_src - 1] != "retrieved_sources":
        ws.cell(row=1, column=c_src,  value="retrieved_sources")
        ws.cell(row=1, column=c_tier, value="retrieved_tiers")

    written = missing = 0
    for r in range(2, ws.max_row + 1):
        qid = ws.cell(row=r, column=c_qid).value
        cond = ws.cell(row=r, column=c_model).value
        if not qid or not cond:
            continue
        rec = done.get((domain, str(qid).strip(), str(cond).strip()))
        if not rec:
            missing += 1
            continue
        ws.cell(row=r, column=c_resp, value=rec["response"][:32000])
        if rec.get("sources"):
            ws.cell(row=r, column=c_src,
                    value=" | ".join(s["title"][:90] for s in rec["sources"]))
            ws.cell(row=r, column=c_tier,
                    value=", ".join(s["tier"] for s in rec["sources"]))
        written += 1

    wb.save(path)
    wb.close()
    return written, missing


# ── preflight ────────────────────────────────────────────────────────────────
def preflight(active: dict) -> dict:
    """Verify keys, resolve Together slugs, and smoke-test every condition with
    one cheap prompt before committing to 400 generations."""
    log("preflight")
    rag_query.load_env()

    for env in ("OPENAI_API_KEY", "TOGETHER_API_KEY"):
        log(f"  {env}: {'present' if os.getenv(env) else 'MISSING'}")

    if os.getenv("TOGETHER_API_KEY"):
        for lbl, slug in resolve_together_slugs().items():
            if slug:
                active[lbl]["model"] = slug
            elif lbl in active:
                log(f"  dropping condition: {lbl}")
                active.pop(lbl)
    else:
        for lbl in [l for l, c in active.items() if c["key_env"] == "TOGETHER_API_KEY"]:
            log(f"  dropping condition (no Together key): {lbl}")
            active.pop(lbl)

    for lbl in list(active):
        cfg = active[lbl]
        try:
            out = chat(cfg, [{"role": "user", "content": "Reply with the single word: ready"}])
            log(f"  OK   {lbl:<24} {cfg['model']:<26} -> {out[:40]!r}")
        except Exception as exc:                                # noqa: BLE001
            log(f"  FAIL {lbl:<24} {cfg['model']:<26} {str(exc)[:130]}")
            active.pop(lbl)

    if not any(c["rag"] for c in active.values()):
        sys.exit("\nThe RAG condition failed preflight. Nothing to evaluate — stopping.")
    if set(active) < set(CONDITIONS):
        log(f"  falling back to {len(active)} conditions: {', '.join(active)}")
    return active


# ── main ─────────────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description="Run the benchmark evaluation.")
    ap.add_argument("--domain", choices=sorted(BENCHMARKS), action="append",
                    help="limit to one domain (repeatable)")
    ap.add_argument("--limit", type=int, help="first N questions per domain (a dry run)")
    ap.add_argument("--preflight", action="store_true", help="checks only, generate nothing")
    ap.add_argument("--write-only", action="store_true",
                    help="write the workbooks from the checkpoint, generate nothing")
    args = ap.parse_args()

    domains = args.domain or sorted(BENCHMARKS)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    done = load_checkpoint()
    log(f"checkpoint: {len(done)} answers already on disk")

    if args.write_only:
        for d in domains:
            w, m = write_workbook(d, done)
            log(f"{d}: wrote {w} responses, {m} rows still empty")
        return 0

    active = preflight({k: dict(v) for k, v in CONDITIONS.items()})
    if args.preflight:
        log("preflight only — stopping before generation")
        return 0

    store = rag_query.load_store()
    log("index loaded")

    started = time.time()
    total_new = 0
    for domain in domains:
        questions = read_questions(domain)
        if args.limit:
            questions = questions[:args.limit]
        log(f"{domain}: {len(questions)} questions x {len(active)} conditions")

        for n, q in enumerate(questions, 1):
            todo = [lbl for lbl in active
                    if (domain, q["question_id"], lbl) not in done]
            if not todo:
                continue

            # Retrieve once per question, on this thread: Chroma is not
            # guaranteed thread-safe. Only the generations fan out.
            hits = []
            if any(active[l]["rag"] for l in todo):
                try:
                    hits = rag_query.retrieve(store, q["question"], domain, rag_query.TOP_K)
                except Exception as exc:                        # noqa: BLE001
                    log(f"  {q['question_id']} retrieval failed: {str(exc)[:120]}")

            def one(lbl: str) -> dict | None:
                cfg = active[lbl]
                try:
                    if cfg["rag"]:
                        answer, sources = run_rag(cfg, q["question"], domain, hits)
                    else:
                        answer, sources = run_base(cfg, q["question"]), []
                except Exception as exc:                        # noqa: BLE001
                    log(f"  {q['question_id']} {lbl} failed: {str(exc)[:120]}")
                    return None
                return {"domain": domain, "question_id": q["question_id"],
                        "tier": q["tier"], "condition": lbl, "model": cfg["model"],
                        "question": q["question"], "response": answer,
                        "sources": sources,
                        "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}

            with ThreadPoolExecutor(max_workers=min(4, len(todo))) as pool:
                for rec in pool.map(one, todo):
                    if rec:
                        append_checkpoint(rec)
                        done[(domain, rec["question_id"], rec["condition"])] = rec
                        total_new += 1

            if n % 5 == 0 or n == len(questions):
                mins = (time.time() - started) / 60
                log(f"  {domain} {n}/{len(questions)}  ({total_new} new, {mins:.1f} min)")

    for d in domains:
        w, m = write_workbook(d, done)
        log(f"{d}: wrote {w} responses into 3_Responses, {m} rows still empty")

    RUN_LOG.write_text(json.dumps({
        "finished": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "conditions_run": {l: c["model"] for l, c in active.items()},
        "conditions_dropped": sorted(set(CONDITIONS) - set(active)),
        "answers_total": len(done), "answers_this_run": total_new,
        "minutes": round((time.time() - started) / 60, 1),
    }, indent=2), encoding="utf-8")

    log(f"done: {total_new} new answers in {(time.time()-started)/60:.1f} min")
    log(f"manifest: {RUN_LOG}")
    if set(active) < set(CONDITIONS):
        log(f"NOTE for the report: conditions not run — "
            f"{', '.join(sorted(set(CONDITIONS) - set(active)))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
