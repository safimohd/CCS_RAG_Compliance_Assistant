#!/usr/bin/env python3
"""
download_rera.py
Bounded, authoritative RERA corpus downloader for the CCS RAG project.

Corpus scope:
- Central RERA Act (current India Code copy used by the benchmark)
- Uttar Pradesh RERA Rules, 2016
- Uttar Pradesh RERA Agreement for Sale/Lease Rules, 2018
- UP RERA Project Bank Account Directions, 3rd Revision, 11-May-2026
- MoHUPA RERA FAQs
- UP RERA Office Order No. 1125/2018-19 (CA/Architect/Engineer certificates)

IMPORTANT:
- Do NOT add benchmark files/questions to the corpus.
- Files are saved only after verifying the PDF magic bytes (%PDF-).
- URLs are intentionally hard-coded to a bounded list; this script does not crawl
  the RERA website.
"""

from __future__ import annotations

import csv
import hashlib
import os
import re
import time
from pathlib import Path
from urllib.parse import urlparse, unquote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


OUT = Path("RealEstate_RAG_Raw")
INDEX = OUT / "rera_master_index.csv"

DOCUMENTS = [
    {
        "category": "01_RERA_Act",
        "title": "Real Estate (Regulation and Development) Act, 2016",
        "year": 2016,
        "source": "India Code",
        "url": "https://www.indiacode.nic.in/bitstream/123456789/2158/1/A201616.pdf",
    },
    {
        "category": "02_UP_RERA_Rules",
        "title": "Uttar Pradesh Real Estate (Regulation and Development) Rules, 2016",
        "year": 2016,
        "source": "UP RERA",
        "url": "https://up-rera.in/pdf/rera.pdf",
    },
    {
        "category": "02_UP_RERA_Rules",
        "title": "U.P. Real Estate (Regulation and Development) (Agreement for Sale/Lease) Rules, 2018",
        "year": 2018,
        "source": "UP RERA",
        "url": "https://up-rera.in/pdf/UPRERA_ATS_2018.pdf",
    },
    {
        "category": "03_UP_RERA_Directions",
        "title": "U.P. Real Estate Project (Maintenance and Operation of Project Bank Accounts) Directions, 2020 — 3rd Revision",
        "year": 2026,
        "source": "UP RERA",
        "url": "https://up-rera.in/pdf/RERAAccountDirections11052026.pdf",
    },
    {
        "category": "04_Official_Guidance",
        "title": "Real Estate (Regulation and Development) Act, 2016 — Frequently Asked Questions",
        "year": 2016,
        "source": "Ministry of Housing and Urban Poverty Alleviation",
        "url": "https://up-rera.in/pdf/faq.pdf",
    },
    {
        "category": "05_UP_RERA_Orders",
        "title": "UP RERA Office Order No. 1125/UP RERA/Karyalaya-Gyapan (T.A.)/2018-19 — CA, Architect and Engineer Certificates",
        "year": 2018,
        "source": "UP RERA",
        "url": "https://up-rera.in/pdf/Order%20for%20CA,Architect,Engineer%20Certificate.pdf",
    },
]

MAX_BYTES = 30 * 1024 * 1024
TIMEOUT = (10, 30)
RETRIES = 3
CHUNK = 1024 * 256

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/153.0 Safari/537.36"
    ),
    "Accept": "application/pdf,*/*;q=0.8",
}


def safe_filename(name: str) -> str:
    name = unquote(name)
    name = re.sub(r"[^\w.\-() ]+", "_", name, flags=re.UNICODE)
    name = re.sub(r"\s+", "_", name).strip("._ ")
    return name or "document.pdf"


def session_with_retries() -> requests.Session:
    s = requests.Session()
    retry = Retry(
        total=RETRIES,
        connect=RETRIES,
        read=RETRIES,
        backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "HEAD"]),
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=8, pool_maxsize=8)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    s.headers.update(HEADERS)
    return s


def is_pdf_prefix(data: bytes) -> bool:
    return data[:5] == b"%PDF-"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def choose_path(category: str, title: str, url: str) -> Path:
    folder = OUT / category
    folder.mkdir(parents=True, exist_ok=True)

    parsed_name = Path(unquote(urlparse(url).path)).name
    if not parsed_name.lower().endswith(".pdf"):
        parsed_name = safe_filename(title) + ".pdf"
    else:
        parsed_name = safe_filename(parsed_name)

    return folder / parsed_name


def download_one(session: requests.Session, doc: dict) -> dict:
    path = choose_path(doc["category"], doc["title"], doc["url"])

    base = {
        **doc,
        "local_path": str(path),
        "status": "",
        "bytes": "",
        "sha256": "",
        "error": "",
    }

    if path.exists():
        try:
            with path.open("rb") as f:
                prefix = f.read(5)
            if is_pdf_prefix(prefix):
                base["status"] = "already_exists"
                base["bytes"] = path.stat().st_size
                base["sha256"] = sha256_file(path)
                return base
            path.unlink()
        except OSError as e:
            base["error"] = f"existing-file-check: {e}"

    tmp = path.with_suffix(path.suffix + ".part")

    try:
        with session.get(doc["url"], timeout=TIMEOUT, stream=True, allow_redirects=True) as r:
            r.raise_for_status()

            content_type = (r.headers.get("Content-Type") or "").lower()
            first = b""
            total = 0

            with tmp.open("wb") as f:
                for chunk in r.iter_content(CHUNK):
                    if not chunk:
                        continue

                    if not first:
                        first = chunk[:16]
                        if not is_pdf_prefix(first):
                            raise ValueError(
                                "response is not a PDF "
                                f"(content-type={content_type!r}, "
                                f"prefix={first[:16]!r})"
                            )

                    total += len(chunk)
                    if total > MAX_BYTES:
                        raise ValueError(f"file exceeds {MAX_BYTES // (1024*1024)} MB limit")

                    f.write(chunk)

            if not is_pdf_prefix(first):
                raise ValueError("missing %PDF- signature")

            if total == 0:
                raise ValueError("empty response")

        os.replace(tmp, path)

        base["status"] = "downloaded"
        base["bytes"] = path.stat().st_size
        base["sha256"] = sha256_file(path)
        return base

    except Exception as e:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass

        base["status"] = "failed"
        base["error"] = str(e)
        return base


def write_index(rows: list[dict]) -> None:
    fields = [
        "category", "title", "year", "source", "url",
        "local_path", "status", "bytes", "sha256", "error"
    ]
    INDEX.parent.mkdir(parents=True, exist_ok=True)
    with INDEX.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def main() -> None:
    for d in DOCUMENTS:
        (OUT / d["category"]).mkdir(parents=True, exist_ok=True)

    session = session_with_retries()
    rows = []

    print("=" * 72)
    print("RERA / UP-RERA CORPUS DOWNLOADER")
    print("=" * 72)
    print(f"Documents in bounded list : {len(DOCUMENTS)}")
    print(f"Output directory          : {OUT.resolve()}")
    print()

    for i, doc in enumerate(DOCUMENTS, 1):
        print(f"[{i}/{len(DOCUMENTS)}] {doc['title']}")
        print(f"        {doc['url']}")

        result = download_one(session, doc)
        rows.append(result)

        if result["status"] == "downloaded":
            print(f"        OK        {result['bytes']} bytes")
        elif result["status"] == "already_exists":
            print(f"        EXISTS    {result['bytes']} bytes")
        else:
            print(f"        FAILED    {result['error']}")

        time.sleep(0.25)

    write_index(rows)

    counts = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1

    print()
    print("=" * 72)
    print("SUMMARY")
    print("=" * 72)
    print(f"PDFs in manifest : {len(DOCUMENTS)}")
    print(f"Downloaded       : {counts.get('downloaded', 0)}")
    print(f"Already existed  : {counts.get('already_exists', 0)}")
    print(f"Failed           : {counts.get('failed', 0)}")
    print(f"Master index     : {INDEX}")
    print()
    print("Only responses beginning with %PDF- are saved.")
    print("Benchmark questions/files are NOT downloaded.")


if __name__ == "__main__":
    main()
