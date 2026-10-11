"""Does the scope dropdown actually change what comes back — and is the
fallback defect gone?

    cd 05_App
    python measure_scope.py

Runs the real retrieve() from rag_query.py against the real index, each question
twice — once unscoped, once with a scope selected — and compares the set of
DOCUMENTS returned.

Two jobs now.

1. MEASUREMENT (the original job, 11 Oct). Decide the feature on evidence. If
   the two sets come back near-identical, the dropdown is theatre and should be
   removed; Prof. Majumdar already said to cut anything that does not visibly
   do something. The 11 Oct run gave 4 changed / 0 identical / 4 fell back, and
   3 of those 4 fallbacks were the defect, not the control.

2. REGRESSION (the new job). The scope is now a metadata filter inside the
   Chroma query rather than a post-filter on the results, so:
     * no case may FALL BACK — out-of-scope documents are now unreachable, and
       any appearing here means the filter is not being applied;
     * every scoped result must lie inside its scope — asserted per document,
       not inferred from the absence of a fallback flag;
     * the UNSCOPED column must be unchanged from 11 Oct. retrieve() with
       titles=None is the untouched code path, so any drift means something
       else moved. The baseline is written on first run and compared after
       that; the one case recorded verbatim in the handover is asserted
       outright.

No API key needed — retrieval only, nothing is generated.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rag_query as rq  # noqa: E402
# is_narrow_doc lives in rag_query, so this test and the app read one copy of
# what "narrowing the search" means and cannot drift apart.

BASELINE = Path(__file__).resolve().parent.parent / "06_Evaluation" / "scope_baseline.json"

# Copied verbatim from app.py so this tests what the app actually does.
FACILITIES = {
    "Hospital — Level 2": ["hospital(level 2", "hospital (level 2"],
    "Hospital — Level 3": ["hospital(level 3", "hospital (level 3"],
    "Clinic or polyclinic": ["clinic_polyclinic", "clinic/polyclinic"],
    "Physiotherapy centre": ["physiotherapy"],
    "Blood centre": ["blood centre", "blood transfusion"],
}

CASES = [
    # Healthcare — the template-flooding cases
    ("Healthcare", "How long must patient medical records be retained?",
     "Hospital — Level 3", FACILITIES["Hospital — Level 3"]),
    ("Healthcare", "What are the minimum staffing requirements?",
     "Hospital — Level 3", FACILITIES["Hospital — Level 3"]),
    ("Healthcare", "What are the minimum infrastructure and space requirements?",
     "Clinic or polyclinic", FACILITIES["Clinic or polyclinic"]),
    # THE defect case: no title match in the unscoped top-24, so the old code
    # fell back to the wide search and answered with a Yoga standards DRAFT.
    ("Healthcare", "What equipment must be available?",
     "Physiotherapy centre", FACILITIES["Physiotherapy centre"]),
    ("Healthcare", "What must be reported to the district authorities?",
     "Blood centre", FACILITIES["Blood centre"]),
    # RERA — the financial-year confusion cases
    ("RERA", "What penalty applies for not submitting the annual audit report?",
     "FY 2023-24", ["2023-24"]),
    ("RERA", "What was the deadline to submit the annual audit report?",
     "FY 2024-25", ["2024-25"]),
    # A control: scoping should NOT help here, the subject is year-independent
    ("RERA", "What share of money from allottees goes into a separate account?",
     "FY 2023-24", ["2023-24"]),
]

# Recorded verbatim in the handover (§5) from the 11 Oct run. The unscoped
# result for this question is the sharpest evidence we have that the dropdown
# earns its place — 0% document overlap — so it is asserted rather than merely
# baselined.
# Each needle must be a substring of a REAL title as the index stores it, not a
# word from the handover's prose description of it. "gazette" was the latter and
# failed on 11 Oct even though the document was present: the handover called it
# "a Hindi gazette", but the title is
# "5001 GI/2023 (1) | रजिस्ट्री सं. डी.एल.- 33004/99 REGD. No. D. L.-33004/99".
KNOWN_11_OCT_UNSCOPED = {
    ("Healthcare", "How long must patient medical records be retained?"): [
        "cosmetology", "ethics", "5001 gi/2023", "mobile clinic",
        "blood centre", "telemedicine",
    ],
}


def docs_of(results):
    seen, out = set(), []
    for r in results:
        if r["title"] not in seen:
            seen.add(r["title"])
            out.append(r["title"])
    return out


def current_tiers(store, domain: str) -> dict[str, str]:
    """title -> tier, straight from the index.

    Lets a change in the unscoped column be diagnosed rather than merely
    reported: a document that vanished because its tier was deliberately
    changed is not the same event as one that vanished because code broke.
    """
    out: dict[str, str] = {}
    try:
        got = store._collection.get(where={"domain": domain},
                                    include=["metadatas"])
    except Exception:                                           # noqa: BLE001
        return out
    for md in (got.get("metadatas") or []):
        if md and md.get("title"):
            out[md["title"]] = md.get("tier") or "?"
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Measure the scope filter.")
    ap.add_argument("--rebaseline", action="store_true",
                    help="overwrite the unscoped baseline. Use ONLY after an "
                         "intended change to the index or to retrieval, and "
                         "read the drift report first.")
    args = ap.parse_args()

    store = rq.load_store()
    print(f"index distance metric: {rq.index_space(store)}")
    print(f"{len(CASES)} questions, each run unscoped then scoped\n")

    old = {}
    if BASELINE.exists() and not args.rebaseline:
        try:
            old = json.loads(BASELINE.read_text(encoding="utf-8"))
            print(f"comparing the unscoped column against {BASELINE.name}\n")
        except Exception:                                       # noqa: BLE001
            print(f"! {BASELINE.name} unreadable — treating this run as the baseline\n")
    elif args.rebaseline:
        print(f"--rebaseline: {BASELINE.name} will be overwritten\n")
    else:
        print(f"no baseline yet — this run writes {BASELINE.name}\n")

    changed = identical = empty = 0
    leaks = []          # scoped results outside their scope — must stay empty
    drift = []          # unscoped results that moved since the baseline
    fresh = {}          # unscoped column -> baselined
    scoped_now = {}     # scoped column -> asserted, not baselined
    tiers = {}          # domain -> {title: tier}, for diagnosing drift

    for domain, q, scope_name, needles in CASES:
        plain = rq.retrieve(store, q, domain, 6)

        universe = rq.index_titles(store, domain)
        tiers.setdefault(domain, current_tiers(store, domain))
        specific, general = rq.resolve_titles(
            universe, needles, lambda t: rq.is_narrow_doc(domain, t))
        titles = sorted(set(specific) | set(general))
        scoped = (rq.retrieve(store, q, domain, 6, titles=titles)
                  if titles else [])

        a, b = docs_of(plain), docs_of(scoped)
        union = set(a) | set(b)
        overlap = len(set(a) & set(b)) / max(1, len(union))

        # Regression 1: every scoped document must be one the filter allowed -
        # checked against the exact allowed set, not by re-matching needles, so
        # a bug in the needle logic cannot make this pass by accident.
        for t in b:
            if t not in titles:
                leaks.append((domain, q, scope_name, t))

        # Regression 2: the unscoped column must not have moved.
        key = f"{domain}||{q}"
        fresh[key] = a
        if key in old and old[key] != a:
            drift.append((key, old[key], a))
        # The scoped column is not baselined: it is the thing being changed.
        # Only the unscoped column is a fixed point.

        scoped_now[f"{domain}||{q}"] = (b, general)
        if not titles:
            verdict = (f"NO DOCUMENTS IN SCOPE — reported to the user, "
                       f"nothing searched outside it")
            empty += 1
        elif set(a) == set(b):
            verdict = "IDENTICAL — the scope changed nothing"
            identical += 1
        else:
            verdict = f"CHANGED — {overlap:.0%} of documents in common"
            changed += 1

        print("=" * 78)
        print(f"[{domain}] {q}")
        print(f"  scope: {scope_name}  ({len(specific)} specific "
              f"+ {len(general)} always-applicable = {len(titles)} in scope)")
        print(f"  VERDICT: {verdict}")
        print(f"  unscoped — {len(a)} documents:")
        for t in a:
            print(f"        {str(t)[:72]}")
        print(f"  scoped — {len(b)} documents   "
              f"(* = not in the unscoped result, [gen] = applies throughout):")
        for t in b:
            tag = " [gen]" if t in general else ""
            print(f"      {'*' if t not in a else ' '} {str(t)[:66]}{tag}")

    print("=" * 78)
    print(f"\nSUMMARY   changed: {changed}   identical: {identical}   "
          f"empty scope: {empty}   (of {len(CASES)})")
    print("          fell back: 0 — not reachable by construction; "
          "the filter is in the query")

    # ── regression report ────────────────────────────────────────────────────
    print("\n" + "=" * 78)
    print("REGRESSION")
    ok = True

    if leaks:
        ok = False
        print(f"\n  FAIL  {len(leaks)} scoped result(s) came from OUTSIDE the scope.")
        print("        The metadata filter is not being applied. This is the "
              "11 Oct defect.")
        for domain, q, scope_name, t in leaks[:10]:
            print(f"          [{domain}] {scope_name}: {str(t)[:60]}")
    else:
        print("\n  PASS  every scoped result lies inside its scope")

    if drift:
        # A document can leave the unscoped column for two very different
        # reasons: its tier was deliberately changed (so it is no longer
        # retrievable, which is the fix working), or retrieval broke. Look up
        # each dropped title's CURRENT tier and say which happened, instead of
        # reporting both as the same failure.
        unexplained = []
        for key, was, now in drift:
            dom = key.split("||", 1)[0]
            t_now = tiers.get(dom, {})
            gone = [t for t in was if t not in now]
            retiered = [t for t in gone
                        if t_now.get(t) not in (None, "A", "B", "C")]
            rest = [t for t in gone if t not in retiered]
            added = [t for t in now if t not in was]
            if retiered:
                print(f"\n  ----  {key}")
                print(f"        {len(retiered)} document(s) left because they "
                      f"are no longer in a retrievable tier:")
                for t in retiered:
                    print(f"          tier {t_now.get(t)}  {str(t)[:60]}")
                print("        That is an intended index change, not drift.")
            if rest or added:
                unexplained.append((key, rest, added, dom))

        if unexplained:
            ok = False
            print(f"\n  FAIL  the unscoped column moved for "
                  f"{len(unexplained)} question(s) with no tier change to "
                  f"explain it.")
            for key, rest, added, dom in unexplained[:5]:
                print(f"          {key}")
                for t in rest:
                    print(f"            gone  (tier {tiers.get(dom,{}).get(t,'?')})"
                          f"  {str(t)[:52]}")
                for t in added:
                    print(f"            new   (tier {tiers.get(dom,{}).get(t,'?')})"
                          f"  {str(t)[:52]}")
            print("\n        If this change was intended, re-run with "
                  "--rebaseline to accept it.")
        else:
            print("\n  PASS  every change in the unscoped column is explained "
                  "by an intended re-tiering")
    elif old:
        print("  PASS  the unscoped column is unchanged from the baseline")
    else:
        print("  ----  no baseline to compare against; writing one now")

    for (domain, q), needles_expected in KNOWN_11_OCT_UNSCOPED.items():
        titles_now = fresh.get(f"{domain}||{q}", [])
        got = " | ".join(titles_now).lower()
        missing = [n for n in needles_expected if n not in got]
        if missing:
            ok = False
            print(f"\n  FAIL  the 11 Oct unscoped result for {q!r} no longer "
                  f"contains: {missing}")
            print("        Handover §5 recorded Cosmetology Clinic, Ethics "
                  "Regulations, a Hindi gazette, Mobile Clinic, Blood Centres "
                  "and Telemedicine.")
            print("        NOTE: that run predates the tier filter moving into "
                  "the query. 38% of\n        Healthcare chunks were being "
                  "fetched and discarded, so the 11 Oct result\n        was "
                  "shaped by a defect. A different set here may be BETTER, not "
                  "worse -\n        judge it on whether the documents answer "
                  "the question.")
            print("        what came back instead:")
            for t in titles_now:
                print(f"          tier {tiers.get(domain, {}).get(t, '?')}  "
                      f"{str(t)[:62]}")
        else:
            print(f"\n  PASS  the 11 Oct unscoped result for this question is "
                  f"reproduced ({len(needles_expected)}/"
                  f"{len(needles_expected)} documents)")

    # ── the control case ─────────────────────────────────────────────────────
    # §5 designated this one as the control: the subject is year-independent, so
    # scoping should not help. On 11 Oct the old post-filter fell back and got
    # it right by accident. The strict filter then got it WRONG - it returned
    # two audit-penalty circulars, while the answer (s.4(2)(l)(D) and the 2020
    # bank-account directions) carries no financial year in its title. That is
    # what the foundational layer exists to fix, so it is asserted here.
    ctl_key = ("RERA||What share of money from allottees goes into a separate "
               "account?")
    if ctl_key in scoped_now:
        titles_b, general_b = scoped_now[ctl_key]
        from_general = [t for t in titles_b if t in general_b]
        if not from_general:
            ok = False
            print(f"\n  FAIL  the control case returned no always-applicable "
                  f"document.")
            print("        A year-independent question scoped to a financial "
                  "year must still reach the Act and the Rules.")
            print("        Check FACILITY_MARKERS / the financial-year rule in "
                  "rag_query.is_narrow_doc\n        (run audit_scope.py to see "
                  "how each title is classified).")
            for t in titles_b:
                print(f"          {str(t)[:68]}")
        else:
            print(f"\n  PASS  the control case reaches "
                  f"{len(from_general)} always-applicable document(s)")
            for t in from_general:
                print(f"          {str(t)[:68]}")

    if not old:
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE.write_text(json.dumps(fresh, indent=2, ensure_ascii=False),
                            encoding="utf-8")
        print(f"\n  baseline written: {BASELINE}")
        print("  Commit it. Re-running compares against it from now on.")

    print("\n" + "=" * 78)
    if not ok:
        print("REGRESSION FAILED — do not deploy this build.")
        return 1
    print("REGRESSION PASSED")
    if changed == 0 and empty == 0:
        print("\n=> The dropdown does nothing measurable. Remove it.")
    elif changed >= len(CASES) * 0.5:
        print("\n=> The dropdown materially changes retrieval. Keep it, and make"
              "\n   the change visible in the interface.")
    else:
        print("\n=> Mixed. Keep the options that demonstrably work; for the ones"
              "\n   with no documents in scope, the dropdown now says so instead"
              "\n   of quietly widening the search.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
