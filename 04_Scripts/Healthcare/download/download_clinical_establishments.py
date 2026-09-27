import os
import re
import csv
import time
import hashlib
import requests

from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed


# ============================================================
# CONFIGURATION
# ============================================================

BASE_URL = "https://clinicalestablishments.mohfw.gov.in"

PAGES = [
    "/notification",
    "/en/allopathic",
    "/en/ayush",
    "/en/standard-treatment-guidelines",
]

OUTPUT_DIR = "Healthcare_Clinical_Establishments"
INDEX_FILE = os.path.join(
    OUTPUT_DIR,
    "clinical_establishments_index.csv"
)

MAX_WORKERS = 6

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/153.0 Safari/537.36"
    )
}


# ============================================================
# HELPERS
# ============================================================

def clean_filename(name):
    """
    Convert webpage title into a safe Windows filename.
    """

    name = re.sub(r"\s+", " ", name).strip()

    # Remove common PDF labels
    name = re.sub(
        r"\(.*?PDF.*?\)",
        "",
        name,
        flags=re.IGNORECASE
    )

    # Windows-invalid characters
    name = re.sub(
        r'[<>:"/\\|?*]',
        "_",
        name
    )

    # Remove trailing dots/spaces
    name = name.strip(" .")

    if not name:
        name = "document"

    return name


def unique_filename(title, url):
    """
    Ensure two documents called 'Download.pdf'
    don't overwrite each other.
    """

    title = clean_filename(title)

    if not title.lower().endswith(".pdf"):
        title += ".pdf"

    # Short hash from URL guarantees uniqueness
    url_hash = hashlib.md5(
        url.encode("utf-8")
    ).hexdigest()[:8]

    base, ext = os.path.splitext(title)

    return f"{base}_{url_hash}{ext}"


def get_page(url):
    """
    Download webpage HTML.
    """

    response = requests.get(
        url,
        headers=HEADERS,
        timeout=45
    )

    response.raise_for_status()

    return response.text


# ============================================================
# FIND PDF LINKS
# ============================================================

def extract_pdf_links(page_url):
    """
    Extract every PDF link from a webpage.
    """

    print(f"Scanning: {page_url}")

    try:
        html = get_page(page_url)
    except Exception as e:
        print(f"ERROR scanning {page_url}: {e}")
        return []

    soup = BeautifulSoup(html, "html.parser")

    documents = []

    for a in soup.find_all("a", href=True):

        href = a["href"].strip()

        if not href:
            continue

        full_url = urljoin(
            page_url,
            href
        )

        # Only PDF documents
        if ".pdf" not in full_url.lower():
            continue

        title = a.get_text(
            " ",
            strip=True
        )

        if not title:
            title = os.path.basename(
                urlparse(full_url).path
            )

        filename = unique_filename(
            title,
            full_url
        )

        documents.append({
            "title": title,
            "url": full_url,
            "filename": filename,
            "source_page": page_url
        })

    return documents


# ============================================================
# DOWNLOAD
# ============================================================

def download_document(doc):

    filepath = os.path.join(
        OUTPUT_DIR,
        doc["filename"]
    )

    # Already downloaded
    if (
        os.path.exists(filepath)
        and os.path.getsize(filepath) > 0
    ):
        return {
            **doc,
            "status": "Already exists"
        }

    for attempt in range(3):

        try:

            response = requests.get(
                doc["url"],
                headers=HEADERS,
                timeout=90
            )

            response.raise_for_status()

            # Make sure we actually received something
            if len(response.content) < 1000:
                raise ValueError(
                    "Downloaded file is suspiciously small"
                )

            with open(
                filepath,
                "wb"
            ) as f:

                f.write(response.content)

            return {
                **doc,
                "status": "Downloaded"
            }

        except Exception as e:

            if attempt == 2:

                return {
                    **doc,
                    "status": f"FAILED: {e}"
                }

            time.sleep(2)


# ============================================================
# MAIN
# ============================================================

def main():

    os.makedirs(
        OUTPUT_DIR,
        exist_ok=True
    )

    print("=" * 70)
    print("CLINICAL ESTABLISHMENTS BULK DOWNLOADER")
    print("=" * 70)

    all_documents = []

    # --------------------------------------------------------
    # Scan all configured pages
    # --------------------------------------------------------

    for page in PAGES:

        page_url = urljoin(
            BASE_URL,
            page
        )

        documents = extract_pdf_links(
            page_url
        )

        all_documents.extend(
            documents
        )

    # --------------------------------------------------------
    # Remove duplicate URLs
    # --------------------------------------------------------

    unique_documents = {}
    
    for doc in all_documents:

        unique_documents[
            doc["url"]
        ] = doc

    documents = list(
        unique_documents.values()
    )

    print()
    print(
        f"Unique PDF documents found: "
        f"{len(documents)}"
    )

    if not documents:
        print(
            "\nNo PDF documents found."
        )
        return

    print()
    print("Starting downloads...")
    print()

    results = []

    # --------------------------------------------------------
    # Parallel downloads
    # --------------------------------------------------------

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:

        futures = [
            executor.submit(
                download_document,
                doc
            )
            for doc in documents
        ]

        for i, future in enumerate(
            as_completed(futures),
            start=1
        ):

            result = future.result()

            results.append(result)

            print(
                f"[{i}/{len(documents)}] "
                f"{result['status']} - "
                f"{result['filename']}"
            )

    # --------------------------------------------------------
    # Save metadata index
    # --------------------------------------------------------

    with open(
        INDEX_FILE,
        "w",
        newline="",
        encoding="utf-8"
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=[
                "title",
                "url",
                "filename",
                "source_page",
                "status"
            ]
        )

        writer.writeheader()

        for result in results:
            writer.writerow(result)

    # --------------------------------------------------------
    # Summary
    # --------------------------------------------------------

    downloaded = sum(
        r["status"] == "Downloaded"
        for r in results
    )

    existing = sum(
        r["status"] == "Already exists"
        for r in results
    )

    failed = sum(
        r["status"].startswith("FAILED")
        for r in results
    )

    print()
    print("=" * 70)
    print("DOWNLOAD COMPLETE")
    print("=" * 70)

    print(
        f"PDFs found      : {len(documents)}"
    )

    print(
        f"Downloaded      : {downloaded}"
    )

    print(
        f"Already existed : {existing}"
    )

    print(
        f"Failed          : {failed}"
    )

    print()
    print(
        f"Documents saved in:"
    )

    print(
        f"  {OUTPUT_DIR}"
    )

    print()
    print(
        f"Metadata index:"
    )

    print(
        f"  {INDEX_FILE}"
    )


if __name__ == "__main__":
    main()