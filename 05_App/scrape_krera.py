"""
K-RERA Project Enrichment Scraper (Playwright version)
=======================================================
Searches rera.karnataka.gov.in by registration number and enriches
the `rera_projects` Supabase table with:
  status, district, taluk, project_type,
  reg_date (approved_on), completion_date

Uses Playwright to handle the JavaScript-heavy site.
Units sold / land area are NOT scraped (require modal — deferred).

Usage
-----
  # Enrich all un-enriched rows:
  python scrape_krera.py

  # Dry-run (print only, no DB writes):
  python scrape_krera.py --dry-run --limit 5

  # Enrich a specific registration number:
  python scrape_krera.py --prm "PRM/KA/RERA/1251/309/PR/170916/000459"

Install (once):
  pip install playwright python-dotenv supabase
  playwright install chromium

Environment variables (or .env file):
  SUPABASE_URL          your project URL
  SUPABASE_SERVICE_KEY  service-role key
"""

import argparse
import asyncio
import os
import time
import logging
from datetime import datetime, timezone
from typing import Optional

import requests
from dotenv import load_dotenv
from playwright.async_api import async_playwright, TimeoutError as PWTimeout

load_dotenv()

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", os.environ.get("SUPABASE_KEY", ""))

KRERA_URL   = "https://rera.karnataka.gov.in/viewAllProjects"
BATCH_SIZE  = 50
PAGE_DELAY  = 2.0   # seconds between searches (be polite)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("krera-scraper")


# ---------------------------------------------------------------------------
# Supabase helpers
# ---------------------------------------------------------------------------

def _sb_headers() -> dict:
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }


def fetch_rows(offset: int, limit: int) -> list[dict]:
    url = f"{SUPABASE_URL}/rest/v1/rera_projects"
    params = {
        "select": "id,ack_number,prm_number",
        "enriched_at": "is.null",
        "limit": str(limit),
        "offset": str(offset),
    }
    resp = requests.get(url, headers=_sb_headers(), params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def fetch_one_by_prm(prm: str) -> Optional[dict]:
    url = f"{SUPABASE_URL}/rest/v1/rera_projects"
    params = {
        "select": "id,ack_number,prm_number",
        "prm_number": f"eq.{prm}",
        "limit": "1",
    }
    resp = requests.get(url, headers=_sb_headers(), params=params, timeout=30)
    resp.raise_for_status()
    rows = resp.json()
    return rows[0] if rows else None


def update_row(row_id: int, payload: dict) -> bool:
    url = f"{SUPABASE_URL}/rest/v1/rera_projects?id=eq.{row_id}"
    payload["enriched_at"] = datetime.now(timezone.utc).isoformat()
    resp = requests.patch(url, headers=_sb_headers(), json=payload, timeout=30)
    return resp.status_code in (200, 204)


# ---------------------------------------------------------------------------
# Playwright scraper
# ---------------------------------------------------------------------------

def _parse_date(text: str) -> Optional[str]:
    text = text.strip()
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            pass
    return None


async def search_and_parse(page, prm_number: str) -> Optional[dict]:
    """
    Search K-RERA by PRM number, parse the first result row.
    Returns a dict of fields or None if not found.
    """
    try:
        await page.goto(KRERA_URL, timeout=90000, wait_until="domcontentloaded")
        await asyncio.sleep(1.5)
    except PWTimeout:
        log.warning("Page load timeout for %s", prm_number)
        return None

    try:
        await page.fill("#regNo2", prm_number)
        await page.click("input[name='btn1']")
        await asyncio.sleep(2.5)
    except Exception as e:
        log.warning("Search interaction failed: %s", e)
        return None

    # Parse results table — skip header rows (rows 0 and 1)
    rows = await page.locator("table tr").all()
    data_rows = rows[2:]  # first two are header
    if not data_rows:
        log.warning("No results for %s", prm_number)
        return None

    first_row = data_rows[0]
    txt = await first_row.inner_text()
    cols = [c.strip() for c in txt.split("\t")]

    # Column indices (from observed output):
    # 0=S.NO, 1=ACK_NO, 2=REG_NO, 3=(view icon blank), 4=PROMOTER,
    # 5=PROJECT_NAME, 6=STATUS, 7=DISTRICT, 8=TALUK, 9=PROJECT_TYPE,
    # 10=APPROVED_ON, 11=COMPLETION_DATE
    if len(cols) < 10:
        log.warning("Unexpected column count %d for %s", len(cols), prm_number)
        return None

    def get(i):
        v = cols[i] if i < len(cols) else ""
        return v if v and v != "N/A" else None

    payload = {
        "status":          get(6),
        "district":        get(7),
        "taluk":           get(8),
        "project_type":    get(9),
    }

    raw_reg  = get(10)
    raw_comp = get(11)
    if raw_reg:
        payload["reg_date"] = _parse_date(raw_reg)
    if raw_comp:
        payload["completion_date"] = _parse_date(raw_comp)

    # Drop None values
    return {k: v for k, v in payload.items() if v is not None}


async def run_enrichment(rows: list[dict], dry_run: bool):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page    = await browser.new_page()

        for row in rows:
            prm = row.get("prm_number", "")
            if not prm:
                log.warning("Row id=%s has no prm_number — skipping", row["id"])
                continue

            log.info("Searching: %s", prm)
            payload = await search_and_parse(page, prm)

            if not payload:
                log.warning("No data for id=%s prm=%s", row["id"], prm)
            elif dry_run:
                log.info("DRY-RUN id=%s: %s", row["id"], payload)
            else:
                ok = update_row(row["id"], payload)
                if ok:
                    log.info("Updated id=%s — status=%s district=%s",
                             row["id"], payload.get("status", "?"),
                             payload.get("district", "?"))
                else:
                    log.warning("DB update failed for id=%s", row["id"])

            await asyncio.sleep(PAGE_DELAY)

        await browser.close()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Enrich K-RERA projects via Playwright")
    parser.add_argument("--prm",      help="Single PRM registration number to enrich")
    parser.add_argument("--dry-run",  action="store_true")
    parser.add_argument("--limit",    type=int, default=0,
                        help="Max rows to process (0 = all)")
    args = parser.parse_args()

    if not SUPABASE_URL or not SUPABASE_KEY:
        raise SystemExit("Set SUPABASE_URL and SUPABASE_SERVICE_KEY in env or .env")

    if args.prm:
        row = fetch_one_by_prm(args.prm)
        if not row:
            raise SystemExit(f"PRM not found in DB: {args.prm}")
        asyncio.run(run_enrichment([row], args.dry_run))
        return

    offset    = 0
    processed = 0

    while True:
        batch = fetch_rows(offset, BATCH_SIZE)
        if not batch:
            break

        to_process = batch
        if args.limit and processed + len(to_process) > args.limit:
            to_process = to_process[:args.limit - processed]

        asyncio.run(run_enrichment(to_process, args.dry_run))
        processed += len(to_process)
        log.info("Batch done — %d rows processed so far.", processed)

        if args.limit and processed >= args.limit:
            break

        offset += BATCH_SIZE

    log.info("All done — %d rows enriched.", processed)


if __name__ == "__main__":
    main()
