"""
run_all_systems.py  —  Unified benchmark runner for ALL systems
===============================================================
Runs Systems A, B, C (via run_v2_evaluation.py) in one shot.
Model is ALWAYS read from the LLM_MODEL env var (default: gpt-5-nano).
Never uses gpt-4o-mini or any hardcoded model name.

Systems
-------
  A  Base LLM        gpt-5-nano, no retrieval context
  B  Plain RAG       gpt-5-nano + ChromaDB
  C  Agentic RAG     gpt-5-nano + query decomposition + multi-hop reflection

Dry-run mode (--dry-run)
------------------------
  Runs only the FIRST question through ALL systems, with judge scoring,
  then prints a summary and exits.  Use this to verify everything works
  before committing to the full 50-question run.

Usage
-----
  # 1. Dry run first — verify model + API keys + ChromaDB are all working:
  python run_all_systems.py --benchmark /path/to/Benchmark_RERA_V5_Final.xlsx --dry-run

  # 2. Full 50-question run with auto-logging:
  python run_all_systems.py --benchmark /path/to/Benchmark_RERA_V5_Final.xlsx

  # 3. Resume a partial run:
  python run_all_systems.py --benchmark /path/to/Benchmark_RERA_V5_Final.xlsx --resume

  # 4. Run only one system:
  python run_all_systems.py --benchmark /path/to/Benchmark_RERA_V5_Final.xlsx --only-system B

Environment variables
---------------------
  OPENAI_API_KEY    required  — used by Systems A, B, C
  TOGETHER_API_KEY  required  — used by LLM-as-judge (DeepSeek)
  LLM_MODEL         optional  — defaults to gpt-5-nano
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# ── dotenv ────────────────────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv()
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

SCRIPT_DIR   = Path(__file__).parent.resolve()
V2_EVAL      = SCRIPT_DIR / "run_v2_evaluation.py"

# ─────────────────────────────────────────────────────────────────────────────
# helpers
# ─────────────────────────────────────────────────────────────────────────────

def _check_env() -> bool:
    """Verify required env vars and confirm the model being used. Returns True on pass."""
    model = os.environ.get("LLM_MODEL", "gpt-5-nano")
    openai_key  = os.environ.get("OPENAI_API_KEY", "")
    together_key = os.environ.get("TOGETHER_API_KEY", "")

    print("=" * 65)
    print("  PRE-FLIGHT CHECK")
    print("=" * 65)
    print(f"  LLM_MODEL        : {model}")
    print(f"  OPENAI_API_KEY   : {'SET (' + openai_key[:8] + '...)' if openai_key else 'MISSING  ← REQUIRED'}")
    print(f"  TOGETHER_API_KEY : {'SET (' + together_key[:8] + '...)' if together_key else 'MISSING  ← required for judge'}")
    print("=" * 65)

    if model != "gpt-5-nano":
        print(f"\n  WARNING: LLM_MODEL is '{model}' — expected 'gpt-5-nano'.")
        print("  Set LLM_MODEL=gpt-5-nano in .env if this is unintended.\n")

    ok = True
    if not openai_key:
        print("  ERROR: OPENAI_API_KEY is not set. Aborting.")
        ok = False
    if not together_key:
        print("  WARNING: TOGETHER_API_KEY not set. Judge scoring will be skipped.")

    return ok


def _build_v2_cmd(benchmark: Path, limit: int | None, resume: bool,
                  no_judge: bool, only_system: str | None,
                  output: Path | None) -> list[str]:
    cmd = [sys.executable, str(V2_EVAL), "--benchmark", str(benchmark)]
    if limit:
        cmd += ["--limit", str(limit)]
    if resume:
        cmd.append("--resume")
    if no_judge:
        cmd.append("--no-judge")
    if only_system:
        cmd += ["--only-system", only_system]
    if output:
        cmd += ["--output", str(output)]
    return cmd


def _print_model_banner() -> None:
    """Print a clear, permanent record of exactly which models are used."""
    model      = os.environ.get("LLM_MODEL",   "gpt-5-nano")
    judge      = os.environ.get("JUDGE_MODEL",  "deepseek-ai/DeepSeek-V4.1-Flash")
    judge_via  = "Together AI"
    answer_via = "OpenAI"

    print("\n" + "=" * 65)
    print("  MODELS IN USE — printed here and in every log file")
    print("=" * 65)
    print(f"  System A  Base LLM       {model} via {answer_via} (no retrieval)")
    print(f"  System B  Plain RAG      {model} via {answer_via} + ChromaDB")
    print(f"  System C  Agentic RAG    {model} via {answer_via} + query decomp")
    print(f"  Judge     LLM-as-judge   {judge} via {judge_via}")
    print("=" * 65 + "\n")


def _eta_message(n_questions: int, systems: list[str]) -> None:
    """Print an ETA estimate before starting."""
    # Rough timings (seconds per question for each system, per judge call)
    answer_s = 20          # ~20s per question total (all three systems)
    judge_s  = 3           # ~3s per judge call
    n_sys    = len(systems)
    judge_calls = n_questions * n_sys

    answer_total = n_questions * answer_s
    judge_total  = judge_calls * judge_s
    total        = answer_total + judge_total

    print(f"\n  Questions      : {n_questions}")
    print(f"  Systems        : {', '.join(systems)}")
    print(f"  Est. answers   : ~{answer_total // 60}m {answer_total % 60}s")
    print(f"  Est. judge     : ~{judge_total // 60}m {judge_total % 60}s  ({judge_calls} calls)")
    print(f"  Est. TOTAL     : ~{total // 60} minutes\n")


# ─────────────────────────────────────────────────────────────────────────────
# main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Unified benchmark runner — Systems A, B, C")
    ap.add_argument("--benchmark", type=Path, required=True,
                    help="Path to the benchmark xlsx file (Benchmark_RERA_V5_Final.xlsx)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Run only 1 question through all systems to verify everything works")
    ap.add_argument("--resume",  action="store_true",
                    help="Skip questions that already have answers in 3_Responses sheet")
    ap.add_argument("--only-system", choices=["A", "B", "C"], default=None,
                    help="Run only one system")
    ap.add_argument("--no-judge",    action="store_true",
                    help="Skip LLM-as-judge scoring (faster, no TOGETHER_API_KEY needed)")
    ap.add_argument("--output", type=Path, default=None,
                    help="Write output to a NEW file (default: write back into benchmark file)")
    args = ap.parse_args()

    benchmark = args.benchmark.resolve()
    if not benchmark.exists():
        print(f"ERROR: Benchmark file not found:\n  {benchmark}")
        sys.exit(1)

    # ── env / model check ────────────────────────────────────────────────────
    if not _check_env():
        sys.exit(1)

    _print_model_banner()

    systems = [args.only_system] if args.only_system else ["A", "B", "C"]

    # ── dry run ──────────────────────────────────────────────────────────────
    if args.dry_run:
        print("\n" + "=" * 65)
        print("  DRY RUN  —  1 question, all systems, full judge")
        print("  (Verifies model, API keys, and ChromaDB are working)")
        print("=" * 65 + "\n")

        # Create a temp output so dry run doesn't pollute the real file
        dry_output = benchmark.parent / f"_dry_run_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"

        cmd = _build_v2_cmd(
            benchmark=benchmark,
            limit=1,
            resume=False,
            no_judge=args.no_judge,
            only_system=args.only_system,
            output=dry_output,
        )

        print(f"  Running: {' '.join(cmd)}\n")
        t0 = time.time()
        result = subprocess.run(cmd, check=False)
        elapsed = time.time() - t0

        print("\n" + "=" * 65)
        if result.returncode == 0:
            print(f"  DRY RUN PASSED  ({elapsed:.0f}s)")
            print(f"  Output file: {dry_output}")
            print("\n  All systems are generating answers correctly.")
            print("  Run without --dry-run for the full 50-question benchmark.")
        else:
            print(f"  DRY RUN FAILED  (exit code {result.returncode})")
            print("  Fix the errors above before running the full benchmark.")
            if dry_output.exists():
                dry_output.unlink()
            sys.exit(result.returncode)
        print("=" * 65 + "\n")
        return

    # ── full run ─────────────────────────────────────────────────────────────
    print("\n" + "=" * 65)
    print("  FULL BENCHMARK  —  50 questions, Systems A/B/C")
    print("=" * 65)

    _eta_message(n_questions=50, systems=systems)

    # Set up auto log path alongside the benchmark file
    log_dir = benchmark.parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp   = datetime.now().strftime("%Y-%m-%d_%H%M")
    log_file = log_dir / f"V3_eval_RERA_{stamp}.log"

    cmd = _build_v2_cmd(
        benchmark=benchmark,
        limit=None,
        resume=args.resume,
        no_judge=args.no_judge,
        only_system=args.only_system,
        output=args.output,
    )

    print(f"  Log file   : {log_file}")
    print(f"  Started at : {datetime.now().strftime('%H:%M:%S')}\n")

    t0 = time.time()
    # Tee to both terminal and log file
    with open(log_file, "w", encoding="utf-8", buffering=1) as lf:
        import io, threading
        def _tee(src, dests):
            for line in iter(src.readline, b""):
                decoded = line.decode("utf-8", errors="replace")
                for d in dests:
                    d.write(decoded)
                    if hasattr(d, "flush"):
                        d.flush()

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        t = threading.Thread(target=_tee, args=(proc.stdout, [sys.stdout, lf]))
        t.start()
        proc.wait()
        t.join()
        returncode = proc.returncode

    elapsed = time.time() - t0
    minutes  = int(elapsed // 60)
    seconds  = int(elapsed % 60)

    print("\n" + "=" * 65)
    model = os.environ.get("LLM_MODEL", "gpt-5-nano")
    if returncode == 0:
        print(f"  BENCHMARK COMPLETE  ({minutes}m {seconds}s)")
        print(f"  Model used  : {model}")
        print(f"  Log saved   : {log_file}")
        print(f"  Output      : {args.output or benchmark}")
    else:
        print(f"  BENCHMARK FAILED  (exit code {returncode}, {minutes}m {seconds}s)")
        print(f"  Log saved   : {log_file}")
        sys.exit(returncode)
    print("=" * 65 + "\n")


if __name__ == "__main__":
    main()
