"""
Market Rates Scraper
====================
Fetches live investment comparison data and upserts into Supabase `market_rates` table.

Sources:
  - Gold price  : BankBazaar (live today + monthly history)
  - Nifty 50    : Yahoo Finance (1Y / 3Y / 5Y / 10Y CAGR computed from adjusted-close)
  - FD rates    : Paisabazaar (top public + private banks, all tenures)

Security rules:
  - No credentials in this file. All secrets from environment / st.secrets.
  - Trigger only: GitHub Actions workflow_dispatch (manual).

Usage:
  python scrape_market_rates.py               # live fetch + upsert
  python scrape_market_rates.py --dry-run     # fetch, print, no DB writes

Environment variables:
  SUPABASE_URL          your Supabase project URL
  SUPABASE_SERVICE_KEY  service-role key

Install:
  pip install requests beautifulsoup4 python-dotenv --break-system-packages
"""

import argparse
import json
import logging
import math
import os
import re
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", os.environ.get("SUPABASE_KEY", ""))

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
TIMEOUT = 20

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("market-rates")


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
# Gold rates — BankBazaar
# ---------------------------------------------------------------------------

def scrape_gold_rates() -> list[dict]:
    """
    Returns rows for `market_rates` with:
      asset_type='gold_24k', value_type='price_per_gram_inr', period='current'
      asset_type='gold_24k', value_type='monthly_history', period='YYYY-MM'
    """
    log.info("Fetching gold rates from BankBazaar…")
    r = requests.get(
        "https://www.bankbazaar.com/gold-rate.html",
        headers=HEADERS,
        timeout=TIMEOUT,
        allow_redirects=True,
    )
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    fetched_at = datetime.now(timezone.utc).isoformat()
    rows = []

    tables = soup.find_all("table")
    today_price_per_8g = None
    monthly_rows = []

    for table in tables:
        # BankBazaar uses <td> for headers, not <th>
        all_rows = table.find_all("tr")
        if not all_rows:
            continue
        first_row_cells = [td.get_text(strip=True) for td in all_rows[0].find_all(["th", "td"])]
        first_row_text  = " ".join(first_row_cells)

        # City table: header row has "City" and "24K Gold Rate"
        if "City" in first_row_text and "24K" in first_row_text:
            for row in all_rows[1:]:
                cells = [td.get_text(strip=True) for td in row.find_all(["th", "td"])]
                if len(cells) >= 3 and "Bangalore" in cells[0]:
                    # cells[2] = "₹ 1,08,600(208▲)" for 8 grams
                    raw = re.sub(r"[^\d]", "", cells[2].split("(")[0])
                    if raw:
                        today_price_per_8g = float(raw)

        # Monthly history: header row has "Month" and "24K Price"
        elif "Month" in first_row_text and "24K" in first_row_text:
            for row in all_rows[1:]:
                cells = [td.get_text(strip=True) for td in row.find_all(["th", "td"])]
                if len(cells) >= 4:
                    month_str     = cells[0]   # e.g. "Oct-26"
                    price_24k_str = re.sub(r"[^\d]", "", cells[3])  # strip Rs., commas
                    if month_str and price_24k_str:
                        try:
                            dt     = datetime.strptime(month_str, "%b-%y")
                            period = dt.strftime("%Y-%m")
                            monthly_rows.append({
                                "period":          period,
                                "price_per_gram":  float(price_24k_str),
                            })
                        except ValueError:
                            pass

    # Build rows
    if today_price_per_8g:
        price_per_gram = round(today_price_per_8g / 8, 2)
        rows.append({
            "asset_type":   "gold_24k",
            "value_type":   "price_per_gram_inr",
            "period":       "current",
            "value":        price_per_gram,
            "source":       "BankBazaar",
            "fetched_at":   fetched_at,
            "notes":        "Live price per gram (24K) for Bangalore",
        })
        log.info("Gold today: ₹%.0f/g (24K)", price_per_gram)

    for mrow in monthly_rows:
        rows.append({
            "asset_type":   "gold_24k",
            "value_type":   "price_per_gram_inr",
            "period":       mrow["period"],
            "value":        mrow["price_per_gram"],
            "source":       "BankBazaar",
            "fetched_at":   fetched_at,
            "notes":        "Monthly average price per gram (24K)",
        })

    log.info("Gold rows: 1 current + %d monthly history", len(monthly_rows))
    return rows


# ---------------------------------------------------------------------------
# Nifty 50 returns — Yahoo Finance
# ---------------------------------------------------------------------------

def _cagr(start: float, end: float, years: float) -> Optional[float]:
    if start <= 0 or years <= 0:
        return None
    return round((((end / start) ** (1 / years)) - 1) * 100, 2)


def scrape_nifty_returns() -> list[dict]:
    """
    Returns rows for `market_rates` with CAGR over 1Y/3Y/5Y/10Y plus current level.
    """
    log.info("Fetching Nifty 50 data from Yahoo Finance…")
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/%5ENSEI"
        "?range=11y&interval=1mo"
    )
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()

    result     = data["chart"]["result"][0]
    timestamps = result["timestamp"]
    closes     = result["indicators"]["adjclose"][0]["adjclose"]

    # Clean + sort
    pairs = sorted(
        [(t, c) for t, c in zip(timestamps, closes) if c is not None],
        key=lambda x: x[0],
    )
    if not pairs:
        log.error("No Nifty data received")
        return []

    latest_ts, latest_price = pairs[-1]
    as_of = datetime.fromtimestamp(latest_ts, tz=timezone.utc)
    fetched_at = datetime.now(timezone.utc).isoformat()

    rows = [{
        "asset_type": "nifty50",
        "value_type": "index_level",
        "period":     "current",
        "value":      round(latest_price, 2),
        "source":     "Yahoo Finance (^NSEI)",
        "fetched_at": fetched_at,
        "notes":      f"Nifty 50 as of {as_of.strftime('%Y-%m-%d')}",
    }]

    for yrs in [1, 3, 5, 10]:
        target_ts = (as_of - timedelta(days=365 * yrs)).timestamp()
        best_pair = min(pairs, key=lambda x: abs(x[0] - target_ts))
        c = _cagr(best_pair[1], latest_price, yrs)
        if c is not None:
            rows.append({
                "asset_type": "nifty50",
                "value_type": f"cagr_{yrs}y_pct",
                "period":     "current",
                "value":      c,
                "source":     "Yahoo Finance (^NSEI)",
                "fetched_at": fetched_at,
                "notes":      (
                    f"CAGR computed from {datetime.fromtimestamp(best_pair[0]).strftime('%Y-%m')} "
                    f"({best_pair[1]:.0f}) to {as_of.strftime('%Y-%m')} ({latest_price:.0f})"
                ),
            })
            log.info("Nifty %dY CAGR: %.2f%%", yrs, c)

    # Monthly history for sparklines (last 60 months)
    recent = pairs[-60:]
    for ts, price in recent:
        period = datetime.fromtimestamp(ts).strftime("%Y-%m")
        rows.append({
            "asset_type": "nifty50",
            "value_type": "monthly_close",
            "period":     period,
            "value":      round(price, 2),
            "source":     "Yahoo Finance (^NSEI)",
            "fetched_at": fetched_at,
            "notes":      "Monthly adjusted close",
        })

    log.info("Nifty rows: %d total", len(rows))
    return rows


# ---------------------------------------------------------------------------
# FD rates — Paisabazaar
# ---------------------------------------------------------------------------

def scrape_fd_rates() -> list[dict]:
    """
    Returns rows for `market_rates` with FD rates per bank per tenure.
    """
    log.info("Fetching FD rates from Paisabazaar…")
    r = requests.get(
        "https://www.paisabazaar.com/fixed-deposit/",
        headers=HEADERS,
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    soup       = BeautifulSoup(r.text, "html.parser")
    fetched_at = datetime.now(timezone.utc).isoformat()
    rows       = []

    tables = soup.find_all("table")
    for table in tables:
        # Paisabazaar uses <td> not <th>
        all_trows = table.find_all("tr")
        if not all_trows:
            continue
        first_cells = [td.get_text(strip=True) for td in all_trows[0].find_all(["th", "td"])]
        first_text  = " ".join(first_cells)

        # Match table 0: "Banks | Highest FD rate | 1-year | 3-year | 5-year | senior"
        if "Banks" in first_text and ("1-year" in first_text or "Highest" in first_text):
            for row in all_trows[1:]:
                cells = [td.get_text(strip=True) for td in row.find_all("td")]
                if len(cells) < 5:
                    continue
                bank_name = cells[0].strip()
                if not bank_name or bank_name.lower() == "bank name":
                    continue

                rate_map = {
                    "highest":    _safe_float(cells[1]) if len(cells) > 1 else None,
                    "1y":         _safe_float(cells[2]) if len(cells) > 2 else None,
                    "3y":         _safe_float(cells[3]) if len(cells) > 3 else None,
                    "5y":         _safe_float(cells[4]) if len(cells) > 4 else None,
                    "senior_add": _safe_float(cells[5]) if len(cells) > 5 else None,
                }

                for tenure_key, rate_val in rate_map.items():
                    if rate_val is None:
                        continue
                    rows.append({
                        "asset_type": "fd",
                        "value_type": f"rate_{tenure_key}_pct",
                        "period":     "current",
                        "value":      rate_val,
                        "source":     "Paisabazaar",
                        "fetched_at": fetched_at,
                        "notes":      bank_name,  # bank name stored in notes
                    })

    # Deduplicate by (asset_type, value_type, period, notes)
    seen = set()
    deduped = []
    for row in rows:
        key = (row["asset_type"], row["value_type"], row["period"], row["notes"])
        if key not in seen:
            seen.add(key)
            deduped.append(row)

    log.info("FD rows: %d (across %d banks)", len(deduped),
             len(set(r["notes"] for r in deduped)))
    return deduped


def _safe_float(s: str) -> Optional[float]:
    try:
        return float(re.sub(r"[^\d.]", "", s))
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Scrape market rates into Supabase")
    parser.add_argument("--dry-run", action="store_true",
                        help="Fetch and print, skip DB writes")
    args = parser.parse_args()

    if not args.dry_run and (not SUPABASE_URL or not SUPABASE_KEY):
        raise SystemExit("Set SUPABASE_URL and SUPABASE_SERVICE_KEY in env or .env")

    all_rows: list[dict] = []

    # --- Gold ---
    try:
        all_rows += scrape_gold_rates()
    except Exception as exc:
        log.error("Gold scrape failed: %s", exc)

    # --- Nifty ---
    try:
        all_rows += scrape_nifty_returns()
    except Exception as exc:
        log.error("Nifty scrape failed: %s", exc)

    # --- FD ---
    try:
        all_rows += scrape_fd_rates()
    except Exception as exc:
        log.error("FD scrape failed: %s", exc)

    log.info("Total rows collected: %d", len(all_rows))

    if args.dry_run:
        # Print a sample of each type
        by_type: dict = {}
        for row in all_rows:
            k = row["asset_type"]
            by_type.setdefault(k, []).append(row)
        for k, v in by_type.items():
            print(f"\n=== {k} ({len(v)} rows) ===")
            print(json.dumps(v[:3], indent=2, ensure_ascii=False))
        log.info("DRY-RUN — no DB writes")
        return

    ok = upsert_rows("market_rates", all_rows)
    if ok:
        log.info("Done — %d rows upserted into market_rates", len(all_rows))
    else:
        raise SystemExit("Upsert failed — check logs")


if __name__ == "__main__":
    main()
