"""Drive the real rag_query.retrieve() against a fake Chroma that honours the
where-filter, and reproduce the 11 Oct Physiotherapy -> Yoga DRAFT defect.

No index and no embedding model needed, so this runs in CI or a container where
huggingface.co is blocked. It tests the filter contract, not retrieval quality.
"""
from __future__ import annotations

import sys
sys.path.insert(0, "/home/claude/patch")
import rag_query as rq


class Doc:
    def __init__(self, title, tier, text, domain="Healthcare"):
        self.page_content = text
        self.metadata = {"title": title, "tier": tier, "domain": domain,
                         "source_file": title, "bucket": "", "source_url": ""}


# The corpus shape measured on 11 Oct: ~20 near-identical CEA facility
# templates that outrank the one document you actually asked for on a generic
# question, plus a Yoga standards DRAFT that the old fallback surfaced.
#
# CHUNKS, not documents — this is what makes the defect possible. retrieve()
# fetches CANDIDATE_K=24 *chunks*; 20 templates at 2 chunks each is 40 chunks
# ahead of physiotherapy, so the physiotherapy document never enters the
# window the post-filter was looking at, even though it is in the corpus. The
# 2-per-source cap runs AFTER that fetch, so it cannot rescue it.
CORPUS = (
    [Doc(f"Clinical Establishment Standards Template {i:02d}", "A",
         f"generic clause about equipment and infrastructure, part {part}")
     for i in range(20) for part in (1, 2)]
    + [Doc("Yoga Standards DRAFT 2024", "A", "equipment for a yoga centre")]
    + [Doc("Physiotherapy Centre Standards", "A",
           "equipment required in a physiotherapy centre")]
)
# Similarity order: every template chunk and the yoga draft beat physiotherapy,
# which is exactly why a post-filter on the top-24 found nothing to keep.
ORDER = {id(d): i for i, d in enumerate(CORPUS)}


def _matches(md, where):
    if "$and" in where:
        return all(_matches(md, c) for c in where["$and"])
    for field, cond in where.items():
        val = md.get(field)
        if isinstance(cond, dict):
            if "$eq" in cond and val != cond["$eq"]:
                return False
            if "$in" in cond and val not in cond["$in"]:
                return False
        elif val != cond:
            return False
    return True


class FakeStore:
    """Records the where-filter it was handed, and actually applies it."""
    def __init__(self):
        self.last_filter = None
        self._collection = self

    # Chroma collection API used by rag_query.index_titles
    metadata = {"hnsw:space": "l2"}

    def get(self, where=None, include=None):
        return {"metadatas": [d.metadata for d in CORPUS
                              if _matches(d.metadata, where or {})]}

    def similarity_search_with_relevance_scores(self, q, k, filter=None):
        self.last_filter = filter
        hits = [d for d in CORPUS if _matches(d.metadata, filter or {})]
        hits.sort(key=lambda d: ORDER[id(d)])
        # Descending relevance in LangChain's reported space.
        return [(d, 0.60 - 0.01 * i) for i, d in enumerate(hits[:k])]


def old_apply_scope(results, needles, limit):
    """The removed app.py function, verbatim, for comparison."""
    if not needles:
        return results[:limit], False
    keep = [r for r in results
            if any(n in (r["title"] or "").lower() for n in needles)]
    return (keep or results)[:limit], not keep


FAILURES = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got:  {got}")
        print(f"        want: {want}")
        FAILURES.append(label)


print("=" * 74)
print("1. the where-filter handed to Chroma")
print("=" * 74)
s = FakeStore()
rq.retrieve(s, "equipment", "Healthcare", 6)
check("titles=None -> domain + tier, both inside the query",
      s.last_filter,
      {"$and": [{"domain": {"$eq": "Healthcare"}},
                {"tier": {"$in": ["A", "B", "C"]}}]})

rq.retrieve(s, "equipment", "Healthcare", 6,
            titles=["Physiotherapy Centre Standards"])
check("titles=[...] -> scope joins domain and tier in the query",
      s.last_filter,
      {"$and": [{"domain": {"$eq": "Healthcare"}},
                {"tier": {"$in": ["A", "B", "C"]}},
                {"title": {"$in": ["Physiotherapy Centre Standards"]}}]})

s2 = FakeStore()
rq.retrieve(s2, "x", "Healthcare", 6, include_drafts=True, include_review=True)
check("include_drafts + include_review widen the tier list in the query",
      s2.last_filter["$and"][1], {"tier": {"$in": ["A", "B", "C", "D", "E"]}})

print()
print("=" * 74)
print("2. the 11 Oct defect: 'What equipment must be available?' + Physiotherapy")
print("=" * 74)
needles = ["physiotherapy"]

raw = rq.retrieve(FakeStore(), "What equipment must be available?",
                  "Healthcare", 24)
old_results, missed = old_apply_scope(raw, needles, 6)
old_titles = [r["title"] for r in old_results]
print(f"  OLD (post-filter, fallback on): fell_back={missed}")
for t in old_titles[:3]:
    print(f"        {t}")
# The defect, stated so it does not depend on this fixture's similarity order:
# the physiotherapy document never enters the top-24 chunk window, the
# post-filter keeps nothing, and the fallback returns documents from OUTSIDE
# the scope. On the real index on 11 Oct the first of those was a Yoga
# standards DRAFT; here it is a template. Either way it is not physiotherapy.
check("OLD fell back (nothing in the top-24 window matched the scope)",
      missed, True)
check("OLD returns documents from OUTSIDE the scope (the defect, reproduced)",
      [t for t in old_titles if "physiotherapy" not in t.lower()] != [], True)
check("OLD never returns the document actually asked for",
      "Physiotherapy Centre Standards" in old_titles, False)

store = FakeStore()
universe = rq.index_titles(store, "Healthcare")
titles = [t for t in universe if any(n in t.lower() for n in needles)]
new_results = rq.retrieve(store, "What equipment must be available?",
                          "Healthcare", 6, titles=titles)
new_titles = [r["title"] for r in new_results]
print(f"\n  NEW (metadata filter): {len(titles)} document(s) in scope")
for t in new_titles:
    print(f"        {t}")
check("NEW returns only the physiotherapy document",
      new_titles, ["Physiotherapy Centre Standards"])
check("NEW cannot return the Yoga DRAFT",
      "Yoga Standards DRAFT 2024" in new_titles, False)
check("no scoped result lies outside the scope",
      [t for t in new_titles if "physiotherapy" not in t.lower()], [])

print()
print("=" * 74)
print("3. an empty scope must NOT widen")
print("=" * 74)
store = FakeStore()
titles = [t for t in rq.index_titles(store, "Healthcare")
          if "mortuary" in t.lower()]
check("scope resolves to zero documents", titles, [])
print("        app.py: scope_empty -> tells the user, never calls retrieve()")
print("        rag_query --scope: prints 'Nothing was searched', exits 1")

print()
print("=" * 74)
print("4. index_titles reads the collection, tier-filtered")
print("=" * 74)
store = FakeStore()
got = rq.index_titles(store, "Healthcare")
check("all 22 distinct A-tier titles found (from 42 chunks)", len(got), 22)
check("titles come back in original case, not lowered",
      "Physiotherapy Centre Standards" in got, True)
check("tier filter excludes D/E",
      rq.index_titles(store, "Healthcare", tiers=("D",)), [])

print()
print("=" * 74)
print(f"{'FAILED: ' + ', '.join(FAILURES) if FAILURES else 'checks 1-4 passed'}")
print("=" * 74)
if FAILURES:
    sys.exit(1)

# ── appended: the general layer (11 Oct, third design) ──────────────────────
FAILURES.clear()
print()
print("=" * 74)
print("5. narrowing excludes competitors and keeps the general law")
print("=" * 74)

RERA_UNIVERSE = [
    "The Real Estate (Regulation and Development) Act, 2016 (central Act)",
    "Karnataka Real Estate (Regulation and Development) Rules, 2017",
    "RERA bank account directions, 2020",
    "Imposing penalty for Non-Submission of Annual Audit Report 2023-24",
    "Extension of Time for Submission of Annual Audit Report 2023-24",
    "Submission of Annual Audit Report of the financial year 2025-26",
]
spec, gen = rq.resolve_titles(RERA_UNIVERSE, ["2023-24"],
                              lambda t: rq.is_narrow_doc("RERA", t))
check("the FY 2023-24 circulars are the specific half", len(spec), 2)
check("the Act, Rules and bank directions stay in scope", len(gen), 3)
check("a DIFFERENT year's circular is excluded",
      "Submission of Annual Audit Report of the financial year 2025-26"
      in set(spec) | set(gen), False)
check("no title lands in both halves", set(spec) & set(gen), set())

HC_UNIVERSE = [
    "Hospital(Level 3) (734.36 KB )",
    "Clinical Establishment Act Standards for Physiotherapy Centre",
    "DRAFT | YOGA STANDARDS FOR CLINICS/OPD",
    "MSR FOR SIDDHA CLINICAL ESTABLISHMENTS | DRAFT",
    "Clinical Establishments (Central Government) Rules 2012",
    "OPERATIONAL GUIDELINES FOR CLINICAL ESTABLISHMENTS ACT",
]
spec, gen = rq.resolve_titles(HC_UNIVERSE, ["physiotherapy"],
                              lambda t: rq.is_narrow_doc("Healthcare", t))
inscope = set(spec) | set(gen)
check("the physiotherapy document is the specific half",
      spec, ["Clinical Establishment Act Standards for Physiotherapy Centre"])
check("the CEA Rules and Operational Guidelines stay in scope", len(gen), 2)
check("the Level 3 hospital template is excluded (a competitor)",
      "Hospital(Level 3) (734.36 KB )" in inscope, False)
check("the Yoga DRAFT is excluded", any("YOGA" in t for t in inscope), False)
check("the Siddha DRAFT is excluded", any("SIDDHA" in t for t in inscope), False)

print()
print("=" * 74)
print("6. why 'general = matches no dropdown option' was rejected")
print("=" * 74)
DROPDOWN_ONLY = ["hospital(level 3", "physiotherapy", "blood centre"]
complement = [t for t in HC_UNIVERSE
              if not any(n in t.lower() for n in DROPDOWN_ONLY)]
check("the complement rule would re-admit the Yoga DRAFT",
      any("YOGA" in t for t in complement), True)
check("the complement rule would re-admit the Siddha DRAFT",
      any("SIDDHA" in t for t in complement), True)
check("is_narrow_doc covers AYUSH types with no dropdown option",
      rq.is_narrow_doc("Healthcare", "DRAFT | YOGA STANDARDS FOR CLINICS/OPD"),
      True)

print()
print("=" * 74)
print(f"{'FAILED: ' + ', '.join(FAILURES) if FAILURES else 'checks 5-6 passed'}")
print("=" * 74)
if FAILURES:
    sys.exit(1)

# ── appended: tier starvation (11 Oct, the seventh finding) ─────────────────
FAILURES.clear()
print()
print("=" * 74)
print("7. excluded tiers must not eat the candidate budget")
print("=" * 74)

# 30 tier-E chunks outranking 2 tier-A ones. CANDIDATE_K is 24, so a post-fetch
# tier filter sees 24 excluded chunks, discards all of them, and returns
# nothing - which is exactly what happened to "What equipment must be
# available?" on the real index: 0 documents from 161.
STARVE = ([Doc(f"Unverified correspondence {i:02d}", "E", "equipment")
           for i in range(30)]
          + [Doc("Physiotherapy Centre Standards", "A", "equipment required"),
             Doc("CEA Operational Guidelines", "A", "equipment required")])
ORDER.clear()
ORDER.update({id(d): i for i, d in enumerate(STARVE)})
CORPUS.clear()
CORPUS.extend(STARVE)

store = FakeStore()
res = rq.retrieve(store, "What equipment must be available?", "Healthcare", 6)
titles = [r["title"] for r in res]
check("the tier filter is in the query", "tier" in str(store.last_filter), True)
check("retrieval returns the tier-A documents, not nothing", len(titles), 2)
check("no tier-E document is returned",
      [t for t in titles if "Unverified" in t], [])

# Same corpus, with the OLD post-fetch behaviour, to show the starvation.
old_hits = [d for d in CORPUS if d.metadata["domain"] == "Healthcare"]
old_hits.sort(key=lambda d: ORDER[id(d)])
survivors = [d for d in old_hits[:rq.CANDIDATE_K]
             if d.metadata["tier"] in ("A", "B", "C")]
check("OLD behaviour returned nothing from this corpus (starvation)",
      len(survivors), 0)

print()
print("=" * 74)
print(f"{'FAILED: ' + ', '.join(FAILURES) if FAILURES else 'ALL CHECKS PASSED'}")
print("=" * 74)
sys.exit(1 if FAILURES else 0)
