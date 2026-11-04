"""
Bangalore 2026 Property Price Estimator
========================================
Builds locality-level 2026 price estimates for all ~1,300 Bangalore localities
in the Kaggle dataset using locality-specific or zone-based scaling factors
derived from NoBroker 2026 data.

Story
-----
Kaggle has 2017 prices for 1,300+ localities.  NoBroker has 2026 prices for
~17 premium localities.  For the 17 that overlap we compute a direct scaling
factor (NoBroker 2026 / Kaggle 2017 median).  For every other locality we
assign it to a Bangalore zone (North / South / East / West / Central /
Peripheral) and apply that zone's average scaling factor.

Output table: property_prices_estimated
  - locality, zone, estimated_ppsf_2026, ppsf_min_2026, ppsf_max_2026
  - source_ppsf_2017, scaling_factor, scaling_method, listing_count_2017

Security rules
--------------
- No credentials in this file.  All secrets from env or .env.
- GitHub Actions trigger: workflow_dispatch only (manual).

Usage
-----
  python estimate_2026_prices.py              # live run + upsert
  python estimate_2026_prices.py --dry-run    # compute + print, no DB writes

Environment variables:
  SUPABASE_URL          your Supabase project URL
  SUPABASE_SERVICE_KEY  service-role key

Install:
  pip install requests beautifulsoup4 pandas python-dotenv --break-system-packages
"""

import argparse
import json
import logging
import os
import re
import statistics
from datetime import datetime, timezone
from io import StringIO
from typing import Optional

import requests
import pandas as pd
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", os.environ.get("SUPABASE_KEY", ""))

KAGGLE_CSV_URL = (
    "https://raw.githubusercontent.com/karlyndiary/"
    "real-estate-price-prediction/main/Bengaluru_House_Data.csv"
)
NOBROKER_URL = "https://www.nobroker.in/blog/property-rates-in-bangalore/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-IN,en;q=0.9",
}
TIMEOUT = 30

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("price-estimator")

# ---------------------------------------------------------------------------
# Zone map — every NoBroker locality assigned to a Bangalore zone
# ---------------------------------------------------------------------------

NOBROKER_ZONE = {
    # Central
    "Koramangala":    "Central",
    "Indiranagar":    "Central",
    "Jayanagar":      "Central",
    "JP Nagar":       "Central",
    "Basavanagudi":   "Central",
    "Richmond Town":  "Central",
    "Shivajinagar":   "Central",
    # South
    "Electronic City":  "South",
    "Bannerghatta Road":"South",
    "BTM Layout":       "South",
    "Hulimavu":         "South",
    "Begur":            "South",
    # East
    "Whitefield":      "East",
    "Marathahalli":    "East",
    "KR Puram":        "East",
    "Varthur":         "East",
    "Sarjapur Road":   "East",
    "Bellandur":       "East",
    "Kadugodi":        "East",
    # West
    "Rajajinagar":     "West",
    "Vijayanagar":     "West",
    "Nagarbhavi":      "West",
    "Kengeri":         "West",
    "Mysore Road":     "West",
    # North
    "Yelahanka":       "North",
    "Hebbal":          "North",
    "Thanisandra":     "North",
    "Jakkur":          "North",
    "Devanahalli":     "North",
    "Kogilu":          "North",
    # Peripheral
    "Hoskote":         "Peripheral",
    "Attibele":        "Peripheral",
    "Dommasandra":     "Peripheral",
}

# Zone keywords — used to auto-assign Kaggle localities
ZONE_KEYWORDS = {
    "Central": [
        "koramangala", "indiranagar", "indira nagar", "jayanagar", "jp nagar",
        "basavanagudi", "richmond", "shivajinagar", "sadashivanagar",
        "malleswaram", "rajmahal", "lavelle", "ulsoor", "domlur",
        "cunningham", "frazer town", "cox town", "wheeler road",
        "pottery road", "cambridge layout", "cleveland town", "banaswadi",
    ],
    "South": [
        "electronic city", "bannerghatta", "btm", "hulimavu", "begur",
        "hongasandra", "arekere", "haralur", "chandapura", "attibele",
        "bommanahalli", "harlur", "carmelaram", "munnekollal",
        "kudlu", "hosur road", "uttarahalli", "nagarabhavi south",
    ],
    "East": [
        "whitefield", "marathahalli", "kr puram", "k.r. puram", "varthur",
        "sarjapur", "bellandur", "kadugodi", "brookefield", "hope farm",
        "kundalahalli", "panathur", "doddanekundi", "mahadevapura",
        "hoodi", "ramamurthy nagar", "banaswadi", "tin factory",
        "graphite india", "itpl", "epip zone",
    ],
    "West": [
        "rajajinagar", "vijayanagar", "nagarbhavi", "kengeri",
        "mysore road", "basaveshwara", "jalahalli", "peenya",
        "magadi road", "kamakshipalya", "yeshwanthpur", "srirampura",
        "mahalakshmi layout", "nandini layout", "marappana palya",
        "chandra layout", "uttarahalli", "banashankari",
    ],
    "North": [
        "yelahanka", "hebbal", "thanisandra", "jakkur", "devanahalli",
        "kogilu", "sahakara nagar", "rt nagar", "nagawara",
        "hennur", "kalyan nagar", "kammanahalli", "lingarajapuram",
        "manyata", "bhelekere", "kalkere", "bagalur", "doddaballapur",
        "sadahalli",
    ],
    "Peripheral": [
        "hoskote", "dommasandra", "budigere", "nelamangala",
        "bidadi", "kanakapura", "anekal", "sarjapura", "electronic city phase",
        "jigani", "bommasandra", "narayanapura", "tavarekere",
    ],
}


def assign_zone(locality: str) -> str:
    """Return best-match zone for a Kaggle locality string."""
    loc_lower = locality.lower().strip()
    for zone, keywords in ZONE_KEYWORDS.items():
        for kw in keywords:
            if kw in loc_lower:
                return zone
    return "Peripheral"   # default for outer-ring localities


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


def upsert_rows(table: str, rows: list[dict]) -> bool:
    if not rows:
        return True
    url  = f"{SUPABASE_URL}/rest/v1/{table}"
    resp = requests.post(url, headers=_sb_headers(), json=rows, timeout=60)
    if resp.status_code not in (200, 201):
        log.warning("Supabase upsert failed (%s): %s %s",
                    table, resp.status_code, resp.text[:300])
        return False
    return True


# ---------------------------------------------------------------------------
# Step 1 — Scrape NoBroker 2026 locality prices
# ---------------------------------------------------------------------------

def fetch_nobroker_prices() -> dict[str, dict]:
    """
    Returns {locality_name: {avg, min, max, yoy}} for all localities
    found on the NoBroker Bangalore rates page.
    """
    log.info("Fetching NoBroker 2026 prices from %s", NOBROKER_URL)
    r = requests.get(NOBROKER_URL, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    text = soup.get_text()

    start_marker = "Koramangala₹"
    idx = text.find(start_marker)
    if idx == -1:
        log.warning("NoBroker table marker not found — trying fallback marker")
        start_marker = "Koramangala"
        idx = text.find(start_marker)
    if idx == -1:
        log.error("Could not locate NoBroker price table")
        return {}

    end_marker = "Factors Affecting"
    end_idx    = text.find(end_marker, idx)
    chunk      = text[idx:end_idx] if end_idx > idx else text[idx:idx + 6000]

    pattern = re.compile(
        r"([A-Za-z][A-Za-z0-9\s]+?)"
        r"(₹[\d,\.]+(?:\s*[–\-]\s*₹[\d,\.]+)?)"
        r"(₹[\d,\.]+(?:\s*[–\-]\s*₹[\d,\.]+)?)"
        r"(₹[\d,\.]+(?:\s*[–\-]\s*₹[\d,\.]+)?)"
        r"([+\-]?\d+\.?\d*%)"
    )

    prices = {}
    for m in pattern.finditer(chunk):
        locality  = m.group(1).strip()
        price2026 = m.group(4).strip()
        yoy_text  = m.group(5)

        if len(locality) < 3:
            continue
        if any(w in locality.lower() for w in
               ("growth", "rate", "2024", "2025", "2026", "y/y", "locality", "area")):
            continue

        price2026 = price2026.replace(",", "").replace("₹", "")
        rng = re.search(r"([\d.]+)\s*[–\-]\s*([\d.]+)", price2026)
        if rng:
            lo, hi = float(rng.group(1)), float(rng.group(2))
            avg = (lo + hi) / 2
        else:
            num = re.search(r"[\d.]+", price2026)
            if not num:
                continue
            lo = hi = avg = float(num.group())

        yoy_m = re.search(r"([+\-]?\d+\.?\d*)", yoy_text)
        yoy   = float(yoy_m.group(1)) if yoy_m else None

        prices[locality] = {"avg": avg, "min": lo, "max": hi, "yoy": yoy}
        log.info("  NoBroker  %-22s  ₹%.0f/sqft  YoY=%s%%",
                 locality, avg, yoy)

    log.info("NoBroker: %d localities scraped", len(prices))
    return prices


# ---------------------------------------------------------------------------
# Step 2 — Load Kaggle 2017 data and compute locality medians
# ---------------------------------------------------------------------------

def _parse_sqft(raw) -> Optional[float]:
    if pd.isna(raw):
        return None
    raw = str(raw).strip()
    rng = re.match(r"([\d.]+)\s*[-–]\s*([\d.]+)", raw)
    if rng:
        return (float(rng.group(1)) + float(rng.group(2))) / 2
    sqm = re.search(r"([\d.]+)\s*Sq\. Meter", raw, re.IGNORECASE)
    if sqm:
        return float(sqm.group(1)) * 10.764
    num = re.search(r"[\d.]+", raw)
    return float(num.group()) if num else None


def fetch_kaggle_medians() -> dict[str, dict]:
    """
    Returns {location: {median_ppsf, count}} for Kaggle 2017 Bangalore listings.
    """
    log.info("Downloading Kaggle Bengaluru_House_Data CSV…")
    r = requests.get(KAGGLE_CSV_URL, timeout=TIMEOUT)
    r.raise_for_status()
    df = pd.read_csv(StringIO(r.text))
    log.info("CSV loaded: %d rows", len(df))

    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]
    df = df.dropna(subset=["location", "price"])

    ppsf_data: dict[str, list] = {}
    for _, row in df.iterrows():
        sqft = _parse_sqft(row.get("total_sqft"))
        if sqft and sqft < 100:
            sqft *= 10.764
        price_lakhs = float(row["price"]) if not pd.isna(row.get("price")) else None
        if not (price_lakhs and sqft and sqft > 0):
            continue
        ppsf = (price_lakhs * 100_000) / sqft
        if ppsf < 500 or ppsf > 100_000:
            continue
        loc = str(row["location"]).strip()
        ppsf_data.setdefault(loc, []).append(ppsf)

    medians = {}
    for loc, vals in ppsf_data.items():
        medians[loc] = {
            "median_ppsf": statistics.median(vals),
            "count":       len(vals),
        }

    log.info("Kaggle medians computed for %d localities", len(medians))
    return medians


# ---------------------------------------------------------------------------
# Step 3 — Fuzzy-match NoBroker names to Kaggle names
# ---------------------------------------------------------------------------

def _normalize(s: str) -> str:
    return re.sub(r"\s+", " ", s.lower().strip())


def find_kaggle_match(nb_locality: str, kaggle_locs: set[str]) -> Optional[str]:
    """Find the best Kaggle locality name for a NoBroker locality."""
    nb_norm = _normalize(nb_locality)
    # Exact match first
    for k in kaggle_locs:
        if _normalize(k) == nb_norm:
            return k
    # Starts-with match
    for k in kaggle_locs:
        kn = _normalize(k)
        if kn.startswith(nb_norm) or nb_norm.startswith(kn):
            return k
    # Substring match
    for k in kaggle_locs:
        kn = _normalize(k)
        if nb_norm in kn or kn in nb_norm:
            return k
    return None


# ---------------------------------------------------------------------------
# Step 4 — Build scaling factors and estimate 2026 prices
# ---------------------------------------------------------------------------

def build_estimates(
    nb_prices: dict,
    kg_medians: dict,
) -> list[dict]:
    """
    Returns a list of estimate dicts ready for Supabase upsert.
    """
    now_ts = datetime.now(timezone.utc).isoformat()
    kaggle_locs = set(kg_medians.keys())

    # --- Direct scaling factors for NoBroker localities ---
    direct_factors: dict[str, float] = {}   # kaggle_locality -> factor
    zone_nb_match:  dict[str, str]   = {}   # kaggle_locality -> nb_locality used

    for nb_loc, nb_data in nb_prices.items():
        kg_loc = find_kaggle_match(nb_loc, kaggle_locs)
        if kg_loc:
            kg_med = kg_medians[kg_loc]["median_ppsf"]
            if kg_med > 0:
                factor = nb_data["avg"] / kg_med
                direct_factors[kg_loc] = factor
                zone_nb_match[kg_loc]  = nb_loc
                log.info("  Direct  %-22s -> %-22s  factor=%.2fx",
                         nb_loc, kg_loc, factor)

    # --- Zone-level average scaling factors ---
    zone_factors: dict[str, list] = {}
    zone_nb_reps: dict[str, str]  = {}   # zone -> a representative NoBroker locality

    for nb_loc, nb_data in nb_prices.items():
        zone = NOBROKER_ZONE.get(nb_loc, assign_zone(nb_loc))
        kg_loc = find_kaggle_match(nb_loc, kaggle_locs)
        if kg_loc and kg_loc in direct_factors:
            zone_factors.setdefault(zone, []).append(direct_factors[kg_loc])
            zone_nb_reps[zone] = nb_loc

    zone_avg_factor: dict[str, float] = {
        z: statistics.mean(v) for z, v in zone_factors.items()
    }
    # Fallback global average if a zone has no data
    all_factors = [f for v in zone_factors.values() for f in v]
    global_avg  = statistics.mean(all_factors) if all_factors else 1.8

    log.info("Zone scaling factors:")
    for z, f in sorted(zone_avg_factor.items()):
        log.info("  %-12s  %.2fx  (n=%d)", z, f, len(zone_factors[z]))
    log.info("  %-12s  %.2fx  (global fallback)", "Peripheral", global_avg)

    # --- Build estimate rows for every Kaggle locality ---
    estimates = []
    for kg_loc, kg_data in kg_medians.items():
        kg_med = kg_data["median_ppsf"]
        zone   = assign_zone(kg_loc)

        if kg_loc in direct_factors:
            # We have a NoBroker match
            nb_loc    = zone_nb_match[kg_loc]
            nb_data   = nb_prices[nb_loc]
            factor    = direct_factors[kg_loc]
            method    = "direct"
            est_avg   = nb_data["avg"]
            est_min   = nb_data["min"]
            est_max   = nb_data["max"]
        else:
            # Zone proxy
            factor  = zone_avg_factor.get(zone, global_avg)
            nb_rep  = zone_nb_reps.get(zone, "")
            method  = "zone_proxy"
            est_avg = round(kg_med * factor, 2)
            est_min = round(est_avg * 0.85, 2)
            est_max = round(est_avg * 1.15, 2)
            nb_loc  = nb_rep

        estimates.append({
            "locality":            kg_loc,
            "city":                "Bangalore",
            "zone":                zone,
            "estimated_ppsf_2026": round(est_avg, 2),
            "ppsf_min_2026":       round(est_min, 2),
            "ppsf_max_2026":       round(est_max, 2),
            "source_ppsf_2017":    round(kg_med, 2),
            "scaling_factor":      round(factor, 4),
            "scaling_method":      method,
            "nobroker_locality":   nb_loc,
            "listing_count_2017":  kg_data["count"],
            "year":                2026,
            "computed_at":         now_ts,
        })

    # Also add NoBroker localities that have no Kaggle match
    for nb_loc, nb_data in nb_prices.items():
        if find_kaggle_match(nb_loc, kaggle_locs) is None:
            zone = NOBROKER_ZONE.get(nb_loc, assign_zone(nb_loc))
            estimates.append({
                "locality":            nb_loc,
                "city":                "Bangalore",
                "zone":                zone,
                "estimated_ppsf_2026": round(nb_data["avg"], 2),
                "ppsf_min_2026":       round(nb_data["min"], 2),
                "ppsf_max_2026":       round(nb_data["max"], 2),
                "source_ppsf_2017":    None,
                "scaling_factor":      None,
                "scaling_method":      "nobroker_only",
                "nobroker_locality":   nb_loc,
                "listing_count_2017":  0,
                "year":                2026,
                "computed_at":         now_ts,
            })

    log.info("Total estimate rows: %d", len(estimates))
    return estimates


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Estimate 2026 Bangalore property prices via locality scaling"
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Compute and print stats, skip DB writes")
    args = parser.parse_args()

    if not args.dry_run and (not SUPABASE_URL or not SUPABASE_KEY):
        raise SystemExit("Set SUPABASE_URL and SUPABASE_SERVICE_KEY in env or .env")

    # Fetch source data
    nb_prices  = fetch_nobroker_prices()
    kg_medians = fetch_kaggle_medians()

    if not nb_prices:
        raise SystemExit("NoBroker scrape returned no data — aborting")
    if not kg_medians:
        raise SystemExit("Kaggle CSV returned no medians — aborting")

    # Build estimates
    estimates = build_estimates(nb_prices, kg_medians)

    if not estimates:
        raise SystemExit("No estimates produced — check inputs")

    # Summary
    direct_count = sum(1 for e in estimates if e["scaling_method"] == "direct")
    zone_count   = sum(1 for e in estimates if e["scaling_method"] == "zone_proxy")
    nb_only      = sum(1 for e in estimates if e["scaling_method"] == "nobroker_only")
    log.info("Summary: %d direct matches | %d zone proxies | %d NoBroker-only",
             direct_count, zone_count, nb_only)

    if args.dry_run:
        print(f"\n=== Estimates ({len(estimates)} localities) ===")
        for e in sorted(estimates, key=lambda x: x["estimated_ppsf_2026"] or 0, reverse=True)[:20]:
            print(f"  [{e['scaling_method']:14s}] {e['locality']:<28}  "
                  f"₹{e['estimated_ppsf_2026']:>8,.0f}/sqft  "
                  f"(factor={e['scaling_factor'] or '-'}x, zone={e['zone']})")
        print(f"\n... {len(estimates)-20} more localities")
        log.info("DRY-RUN — no DB writes")
        return

    # Upsert to Supabase in batches of 500
    batch_size = 500
    total_ok   = 0
    for i in range(0, len(estimates), batch_size):
        batch = estimates[i:i + batch_size]
        ok    = upsert_rows("property_prices_estimated", batch)
        if ok:
            total_ok += len(batch)
            log.info("Upserted rows %d–%d", i + 1, i + len(batch))
        else:
            log.error("Batch %d–%d FAILED", i + 1, i + len(batch))

    log.info("Done — %d / %d rows upserted into property_prices_estimated",
             total_ok, len(estimates))


if __name__ == "__main__":
    main()
