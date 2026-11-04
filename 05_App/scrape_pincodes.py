"""
Karnataka Pincode Scraper
=========================
Fetches Karnataka pincode data from the free India Post API
(api.postalpincode.in) and stores results in the Supabase `pincodes` table.

The Karnataka pincode range is roughly 560001 to 591346.
We iterate in batches, calling the API once per pincode.

Usage
-----
  # Fetch all Karnataka pincodes (slow — ~30k calls):
  python scrape_pincodes.py

  # Fetch a sample of N pincodes (dry run):
  python scrape_pincodes.py --limit 50 --dry-run

  # Resume from a specific pincode:
  python scrape_pincodes.py --start 570001

Environment variables (or .env):
  SUPABASE_URL          your project URL
  SUPABASE_SERVICE_KEY  service-role key

Install:
  pip install requests python-dotenv --break-system-packages
"""

import argparse
import os
import time
import logging
import json
from datetime import datetime, timezone
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", os.environ.get("SUPABASE_KEY", ""))

# Karnataka pincode range (approximate)
KA_PINCODE_START = 560001
KA_PINCODE_END   = 591346

INDIA_POST_API = "https://api.postalpincode.in/pincode/{pincode}"
REQUEST_DELAY  = 0.3   # seconds between calls (free API, be polite)
BATCH_SIZE     = 200   # upsert rows in one Supabase call

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("pincode-scraper")


# ---------------------------------------------------------------------------
# Supabase helpers
# ---------------------------------------------------------------------------

def _sb_headers() -> dict:
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }


def upsert_rows(rows: list[dict]) -> bool:
    """Upsert a batch of pincode rows into Supabase."""
    url = f"{SUPABASE_URL}/rest/v1/pincodes"
    resp = requests.post(url, headers=_sb_headers(), json=rows, timeout=30)
    if resp.status_code not in (200, 201):
        log.warning("Supabase upsert failed: %s %s", resp.status_code, resp.text[:200])
        return False
    return True


def get_max_pincode() -> Optional[int]:
    """Find the highest pincode already in the DB (for resuming)."""
    url = f"{SUPABASE_URL}/rest/v1/pincodes"
    params = {
        "select": "pincode",
        "state": "eq.Maharashtra",   # exclude; we only want Karnataka
        "order": "pincode.desc",
        "limit": "1",
    }
    # Actually just get latest Karnataka row
    params2 = {
        "select": "pincode",
        "state": "eq.Karnataka",
        "order": "pincode.desc",
        "limit": "1",
    }
    hdrs = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
    }
    resp = requests.get(url, headers=hdrs, params=params2, timeout=30)
    if resp.status_code == 200:
        rows = resp.json()
        if rows:
            try:
                return int(rows[0]["pincode"])
            except (ValueError, KeyError):
                pass
    return None


# ---------------------------------------------------------------------------
# India Post API
# ---------------------------------------------------------------------------

def fetch_pincode(pincode: int) -> list[dict]:
    """
    Call the India Post API for one pincode.
    Returns a list of post office records (there can be multiple per pincode).
    Returns [] on error or non-Karnataka result.
    """
    url = INDIA_POST_API.format(pincode=pincode)
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code != 200:
            return []
        data = resp.json()
        if not data or data[0].get("Status") != "Success":
            return []
        post_offices = data[0].get("PostOffice") or []
        rows = []
        for po in post_offices:
            state = po.get("State", "")
            if state != "Karnataka":
                continue
            rows.append({
                "pincode":         str(pincode),
                "office_name":     po.get("Name", ""),
                "delivery_status": po.get("DeliveryStatus", ""),
                "district":        po.get("District", ""),
                "division_name":   po.get("Division", ""),
                "region":          po.get("Region", ""),
                "block":           po.get("Block", ""),
                "state":           state,
                "country":         po.get("Country", "India"),
                "fetched_at":      datetime.now(timezone.utc).isoformat(),
            })
        return rows
    except Exception as exc:
        log.debug("Error fetching %d: %s", pincode, exc)
        return []


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Scrape Karnataka pincodes from India Post API")
    parser.add_argument("--start",   type=int, default=KA_PINCODE_START,
                        help="Start pincode (default: 560001)")
    parser.add_argument("--end",     type=int, default=KA_PINCODE_END,
                        help="End pincode (default: 591346)")
    parser.add_argument("--limit",   type=int, default=0,
                        help="Max pincodes to process (0 = all)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Parse but do not write to Supabase")
    parser.add_argument("--resume",  action="store_true",
                        help="Auto-detect last stored pincode and resume from there")
    args = parser.parse_args()

    if not args.dry_run and (not SUPABASE_URL or not SUPABASE_KEY):
        raise SystemExit("Set SUPABASE_URL and SUPABASE_SERVICE_KEY in env or .env")

    start = args.start
    if args.resume and not args.dry_run:
        last = get_max_pincode()
        if last:
            start = last + 1
            log.info("Resuming from pincode %d (last stored: %d)", start, last)

    buffer: list[dict] = []
    total_rows   = 0
    total_calls  = 0
    skipped      = 0

    for pin in range(start, args.end + 1):
        if args.limit and total_calls >= args.limit:
            break

        rows = fetch_pincode(pin)
        total_calls += 1

        if not rows:
            skipped += 1
        else:
            buffer.extend(rows)
            log.info("  %d → %d record(s)  district=%s",
                     pin, len(rows), rows[0].get("district", "?"))

        # Flush buffer
        if len(buffer) >= BATCH_SIZE:
            if not args.dry_run:
                upsert_rows(buffer)
            else:
                log.info("DRY-RUN: would upsert %d rows", len(buffer))
            total_rows += len(buffer)
            buffer = []

        time.sleep(REQUEST_DELAY)

        if total_calls % 500 == 0:
            log.info("Progress: %d pincodes checked, %d rows collected, %d skipped",
                     total_calls, total_rows + len(buffer), skipped)

    # Final flush
    if buffer:
        if not args.dry_run:
            upsert_rows(buffer)
        else:
            log.info("DRY-RUN: would upsert %d rows", len(buffer))
        total_rows += len(buffer)

    log.info("Done — %d pincodes checked, %d Karnataka rows stored, %d skipped",
             total_calls, total_rows, skipped)


if __name__ == "__main__":
    main()
