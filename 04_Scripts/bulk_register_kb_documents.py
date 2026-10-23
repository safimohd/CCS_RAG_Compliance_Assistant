"""
bulk_register_kb_documents.py
Reads all downloaded PDFs from Healthcare and K-RERA metadata CSVs,
checks which doc_ids are already in Supabase kb_documents, and upserts
the missing ones via the REST API.

Usage (run from repo root):
    set SUPABASE_URL=https://hcezxgqsioeduhqwhxio.supabase.co
    set SUPABASE_SERVICE_KEY=<your service role key>
    python 04_Scripts/bulk_register_kb_documents.py

On macOS/Linux:
    SUPABASE_URL=... SUPABASE_SERVICE_KEY=... python 04_Scripts/bulk_register_kb_documents.py

Credentials come from environment variables ONLY -- never hardcode them.
"""

import csv
import os
import re
import sys
import time
import requests
from pathlib import Path

# ── Config ──────────────────────────────────────────────────────────────────
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SVC_KEY      = os.environ.get("SUPABASE_SERVICE_KEY", "")

if not SUPABASE_URL or not SVC_KEY:
    sys.exit(
        "ERROR: Set SUPABASE_URL and SUPABASE_SERVICE_KEY environment variables.\n"
        "  Windows:  set SUPABASE_URL=...  &&  set SUPABASE_SERVICE_KEY=...\n"
        "  Mac/Linux: SUPABASE_URL=... SUPABASE_SERVICE_KEY=... python ..."
    )

HDRS = {
    "apikey":        SVC_KEY,
    "Authorization": f"Bearer {SVC_KEY}",
    "Content-Type":  "application/json",
    "Prefer":        "return=representation",
}

BATCH_SIZE = 50   # rows per POST
DELAY      = 0.5  # seconds between batches


# ── Helpers ──────────────────────────────────────────────────────────────────
def slugify(text: str, max_len: int = 80) -> str:
    """Convert a title to a safe doc_id slug."""
    s = text.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_-]+", "_", s)
    return s[:max_len].strip("_")


def fetch_existing_doc_ids() -> set:
    """Return the set of doc_id values already in kb_documents."""
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/kb_documents?select=doc_id&limit=2000",
        headers=HDRS, timeout=20,
    )
    if r.status_code != 200:
        sys.exit(f"Cannot fetch existing kb_documents ({r.status_code}): {r.text[:300]}")
    return {row["doc_id"] for row in r.json()}


def upsert_batch(rows: list[dict]) -> int:
    """POST a batch of rows. Returns count inserted."""
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/kb_documents?on_conflict=doc_id",
        headers={**HDRS, "Prefer": "resolution=ignore-duplicates,return=representation"},
        json=rows,
        timeout=30,
    )
    if r.status_code not in (200, 201):
        print(f"  WARNING: batch upsert returned {r.status_code}: {r.text[:200]}")
        return 0
    return len(r.json()) if r.text else 0


# ── Healthcare index → rows ───────────────────────────────────────────────────
def healthcare_rows(csv_path: str, existing: set) -> list[dict]:
    rows_out = []
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for r in reader:
            status_val = r.get("status", "").strip().lower()
            if not status_val.startswith("downloaded"):
                continue   # skip failures

            title = r.get("title", "").strip() or r.get("filename", "unknown")
            # Build a stable doc_id from authority + slugified title
            authority = r.get("authority", r.get("folder", "hc")).strip().lower()
            authority_slug = re.sub(r"[^\w]", "_", authority)[:20].strip("_")
            doc_id = f"hc_{authority_slug}_{slugify(title)}"[:100]

            if doc_id in existing:
                continue   # already registered

            rows_out.append({
                "bot":          "Healthcare",
                "doc_id":       doc_id,
                "title":        title[:200],
                "status":       "current",
                "source_url":   r.get("url", "").strip() or None,
                "expected_update_date": "2027-10-23",
                "alert_days_before":    30,
            })
    return rows_out


# ── K-RERA metadata → rows ────────────────────────────────────────────────────
def krera_rows(csv_path: str, existing: set) -> list[dict]:
    rows_out = []
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for r in reader:
            status_val = r.get("status", "").strip().lower()
            if status_val != "downloaded":
                continue

            title    = r.get("title", "").strip() or r.get("filename", "unknown")
            doc_type = r.get("type", "general").strip().lower()
            type_slug = re.sub(r"[^\w]", "_", doc_type)[:20].strip("_")
            doc_id   = f"rera_{type_slug}_{slugify(title)}"[:100]

            if doc_id in existing:
                continue

            rows_out.append({
                "bot":          "RERA",
                "doc_id":       doc_id,
                "title":        title[:200],
                "status":       "current",
                "source_url":   r.get("url", "").strip() or None,
                "expected_update_date": "2027-10-23",
                "alert_days_before":    30,
            })
    return rows_out


# ── Main ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    repo_root = Path(__file__).parent.parent

    print("=" * 60)
    print("BULK REGISTER DOWNLOADED PDFs INTO kb_documents")
    print("=" * 60)

    print("\nFetching existing doc_ids from Supabase...")
    existing = fetch_existing_doc_ids()
    print(f"  Already registered: {len(existing)} documents")

    # Collect new rows from both sources
    all_new_rows = []

    hc_csv = repo_root / "03_Metadata/Healthcare/healthcare_master_index.csv"
    if hc_csv.exists():
        hc = healthcare_rows(str(hc_csv), existing)
        print(f"\nHealthcare master index: {len(hc)} new rows to register")
        all_new_rows.extend(hc)
    else:
        print(f"\nWARNING: {hc_csv} not found, skipping Healthcare")

    krera_csv = repo_root / "03_Metadata/RERA/krera_metadata.csv"
    if krera_csv.exists():
        kr = krera_rows(str(krera_csv), existing)
        print(f"K-RERA metadata: {len(kr)} new rows to register")
        all_new_rows.extend(kr)
    else:
        print(f"WARNING: {krera_csv} not found, skipping K-RERA")

    if not all_new_rows:
        print("\nNothing new to register. All downloaded docs are already in kb_documents.")
        sys.exit(0)

    print(f"\nTotal to insert: {len(all_new_rows)} rows")
    print(f"Uploading in batches of {BATCH_SIZE}...\n")

    total_inserted = 0
    for i in range(0, len(all_new_rows), BATCH_SIZE):
        batch = all_new_rows[i : i + BATCH_SIZE]
        n = upsert_batch(batch)
        total_inserted += n
        print(f"  Batch {i // BATCH_SIZE + 1}: sent {len(batch)} rows, "
              f"confirmed {n} inserted")
        time.sleep(DELAY)

    print(f"\n{'='*60}")
    print(f"Done. {total_inserted} rows inserted into kb_documents.")
    print(f"Refresh the admin dashboard to see the updated Document Registry.")
    print(f"{'='*60}")

