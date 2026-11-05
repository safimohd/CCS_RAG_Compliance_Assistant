"""
Karnataka Pincode Scraper (parallel, with progress display)
============================================================
Fetches Karnataka pincode data from the free India Post API
(api.postalpincode.in) and stores results in the Supabase `pincodes` table.

Runs CONCURRENCY workers in parallel. Each worker fetches one pincode,
buffers Karnataka rows, and flushes to Supabase every BATCH_SIZE records.

Usage
-----
  python scrape_pincodes.py                        # fetch all
  python scrape_pincodes.py --resume               # continue from last DB entry
  python scrape_pincodes.py --dry-run --limit 50   # test without DB writes
  python scrape_pincodes.py --concurrency 5        # 5 parallel workers (default 3)

Environment variables (or .env):
  SUPABASE_URL          your project URL
  SUPABASE_SERVICE_KEY  service-role key

Install:
  pip install aiohttp python-dotenv requests --break-system-packages
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
import aiohttp
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", os.environ.get("SUPABASE_KEY", ""))

KA_PINCODE_START = 560001
KA_PINCODE_END   = 591346
TOTAL_PINCODES   = KA_PINCODE_END - KA_PINCODE_START + 1

INDIA_POST_API = "https://api.postalpincode.in/pincode/{pincode}"
REQUEST_DELAY  = 1.2   # seconds between calls per worker
BATCH_SIZE     = 200   # upsert rows in one Supabase call

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("pincode-scraper")

# Shared stats across all workers
_stats = {"done": 0, "rows": 0, "fail": 0, "start": 0.0, "total": 0}
_lock  = asyncio.Lock()   # protects the shared buffer


def _elapsed() -> str:
    secs = int(time.time() - _stats["start"])
    h, r = divmod(secs, 3600)
    m, s = divmod(r, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _progress() -> str:
    total   = _stats["total"]
    done    = _stats["done"]
    elapsed = _elapsed()
    secs    = time.time() - _stats["start"]
    rps     = done / secs if secs > 1 else 0

    if total:
        pct      = done / total * 100
        progress = f"{done}/{total} ({pct:.0f}%)"
    else:
        progress = str(done)

    if rps > 0 and total and done < total:
        secs_left  = (total - done) / rps
        eta_time   = datetime.now() + dt.timedelta(seconds=secs_left)
        m_left, _  = divmod(int(secs_left), 60)
        h_left, m_left = divmod(m_left, 60)
        remaining  = f"{h_left}h {m_left}m left" if h_left else f"{m_left}m left"
        eta_str    = eta_time.strftime("%I:%M %p")
        return f"{progress} | elapsed {elapsed} | {remaining} | ETA {eta_str} | {_stats['rows']} KA rows"
    return f"{progress} | elapsed {elapsed} | {_stats['rows']} KA rows"


# ---------------------------------------------------------------------------
# Supabase helpers (sync, called from asyncio via run_in_executor)
# ---------------------------------------------------------------------------

def _sb_headers() -> dict:
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }


def _upsert_rows_sync(rows: list[dict]) -> bool:
    url = f"{SUPABASE_URL}/rest/v1/pincodes?on_conflict=pincode%2Coffice_name"
    for attempt in range(3):
        try:
            resp = requests.post(url, headers=_sb_headers(), json=rows, timeout=30)
            if resp.status_code in (200, 201):
                return True
            log.warning("Supabase upsert HTTP %s: %s", resp.status_code, resp.text[:100])
        except Exception as e:
            if attempt < 2:
                log.warning("Supabase upsert attempt %d failed: %s — retrying", attempt + 1, e)
                time.sleep(2 ** attempt)
            else:
                log.error("Supabase upsert failed after 3 attempts: %s", e)
    return False


def get_max_pincode() -> Optional[int]:
    url    = f"{SUPABASE_URL}/rest/v1/pincodes"
    params = {"select": "pincode", "state": "eq.Karnataka",
              "order": "pincode.desc", "limit": "1"}
    hdrs   = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    try:
        resp = requests.get(url, headers=hdrs, params=params, timeout=30)
        if resp.status_code == 200:
            rows = resp.json()
            if rows:
                return int(rows[0]["pincode"])
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Async worker
# ---------------------------------------------------------------------------

async def fetch_pincode(session: aiohttp.ClientSession, pincode: int) -> list[dict]:
    url = INDIA_POST_API.format(pincode=pincode)
    for attempt in range(3):
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json(content_type=None)
                if not data or data[0].get("Status") != "Success":
                    return []
                rows = []
                for po in (data[0].get("PostOffice") or []):
                    if po.get("State") != "Karnataka":
                        continue
                    rows.append({
                        "pincode":         str(pincode),
                        "office_name":     po.get("Name", ""),
                        "delivery_status": po.get("DeliveryStatus", ""),
                        "district":        po.get("District", ""),
                        "division_name":   po.get("Division", ""),
                        "region":          po.get("Region", ""),
                        "block":           po.get("Block", ""),
                        "state":           "Karnataka",
                        "country":         po.get("Country", "India"),
                        "fetched_at":      datetime.now(timezone.utc).isoformat(),
                    })
                return rows
        except Exception as e:
            if attempt < 2:
                await asyncio.sleep(5)
            else:
                log.debug("Failed pincode %d: %s", pincode, e)
    return []


async def worker(worker_id: int, queue: asyncio.Queue,
                 buffer: list, dry_run: bool, semaphore: asyncio.Semaphore,
                 loop):
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                pincode = queue.get_nowait()
            except asyncio.QueueEmpty:
                break

            async with semaphore:
                rows = await fetch_pincode(session, pincode)

            async with _lock:
                _stats["done"] += 1
                if rows:
                    buffer.extend(rows)
                    _stats["rows"] += len(rows)
                    log.info("[W%d] [%s] %d → %d record(s) district=%s",
                             worker_id, _progress(), pincode, len(rows),
                             rows[0].get("district", "?"))
                else:
                    _stats["fail"] += 1

                # Flush buffer when full
                if len(buffer) >= BATCH_SIZE:
                    to_flush = buffer.copy()
                    buffer.clear()
                    if not dry_run:
                        ok = await loop.run_in_executor(None, _upsert_rows_sync, to_flush)
                        log.info("[W%d] [%s] Flushed %d rows to Supabase (%s)",
                                 worker_id, _progress(), len(to_flush),
                                 "OK" if ok else "FAILED")
                    else:
                        log.info("[W%d] DRY-RUN: would upsert %d rows", worker_id, len(to_flush))

            queue.task_done()
            await asyncio.sleep(REQUEST_DELAY)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def run(args):
    start_pin = args.start

    if args.resume and not args.dry_run:
        last = get_max_pincode()
        if last:
            start_pin = last + 1
            log.info("Resuming from pincode %d (last stored: %d)", start_pin, last)

    end_pin = args.end
    if args.limit:
        end_pin = min(end_pin, start_pin + args.limit - 1)

    _stats["total"] = end_pin - start_pin + 1
    _stats["start"] = time.time()

    log.info("Starting: pincodes %d to %d (%d total) | %d workers",
             start_pin, end_pin, _stats["total"], args.concurrency)

    queue = asyncio.Queue()
    for pin in range(start_pin, end_pin + 1):
        await queue.put(pin)

    buffer    = []
    semaphore = asyncio.Semaphore(args.concurrency)
    loop      = asyncio.get_event_loop()

    workers = [
        asyncio.create_task(
            worker(i + 1, queue, buffer, args.dry_run, semaphore, loop)
        )
        for i in range(args.concurrency)
    ]
    await asyncio.gather(*workers)

    # Final flush
    if buffer:
        if not args.dry_run:
            ok = await loop.run_in_executor(None, _upsert_rows_sync, buffer)
            log.info("Final flush: %d rows (%s)", len(buffer), "OK" if ok else "FAILED")
        else:
            log.info("DRY-RUN: final flush of %d rows", len(buffer))

    log.info("ALL DONE — %d pincodes checked | %d Karnataka rows stored | %d no-data | elapsed %s",
             _stats["done"], _stats["rows"], _stats["fail"], _elapsed())


def main():
    parser = argparse.ArgumentParser(description="Scrape Karnataka pincodes (parallel)")
    parser.add_argument("--start",       type=int, default=KA_PINCODE_START)
    parser.add_argument("--end",         type=int, default=KA_PINCODE_END)
    parser.add_argument("--limit",       type=int, default=0,
                        help="Max pincodes to process (0 = all)")
    parser.add_argument("--dry-run",     action="store_true")
    parser.add_argument("--resume",      action="store_true",
                        help="Auto-detect last stored pincode and resume")
    parser.add_argument("--concurrency", type=int, default=3,
                        help="Parallel workers (default 3)")
    args = parser.parse_args()

    if not args.dry_run and (not SUPABASE_URL or not SUPABASE_KEY):
        raise SystemExit("Set SUPABASE_URL and SUPABASE_SERVICE_KEY in env or .env")

    asyncio.run(run(args))


if __name__ == "__main__":
    main()
