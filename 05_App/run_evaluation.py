"""
run_evaluation.py — the benchmark evaluation runner.

Generates four answers per benchmark question and writes them into sheet
`3_Responses` of the two benchmark workbooks, in place, preserving formulas.

    Condition                 Model                       Provider     Key
    ------------------------  --------------------------  -----------  ------------------
    gpt-5-nano (base)         gpt-5-nano                  OpenAI       OPENAI_API_KEY
    gpt-oss-20b (base)        openai/gpt-oss-20b          OpenRouter   OPENROUTER_API_KEY
    gemma-4-26b (base)        google/gemma-4-26b-a4b-it   OpenRouter   OPENROUTER_API_KEY
    RAG (corpus-augmented)    gpt-5-nano                  OpenAI       OPENAI_API_KEY

Two substitutions, both forced and both reportable (project report §9):

  * Together -> OpenRouter. The Together key returns a persistent 401 and is
    the professor's, so it cannot be regenerated here.
  * gemma-3n-E4B -> gemma-4-26b-a4b-it. Gemma 3n aged out of free hosting
    mid-project. Measured 11 Oct 2026: `openai/gpt-oss-20b:free` now also
    resolves with an EMPTY endpoints array (no provider serves it free any
    more) while `google/gemma-4-26b-a4b-it:free` has one live endpoint. The
    free tier moved under both models, in opposite directions. Hence
    `verify_slugs()` below, which refuses to launch 400 generations against a
    model id that resolves but has nowhere to run.

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

OPENAI_BASE     = None                            # library default
OPENROUTER_BASE = "https://openrouter.ai/api/v1"

# Condition label -> how to run it.
#
# `sheet_label` is the join key: it must match column C of 3_Responses exactly,
# because the scorer, reconciliation and analysis sheets carry ~2030 formulas
# keyed on that text. The dict key is the TRUE condition name used in the
# checkpoint, the manifest and the report. Where the two differ, the workbook
# still says `gemma-3n-E4B (base)` while the model actually run is Gemma 4 —
# deliberate, so that nothing has to be re-keyed inside the graded workbook.
# Relabel that column by hand only once the formulas have been checked.
#
# `provider_order` pins the serving provider. OpenRouter load-balances across
# providers by default, and for gpt-oss-20b those providers differ in
# quantization (fp4 / fp8 / bf16) and in supported parameters. Unpinned, two
# questions in the same condition can be served by different hardware at
# different precision — which is not one condition. Pinned, the condition is
# identical across all 100 questions and the provider is recorded per row.
CONDITIONS = {
    "gpt-5-nano (base)":      {"model": "gpt-5-nano", "base_url": OPENAI_BASE,
                               "key_env": "OPENAI_API_KEY", "rag": False,
                               "verify": False, "provider_order": None,
                               "sheet_label": "gpt-5-nano (base)"},
    # DISABLED by decision, 11 Oct 2026, and kept here rather than deleted
    # because its absence is itself the reportable result. `:free` for this
    # model now has zero live endpoints, so running it at all requires paid
    # credits. Held to the free tier, the 4-condition design is not available.
    # Set "enabled": True (and keep the paid slug, not ':free') to restore it.
    "gpt-oss-20b (base)":     {"model": "openai/gpt-oss-20b", "base_url": OPENROUTER_BASE,
                               "key_env": "OPENROUTER_API_KEY", "rag": False,
                               "verify": True, "provider_order": ["Darkbloom"],
                               "enabled": False,
                               "sheet_label": "gpt-oss-20b (base)"},
    "gemma-4-26b (base)":     {"model": "google/gemma-4-26b-a4b-it:free",
                               "base_url": OPENROUTER_BASE,
                               "key_env": "OPENROUTER_API_KEY", "rag": False,
                               "verify": True, "provider_order": ["Google AI Studio"],
                               "sheet_label": "gemma-3n-E4B (base)"},
    "RAG (corpus-augmented)": {"model": "gpt-5-nano", "base_url": OPENAI_BASE,
                               "key_env": "OPENAI_API_KEY", "rag": True,
                               "verify": False, "provider_order": None,
                               "sheet_label": "RAG (corpus-augmented)"},
}
FALLBACK_CONDITIONS = ["gpt-5-nano (base)", "RAG (corpus-augmented)"]

MAX_RETRIES  = 4
RETRY_BASE_S = 4
RATE_LIMIT_RETRIES = 6        # a 429 is worth more patience than a bad request
RATE_LIMIT_MAX_WAIT_S = 300
_print_lock  = threading.Lock()


def log(msg: str) -> None:
    with _print_lock:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


# ── provider plumbing ────────────────────────────────────────────────────────
_clients: dict[tuple, object] = {}
_client_lock = threading.Lock()


def client_for(cfg) -> object:
    """One OpenAI-compatible client per (base_url, key) pair. OpenRouter speaks
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


def _openrouter_get(path: str) -> dict:
    """Plain GET against the OpenRouter REST API (not the chat protocol)."""
    import urllib.request
    key = os.getenv("OPENROUTER_API_KEY") or ""
    req = urllib.request.Request(
        f"{OPENROUTER_BASE}{path}",
        headers={"Authorization": f"Bearer {key}",
                 "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def verify_slugs(active: dict) -> dict:
    """A model id that resolves is not a model that runs.

    `openai/gpt-oss-20b:free` returns HTTP 200 with `endpoints: []` — the id is
    still registered, every provider has stopped serving it free. A chat call
    against it fails at routing with no fallback. The same thing had already
    happened to `gemma-3n-E4B`. So check the endpoint list, not the id, and
    check it before 400 generations rather than after.

    A condition whose model has no live endpoint is dropped here, loudly.
    """
    for lbl in [l for l, c in active.items() if c.get("verify")]:
        slug = active[lbl]["model"]
        try:
            data = _openrouter_get(f"/models/{slug}/endpoints").get("data") or {}
            endpoints = data.get("endpoints") or []
        except Exception as exc:                                # noqa: BLE001
            log(f"  ! {lbl}: could not check endpoints for {slug}: {str(exc)[:110]}")
            log(f"    dropping condition: {lbl}")
            active.pop(lbl)
            continue

        if not endpoints:
            log(f"  ! {lbl}: '{slug}' resolves but has NO live endpoints "
                f"(this is the Gemma-3n failure, recurring)")
            log(f"    dropping condition: {lbl}")
            active.pop(lbl)
            continue

        names = [e.get("provider_name") for e in endpoints]
        free = [e for e in endpoints
                if str(e.get("pricing", {}).get("prompt")) == "0"]
        log(f"  OK   {lbl:<22} {slug:<34} "
            f"{len(endpoints)} endpoint(s), {len(free)} free")

        want = active[lbl].get("provider_order")
        if want:
            missing = [p for p in want if p not in names]
            if missing:
                log(f"    ! pinned provider(s) {missing} not serving {slug}; "
                    f"available: {names[:6]}")
                log(f"    unpinning {lbl} — provider is recorded per row instead")
                active[lbl]["provider_order"] = None
    return active


def check_free_quota(active: dict) -> None:
    """Free variants are capped per UTC day: 50 requests under 10 credits
    purchased all-time, 1000 at or above. 100 questions in a free condition
    silently stops at 50 and waits for midnight UTC — the overnight run would
    half-finish and look finished. Say so before it happens, not after."""
    free_conds = [l for l, c in active.items()
                  if ":free" in str(c.get("model", ""))]
    if not free_conds:
        return
    try:
        info = _openrouter_get("/key").get("data") or {}
    except Exception as exc:                                    # noqa: BLE001
        log(f"  ! could not read OpenRouter key limits: {str(exc)[:110]}")
        return

    fmd = info.get("free_model_daily_requests") or {}
    remaining, limit = fmd.get("remaining"), fmd.get("limit")
    log(f"  free-model quota: {fmd.get('used')}/{limit} used today, "
        f"{remaining} remaining  (conditions on a free variant: "
        f"{', '.join(free_conds)})")
    if isinstance(remaining, int) and remaining < 100:
        log(f"  ! WARNING: {remaining} free requests left today but ~100 "
            f"needed per free condition per domain.")
        log(f"    The run will stall at the cap and resume after 00:00 UTC "
            f"(05:30 IST). Options: purchase $10 of OpenRouter credits to "
            f"raise the daily cap to 1000, or drop ':free' from the model id "
            f"in CONDITIONS to use the paid endpoint (~6 cents for the whole "
            f"400-generation run).")


class DailyCapReached(RuntimeError):
    """The OpenRouter free-model per-day cap is spent. Retrying is pointless
    until 00:00 UTC, so this aborts the condition instead of the whole run."""


def _retry_after_s(exc) -> float | None:
    """Honour a Retry-After header when the provider sends one."""
    hdrs = getattr(getattr(exc, "response", None), "headers", None)
    if not hdrs:
        return None
    for name in ("retry-after", "Retry-After", "x-ratelimit-reset"):
        raw = hdrs.get(name)
        if raw:
            try:
                return max(1.0, min(float(raw), RATE_LIMIT_MAX_WAIT_S))
            except (TypeError, ValueError):
                continue
    return None


def chat(cfg, messages: list[dict]) -> tuple[str, str | None]:
    """One completion, with retry. Returns (text, serving_provider).

    Parameter support differs between providers and between reasoning and
    non-reasoning models, so unsupported parameters are dropped rather than
    allowed to fail the run. A 429 is treated differently from a bad request:
    it is transient, so it gets more attempts and a longer wait, and the
    Retry-After header is honoured when present.
    """
    cli = client_for(cfg)
    kwargs: dict = {"model": cfg["model"], "messages": messages}
    if cfg.get("provider_order"):
        kwargs["extra_body"] = {"provider": {"order": cfg["provider_order"],
                                             "allow_fallbacks": False}}
    last: Exception | None = None
    rate_limited = 0

    attempt = 0
    while attempt < MAX_RETRIES:
        try:
            resp = cli.chat.completions.create(**kwargs)
            text = (resp.choices[0].message.content or "").strip()
            # OpenRouter reports which provider actually served the request as
            # a top-level, non-standard field. Record it: an unrecorded
            # provider switch mid-run is an unrecorded change of condition.
            provider = getattr(resp, "provider", None)
            if provider is None:
                extra = getattr(resp, "model_extra", None) or {}
                provider = extra.get("provider")
            return text, provider
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
                continue                       # not a real attempt, don't count it

            is_429 = ("429" in text or "rate limit" in text
                      or "too many requests" in text)
            if is_429 and ("per-day" in text or "per day" in text
                           or "daily" in text or "free-models-per-day" in text):
                raise DailyCapReached(str(exc)[:200]) from exc

            if is_429 and rate_limited < RATE_LIMIT_RETRIES:
                rate_limited += 1
                wait = _retry_after_s(exc) or min(
                    RETRY_BASE_S * (2 ** rate_limited), RATE_LIMIT_MAX_WAIT_S)
                log(f"  429 rate limited, waiting {wait:.0f}s "
                    f"(attempt {rate_limited}/{RATE_LIMIT_RETRIES})")
                time.sleep(wait)
                continue                       # rate limits don't consume attempts

            attempt += 1
            if attempt >= MAX_RETRIES:
                break
            wait = RETRY_BASE_S * (2 ** (attempt - 1))
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
def run_base(cfg, question: str) -> tuple[str, str | None]:
    """Bare question. No system prompt, no context — brief §4.3. The template
    is identical across all three base models so the comparison is clean."""
    return chat(cfg, [{"role": "user", "content": question}])


def run_rag(cfg, question: str, domain: str, results
            ) -> tuple[str, list[dict], str | None]:
    if not results:
        return ("No sources retrieved from the corpus for this question. "
                "The corpus does not contain evidence to answer it."), [], None
    user = (f"Domain: {domain}\n\nQuestion: {question}\n\n"
            f"Retrieved sources:\n\n{rag_query.format_sources(results)}")
    answer, provider = chat(
        cfg, [{"role": "system", "content": rag_query.SYSTEM_PROMPT},
              {"role": "user", "content": user}])
    meta = [{"title": r["title"], "tier": r["tier"],
             "similarity": round(float(r["similarity"]), 4),
             "url": r["url"]} for r in results]
    return answer, meta, provider


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

    # The workbook's column C still carries the ORIGINAL condition labels, and
    # the ~2030 formulas are keyed on that text. Map sheet label -> true
    # condition so a substituted model writes into its own row without any
    # cell in the graded workbook having to be re-keyed.
    by_sheet_label = {c.get("sheet_label", lbl): lbl
                      for lbl, c in CONDITIONS.items()}

    written = missing = 0
    for r in range(2, ws.max_row + 1):
        qid = ws.cell(row=r, column=c_qid).value
        cond = ws.cell(row=r, column=c_model).value
        if not qid or not cond:
            continue
        label = by_sheet_label.get(str(cond).strip(), str(cond).strip())
        rec = done.get((domain, str(qid).strip(), label))
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
    """Verify keys, confirm every model id has a live endpoint, report the free
    daily quota, and smoke-test every condition with one cheap prompt before
    committing to 400 generations."""
    log("preflight")
    rag_query.load_env()

    for env in ("OPENAI_API_KEY", "OPENROUTER_API_KEY"):
        log(f"  {env}: {'present' if os.getenv(env) else 'MISSING'}")

    if os.getenv("OPENROUTER_API_KEY"):
        active = verify_slugs(active)
        check_free_quota(active)
    else:
        for lbl in [l for l, c in active.items()
                    if c["key_env"] == "OPENROUTER_API_KEY"]:
            log(f"  dropping condition (no OpenRouter key): {lbl}")
            active.pop(lbl)

    for lbl in list(active):
        cfg = active[lbl]
        try:
            out, provider = chat(
                cfg, [{"role": "user", "content": "Reply with the single word: ready"}])
            via = f" via {provider}" if provider else ""
            log(f"  OK   {lbl:<24} {cfg['model']:<34} -> {out[:40]!r}{via}")
        except Exception as exc:                                # noqa: BLE001
            log(f"  FAIL {lbl:<24} {cfg['model']:<34} {str(exc)[:130]}")
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

    disabled = [l for l, c in CONDITIONS.items() if c.get("enabled") is False]
    for lbl in disabled:
        log(f"condition disabled by configuration: {lbl}")
    active = preflight({k: dict(v) for k, v in CONDITIONS.items()
                        if k not in disabled})
    if args.preflight:
        log("preflight only — stopping before generation")
        return 0

    store = rag_query.load_store()
    log("index loaded")

    started = time.time()
    total_new = 0
    capped: set[str] = set()
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
                        answer, sources, provider = run_rag(
                            cfg, q["question"], domain, hits)
                    else:
                        answer, provider = run_base(cfg, q["question"])
                        sources = []
                except DailyCapReached as exc:
                    log(f"  {q['question_id']} {lbl}: free daily cap reached "
                        f"({str(exc)[:90]})")
                    capped.add(lbl)
                    return None
                except Exception as exc:                        # noqa: BLE001
                    log(f"  {q['question_id']} {lbl} failed: {str(exc)[:120]}")
                    return None
                return {"domain": domain, "question_id": q["question_id"],
                        "tier": q["tier"], "condition": lbl, "model": cfg["model"],
                        "sheet_label": cfg.get("sheet_label", lbl),
                        "provider": provider,
                        "question": q["question"], "response": answer,
                        "sources": sources,
                        "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}

            with ThreadPoolExecutor(max_workers=min(4, len(todo))) as pool:
                for rec in pool.map(one, todo):
                    if rec:
                        append_checkpoint(rec)
                        done[(domain, rec["question_id"], rec["condition"])] = rec
                        total_new += 1

            # A spent daily cap does not recover within the run. Drop the
            # condition rather than spending the remaining questions failing
            # it one at a time; everything already on disk is kept, and a
            # re-run after 00:00 UTC resumes exactly here.
            for lbl in sorted(capped):
                if lbl in active:
                    log(f"  dropping {lbl} for the rest of this run "
                        f"(free daily cap; re-run after 00:00 UTC to finish it)")
                    active.pop(lbl)

            if n % 5 == 0 or n == len(questions):
                mins = (time.time() - started) / 60
                log(f"  {domain} {n}/{len(questions)}  ({total_new} new, {mins:.1f} min)")

    for d in domains:
        w, m = write_workbook(d, done)
        log(f"{d}: wrote {w} responses into 3_Responses, {m} rows still empty")

    # Which provider actually served each condition, counted from the
    # checkpoint. If a condition shows more than one provider, the condition
    # was not constant and the report has to say so.
    providers: dict[str, dict[str, int]] = {}
    for rec in done.values():
        if rec.get("provider"):
            providers.setdefault(rec["condition"], {})
            providers[rec["condition"]][rec["provider"]] = (
                providers[rec["condition"]].get(rec["provider"], 0) + 1)

    RUN_LOG.write_text(json.dumps({
        "finished": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "conditions_run": {l: c["model"] for l, c in active.items()},
        "conditions_dropped_at_runtime": sorted(
            set(CONDITIONS) - set(active) - set(disabled)),
        "conditions_disabled_by_configuration": sorted(disabled),
        "sheet_label_map": {l: c.get("sheet_label", l)
                            for l, c in CONDITIONS.items()
                            if c.get("sheet_label", l) != l},
        "providers_served": providers,
        "capped_on_free_tier": sorted(capped),
        "answers_total": len(done), "answers_this_run": total_new,
        "minutes": round((time.time() - started) / 60, 1),
    }, indent=2), encoding="utf-8")

    for cond, seen in providers.items():
        if len(seen) > 1:
            log(f"NOTE for the report: {cond} was served by {len(seen)} "
                f"different providers {seen} — not a single condition")

    log(f"done: {total_new} new answers in {(time.time()-started)/60:.1f} min")
    log(f"manifest: {RUN_LOG}")
    if disabled:
        log(f"NOTE for the report: conditions disabled by configuration — "
            f"{', '.join(sorted(disabled))}")
    runtime_dropped = sorted(set(CONDITIONS) - set(active) - set(disabled))
    if runtime_dropped:
        log(f"NOTE for the report: conditions dropped at runtime — "
            f"{', '.join(runtime_dropped)}")
    if capped:
        log(f"INCOMPLETE: {', '.join(sorted(capped))} hit the free daily cap. "
            f"Re-run after 00:00 UTC (05:30 IST) to finish; the checkpoint "
            f"resumes where it stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
