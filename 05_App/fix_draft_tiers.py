"""Re-tier the two DRAFT documents that were filed as retrievable.

    cd 05_App
    python fix_draft_tiers.py --dry-run     # show what would change
    python fix_draft_tiers.py               # apply

Measured 11 Oct 2026 against the live index. Twelve documents say DRAFT or
Proposed in their title. Ten are correctly tier D, which retrieval excludes by
default. Two were not:

    tier C   DRAFT | YOGA STANDARDS FOR | CLINICS/OPD, | MINIMUM 10 BEDS, ...
    tier C   MSR FOR SIDDHA CLINICAL ESTABLISHMENTS | 1 | DRAFT | MINIMUM ...

Tier C is "supporting guidance", which IS retrieved. So these two drafts were
being returned in ordinary searches and presented alongside binding law. They
are exactly the two documents that polluted the measured results: the Yoga
draft answered "What equipment must be available?" for a physiotherapy centre,
and both answered "What are the minimum staffing requirements?".

That makes this the project's sixth instance of the same failure shape: the
authority-tier system reported success for ten of twelve drafts and failed
silently for two, and the two failures were the ones a reader actually saw.

Two places need the fix, or a rebuild quietly undoes it:

  1. the live index (05_App/chroma_db/chroma.sqlite3), so the running app and
     the deployed app stop retrieving them;
  2. 03_Metadata/ingestion_report.csv, so a future `ingest_corpus.py` run
     assigns tier D from the start.

Also updates tier_label where present, so the two stay consistent.

Back up the index before applying if you want a way back:
    copy chroma_db\\chroma.sqlite3 chroma_db\\chroma.sqlite3.bak
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
INDEX_DB = APP_DIR / "chroma_db" / "chroma.sqlite3"
REPORT = APP_DIR.parent / "03_Metadata" / "ingestion_report.csv"

# Matched on a distinctive title fragment rather than the full string, because
# these titles are OCR'd cover pages and carry stray whitespace and pipes.
TARGETS = [
    ("YOGA STANDARDS", "yoga standards for"),
    ("SIDDHA MSR", "msr for siddha clinical establishments"),
]
NEW_TIER = "D"
NEW_LABEL = "Draft / proposed"


def find_ids(con) -> dict[str, list[str]]:
    """Chunk ids per target, with the tier they currently carry."""
    cur = con.cursor()
    cur.execute("""
        SELECT id,
               MAX(CASE WHEN key='title' THEN string_value END),
               MAX(CASE WHEN key='tier'  THEN string_value END)
        FROM embedding_metadata GROUP BY id
    """)
    out: dict[str, list[str]] = {name: [] for name, _ in TARGETS}
    tiers: dict[str, set] = {name: set() for name, _ in TARGETS}
    for cid, title, tier in cur.fetchall():
        tl = (title or "").lower()
        for name, needle in TARGETS:
            if needle in tl:
                out[name].append(cid)
                tiers[name].add(tier)
    for name, _ in TARGETS:
        if out[name]:
            print(f"  {name}: {len(out[name])} chunks, currently tier "
                  f"{sorted(t for t in tiers[name] if t)}")
        else:
            print(f"  {name}: NOT FOUND in the index")
    return out


def fix_index(dry: bool) -> int:
    if not INDEX_DB.exists():
        print(f"  ! no index at {INDEX_DB} - skipping")
        return 0
    con = sqlite3.connect(INDEX_DB)
    try:
        ids = find_ids(con)
        flat = [cid for v in ids.values() for cid in v]
        if not flat:
            return 0
        if dry:
            print(f"  would update {len(flat)} chunks to tier {NEW_TIER}")
            return 0
        cur = con.cursor()
        marks = ",".join("?" * len(flat))
        cur.execute(f"""UPDATE embedding_metadata SET string_value=?
                        WHERE key='tier' AND id IN ({marks})""",
                    [NEW_TIER, *flat])
        changed = cur.rowcount
        cur.execute(f"""UPDATE embedding_metadata SET string_value=?
                        WHERE key='tier_label' AND id IN ({marks})""",
                    [NEW_LABEL, *flat])
        con.commit()
        print(f"  updated {changed} chunk tiers (and {cur.rowcount} labels)")
        return changed
    finally:
        con.close()


def fix_report(dry: bool) -> int:
    if not REPORT.exists():
        print(f"  ! no report at {REPORT} - skipping")
        return 0
    rows = list(csv.DictReader(REPORT.open(encoding="utf-8-sig", newline="")))
    if not rows:
        return 0
    fields = list(rows[0].keys())
    n = 0
    for r in rows:
        tl = (r.get("title") or "").lower()
        if any(needle in tl for _, needle in TARGETS):
            if r.get("tier") != NEW_TIER:
                print(f"  {r.get('tier')} -> {NEW_TIER}: "
                      f"{(r.get('title') or '')[:58]}")
                r["tier"] = NEW_TIER
                if "tier_label" in r:
                    r["tier_label"] = NEW_LABEL
                n += 1
    if n and not dry:
        with REPORT.open("w", encoding="utf-8-sig", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=fields)
            w.writeheader()
            w.writerows(rows)
    print(f"  {'would update' if dry else 'updated'} {n} row(s) in "
          f"{REPORT.name}")
    return n


def verify() -> int:
    """Confirm no retrievable document still says DRAFT in its title."""
    if not INDEX_DB.exists():
        return 0
    con = sqlite3.connect(f"file:{INDEX_DB}?mode=ro", uri=True)
    cur = con.cursor()
    cur.execute("""
        SELECT MAX(CASE WHEN key='title' THEN string_value END) t,
               MAX(CASE WHEN key='tier'  THEN string_value END) tr
        FROM embedding_metadata GROUP BY id
    """)
    bad = set()
    for title, tier in cur.fetchall():
        tl = (title or "").lower()
        if ("draft" in tl or "proposed" in tl) and tier in ("A", "B", "C"):
            bad.add((tier, (title or "")[:70]))
    con.close()
    if bad:
        print(f"  STILL RETRIEVABLE: {len(bad)} draft document(s)")
        for tier, t in sorted(bad):
            print(f"    tier {tier}  {t}")
        return 1
    print("  no document saying DRAFT sits in a retrievable tier")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    print("index:")
    fix_index(args.dry_run)
    print("ingestion report:")
    fix_report(args.dry_run)
    print("verify:")
    rc = verify() if not args.dry_run else 0
    if not args.dry_run:
        print("\nRestart the app (or redeploy) so the index is re-read.")
        print("The deployed apps pick this up only when the Release asset is "
              "repackaged:\n  tar -czf chroma_db.tar.gz chroma_db")
    return rc


if __name__ == "__main__":
    sys.exit(main())
