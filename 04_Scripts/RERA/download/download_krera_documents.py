import os
import re
import csv
import time
import hashlib
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

BASE_URL = "https://rera.karnataka.gov.in"

PAGES = {
    "circular": f"{BASE_URL}/circularPage",
    "notification": f"{BASE_URL}/notificationPage",
}

OUTPUT_DIR = "KRERA_Documents"
METADATA_FILE = os.path.join(OUTPUT_DIR, "krera_metadata.csv")

TIMEOUT = 45
MAX_RETRIES = 5
RETRY_DELAY = 5

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/153.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/pdf,*/*",
}


def clean_filename(text):
    text = re.sub(r"[<>:\"/\\|?*]", "_", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip().rstrip(".")[:180]


def unique_filename(title, url):
    title = clean_filename(title)

    # Small hash prevents collisions when titles are similar
    url_hash = hashlib.md5(url.encode()).hexdigest()[:8]

    return f"{title}_{url_hash}.pdf"


def get_page(session, url):
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            print(f"\nFetching page: {url}")
            print(f"Attempt {attempt}/{MAX_RETRIES}")

            response = session.get(
                url,
                headers=HEADERS,
                timeout=TIMEOUT
            )

            response.raise_for_status()

            print(f"SUCCESS - {len(response.content):,} bytes")
            return response.text

        except Exception as e:
            print(f"FAILED: {e}")

            if attempt < MAX_RETRIES:
                print(f"Waiting {RETRY_DELAY}s...")
                time.sleep(RETRY_DELAY)

    return None


def extract_documents(html, page_url, doc_type):
    soup = BeautifulSoup(html, "html.parser")

    documents = []

    for a in soup.find_all("a", href=True):

        href = a.get("href", "").strip()
        title = a.get_text(" ", strip=True)

        if not href:
            continue

        full_url = urljoin(page_url, href)

        # KRERA document endpoints
        is_document = (
            "reraDocument" in full_url
            or href.lower().endswith(".pdf")
        )

        if not is_document:
            continue

        if not title:
            title = f"{doc_type}_document"

        documents.append({
            "type": doc_type,
            "title": title,
            "url": full_url,
            "source_page": page_url,
        })

    return documents


def download_file(session, document, filepath):

    if os.path.exists(filepath):
        print(f"SKIP - already exists: {os.path.basename(filepath)}")
        return "already_exists"

    for attempt in range(1, MAX_RETRIES + 1):

        try:
            print(
                f"\nDownloading:\n"
                f"{document['title']}\n"
                f"Attempt {attempt}/{MAX_RETRIES}"
            )

            response = session.get(
                document["url"],
                headers=HEADERS,
                timeout=TIMEOUT,
                stream=True
            )

            response.raise_for_status()

            content_type = response.headers.get(
                "Content-Type", ""
            ).lower()

            # Download into temporary file first
            temp_file = filepath + ".part"

            with open(temp_file, "wb") as f:

                for chunk in response.iter_content(
                    chunk_size=1024 * 64
                ):
                    if chunk:
                        f.write(chunk)

            # Check that we actually received a PDF
            with open(temp_file, "rb") as f:
                first_bytes = f.read(5)

            if first_bytes != b"%PDF-":
                os.remove(temp_file)

                print(
                    "FAILED - response was not a PDF "
                    f"(Content-Type: {content_type})"
                )

                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_DELAY)

                continue

            os.replace(temp_file, filepath)

            size = os.path.getsize(filepath)

            print(
                f"SUCCESS - {size:,} bytes"
            )

            return "downloaded"

        except Exception as e:

            print(f"FAILED: {e}")

            temp_file = filepath + ".part"

            if os.path.exists(temp_file):
                os.remove(temp_file)

            if attempt < MAX_RETRIES:
                print(f"Waiting {RETRY_DELAY}s...")
                time.sleep(RETRY_DELAY)

    return "failed"


def main():

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    session = requests.Session()

    all_documents = []

    # -------------------------------------------------
    # STEP 1: Scrape both official pages
    # -------------------------------------------------

    for doc_type, page_url in PAGES.items():

        html = get_page(session, page_url)

        if html is None:
            print(
                f"\nCould not access {doc_type} page."
            )
            continue

        docs = extract_documents(
            html,
            page_url,
            doc_type
        )

        print(
            f"Found {len(docs)} {doc_type} documents."
        )

        all_documents.extend(docs)

    # -------------------------------------------------
    # STEP 2: Remove duplicates
    # -------------------------------------------------

    unique = {}

    for doc in all_documents:
        unique[doc["url"]] = doc

    documents = list(unique.values())

    print("\n" + "=" * 70)
    print(f"TOTAL UNIQUE DOCUMENTS FOUND: {len(documents)}")
    print("=" * 70)

    # -------------------------------------------------
    # STEP 3: Download documents
    # -------------------------------------------------

    metadata = []

    for index, doc in enumerate(documents, start=1):

        filename = unique_filename(
            doc["title"],
            doc["url"]
        )

        filepath = os.path.join(
            OUTPUT_DIR,
            filename
        )

        print(
            f"\n[{index}/{len(documents)}]"
        )

        status = download_file(
            session,
            doc,
            filepath
        )

        metadata.append({
            "type": doc["type"],
            "title": doc["title"],
            "url": doc["url"],
            "source_page": doc["source_page"],
            "filename": filename,
            "status": status,
            "filepath": filepath,
        })

        # Small delay to avoid hammering the server
        time.sleep(2)

    # -------------------------------------------------
    # STEP 4: Save metadata
    # -------------------------------------------------

    with open(
        METADATA_FILE,
        "w",
        newline="",
        encoding="utf-8-sig"
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=[
                "type",
                "title",
                "url",
                "source_page",
                "filename",
                "status",
                "filepath",
            ]
        )

        writer.writeheader()
        writer.writerows(metadata)

    # -------------------------------------------------
    # SUMMARY
    # -------------------------------------------------

    downloaded = sum(
        x["status"] == "downloaded"
        for x in metadata
    )

    existing = sum(
        x["status"] == "already_exists"
        for x in metadata
    )

    failed = sum(
        x["status"] == "failed"
        for x in metadata
    )

    print("\n" + "=" * 70)
    print("DOWNLOAD COMPLETE")
    print("=" * 70)

    print(f"Documents found : {len(documents)}")
    print(f"Downloaded       : {downloaded}")
    print(f"Already existed  : {existing}")
    print(f"Failed           : {failed}")

    print(
        f"\nDocuments saved in:\n"
        f"{os.path.abspath(OUTPUT_DIR)}"
    )

    print(
        f"\nMetadata saved in:\n"
        f"{os.path.abspath(METADATA_FILE)}"
    )


if __name__ == "__main__":
    main()