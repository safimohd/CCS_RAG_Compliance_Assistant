"""
K-RERA Project Enrichment Scraper (Playwright, parallel)
=========================================================
Searches rera.karnataka.gov.in by registration number and enriches
the `rera_projects` Supabase table with:
  status, district, taluk, project_type, reg_date, completion_date

Runs CONCURRENCY searches in parallel. If the laptop sleeps mid-run,
just re-run — rows already written to Supabase are skipped automatically
(enriched_at IS NULL filter).

Usage
-----
  python scrape_krera.py                        # enrich all
  python scrape_krera.py --dry-run --limit 5   # test without DB writes
  python scrape_krera.py --prm "PRM/KA/..."    # single row
  python scrape_krera.py --concurrency 5       # run 5 in parallel (default 3)

Install (once):
  pip install playwright python-dotenv requests
  playwright install chromium

Environment variables (or .env):
  SUPABASE_URL          your project URL
  SUPABASE_SERVICE_KEY  service-role key
"""

import argparse
import asyncio
import os
import time
import logging
import datetime as dt
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
PAGE_DELAY  = 1.5   # seconds between searches per worker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("krera-scraper")

# Shared counters (updated by all workers)
_stats = {"done": 0, "ok": 0, "fail": 0, "start": 0.0, "total": 0}


def _elapsed() -> str:
    secs = int(time.time() - _stats["start"])
    h, r = divmod(secs, 3600)
    m, s = divmod(r, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _rate() -> float:
    """Rows per second."""
    secs = time.time() - _stats["start"]
    if secs < 1:
        return 0.0
    return _stats["done"] / secs


def _progress() -> str:
    total   = _stats["total"]
    done    = _stats["done"]
    elapsed = _elapsed()
    rps     = _rate()

    if total:
        pct = done / total * 100
        progress = f"{done}/{total} ({pct:.0f}%)"
    else:
        progress = str(done)

    if rps > 0 and total and done < total:
        secs_left = (total - done) / rps
        eta_time  = datetime.now() + dt.timedelta(seconds=secs_left)
        m_left, s_left = divmod(int(secs_left), 60)
        h_left, m_left = divmod(m_left, 60)
        if h_left:
            remaining = f"{h_left}h {m_left}m left"
        else:
            remaining = f"{m_left}m left"
        eta_str = eta_time.strftime("%I:%M %p")
        return f"{progress} | elapsed {elapsed} | {remaining} | ETA {eta_str}"
    else:
        return f"{progress} | elapsed {elapsed}"


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
    for attempt in range(3):
        try:
            resp = requests.patch(url, headers=_sb_headers(), json=payload, timeout=30)
            return resp.status_code in (200, 204)
        except Exception as e:
            if attempt < 2:
                log.warning("DB write attempt %d failed for id=%s: %s — retrying", attempt + 1, row_id, e)
                time.sleep(2 ** attempt)
            else:
                log.error("DB write failed after 3 attempts for id=%s: %s", row_id, e)
    return False


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


async def _extract_total_units(page) -> Optional[int]:
    """
    Clicks into the project detail modal and reads total_units from the
    'Type of Inventory' table (the TOTAL row in the Project Details tab).
    Returns None if the detail page or the TOTAL row is not found.
    """
    try:
        # Click the first 'View Project Details' link in the results table
        detail_link = page.locator("table a").first
        await detail_link.click()
        await asyncio.sleep(2.0)

        # Click the 'Project Details' tab (#menu2)
        proj_tab = page.locator("a[href='#menu2']")
        await proj_tab.click()
        await asyncio.sleep(1.0)

        # Find the TOTAL row in the inventory table inside #menu2
        # The row has a cell with text "TOTAL" and the next cell is total units
        rows = await page.locator("#menu2 table tr").all()
        for row in rows:
            txt = await row.inner_text()
            cols = [c.strip() for c in txt.split("\t")]
            if cols and cols[0].upper() == "TOTAL" and len(cols) >= 3:
                try:
                    return int(cols[2])
                except ValueError:
                    pass

        # Fallback: look for "Total No. of Units" label in key-value cells
        cells = await page.locator("#menu2 td").all()
        for i, cell in enumerate(cells):
            label = (await cell.inner_text()).strip()
            if "total no. of units" in label.lower() and i + 1 < len(cells):
                val = (await cells[i + 1].inner_text()).strip()
                try:
                    return int(val)
                except ValueError:
                    pass

        log.warning("TOTAL row not found in Project Details tab")
        return None

    except Exception as e:
        log.warning("Detail page extraction failed: %s", e)
        return None
    finally:
        # Close the modal if it's open (press Escape)
        try:
            await page.keyboard.press("Escape")
            await asyncio.sleep(0.5)
        except Exception:
            pass


async def search_and_parse(page, prm_number: str) -> Optional[dict]:
    try:
        await page.goto(KRERA_URL, timeout=90000, wait_until="domcontentloaded")
        await asyncio.sleep(1.2)
    except PWTimeout:
        log.warning("Page load timeout for %s", prm_number)
        return None

    try:
        await page.fill("#regNo2", prm_number)
        await page.click("input[name='btn1']")
        await asyncio.sleep(2.5)
    except Exception as e:
        log.warning("Search interaction failed for %s: %s", prm_number, e)
        return None

    rows = await page.locator("table tr").all()
    data_rows = rows[2:]  # first two are header rows
    if not data_rows:
        log.warning("No results for %s", prm_number)
        return None

    txt = await data_rows[0].inner_text()
    cols = [c.strip() for c in txt.split("\t")]

    # Column indices (from observed page structure):
    # 0=S.NO 1=ACK_NO 2=REG_NO 3=(icon) 4=PROMOTER 5=PROJECT_NAME
    # 6=STATUS 7=DISTRICT 8=TALUK 9=PROJECT_TYPE 10=APPROVED_ON 11=COMPLETION_DATE
    if len(cols) < 10:
        log.warning("Unexpected column count %d for %s", len(cols), prm_number)
        return None

    def get(i):
        v = cols[i] if i < len(cols) else ""
        return v.strip() if v and v.strip() not in ("", "N/A") else None

    payload = {
        "status":       get(6),
        "district":     get(7),
        "taluk":        get(8),
        "project_type": get(9),
    }
    if get(10):
        payload["reg_date"] = _parse_date(get(10))
    if get(11):
        payload["completion_date"] = _parse_date(get(11))

    # Extract total_units from the detail page
    total_units = await _extract_total_units(page)
    if total_units is not None:
        payload["total_units"] = total_units

    return {k: v for k, v in payload.items() if v is not None}


async def worker(worker_id: int, queue: asyncio.Queue,
                 browser, dry_run: bool, semaphore: asyncio.Semaphore):
    page = await browser.new_page()
    while True:
        try:
            row = queue.get_nowait()
        except asyncio.QueueEmpty:
            break

        prm = row.get("prm_number", "")
        if not prm:
            log.warning("[W%d] Row id=%s has no prm_number — skipping", worker_id, row["id"])
            queue.task_done()
            continue

        async with semaphore:
            payload = await search_and_parse(page, prm)

        _stats["done"] += 1
        prog = _progress()

        if not payload:
            _stats["fail"] += 1
            log.warning("[W%d] [%s] No data id=%s", worker_id, prog, row["id"])
        elif dry_run:
            _stats["ok"] += 1
            log.info("[W%d] [%s] DRY-RUN id=%s: %s", worker_id, prog, row["id"], payload)
        else:
            ok = update_row(row["id"], payload)
            if ok:
                _stats["ok"] += 1
                log.info("[W%d] [%s] OK id=%s status=%s district=%s",
                         worker_id, prog, row["id"],
                         payload.get("status", "?"), payload.get("district", "?"))
            else:
                _stats["fail"] += 1
                log.warning("[W%d] [%s] DB fail id=%s", worker_id, prog, row["id"])

        queue.task_done()
        await asyncio.sleep(PAGE_DELAY)

    await page.close()


async def run_enrichment(rows: list[dict], dry_run: bool, concurrency: int):
    queue     = asyncio.Queue()
    semaphore = asyncio.Semaphore(concurrency)

    for row in rows:
        await queue.put(row)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        workers = [
            asyncio.create_task(
                worker(i + 1, queue, browser, dry_run, semaphore)
            )
            for i in range(concurrency)
        ]
        await asyncio.gather(*workers)
        await browser.close()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Enrich K-RERA projects via Playwright")
    parser.add_argument("--prm",         help="Single PRM registration number")
    parser.add_argument("--dry-run",     action="store_true")
    parser.add_argument("--limit",       type=int, default=0,
                        help="Max rows to process (0 = all)")
    parser.add_argument("--concurrency", type=int, default=3,
                        help="Parallel browser tabs (default 3)")
    args = parser.parse_args()

    if not SUPABASE_URL or not SUPABASE_KEY:
        raise SystemExit("Set SUPABASE_URL and SUPABASE_SERVICE_KEY in env or .env")

    _stats["start"] = time.time()

    # Fetch total unenriched count for progress display
    if not args.prm:
        try:
            url = f"{SUPABASE_URL}/rest/v1/rera_projects"
            resp = requests.get(url, headers={**_sb_headers(), "Prefer": "count=exact"},
                                params={"select": "id", "enriched_at": "is.null", "limit": "1"},
                                timeout=15)
            cr = resp.headers.get("content-range", "")
            _stats["total"] = int(cr.split("/")[1]) if "/" in cr else 0
            if args.limit:
                _stats["total"] = min(_stats["total"], args.limit)
            log.info("Total rows to enrich: %d", _stats["total"])
        except Exception:
            pass

    if args.prm:
        row = fetch_one_by_prm(args.prm)
        if not row:
            raise SystemExit(f"PRM not found in DB: {args.prm}")
        asyncio.run(run_enrichment([row], args.dry_run, 1))
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

        log.info("--- Batch starting: rows %d to %d ---",
                 offset + 1, offset + len(to_process))
        asyncio.run(run_enrichment(to_process, args.dry_run, args.concurrency))
        processed += len(to_process)

        total_secs = int(time.time() - _stats["start"])
        log.info("Batch done — %d processed | %d ok | %d failed | elapsed %s",
                 processed, _stats["ok"], _stats["fail"], _elapsed())

        if args.limit and processed >= args.limit:
            break

        offset += BATCH_SIZE

    log.info("ALL DONE — %d rows | %d ok | %d failed | total time %s",
             processed, _stats["ok"], _stats["fail"], _elapsed())


if __name__ == "__main__":
    main()
