"""
Bangalore Property Listings Loader
====================================
Downloads the Bengaluru House Data CSV (13,320 rows) from GitHub and
loads it into the Supabase `property_listings` table.

Source: Kaggle — Bengaluru House Price Data
  https://www.kaggle.com/datasets/amitabhajoy/bengaluru-house-price-data
GitHub mirror (public):
  https://raw.githubusercontent.com/karlyndiary/real-estate-price-prediction/main/Bengaluru_House_Data.csv

Columns in source:
  area_type, availability, location, size, society, total_sqft, bath, balcony, price (in lakhs)

Derived columns:
  is_gated     — True when society is not empty
  bhk          — integer extracted from size (e.g. "2 BHK" → 2)
  price_per_sqft — (price * 100000) / total_sqft

Usage
-----
  python load_property_listings.py              # download, process, upsert
  python load_property_listings.py --dry-run    # process and print, no DB writes
  python load_property_listings.py --csv /path/to/local.csv  # use local file

Environment variables (or .streamlit/secrets.toml):
  SUPABASE_URL          your project URL
  SUPABASE_SERVICE_KEY  service-role key

Install:
  pip install requests pandas python-dotenv --break-system-packages
"""

import argparse
import os
import re
import logging
import json
import math
from datetime import datetime, timezone
from io import StringIO

import requests
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", os.environ.get("SUPABASE_KEY", ""))

CSV_URL = (
    "https://raw.githubusercontent.com/karlyndiary/"
    "real-estate-price-prediction/main/Bengaluru_House_Data.csv"
)

BATCH_SIZE  = 500
SOURCE_YEAR = 2017   # dataset was compiled ~2017–2019
CITY        = "Bangalore"
STATE       = "Karnataka"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("listings-loader")


# ---------------------------------------------------------------------------
# Supabase helpers
# ---------------------------------------------------------------------------

def _sb_headers() -> dict:
    return {
        "apikey":        SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type":  "application/json",
        "Prefer":        "resolution=merge-duplicates,return=minimal",
    }


def upsert_rows(rows: list[dict]) -> bool:
    url  = f"{SUPABASE_URL}/rest/v1/property_listings"
    resp = requests.post(url, headers=_sb_headers(), json=rows, timeout=60)
    if resp.status_code not in (200, 201):
        log.warning("Supabase upsert failed: %s %s", resp.status_code, resp.text[:300])
        return False
    return True


# ---------------------------------------------------------------------------
# Data processing
# ---------------------------------------------------------------------------

def _parse_sqft(raw: str) -> float | None:
    """
    Handle formats like '1056', '2600', '1000 - 1200' (take midpoint),
    '34.46Sq. Meter' → convert m² to sqft.
    """
    if pd.isna(raw):
        return None
    raw = str(raw).strip()
    # Range → midpoint
    rng = re.match(r"([\d.]+)\s*[-–]\s*([\d.]+)", raw)
    if rng:
        return (float(rng.group(1)) + float(rng.group(2))) / 2
    # Square metres
    sqm = re.search(r"([\d.]+)\s*Sq\. Meter", raw, re.IGNORECASE)
    if sqm:
        return float(sqm.group(1)) * 10.764
    # Plain number
    num = re.search(r"[\d.]+", raw)
    return float(num.group()) if num else None


def _parse_bhk(size_str: str) -> int | None:
    """'2 BHK' → 2, '3 Bedroom' → 3, etc."""
    if pd.isna(size_str):
        return None
    m = re.search(r"(\d+)", str(size_str))
    return int(m.group(1)) if m else None


def process_csv(df: pd.DataFrame) -> list[dict]:
    """Clean and shape the raw dataframe into Supabase-ready dicts."""
    log.info("Raw rows: %d", len(df))

    # Normalize column names
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]

    # Drop rows without location or price
    df = df.dropna(subset=["location", "price"])
    log.info("After dropping nulls: %d", len(df))

    rows = []
    now_ts = datetime.now(timezone.utc).isoformat()

    for _, row in df.iterrows():
        sqft = _parse_sqft(row.get("total_sqft"))
        if sqft and sqft < 100:          # sanity — sq metres slipped through
            sqft = sqft * 10.764

        price_lakhs = float(row["price"]) if not pd.isna(row.get("price")) else None

        ppsf = None
        if price_lakhs and sqft and sqft > 0:
            ppsf = round((price_lakhs * 100_000) / sqft, 2)
            # Filter obvious outliers
            if ppsf < 500 or ppsf > 100_000:
                ppsf = None

        society_raw = row.get("society")
        is_gated = bool(isinstance(society_raw, str) and society_raw.strip())

        area_type = str(row.get("area_type", "")).strip() if not pd.isna(row.get("area_type")) else None
        size_str  = str(row.get("size", "")).strip()     if not pd.isna(row.get("size"))      else None
        avail     = str(row.get("availability", "")).strip() if not pd.isna(row.get("availability")) else None

        bath    = int(row["bath"])    if not pd.isna(row.get("bath"))    else None
        balcony = int(row["balcony"]) if not pd.isna(row.get("balcony")) else None

        rows.append({
            "area_type":     area_type,
            "is_gated":      is_gated,
            "society":       society_raw.strip() if is_gated else None,
            "location":      str(row["location"]).strip(),
            "size":          size_str,
            "bhk":           _parse_bhk(size_str),
            "total_sqft":    round(sqft, 2) if sqft else None,
            "price_lakhs":   price_lakhs,
            "price_per_sqft": ppsf,
            "bath":          bath,
            "balcony":       balcony,
            "availability":  avail,
            "city":          CITY,
            "state":         STATE,
            "source":        "Kaggle/Bengaluru_House_Data",
            "source_year":   SOURCE_YEAR,
            "loaded_at":     now_ts,
        })

    log.info("Processed %d rows", len(rows))
    return rows


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Load Bangalore property listings into Supabase")
    parser.add_argument("--dry-run", action="store_true",
                        help="Process and print stats, skip DB writes")
    parser.add_argument("--csv", default=None,
                        help="Path to local CSV instead of downloading")
    args = parser.parse_args()

    if not args.dry_run and (not SUPABASE_URL or not SUPABASE_KEY):
        raise SystemExit("Set SUPABASE_URL and SUPABASE_SERVICE_KEY in env or .env")

    # Load data
    if args.csv:
        log.info("Reading local CSV: %s", args.csv)
        df = pd.read_csv(args.csv)
    else:
        log.info("Downloading CSV from GitHub…")
        resp = requests.get(CSV_URL, timeout=30)
        resp.raise_for_status()
        log.info("Downloaded %d bytes", len(resp.content))
        df = pd.read_csv(StringIO(resp.text))

    rows = process_csv(df)

    if not rows:
        raise SystemExit("No rows after processing — check CSV format")

    # Summary stats
    import collections
    gated     = sum(1 for r in rows if r["is_gated"])
    standalone = len(rows) - gated
    bhk_counts = collections.Counter(r["bhk"] for r in rows if r["bhk"])
    locs       = len(set(r["location"] for r in rows))
    log.info("Summary: %d total | %d gated | %d standalone | %d localities",
             len(rows), gated, standalone, locs)
    log.info("BHK distribution: %s", dict(sorted(bhk_counts.items())))

    if args.dry_run:
        print(json.dumps(rows[:5], indent=2, ensure_ascii=False))
        log.info("DRY-RUN — no DB writes")
        return

    # Upsert in batches
    total_ok = 0
    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i:i + BATCH_SIZE]
        ok = upsert_rows(batch)
        if ok:
            total_ok += len(batch)
            log.info("Upserted rows %d–%d", i + 1, i + len(batch))
        else:
            log.error("Batch %d–%d FAILED", i + 1, i + len(batch))

    log.info("Done — %d / %d rows upserted into property_listings", total_ok, len(rows))


if __name__ == "__main__":
    main()
