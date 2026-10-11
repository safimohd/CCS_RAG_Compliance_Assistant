"""Print every retrievable document title and how the scope logic classifies it.

    cd 05_App
    python audit_scope.py > scope_audit.txt

Three buckets per domain:

  SPECIFIC       reachable by at least one dropdown option.
  GENERAL        not tied to any one facility or year, so in scope whatever is
                 selected. This is the large majority.
  UNREACHABLE    tied to a facility type or year that has no dropdown option,
                 so only reachable with no scope selected. Each is either worth
                 a new dropdown option, or correctly excluded.

The UNREACHABLE bucket is the point: it is the only place where narrowing the
search can hide a document.

No API key needed. Needs the index and the embedding model, so run it on the
laptop.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rag_query as rq  # noqa: E402

# Imported from app.py so this audits what the app actually uses. app.py is a
# Streamlit script, so pull the constants out of its source rather than
# importing it (importing would try to render a page).
APP = (Path(__file__).resolve().parent / "app.py").read_text(encoding="utf-8")
_ns: dict = {}
for block in ("HEALTHCARE_FACILITIES", "RERA_YEARS"):
    start = APP.index(f"{block} = ")
    depth, i = 0, APP.index("=", start) + 1
    while True:
        ch = APP[i]
        if ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
            if depth == 0:
                i += 1
                break
        i += 1
    exec(f"{block} = " + APP[APP.index('=', start) + 1:i], _ns)   # noqa: S102

HEALTHCARE_FACILITIES = _ns["HEALTHCARE_FACILITIES"]
RERA_YEARS = _ns["RERA_YEARS"]

OPTIONS = {
    "Healthcare": {k: v for k, v in HEALTHCARE_FACILITIES.items() if v},
    "RERA": {f"FY {y}": [y] for y in RERA_YEARS[1:]},
}


def main() -> int:
    store = rq.load_store()
    total_unreachable = 0

    for domain in ("Healthcare", "RERA"):
        universe = rq.index_titles(store, domain)
        options = OPTIONS[domain]

        reach: dict[str, list[str]] = {t: [] for t in universe}
        for label, needles in options.items():
            for t in universe:
                if any(n.lower() in t.lower() for n in needles):
                    reach[t].append(label)

        # A document is GENERAL when it is not tied to one facility or year,
        # so it stays in scope whatever is selected.
        founds = {t for t in universe if not rq.is_narrow_doc(domain, t)}

        specific = [t for t in universe if reach[t]]
        found_only = [t for t in universe if t in founds and not reach[t]]
        unreachable = [t for t in universe if not reach[t] and t not in founds]
        total_unreachable += len(unreachable)

        print("=" * 78)
        print(f"{domain}: {len(universe)} retrievable documents "
              f"(tiers A/B/C), {len(options)} scope options")
        print("=" * 78)

        print(f"\n  SPECIFIC — reachable by a dropdown option ({len(specific)})")
        for t in specific:
            print(f"    [{', '.join(reach[t])}]")
            print(f"        {t[:90]}")

        print(f"\n  GENERAL — in scope whatever is selected ({len(found_only)})")
        for t in found_only:
            print(f"        {t[:90]}")
        if not found_only:
            print("        (none — every scope selection currently hides the "
                  "standing law)")

        print(f"\n  UNREACHABLE — tied to a facility/year with no dropdown "
              f"option ({len(unreachable)})")
        print("        These are reachable only with no scope selected. Each is "
              "either worth a new\n        dropdown option, or correctly "
              "excluded (a draft for an AYUSH facility type).")
        for t in unreachable:
            print(f"        {t[:90]}")

        # Options that match nothing are dead controls. Prof. Majumdar's
        # standing instruction is to cut anything that does not visibly do
        # something, and a dropdown entry that can never return a document
        # is the clearest possible case.
        dead = [lbl for lbl, needles in options.items()
                if not any(any(n.lower() in t.lower() for n in needles)
                           for t in universe)]
        print(f"\n  DEAD OPTIONS — match zero documents ({len(dead)})")
        for lbl in dead:
            print(f"        {lbl}")
        if not dead:
            print("        (none)")
        print()

    print("=" * 78)
    print(f"{total_unreachable} document(s) reachable only with no scope "
          f"selected, across both domains.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
