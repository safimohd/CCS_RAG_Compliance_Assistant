import os
import re
import csv
import time
import hashlib
import requests
from urllib.parse import urljoin, urlparse, unquote
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed

# ============================================================
# ABDM DOCUMENT DOWNLOADER
# ============================================================

ROOT = "Healthcare_RAG_Raw"
OUT_DIR = os.path.join(ROOT, "03_ABDM")
INDEX_FILE = os.path.join(OUT_DIR, "abdm_master_index.csv")

os.makedirs(OUT_DIR, exist_ok=True)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/153.0 Safari/537.36"
    )
}

# ------------------------------------------------------------
# Official ABDM pages to crawl
# ------------------------------------------------------------

START_URLS = [
    "https://abdm.gov.in/",
    "https://abdm.gov.in/DHIS/recipient-list",
    "https://abdm.gov.in/UHI",
    "https://abdm.gov.in/hackathon",
    "https://abdm.gov.in/about",
]

# Known official ABDM PDFs discovered during research.
# These are added explicitly because some ABDM pages are
# JavaScript-heavy and don't expose every PDF through HTML.
KNOWN_PDFS = [
    "https://abdm.gov.in/static/media/health_management_policy_bac9429a79.80f74bc3e039c00acd4f.pdf",
    "https://abdm.gov.in/static/media/New_Privacy_Policy.3833de7c114b64627a9d.pdf",
    "https://abdm.gov.in/static/media/OperationalGuidelinesDHIS.a35651cdf843e0b399c2.pdf",
]

# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------

def normalise_url(url):
    url = url.strip()
    url = url.split("#")[0]
    return url


def is_abdm_url(url):
    host = urlparse(url).netloc.lower()
    return host.endswith("abdm.gov.in")


def is_pdf_url(url):
    path = urlparse(url).path.lower()
    return path.endswith(".pdf") or ".pdf?" in url.lower()


def safe_filename(url):
    parsed = urlparse(url)
    raw_name = unquote(os.path.basename(parsed.path))

    if not raw_name.lower().endswith(".pdf"):
        raw_name += ".pdf"

    # Clean weird names
    raw_name = re.sub(r'[<>:"/\\|?*]', "_", raw_name)
    raw_name = re.sub(r"\s+", " ", raw_name).strip()

    # Hash prevents collisions such as Download.pdf
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:10]

    base, ext = os.path.splitext(raw_name)

    # Keep filename manageable
    base = base[:150]

    return f"{base}_{digest}{ext}"


def get_page(url):
    try:
        r = requests.get(
            url,
            headers=HEADERS,
            timeout=30,
            allow_redirects=True
        )
        r.raise_for_status()
        return r.text
    except Exception as e:
        print(f"[PAGE FAILED] {url} -> {e}")
        return None


# ------------------------------------------------------------
# Crawl pages for PDF links
# ------------------------------------------------------------

def extract_pdf_links(page_url, html):
    soup = BeautifulSoup(html, "html.parser")
    found = set()

    for tag in soup.find_all(["a", "iframe", "embed", "object", "source"]):
        for attr in ["href", "src", "data"]:
            value = tag.get(attr)

            if not value:
                continue

            full = urljoin(page_url, value)
            full = normalise_url(full)

            if is_pdf_url(full) and is_abdm_url(full):
                found.add(full)

    # Also search raw HTML for PDF URLs
    raw_matches = re.findall(
        r'https?://[^"\'>\s]+?\.pdf(?:\?[^"\'>\s]*)?',
        html,
        flags=re.IGNORECASE
    )

    for url in raw_matches:
        url = normalise_url(url)

        if is_abdm_url(url):
            found.add(url)

    return found


# ------------------------------------------------------------
# Download one PDF
# ------------------------------------------------------------

def download_pdf(url):
    filename = safe_filename(url)
    filepath = os.path.join(OUT_DIR, filename)

    if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
        return {
            "url": url,
            "filename": filename,
            "status": "already_exists",
            "size": os.path.getsize(filepath),
        }

    for attempt in range(1, 4):

        try:
            r = requests.get(
                url,
                headers=HEADERS,
                timeout=60,
                allow_redirects=True
            )

            r.raise_for_status()

            content = r.content

            # Basic validation
            if not content.startswith(b"%PDF"):
                return {
                    "url": url,
                    "filename": filename,
                    "status": "not_pdf",
                    "size": len(content),
                }

            with open(filepath, "wb") as f:
                f.write(content)

            return {
                "url": url,
                "filename": filename,
                "status": "downloaded",
                "size": len(content),
            }

        except Exception as e:

            if attempt < 3:
                time.sleep(2 * attempt)

            else:
                return {
                    "url": url,
                    "filename": filename,
                    "status": f"failed: {e}",
                    "size": 0,
                }


# ============================================================
# MAIN
# ============================================================

print("=" * 70)
print("ABDM DOCUMENT DOWNLOADER")
print("=" * 70)

all_pdf_urls = set(KNOWN_PDFS)

print("\n[1/4] Crawling official ABDM pages...\n")

for page_url in START_URLS:

    print(f"Scanning: {page_url}")

    html = get_page(page_url)

    if not html:
        continue

    pdfs = extract_pdf_links(page_url, html)

    print(f"  PDFs found: {len(pdfs)}")

    all_pdf_urls.update(pdfs)

print("\n[2/4] PDF discovery complete")
print(f"TOTAL UNIQUE PDFS FOUND: {len(all_pdf_urls)}")

# ------------------------------------------------------------
# Download
# ------------------------------------------------------------

print("\n[3/4] Downloading PDFs...\n")

results = []

with ThreadPoolExecutor(max_workers=6) as executor:

    futures = {
        executor.submit(download_pdf, url): url
        for url in sorted(all_pdf_urls)
    }

    for future in as_completed(futures):

        result = future.result()
        results.append(result)

        status = result["status"]

        if status == "downloaded":
            print(
                f"[OK] {result['filename']} "
                f"({result['size']:,} bytes)"
            )

        elif status == "already_exists":
            print(f"[EXISTS] {result['filename']}")

        else:
            print(
                f"[FAILED] {result['url']} -> {status}"
            )


# ------------------------------------------------------------
# Master index
# ------------------------------------------------------------

print("\n[4/4] Writing master index...")

results.sort(key=lambda x: x["url"])

with open(
    INDEX_FILE,
    "w",
    newline="",
    encoding="utf-8"
) as f:

    writer = csv.DictWriter(
        f,
        fieldnames=[
            "url",
            "filename",
            "status",
            "size"
        ]
    )

    writer.writeheader()
    writer.writerows(results)


# ------------------------------------------------------------
# Summary
# ------------------------------------------------------------

downloaded = sum(
    1 for r in results
    if r["status"] == "downloaded"
)

existing = sum(
    1 for r in results
    if r["status"] == "already_exists"
)

failed = len(results) - downloaded - existing

print("\n" + "=" * 70)
print("ABDM DOWNLOAD SUMMARY")
print("=" * 70)

print(f"Unique PDFs found : {len(all_pdf_urls)}")
print(f"Downloaded        : {downloaded}")
print(f"Already existed   : {existing}")
print(f"Failed/invalid    : {failed}")

print(f"\nOutput folder:")
print(OUT_DIR)

print("\nMaster index:")
print(INDEX_FILE)

print("=" * 70)