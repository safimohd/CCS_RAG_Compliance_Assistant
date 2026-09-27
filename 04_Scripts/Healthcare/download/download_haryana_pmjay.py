import os
import re
import csv
import time
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from concurrent.futures import ThreadPoolExecutor, as_completed

PAGE_URL = "https://ayushmanbharat.haryana.gov.in/policy-guidelines/"
OUTPUT_DIR = "Haryana_PMJAY_Policy_Guidelines"
INDEX_FILE = os.path.join(OUTPUT_DIR, "document_index.csv")

os.makedirs(OUTPUT_DIR, exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}


def clean_filename(name):
    name = re.sub(r'\(PDF.*?\)', '', name, flags=re.I)
    name = re.sub(r'\(pdf.*?\)', '', name, flags=re.I)
    name = re.sub(r'\s+', ' ', name).strip()
    name = re.sub(r'[<>:"/\\|?*]', '_', name)

    if not name.lower().endswith(".pdf"):
        name += ".pdf"

    return name


def get_links():
    response = requests.get(
        PAGE_URL,
        headers=HEADERS,
        timeout=30
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    documents = []
    seen_urls = set()

    for a in soup.find_all("a", href=True):

        url = urljoin(PAGE_URL, a["href"])

        # Only PDFs
        if ".pdf" not in url.lower():
            continue

        if url in seen_urls:
            continue

        seen_urls.add(url)

        title = a.get_text(" ", strip=True)

        if not title:
            title = url.split("/")[-1]

        filename = clean_filename(title)

        documents.append({
            "title": title,
            "url": url,
            "filename": filename
        })

    return documents


def download_document(doc):
    filepath = os.path.join(OUTPUT_DIR, doc["filename"])

    # Don't download again if already present
    if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
        return doc, "Already exists"

    for attempt in range(3):

        try:
            response = requests.get(
                doc["url"],
                headers=HEADERS,
                timeout=60
            )

            response.raise_for_status()

            with open(filepath, "wb") as f:
                f.write(response.content)

            return doc, "Downloaded"

        except Exception as e:

            if attempt == 2:
                return doc, f"FAILED: {e}"

            time.sleep(2)


def main():

    print("Reading Haryana Ayushman Bharat page...")

    documents = get_links()

    print(f"\nFound {len(documents)} unique PDF files.")
    print("Starting downloads...\n")

    results = []

    with ThreadPoolExecutor(max_workers=5) as executor:

        futures = [
            executor.submit(download_document, doc)
            for doc in documents
        ]

        for i, future in enumerate(as_completed(futures), 1):

            doc, status = future.result()

            print(
                f"[{i}/{len(documents)}] "
                f"{status}: {doc['filename']}"
            )

            results.append({
                "title": doc["title"],
                "url": doc["url"],
                "filename": doc["filename"],
                "status": status
            })

    # Save index
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
                "status"
            ]
        )

        writer.writeheader()
        writer.writerows(results)

    successful = sum(
        1 for r in results
        if r["status"] in ["Downloaded", "Already exists"]
    )

    failed = len(results) - successful

    print("\n--------------------------------")
    print("DOWNLOAD COMPLETE")
    print("--------------------------------")
    print(f"Total PDFs found : {len(documents)}")
    print(f"Successful       : {successful}")
    print(f"Failed           : {failed}")
    print(f"\nFolder: {OUTPUT_DIR}")
    print(f"Index : {INDEX_FILE}")


if __name__ == "__main__":
    main()