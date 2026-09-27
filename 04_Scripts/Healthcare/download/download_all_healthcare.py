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
# MASTER HEALTHCARE RAG DOWNLOADER
# ============================================================

ROOT_DIR = "Healthcare_RAG_Raw"
INDEX_FILE = os.path.join(ROOT_DIR, "healthcare_master_index.csv")

MAX_WORKERS = 6

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/153.0 Safari/537.36"
    )
}


# ============================================================
# SOURCE CONFIGURATION
#
# We crawl official pages and collect PDF links.
# Existing downloads are skipped.
# ============================================================

SOURCES = [

    # --------------------------------------------------------
    # 01 - PM-JAY / NHA / AYUSHMAN BHARAT
    # --------------------------------------------------------

    {
        "name": "PMJAY_Haryana",
        "authority": "NHA / Haryana Ayushman Bharat",
        "folder": "01_PMJAY_NHA",
        "pages": [
            "https://ayushmanbharat.haryana.gov.in/policy-guidelines/"
        ]
    },


    # --------------------------------------------------------
    # 02 - CLINICAL ESTABLISHMENTS
    # --------------------------------------------------------

    {
        "name": "Clinical_Establishments",
        "authority": "Ministry of Health and Family Welfare",
        "folder": "02_Clinical_Establishments",
        "pages": [
            "https://clinicalestablishments.mohfw.gov.in/notification",
            "https://clinicalestablishments.mohfw.gov.in/en/allopathic",
            "https://clinicalestablishments.mohfw.gov.in/en/ayush",
            "https://clinicalestablishments.mohfw.gov.in/en/standard-treatment-guidelines"
        ]
    },


    # --------------------------------------------------------
    # 03 - ABDM
    # --------------------------------------------------------

    {
        "name": "ABDM",
        "authority": "National Health Authority",
        "folder": "03_ABDM",
        "pages": [
            "https://abdm.gov.in/publications/policies_regulations/health_data_management_policy",
            "https://abdm.gov.in/"
        ]
    },


    # --------------------------------------------------------
    # 04 - NMC
    # --------------------------------------------------------

    {
        "name": "NMC",
        "authority": "National Medical Commission",
        "folder": "04_NMC",
        "pages": [
            "https://nmc.org.in/page/rules-regulations-rules-regulations-nmc",
            "https://nmc.org.in/page/rules-regulations-rules-regulations-of-erstwhile-mci-code-of-medical-ethics-regulations-2002"
        ]
    },


    # --------------------------------------------------------
    # 05 - TELEMEDICINE
    # --------------------------------------------------------

    {
        "name": "Telemedicine",
        "authority": "Ministry of Health and Family Welfare",
        "folder": "05_Telemedicine",
        "pages": [
            "https://esanjeevani.mohfw.gov.in/assets/guidelines/Telemedicine_Practice_Guidelines.pdf"
        ]
    },


    # --------------------------------------------------------
    # 06 - DATA PRIVACY
    # --------------------------------------------------------

    {
        "name": "DPDP",
        "authority": "Ministry of Electronics and Information Technology",
        "folder": "06_Data_Privacy",
        "pages": [
            "https://www.meity.gov.in/documents/act-and-policies/digital-personal-data-protection-rules-2025-gDOxUjMtQWa?pageTitle=Digital-Personal-Data-Protection-Rules-2025",
            "https://www.meity.gov.in/documents/act-and-policies?page=1&persona=Researcher"
        ]
    },


    # --------------------------------------------------------
    # 07 - AYUSHMAN AROGYA MANDIR / MOHFW
    # --------------------------------------------------------

    {
        "name": "Ayushman_Arogya_Mandir",
        "authority": "Ministry of Health and Family Welfare",
        "folder": "07_MoHFW_Primary_Healthcare",
        "pages": [
            "https://aam.mohfw.gov.in/document/6"
        ]
    }

]


# ============================================================
# FILENAME HELPERS
# ============================================================

def clean_filename(name):

    name = re.sub(r"\s+", " ", name).strip()

    name = re.sub(
        r"\(.*?PDF.*?\)",
        "",
        name,
        flags=re.IGNORECASE
    )

    name = re.sub(
        r'[<>:"/\\|?*]',
        "_",
        name
    )

    name = name.strip(" .")

    if not name:
        name = "document"

    return name


def unique_filename(title, url):

    title = clean_filename(title)

    if not title.lower().endswith(".pdf"):
        title += ".pdf"

    url_hash = hashlib.md5(
        url.encode("utf-8")
    ).hexdigest()[:8]

    base, ext = os.path.splitext(title)

    return f"{base}_{url_hash}{ext}"


# ============================================================
# WEB REQUEST
# ============================================================

def fetch(url):

    response = requests.get(
        url,
        headers=HEADERS,
        timeout=60
    )

    response.raise_for_status()

    return response


# ============================================================
# PDF DETECTION
# ============================================================

def is_pdf_url(url):

    return (
        ".pdf" in url.lower()
        or
        "pdf" in urlparse(url).path.lower()
    )


# ============================================================
# EXTRACT PDF LINKS
# ============================================================

def extract_documents(
    page_url,
    source_name,
    authority,
    folder
):

    print(f"Scanning: {page_url}")

    documents = []

    try:

        response = fetch(page_url)

    except Exception as e:

        print(
            f"  ERROR: Could not access page: {e}"
        )

        return []

    # --------------------------------------------------------
    # Direct PDF page
    # --------------------------------------------------------

    if is_pdf_url(page_url):

        filename = unique_filename(
            os.path.basename(
                urlparse(page_url).path
            ),
            page_url
        )

        documents.append({
            "source": source_name,
            "authority": authority,
            "folder": folder,
            "title": os.path.basename(
                urlparse(page_url).path
            ),
            "url": page_url,
            "filename": filename,
            "source_page": page_url
        })

        return documents

    # --------------------------------------------------------
    # HTML page
    # --------------------------------------------------------

    soup = BeautifulSoup(
        response.text,
        "html.parser"
    )

    for a in soup.find_all(
        "a",
        href=True
    ):

        href = a["href"].strip()

        if not href:
            continue

        full_url = urljoin(
            page_url,
            href
        )

        if not is_pdf_url(full_url):
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
            "source": source_name,
            "authority": authority,
            "folder": folder,
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

    folder = os.path.join(
        ROOT_DIR,
        doc["folder"]
    )

    os.makedirs(
        folder,
        exist_ok=True
    )

    filepath = os.path.join(
        folder,
        doc["filename"]
    )

    # --------------------------------------------------------
    # Skip existing
    # --------------------------------------------------------

    if (
        os.path.exists(filepath)
        and os.path.getsize(filepath) > 0
    ):

        return {
            **doc,
            "local_file": filepath,
            "status": "Already exists"
        }


    # --------------------------------------------------------
    # Download with retry
    # --------------------------------------------------------

    for attempt in range(3):

        try:

            response = requests.get(
                doc["url"],
                headers=HEADERS,
                timeout=90
            )

            response.raise_for_status()

            if len(response.content) < 500:

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
                "local_file": filepath,
                "status": "Downloaded"
            }

        except Exception as e:

            if attempt == 2:

                return {
                    **doc,
                    "local_file": filepath,
                    "status": f"FAILED: {e}"
                }

            time.sleep(3)


# ============================================================
# MAIN
# ============================================================

def main():

    os.makedirs(
        ROOT_DIR,
        exist_ok=True
    )

    print()
    print("=" * 75)
    print("INDIA HEALTHCARE REGULATORY RAG")
    print("MASTER DOCUMENT DOWNLOADER")
    print("=" * 75)
    print()

    all_documents = []


    # ========================================================
    # STEP 1 — SCAN SOURCES
    # ========================================================

    for source in SOURCES:

        print()
        print(
            f"========== {source['name']} =========="
        )

        for page in source["pages"]:

            docs = extract_documents(
                page,
                source["name"],
                source["authority"],
                source["folder"]
            )

            all_documents.extend(docs)


    # ========================================================
    # STEP 2 — REMOVE DUPLICATES
    # ========================================================

    unique = {}

    for doc in all_documents:

        unique[doc["url"]] = doc

    documents = list(
        unique.values()
    )


    print()
    print("=" * 75)
    print(
        f"TOTAL UNIQUE PDFS FOUND: {len(documents)}"
    )
    print("=" * 75)
    print()


    # ========================================================
    # STEP 3 — DOWNLOAD
    # ========================================================

    results = []

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
                f"{result['status']} | "
                f"{result['source']} | "
                f"{result['filename']}"
            )


    # ========================================================
    # STEP 4 — SAVE MASTER INDEX
    # ========================================================

    fields = [
        "source",
        "authority",
        "folder",
        "title",
        "url",
        "filename",
        "local_file",
        "source_page",
        "status"
    ]

    with open(
        INDEX_FILE,
        "w",
        newline="",
        encoding="utf-8"
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=fields
        )

        writer.writeheader()

        for result in results:

            writer.writerow({
                field: result.get(
                    field,
                    ""
                )
                for field in fields
            })


    # ========================================================
    # STEP 5 — SUMMARY
    # ========================================================

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
    print("=" * 75)
    print("MASTER DOWNLOAD COMPLETE")
    print("=" * 75)

    print(
        f"Total PDFs found : {len(documents)}"
    )

    print(
        f"Downloaded       : {downloaded}"
    )

    print(
        f"Already existed  : {existing}"
    )

    print(
        f"Failed           : {failed}"
    )

    print()
    print(
        f"Root folder:"
    )

    print(
        f"  {ROOT_DIR}"
    )

    print()
    print(
        f"Master index:"
    )

    print(
        f"  {INDEX_FILE}"
    )

    print()


if __name__ == "__main__":
    main()