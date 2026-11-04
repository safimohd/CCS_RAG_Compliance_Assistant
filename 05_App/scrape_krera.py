"""
K-RERA Project Enrichment Scraper
==================================
Fetches project detail pages from rera.karnataka.gov.in and enriches
the `rera_projects` Supabase table with:
  status, district, taluk, project_type, total_units, sold_units,
  completion_date, reg_date, lapse_date, land_area_sqft, detail_url

Usage
-----
  # Enrich all un-enriched rows (enriched_at IS NULL):
  python scrape_krera.py

  # Enrich a specific ACK number:
  python scrape_krera.py --ack "ACK/KA/RERA/1251/310/PR/281026/010719"

  # Dry-run (parse + print, no DB writes):
  python scrape_krera.py --dry-run --limit 5

  # Resume / re-enrich rows older than N days:
  python scrape_krera.py --re-enrich-days 30

Environment variables (or .env file):
  SUPABASE_URL          your project URL
  SUPABASE_SERVICE_KEY  service-role key (not anon key — needs UPDATE)

Install dependencies (once):
  pip install requests beautifulsoup4 python-dotenv supabase --break-system-packages
"""

import argparse
import os
import re
import time
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

load_dotenv()

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", os.environ.get("SUPABASE_KEY", ""))

BASE_URL = "https://rera.karnataka.gov.in"

DETAIL_URL_TEMPLATE = (
    "https://rera.karnataka.gov.in/projectViewDetails"
    "?id={prm_number}"          # used if detail_url column is populated
)

# ACK number forms the search URL on the portal
ACK_SEARCH_URL = (
    "https://rera.karnataka.gov.in/viewAllProjects"
    "?acknumber={ack}"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; CCS-KRERA-Bot/1.0; "
        "+https://github.com/ccs-project/scraper)"
    ),
    "Accept-Language": "en-IN,en;q=0.9",
}

BATCH_SIZE    = 50     # rows fetched from Supabase per iteration
REQUEST_DELAY = 1.5    # seconds between portal requests (be polite)
MAX_RETRIES   = 3

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("krera-scraper")


# ---------------------------------------------------------------------------
# Supabase helpers (plain REST, no extra SDK needed)
# ---------------------------------------------------------------------------

def _sb_headers() -> dict:
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }


def fetch_rows(offset: int, limit: int, re_enrich_days: Optional[int]) -> list[dict]:
    """Fetch rows that need enriching from Supabase."""
    url = f"{SUPABASE_URL}/rest/v1/rera_projects"

    if re_enrich_days:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=re_enrich_days)).isoformat()
        filter_q = f"enriched_at=is.null,or=(enriched_at=lt.{cutoff})"
        params = {
            "select": "id,ack_number,prm_number,detail_url",
            "and": f"(enriched_at.is.null),or=(enriched_at.lt.{cutoff})",
        }
    else:
        params = {
            "select": "id,ack_number,prm_number,detail_url",
            "enriched_at": "is.null",
        }

    params["limit"]  = str(limit)
    params["offset"] = str(offset)

    resp = requests.get(url, headers=_sb_headers(), params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def fetch_one(ack_number: str) -> Optional[dict]:
    """Fetch a single row by ACK number."""
    url = f"{SUPABASE_URL}/rest/v1/rera_projects"
    params = {
        "select": "id,ack_number,prm_number,detail_url",
        "ack_number": f"eq.{ack_number}",
        "limit": "1",
    }
    resp = requests.get(url, headers=_sb_headers(), params=params, timeout=30)
    resp.raise_for_status()
    rows = resp.json()
    return rows[0] if rows else None


def update_row(row_id: int, payload: dict) -> bool:
    """PATCH a row in Supabase."""
    url = f"{SUPABASE_URL}/rest/v1/rera_projects?id=eq.{row_id}"
    payload["enriched_at"] = datetime.now(timezone.utc).isoformat()
    resp = requests.patch(url, headers=_sb_headers(), json=payload, timeout=30)
    return resp.status_code in (200, 204)


# ---------------------------------------------------------------------------
# Portal scraping
# ---------------------------------------------------------------------------

def _get_html(url: str) -> Optional[str]:
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.get(url, headers=HEADERS, timeout=20)
            if r.status_code == 200:
                return r.text
            log.warning("HTTP %s for %s (attempt %d)", r.status_code, url, attempt)
        except requests.RequestException as exc:
            log.warning("Request error %s (attempt %d): %s", url, attempt, exc)
        time.sleep(REQUEST_DELAY * attempt)
    return None


def _parse_date(text: str) -> Optional[date]:
    """Parse dd/mm/yyyy or dd-mm-yyyy or similar."""
    text = text.strip()
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d %b %Y", "%d-%b-%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    return None


def _extract_text(soup: BeautifulSoup, label: str) -> str:
    """Find the value cell adjacent to a label cell on the detail page."""
    td = soup.find(lambda tag: tag.name == "td" and label.lower() in tag.get_text(strip=True).lower())
    if td:
        sibling = td.find_next_sibling("td")
        if sibling:
            return sibling.get_text(strip=True)
    return ""


def parse_project_detail(html: str, url: str) -> dict:
    """
    Parse K-RERA project detail HTML into a flat dict matching our columns.
    The portal renders a table-based layout; we probe for known label text.
    """
    soup = BeautifulSoup(html, "html.parser")
    data: dict = {"detail_url": url}

    # --- Registration Status ---
    raw = _extract_text(soup, "Status")
    if not raw:
        raw = _extract_text(soup, "Project Status")
    data["status"] = raw or None

    # --- District / Taluk ---
    data["district"] = _extract_text(soup, "District") or None
    data["taluk"]    = _extract_text(soup, "Taluk") or None

    # --- Project Type ---
    for lbl in ("Project Type", "Type of Project", "Type"):
        v = _extract_text(soup, lbl)
        if v:
            data["project_type"] = v
            break

    # --- Units ---
    for lbl in ("Total Units", "Total No. of Units", "No of Units"):
        v = _extract_text(soup, lbl)
        if v and v.isdigit():
            data["total_units"] = int(v)
            break

    for lbl in ("Units Sold", "No. of Units Sold", "Sold Units"):
        v = _extract_text(soup, lbl)
        if v and v.isdigit():
            data["sold_units"] = int(v)
            break

    # --- Dates ---
    for lbl in ("Date of Registration", "Registration Date", "Reg Date"):
        v = _extract_text(soup, lbl)
        if v:
            d = _parse_date(v)
            if d:
                data["reg_date"] = d.isoformat()
                break

    for lbl in ("Proposed Date of Completion", "Completion Date", "Project Completion Date"):
        v = _extract_text(soup, lbl)
        if v:
            d = _parse_date(v)
            if d:
                data["completion_date"] = d.isoformat()
                break

    for lbl in ("Lapse Date", "Expiry Date", "Valid Till"):
        v = _extract_text(soup, lbl)
        if v:
            d = _parse_date(v)
            if d:
                data["lapse_date"] = d.isoformat()
                break

    # --- Land Area ---
    for lbl in ("Land Area", "Total Land Area", "Site Area"):
        v = _extract_text(soup, lbl)
        # strip "sq.ft", "sqft", "sq ft", etc.
        num = re.sub(r"[^\d.]", "", v.lower().replace("sq", "").replace("ft", ""))
        if num:
            try:
                data["land_area_sqft"] = float(num)
                break
            except ValueError:
                pass

    return data


def build_detail_url(row: dict) -> str:
    """
    Construct the K-RERA project detail URL.
    Try stored detail_url first, then PRM number, then ACK search.
    """
    if row.get("detail_url"):
        return row["detail_url"]

    prm = row.get("prm_number", "")
    if prm:
        return f"{BASE_URL}/projectViewDetails?id={prm}"

    ack = row.get("ack_number", "")
    return ACK_SEARCH_URL.format(ack=requests.utils.quote(ack, safe=""))


def enrich_row(row: dict, dry_run: bool) -> bool:
    """Fetch detail page, parse, and update Supabase. Returns True on success."""
    url = build_detail_url(row)
    log.info("  GET %s", url)

    html = _get_html(url)
    if not html:
        log.warning("  No HTML returned — skipping row id=%s", row["id"])
        return False

    parsed = parse_project_detail(html, url)

    # Only keep fields with actual values to avoid overwriting with None
    payload = {k: v for k, v in parsed.items() if v is not None}

    if dry_run:
        log.info("  DRY-RUN payload: %s", payload)
        return True

    ok = update_row(row["id"], payload)
    if ok:
        log.info("  Updated id=%s — status=%s district=%s", row["id"],
                 payload.get("status", "?"), payload.get("district", "?"))
    else:
        log.warning("  DB update failed for id=%s", row["id"])
    return ok


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Enrich K-RERA projects in Supabase")
    parser.add_argument("--ack",             help="Single ACK number to enrich")
    parser.add_argument("--dry-run",         action="store_true")
    parser.add_argument("--limit",           type=int, default=0,
                        help="Max rows to process (0 = all)")
    parser.add_argument("--re-enrich-days",  type=int, default=0,
                        help="Re-enrich rows whose enriched_at is older than N days")
    args = parser.parse_args()

    if not SUPABASE_URL or not SUPABASE_KEY:
        raise SystemExit("Set SUPABASE_URL and SUPABASE_SERVICE_KEY in env or .env")

    # --- Single ACK mode ---
    if args.ack:
        row = fetch_one(args.ack)
        if not row:
            raise SystemExit(f"ACK not found in DB: {args.ack}")
        enrich_row(row, args.dry_run)
        return

    # --- Batch mode ---
    offset    = 0
    processed = 0
    re_days   = args.re_enrich_days if args.re_enrich_days > 0 else None

    while True:
        rows = fetch_rows(offset, BATCH_SIZE, re_days)
        if not rows:
            break

        for row in rows:
            enrich_row(row, args.dry_run)
            processed += 1
            time.sleep(REQUEST_DELAY)

            if args.limit and processed >= args.limit:
                log.info("Reached limit of %d rows — stopping.", args.limit)
                return

        offset += BATCH_SIZE
        log.info("Batch done — %d rows processed so far.", processed)

    log.info("All done — %d rows enriched.", processed)


if __name__ == "__main__":
    main()
