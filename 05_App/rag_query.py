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
import re
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

# ── what "narrowing the search" means ────────────────────────────────────────
# Which documents speak for ONE facility type or ONE financial year. Choosing a
# scope excludes the documents tied to a DIFFERENT one and keeps everything
# general. Measured against the real index on 11 Oct 2026: only 17 of 161
# retrievable Healthcare documents are tied to a facility type, and 13 of 53
# RERA documents to a year, so the general law is the large majority and
# excluding competitors is derivable rather than a 144-entry allowlist.
#
# This lives here, not in app.py, because it is a fact about the corpus rather
# than about the interface: app.py, measure_scope.py and audit_scope.py all
# read it from here so they cannot drift apart.
#
# The AYUSH entries matter and are not padding. Yoga, siddha and the rest have
# their own facility standards but no dropdown entry, so without them a
# Physiotherapy search would treat DRAFT YOGA STANDARDS as a general document
# and return it - the 11 Oct defect in a new disguise.
FACILITY_MARKERS = [
    "hospital(level", "hospital (level",
    "clinic_polyclinic", "clinic/polyclinic",
    "mobile clinic", "mobile dental van", "dental lab",
    "cosmetology", "hair transplant", "physiotherapy",
    "blood centre", "blood transfusion", "mortuary", "sample collection",
    "clinical establishment act standards for",
    "yoga", "siddha", "ayurveda", "unani", "naturopathy",
    "homeopathy", "sowa-rigpa", "sowa rigpa",
]

# RERA documents that speak for one stakeholder role only (circulars,
# notifications, guidelines). The Act, Karnataka Rules, and bank-account
# directions contain NONE of these markers and are correctly classified as
# general (always returned regardless of role selection).
RERA_NARROW_MARKERS = [
    "circular", "notification", "corrigendum", "press release",
    "public notice", "public notification", "guidelines for",
    "model form", "procedure for",
    "quarterly", "annual audit", "audit report",
    "bank account", "fees and head", "fee for", "revised charges",
    "penalty",
    "extension of time", "extension of registration", "force majeure",
    "real estate agent", "land owner", "ongoing project",
    "allotment", "conciliation", "monetary",
    "appellate", "k-reat",
    "change of project", "transferring",
    "national building code",
    "ಪುನರಾವೃತ",
]


def is_narrow_doc(domain: str, title: str) -> bool:
    """True when a document speaks for one facility type or one stakeholder role only."""
    tl = (title or "").lower()
    if domain == "RERA":
        return any(m in tl for m in RERA_NARROW_MARKERS)
    return any(m in tl for m in FACILITY_MARKERS)

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


def index_titles(store, domain: str,
                 tiers: tuple[str, ...] = ("A", "B", "C")) -> list[str]:
    """Distinct document titles actually present in the index for a domain.

    Read from the collection, NOT from 03_Metadata/ingestion_report.csv. The CSV
    records what ingestion intended; the collection is what retrieval will
    actually match on. A scope filter built from the CSV returns nothing at all
    if even one title was normalised differently at write time - and it would
    fail silently, which is the failure mode this project keeps hitting.

    Returns the exact strings stored in metadata, original case preserved, so
    they can be passed straight back as a `title` filter.
    """
    try:
        got = store._collection.get(where={"domain": domain},
                                    include=["metadatas"])
    except Exception:                                           # noqa: BLE001
        return []
    seen = {}
    for md in (got.get("metadatas") or []):
        if not md or md.get("tier") not in tiers:
            continue
        title = md.get("title")
        if title:
            seen[title] = True
    return sorted(seen)


def resolve_titles(universe: list[str], needles, is_narrow=None
                   ) -> tuple[list[str], list[str]]:
    """Split a title universe into (matches this scope, applies to every scope).

    `is_narrow(title) -> bool` says whether a document is tied to ONE particular
    facility type or financial year. A scope then excludes only the documents
    tied to a DIFFERENT one, and keeps everything general.

    This is the third design, and the measurements that killed the first two are
    worth keeping:

    1. Post-filter the top 24 results by title, fall back to the unscoped list
       when nothing matches. Measured: "What equipment must be available?"
       scoped to Physiotherapy returned templates for other facilities, because
       ~20 near-identical CEA templates fill the 24-CHUNK window before the
       physiotherapy document is reached. Silent, and wrong.

    2. Restrict strictly to documents whose title matches the scope. Measured
       against the real index: a scope reaches 16 of 161 retrievable Healthcare
       documents and 13 of 53 RERA ones, so selecting anything hid 90% and 75%
       of the library respectively. Scoping FY 2023-24 and asking what share of
       allottees' money goes into a separate account returned two audit-penalty
       circulars; the answer is in s.4(2)(l)(D) of the Act and the 2020
       bank-account directions, neither of which carries a year in its title.

    3. This one. Only 17 of 161 Healthcare documents are tied to a facility type
       and only 13 of 53 RERA documents to a year, so "exclude the competitors,
       keep the general law" is derivable rather than a 144-entry allowlist -
       and it is what a reader means by narrowing a search.

    Note that an allowlist of foundational titles was tried in between and
    rejected for being unmaintainable at 144 entries, while the complement rule
    ("general = matches no dropdown option") is unsafe on its own: yoga and
    siddha are not dropdown options, so their DRAFT standards would read as
    general. is_narrow therefore covers facility types the dropdown does NOT
    offer, which is what keeps them out.
    """
    nl = [n.lower() for n in needles or ()]
    specific = [t for t in universe if any(n in (t or "").lower() for n in nl)]
    if is_narrow is None:
        return specific, []
    sset = set(specific)
    general = [t for t in universe if t not in sset and not is_narrow(t)]
    return specific, general


def retrieve(store, question: str, domain: str, k: int = TOP_K,
             include_drafts: bool = False, include_review: bool = False,
             titles: list[str] | None = None):
    """Domain-filtered retrieval, re-ranked so authority outranks raw similarity.

    `titles` restricts the search to those exact document titles, as a metadata
    filter inside the Chroma query. This is the scope mechanism, and it has to
    happen HERE rather than as a post-filter on the results.

    Post-filtering was the defect measured on 11 Oct: fetch the top 24 by
    similarity, then keep the ones whose title matches the chosen scope. When
    ~20 near-identical facility templates outrank the one document you asked
    for, nothing in the top 24 matches, and the old code fell back to the
    unscoped list - answering a question about physiotherapy equipment with a
    Yoga standards DRAFT. The scope was a preference applied after the fact; it
    is now a constraint applied before.

    With `titles` set, the search cannot leave the scope. If the scope contains
    no documents, the caller must say so - not widen. Callers therefore resolve
    the title list first (see index_titles) and decide explicitly.
    """
    allowed = {"A", "B", "C"}
    if include_drafts: allowed.add("D")
    if include_review: allowed.add("E")

    # Every filter goes INTO the query. The tier filter used to run on the
    # results, after CANDIDATE_K chunks had already been fetched - and 38% of
    # Healthcare chunks and 29% of RERA chunks are in excluded tiers (D draft,
    # E unverified), so excluded material consumed the candidate budget and was
    # then discarded. Measured 11 Oct 2026: "What equipment must be available?"
    # returned ZERO documents unscoped, and "What are the minimum staffing
    # requirements?" returned one, because the top 24 chunks were nearly all
    # tier D/E. The corpus answers both questions; retrieval never saw them.
    #
    # Same defect as the scope post-filter, in a different place: a constraint
    # applied after retrieval cannot widen the candidate pool it needed.
    conds: list[dict] = [{"domain": {"$eq": domain}},
                         {"tier": {"$in": sorted(allowed)}}]
    if titles:
        conds.append({"title": {"$in": list(titles)}})
    where = {"$and": conds}

    hits = store.similarity_search_with_relevance_scores(
        question, k=CANDIDATE_K, filter=where)
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
    ap.add_argument("--scope", action="append", metavar="SUBSTRING",
                    help="restrict the search to documents whose title contains "
                         "this (repeatable, case-insensitive). Mirrors the app's "
                         "scope dropdown.")
    ap.add_argument("--list-titles", action="store_true",
                    help="print the document titles retrieval can match, and exit")
    args = ap.parse_args()
    question = " ".join(args.question)

    store = load_store()

    if args.list_titles:
        for t in index_titles(store, args.domain):
            print(t)
        return 0

    scope_titles = None
    if args.scope:
        needles = [s.lower() for s in args.scope]
        universe = index_titles(store, args.domain)
        scope_titles = [t for t in universe
                        if any(n in t.lower() for n in needles)]
        print(f"\nscope {args.scope} -> {len(scope_titles)} document(s) "
              f"of {len(universe)} in {args.domain}")
        for t in scope_titles:
            print(f"    {t[:72]}")
        if not scope_titles:
            # Do NOT widen. A silent fall back to the whole corpus is the bug.
            print("\nNo document in the corpus matches that scope. "
                  "Nothing was searched.\n"
                  "Run with --list-titles to see what is available.")
            return 1

    results = retrieve(store, question, args.domain, args.k,
                       args.include_drafts, args.include_review,
                       titles=scope_titles)

    if not results:
        where = f" within the selected scope" if scope_titles else ""
        print(f"\nNo sources retrieved for domain {args.domain}{where}.")
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
