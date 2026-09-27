"""
Indian Regulatory Compliance Intelligence Assistant
Domain-filtered, authority-aware retrieval and answer generation.

Retrieval respects the corpus authority tiers, which is the point of the whole
corpus structure:

    A  Core regulatory              Acts, Rules, Regulations, gazette notifications,
                                    binding circulars, PM-JAY instruments that create
                                    entitlements or obligations.   CITABLE AS BINDING.
    B  Sector regulation            NMC regulations and medical-education standards.
    C  Supporting guidance          Guidelines, FAQs, manuals, SOPs, model forms.
                                    Explanatory only - NOT binding law.
    D  Draft / proposed             Drafts and proposals.  NEVER present as binding.
    E  Review                       Binding status unverified, or administrative
                                    correspondence.  Excluded from retrieval by default.

Usage:
    # retrieval only - no LLM, no API key needed. Works as soon as the index exists.
    python rag_query.py --retrieve-only --domain RERA "penalty for late quarterly updates"

    # full answer
    python rag_query.py --domain Healthcare "how long must indoor patient records be kept?"

    # include draft material, clearly labelled
    python rag_query.py --domain RERA --include-drafts "proposed amendments to TEQ regulations"

Configuration:
    Copy .env.example to .env and set OPENAI_API_KEY. .env is gitignored - never commit it.
"""

from __future__ import annotations

import argparse
import os
import sys
import textwrap
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
INDEX_DIR    = PROJECT_ROOT / "05_App" / "chroma_db"

EMBED_MODEL  = "BAAI/bge-small-en-v1.5"
COLLECTION   = "regulatory_corpus"
LLM_MODEL    = os.getenv("LLM_MODEL", "gpt-5-nano")
LLM_BASE_URL = os.getenv("LLM_BASE_URL")            # unset = OpenAI default

TOP_K          = 6      # matches the cohort retrieval setting
CANDIDATE_K    = 24     # over-fetch, then re-rank by authority tier

TIER_WEIGHT = {"A": 1.00, "B": 0.95, "C": 0.80, "D": 0.55, "E": 0.35}
TIER_NAME = {
    "A": "Core regulatory (binding)",
    "B": "Sector regulation (binding within sector)",
    "C": "Supporting guidance (explanatory, not binding)",
    "D": "DRAFT / PROPOSED (not binding)",
    "E": "Review (binding status unverified)",
}

DOMAINS = {"Healthcare", "RERA"}

SYSTEM_PROMPT = """You are an Indian regulatory compliance assistant. You answer only from the \
retrieved sources supplied to you.

Structure every answer as:
1. A direct answer to the question.
2. The sources relied on, cited by their document TITLE (never a filename).
3. The regulatory basis - the specific provision, section, rule or clause.
4. A concise compliance takeaway.

Hard rules, in order of priority:
- If the sources do not contain sufficient evidence, say so explicitly and stop. Never fill a \
gap from your own knowledge. An honest "the corpus does not settle this" is a correct answer.
- Material marked DRAFT / PROPOSED is not binding regulation. If you use it, say plainly that \
it is a draft or proposal and has no binding force.
- Material marked Supporting guidance explains or operationalises the law; it does not create \
obligations on its own. Where it conflicts with Core regulatory material, the Core material governs.
- Material marked Review has unverified binding status. Flag it as unverified if you use it.
- Never invent a section number, a date, a monetary figure or a citation. If a figure is not in \
the sources, say it is not stated.
- Where the question rests on a false premise, correct the premise before answering."""


# ── retrieval ────────────────────────────────────────────────────────────────
def load_store():
    if not INDEX_DIR.exists():
        sys.exit(f"ERROR: no index at {INDEX_DIR}\nRun: python ingest_corpus.py")
    from langchain_huggingface import HuggingFaceEmbeddings
    from langchain_chroma import Chroma
    embeddings = HuggingFaceEmbeddings(
        model_name=EMBED_MODEL, encode_kwargs={"normalize_embeddings": True})
    return Chroma(collection_name=COLLECTION, embedding_function=embeddings,
                  persist_directory=str(INDEX_DIR))


def index_space(store) -> str:
    """Which distance metric the Chroma collection was actually built with.

    ingest_corpus.py calls Chroma.from_documents without collection_metadata, so
    Chroma uses its DEFAULT L2 space - even though the embeddings are unit-
    normalised for cosine. Detect rather than assume, so this keeps working if
    the index is later rebuilt with {"hnsw:space": "cosine"}.
    """
    try:
        return ((store._collection.metadata or {}).get("hnsw:space") or "l2").lower()
    except Exception:
        return "l2"


def to_cosine(score: float, space: str) -> float:
    """Convert LangChain's reported relevance score to true cosine similarity.

    For a cosine collection LangChain reports 1 - distance, which IS the cosine.
    For an L2 collection it reports 1 - L2/sqrt(2), which for UNIT vectors runs
    from 1.0 down to about -0.414 and is not a similarity at all: a genuine
    cosine of 0.55 is reported as 0.33.

    For unit vectors  d = sqrt(2) * (1 - score)  and  cos = 1 - d^2 / 2,
    so  cos = 1 - (1 - score)^2.  Exact, not an approximation - verified by a
    round trip over cos in [-1, 1].
    """
    if space.startswith("cosine"):
        return score
    return 1.0 - (1.0 - score) ** 2


def retrieve(store, question: str, domain: str, k: int = TOP_K,
             include_drafts: bool = False, include_review: bool = False):
    """Domain-filtered retrieval, re-ranked so authority outranks raw similarity."""
    allowed = {"A", "B", "C"}
    if include_drafts: allowed.add("D")
    if include_review: allowed.add("E")

    hits = store.similarity_search_with_relevance_scores(
        question, k=CANDIDATE_K, filter={"domain": domain})
    space = index_space(store)

    ranked = []
    for doc, raw in hits:
        tier = doc.metadata.get("tier", "E")
        if tier not in allowed:
            continue
        score = to_cosine(raw, space)
        # Weight a NON-NEGATIVE relevance, never a raw cosine. Multiplying a
        # negative score by a tier weight inverts the ranking: -0.30 * 1.00 (A)
        # = -0.30 sorts BELOW -0.30 * 0.80 (C) = -0.24, so a weak match in
        # non-binding guidance would outrank a weak match in the Act itself.
        # Authority would invert exactly when matches are poor - when the tier
        # system matters most. (cos + 1) / 2 is order-preserving and in [0, 1].
        rel = (score + 1.0) / 2.0
        ranked.append((rel * TIER_WEIGHT.get(tier, 0.3), score, tier, doc))
    ranked.sort(key=lambda r: r[0], reverse=True)

    # keep at most 2 chunks per document so one long PDF cannot crowd out the rest
    seen, out = {}, []
    for adj, raw, tier, doc in ranked:
        key = doc.metadata.get("source_file", "")
        if seen.get(key, 0) >= 2:
            continue
        seen[key] = seen.get(key, 0) + 1
        out.append({"adjusted": adj, "similarity": raw, "tier": tier,
                    "title": doc.metadata.get("title") or key,
                    "source_file": key,
                    "bucket": doc.metadata.get("bucket", ""),
                    "url": doc.metadata.get("source_url", ""),
                    "text": doc.page_content})
        if len(out) >= k:
            break
    return out


def format_sources(results) -> str:
    blocks = []
    for i, r in enumerate(results, 1):
        head = f"[{i}] {r['title']}\n    authority: {TIER_NAME[r['tier']]}"
        if r["url"]:
            head += f"\n    url: {r['url']}"
        blocks.append(f"{head}\n    ---\n{textwrap.indent(r['text'].strip(), '    ')}")
    return "\n\n".join(blocks)


# ── generation ───────────────────────────────────────────────────────────────
def load_env():
    """Minimal .env reader - avoids a python-dotenv dependency."""
    env = PROJECT_ROOT / "05_App" / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def generate(question: str, results, domain: str) -> str:
    load_env()
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        sys.exit("ERROR: OPENAI_API_KEY not set.\n"
                 "Copy 05_App/.env.example to 05_App/.env and put your key there.\n"
                 "Or run with --retrieve-only to test retrieval without an LLM.")
    try:
        from openai import OpenAI
    except ImportError:
        sys.exit("ERROR: pip install openai")

    client = OpenAI(api_key=key, base_url=LLM_BASE_URL) if LLM_BASE_URL else OpenAI(api_key=key)
    user = (f"Domain: {domain}\n\nQuestion: {question}\n\n"
            f"Retrieved sources:\n\n{format_sources(results)}")
    resp = client.chat.completions.create(
        model=LLM_MODEL,
        messages=[{"role": "system", "content": SYSTEM_PROMPT},
                  {"role": "user", "content": user}],
    )
    return resp.choices[0].message.content


# ── cli ──────────────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description="Query the regulatory corpus.")
    ap.add_argument("question", nargs="+")
    ap.add_argument("--domain", required=True, choices=sorted(DOMAINS))
    ap.add_argument("--k", type=int, default=TOP_K)
    ap.add_argument("--retrieve-only", action="store_true",
                    help="show retrieved passages, call no LLM")
    ap.add_argument("--include-drafts", action="store_true",
                    help="allow tier D (draft/proposed) into retrieval")
    ap.add_argument("--include-review", action="store_true",
                    help="allow tier E (unverified) into retrieval")
    args = ap.parse_args()
    question = " ".join(args.question)

    store = load_store()
    results = retrieve(store, question, args.domain, args.k,
                       args.include_drafts, args.include_review)

    if not results:
        print(f"\nNo sources retrieved for domain {args.domain}.")
        print("The corpus does not contain evidence to answer this question.")
        return 0

    print(f"\nQUESTION [{args.domain}]: {question}")
    print("=" * 78)
    print(f"\nRETRIEVED {len(results)} passages:\n")
    for i, r in enumerate(results, 1):
        print(f"  [{i}] tier {r['tier']}  sim {r['similarity']:.3f}  {r['title'][:66]}")
    if any(r["tier"] == "D" for r in results):
        print("\n  ! contains DRAFT/PROPOSED material - not binding")
    if any(r["tier"] == "E" for r in results):
        print("\n  ! contains REVIEW material - binding status unverified")

    if args.retrieve_only:
        print("\n" + "=" * 78)
        for i, r in enumerate(results, 1):
            print(f"\n[{i}] {r['title']}\n    {TIER_NAME[r['tier']]}  |  {r['bucket']}")
            print(textwrap.indent(textwrap.fill(r["text"][:700], 74), "    "))
        return 0

    print("\n" + "=" * 78)
    print(generate(question, results, args.domain))
    return 0


if __name__ == "__main__":
    sys.exit(main())
