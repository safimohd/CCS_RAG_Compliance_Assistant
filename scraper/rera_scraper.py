"""
K-RERA Project Scraper
Fetches all registered projects from rera.karnataka.gov.in
and upserts them into Supabase.

HOW IT WORKS (confirmed by browser inspection, Nov 2026):
  - GET /viewAllProjects returns a single 6MB HTML page
  - All 10,000+ project records are embedded as 4 JS arrays in that HTML
  - No CAPTCHA, no JS rendering, no Playwright needed — plain requests + regex
  - Arrays: applicationNameList (ACK no), applicationNameList2 (PRM/reg no),
            applicationNameList3 (project name), applicationNameList4 (promoter)

SECRETS REQUIRED (set as GitHub Actions secrets or env vars):
  SUPABASE_URL   — your project URL
  SUPABASE_KEY   — service role key (write access)
  NTFY_TOPIC     — your ntfy.sh topic name for failure alerts (optional)
"""

import os
import re
import sys
import time
import json
import logging
import requests
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
RERA_URL    = "https://rera.karnataka.gov.in/viewAllProjects"
HEADERS     = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}
SUPABASE_URL  = os.environ["SUPABASE_URL"]
SUPABASE_KEY  = os.environ.get("SUPABASE_KEY") or os.environ["SUPABASE_SERVICE_KEY"]
NTFY_TOPIC    = os.environ.get("NTFY_TOPIC", "")   # optional
TABLE         = "rera_projects"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Step 1: Fetch the listing page
# ---------------------------------------------------------------------------
def fetch_listing() -> str:
    log.info("Fetching %s …", RERA_URL)
    r = requests.get(RERA_URL, headers=HEADERS, timeout=60)
    r.raise_for_status()
    log.info("Got %d bytes", len(r.text))
    return r.text


# ---------------------------------------------------------------------------
# Step 2: Extract JS arrays from the HTML via regex
# ---------------------------------------------------------------------------
_ARRAY_RE = re.compile(
    r"var\s+(applicationNameList\d*)\s*=\s*new Array\(\s*\);"
    r".*?"
    r"(?=var\s+applicationNameList|\Z)",
    re.DOTALL,
)
_PUSH_RE = re.compile(r"\.push\('([^']+)'\)")


def extract_arrays(html: str) -> dict[str, list[str]]:
    """Return a dict  {array_name: [values…]}  for all 4 applicationNameList* vars."""
    results: dict[str, list[str]] = {}
    for m in _ARRAY_RE.finditer(html):
        name   = m.group(1)
        block  = m.group(0)
        values = _PUSH_RE.findall(block)
        results[name] = values
        log.info("  %-30s  %d entries", name, len(values))
    return results


def build_records(arrays: dict[str, list[str]]) -> list[dict]:
    ack     = arrays.get("applicationNameList",  [])
    prm     = arrays.get("applicationNameList2", [])
    names   = arrays.get("applicationNameList3", [])
    promoters = arrays.get("applicationNameList4", [])

    n = len(ack)
    if not n:
        raise ValueError("No ACK numbers found — page structure may have changed")

    log.info("Building %d project records …", n)
    scraped_at = datetime.now(timezone.utc).isoformat()
    records = []
    for i in range(n):
        records.append({
            "ack_number":    ack[i]        if i < len(ack)       else None,
            "prm_number":    prm[i]        if i < len(prm)       else None,
            "project_name":  names[i]      if i < len(names)     else None,
            "promoter_name": promoters[i]  if i < len(promoters) else None,
            "scraped_at":    scraped_at,
        })
    return records


# ---------------------------------------------------------------------------
# Step 3: Upsert to Supabase in batches
# ---------------------------------------------------------------------------
BATCH_SIZE = 500


def supabase_upsert(records: list[dict]) -> None:
    url = f"{SUPABASE_URL}/rest/v1/{TABLE}"
    headers = {
        "apikey":        SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type":  "application/json",
        "Prefer":        "resolution=merge-duplicates",   # upsert on PK
    }

    total    = len(records)
    inserted = 0
    for start in range(0, total, BATCH_SIZE):
        batch = records[start : start + BATCH_SIZE]
        r = requests.post(url, headers=headers, json=batch, timeout=30)
        if r.status_code not in (200, 201):
            raise RuntimeError(
                f"Supabase error {r.status_code} at batch {start}: {r.text[:300]}"
            )
        inserted += len(batch)
        log.info("  Upserted %d / %d …", inserted, total)
        time.sleep(0.2)   # be gentle

    log.info("Done — %d records upserted.", inserted)


# ---------------------------------------------------------------------------
# Step 4: Optional ntfy.sh failure notification
# ---------------------------------------------------------------------------
def notify_failure(message: str) -> None:
    if not NTFY_TOPIC:
        return
    try:
        requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=message.encode("utf-8"),
            headers={
                "Title":    "K-RERA scraper FAILED",
                "Priority": "urgent",
                "Tags":     "rotating_light",
            },
            timeout=10,
        )
    except Exception as e:
        log.warning("Could not send ntfy notification: %s", e)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    try:
        html    = fetch_listing()
        arrays  = extract_arrays(html)
        records = build_records(arrays)
        supabase_upsert(records)
        log.info("Scrape complete at %s", datetime.now(timezone.utc).isoformat())
    except Exception as exc:
        log.exception("Scrape failed: %s", exc)
        notify_failure(str(exc))
        sys.exit(1)


if __name__ == "__main__":
    main()
