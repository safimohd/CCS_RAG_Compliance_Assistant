"""
Bangalore Property Price Scraper
=================================
Scrapes locality-level property prices (₹/sqft) from NoBroker's
Bangalore rates page and stores them in the Supabase `property_prices` table.

Source: https://www.nobroker.in/blog/property-rates-in-bangalore/
Data: 2026 rates, YoY growth, locality breakdown

Usage
-----
  python scrape_property_prices.py              # live scrape + store
  python scrape_property_prices.py --dry-run    # scrape + print, no DB writes
  python scrape_property_prices.py --city Bangalore  # (default)

Environment variables (or .env):
  SUPABASE_URL          your project URL
  SUPABASE_SERVICE_KEY  service-role key

Install:
  pip install requests beautifulsoup4 python-dotenv --break-system-packages
"""

import argparse
import os
import re
import time
import logging
import json
from datetime import datetime, timezone
from typing import Optional

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", os.environ.get("SUPABASE_KEY", ""))

SOURCE_URL = "https://www.nobroker.in/blog/property-rates-in-bangalore/"
CITY       = "Bangalore"
STATE      = "Karnataka"
YEAR       = 2026

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-IN,en;q=0.9",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("price-scraper")


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


def upsert_prices(rows: list[dict]) -> bool:
    url = f"{SUPABASE_URL}/rest/v1/property_prices"
    resp = requests.post(url, headers=_sb_headers(), json=rows, timeout=30)
    if resp.status_code not in (200, 201):
        log.warning("Supabase upsert failed: %s %s", resp.status_code, resp.text[:300])
        return False
    return True


# ---------------------------------------------------------------------------
# Scraping helpers
# ---------------------------------------------------------------------------

def _parse_price_range(text: str) -> tuple[Optional[int], Optional[int]]:
    """
    Parse price strings like '₹20,000–₹37,000' or '₹11,050 per sq.ft.'
    Returns (min_sqft, max_sqft) as integers.
    """
    text = text.replace(",", "").replace("₹", "").replace("per sq.ft.", "").strip()
    # Match range: 20000–37000
    m = re.search(r"(\d+)\s*[–\-]\s*(\d+)", text)
    if m:
        return int(m.group(1)), int(m.group(2))
    # Single value
    m = re.search(r"(\d+)", text)
    if m:
        v = int(m.group(1))
        return v, v
    return None, None


def _parse_yoy(text: str) -> Optional[float]:
    """Parse '+9.7%' or '-13.0%' into a float."""
    m = re.search(r"([+\-]?\d+\.?\d*)\s*%", text)
    if m:
        return float(m.group(1))
    return None


def scrape_nobroker(html: str) -> list[dict]:
    """
    Parse the NoBroker Bangalore rates page HTML.
    Extracts the area-wise rate table (locality, 2024, 2025, 2026 rates, YoY growth).
    Returns list of dicts ready for Supabase.
    """
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text()

    # The table is rendered as plain text in this format:
    # LocalityName₹X–₹Y₹X–₹Y₹X–₹Y+Z%
    # Find the section starting from Koramangala
    start_marker = "Koramangala₹"
    idx = text.find(start_marker)
    if idx == -1:
        log.warning("Could not find table start marker in page")
        return []

    # Grab a chunk — the table ends around "Factors Affecting"
    end_marker = "Factors Affecting"
    end_idx = text.find(end_marker, idx)
    table_text = text[idx:end_idx] if end_idx > idx else text[idx:idx+5000]

    # Pattern: LocalityName + three price ranges + YoY
    # e.g. "Koramangala₹17,000–₹30,000₹17,900–₹35,000₹20,000–₹37,000+9.7%"
    # We also want to handle single-value prices: ₹12,528.22
    pattern = re.compile(
        r"([A-Za-z][A-Za-z0-9\s]+?)"                             # locality name
        r"(₹[\d,\.]+(?:\s*[–\-]\s*₹[\d,\.]+)?)"                # 2024 range
        r"(₹[\d,\.]+(?:\s*[–\-]\s*₹[\d,\.]+)?)"                # 2025 range
        r"(₹[\d,\.]+(?:\s*[–\-]\s*₹[\d,\.]+)?)"                # 2026 range
        r"([+\-]?\d+\.?\d*%)"                                    # YoY growth
    )

    rows = []
    now_ts = datetime.now(timezone.utc).isoformat()

    for m in pattern.finditer(table_text):
        locality  = m.group(1).strip()
        price2026 = m.group(4).strip()
        yoy_text  = m.group(5)

        # Skip obviously bad names or names containing header junk
        if len(locality) < 3 or locality.lower() in ("area", "locality", "rate", "price"):
            continue
        # Skip if locality contains non-locality words (table header bleed)
        if any(w in locality.lower() for w in ("growth", "rate", "2024", "2025", "2026", "y/y", "locality")):
            continue

        min_p, max_p = _parse_price_range(price2026)
        yoy = _parse_yoy(yoy_text)
        avg_p = int((min_p + max_p) / 2) if (min_p and max_p) else (min_p or max_p)

        row = {
            "locality":       locality,
            "city":           CITY,
            "state":          STATE,
            "price_min_sqft": min_p,
            "price_max_sqft": max_p,
            "price_avg_sqft": avg_p,
            "yoy_growth_pct": yoy,
            "year":           YEAR,
            "source":         "NoBroker",
            "source_url":     SOURCE_URL,
            "scraped_at":     now_ts,
        }
        rows.append(row)
        log.info("  %-20s  ₹%s–₹%s /sqft  YoY=%s%%",
                 locality,
                 f"{min_p:,}" if min_p else "?",
                 f"{max_p:,}" if max_p else "?",
                 yoy)

    return rows


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Scrape Bangalore property prices from NoBroker")
    parser.add_argument("--dry-run", action="store_true",
                        help="Scrape and print, do not write to Supabase")
    parser.add_argument("--city", default=CITY)
    args = parser.parse_args()

    if not args.dry_run and (not SUPABASE_URL or not SUPABASE_KEY):
        raise SystemExit("Set SUPABASE_URL and SUPABASE_SERVICE_KEY in env or .env")

    log.info("Fetching %s", SOURCE_URL)
    try:
        resp = requests.get(SOURCE_URL, headers=HEADERS, timeout=30)
        resp.raise_for_status()
    except Exception as exc:
        raise SystemExit(f"Failed to fetch source page: {exc}")

    log.info("Page fetched — %d bytes", len(resp.content))

    rows = scrape_nobroker(resp.text)

    if not rows:
        raise SystemExit("No price rows extracted — page structure may have changed")

    log.info("Extracted %d locality rows", len(rows))

    if args.dry_run:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        log.info("DRY-RUN — no DB writes")
        return

    ok = upsert_prices(rows)
    if ok:
        log.info("Upserted %d rows into property_prices", len(rows))
    else:
        raise SystemExit("DB upsert failed — check logs above")


if __name__ == "__main__":
    main()
