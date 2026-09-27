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
# INDIA HEALTHCARE REGULATORY RAG
# REMAINING DOCUMENT DOWNLOADER
# ============================================================

ROOT = "Healthcare_RAG_Remaining"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/153.0.0.0 Safari/537.36"
    )
}

TIMEOUT = 45
MAX_WORKERS = 8
RETRIES = 3

os.makedirs(ROOT, exist_ok=True)

# ============================================================
# 1. PAGE SOURCES
# ============================================================

PAGE_SOURCES = {

    # ----------------------------
    # Clinical Establishments
    # ----------------------------
    "08_CEA_Remaining": [
        "https://clinicalestablishments.mohfw.gov.in/notification",
        "https://clinicalestablishments.mohfw.gov.in/en/national-council---members",
    ],

    # ----------------------------
    # ABDM
    # ----------------------------
    "09_ABDM": [
        "https://abdm.gov.in/",
        "https://abdm.gov.in/publications/policies_regulations/health_data_management_policy",
    ],

    # ----------------------------
    # DPDP
    # ----------------------------
    "10_DPDP": [
        "https://www.meity.gov.in/documents/act-and-policies/digital-personal-data-protection-rules-2025-gDOxUjMtQWa?pageTitle=Digital-Personal-Data-Protection-Rules-2025",
        "https://www.meity.gov.in/documents/act-and-policies?page=1&persona=Researcher",
    ],

    # ----------------------------
    # NMC
    # ----------------------------
    "11_NMC_2002_2026": [
        "https://nmc.org.in/page/rules-regulations-rules-regulations-of-erstwhile-mci-code-of-medical-ethics-regulations-2002",
        "https://nmc.org.in/whats-new",
        "https://nmc.org.in/whats-new?type=news",
        "https://nmc.org.in/information-desk/all-news/",
    ],

    # ----------------------------
    # HIV / AIDS
    # ----------------------------
    "12_HIV_AIDS": [
        "https://naco.mohfw.gov.in/hiv-aids-p-c-act-2017/",
    ],

    # ----------------------------
    # Atomic Energy / AERB
    # ----------------------------
    "13_AERB": [
        "https://www.aerb.gov.in/english/acts-regulations/rules",
        "https://dae.gov.in/acts-rules/page/3/",
    ],

    # ----------------------------
    # Biomedical Waste
    # ----------------------------
    "14_BIOMEDICAL_WASTE": [
        "https://www.indiacode.nic.in/handle/123456789/18574",
    ],
}


# ============================================================
# 2. DIRECT DOCUMENTS
# ============================================================

DIRECT_DOCUMENTS = {

    # --------------------------------------------------------
    # NMC — Code of Medical Ethics 2002
    # --------------------------------------------------------
    "11_NMC_2002_2026": [
        (
            "NMC_Code_of_Medical_Ethics_2002.pdf",
            "https://www.nmc.org.in/wp-content/uploads/2017/10/Ethics-Regulations-2002.pdf"
        ),
    ],

    # --------------------------------------------------------
    # Mental Healthcare Act
    # --------------------------------------------------------
    "15_HEALTHCARE_ACTS": [
        (
            "Mental_Healthcare_Act_2017.pdf",
            "https://www.mohfw.gov.in/sites/default/files/Mental%20Healthcare%20Act%2C%202017_0.pdf"
        ),

        # PCPNDT
        (
            "PCPNDT_Act_1994.pdf",
            "https://www.indiacode.nic.in/bitstream/123456789/1937/1/199457.pdf"
        ),

        # MTP
        (
            "MTP_Act_1971.pdf",
            "https://www.indiacode.nic.in/bitstream/123456789/1593/1/197134.pdf"
        ),

        # NCAHP
        (
            "NCAHP_Act_2021.pdf",
            "https://www.indiacode.nic.in/bitstream/123456789/16824/1/a2021-14.pdf"
        ),
    ],

    # --------------------------------------------------------
    # ART / Surrogacy / THOTA
    # --------------------------------------------------------
    "16_REPRODUCTIVE_TRANSPLANT": [
        (
            "ART_Regulation_Act_2021.pdf",
            "https://www.indiacode.nic.in/indiacode/handle/123456789/17031?view_type=browse"
        ),

        (
            "Surrogacy_Regulation_Act_2021.pdf",
            "https://www.indiacode.nic.in/indiacode/handle/123456789/17046?view_type=browse"
        ),

        (
            "THOTA_1994_IndiaCode.pdf",
            "https://www.indiacode.nic.in/indiacode/handle/123456789/1962?view_type=browse"
        ),
    ],

    # --------------------------------------------------------
    # DPDP Act
    # --------------------------------------------------------
    "10_DPDP": [
        (
            "Digital_Personal_Data_Protection_Act_2023_IndiaCode.pdf",
            "https://www.indiacode.nic.in/indiacode/handle/123456789/22037?view_type=browse"
        ),
    ],

    # --------------------------------------------------------
    # Biomedical Waste — known official India Code amendment
    # --------------------------------------------------------
    "14_BIOMEDICAL_WASTE": [
        (
            "Biomedical_Waste_Management_Amendment_Rules_2018.pdf",
            "https://upload.indiacode.nic.in/showfile?actid=AC_CEN_16_18_00011_198629_1517807327582&filename=Bio+medical+waste+management+%28amendment%29183847.pdf&type=rule"
        ),
    ],

    # --------------------------------------------------------
    # AERB
    # --------------------------------------------------------
    "13_AERB": [
        (
            "Atomic_Energy_Radiation_Protection_Rules_2004.pdf",
            "https://dae.gov.in/wp-content/uploads/2023/06/Atomic-Energy-Radiation-Protection-Rules-2004.pdf"
        ),

        (
            "AERB_Medical_Diagnostic_Xray_Safety_Code.pdf",
            "https://www.aerb.gov.in/storage/uploads/documents/regdocMS24h.pdf"
        ),
    ],
}


# ============================================================
# KEYWORD FILTERS FOR NMC CURRENT DOCUMENTS
# ============================================================

NMC_KEYWORDS = [
    "regulation",
    "regulations",
    "guideline",
    "guidelines",
    "notification",
    "advisory",
    "registration",
    "licence",
    "license",
    "professional conduct",
    "ethics",
    "medical practitioner",
    "medical institution",
    "patient safety",
    "appeal",
    "assessment",
    "recognition",
    "medical education",
    "mbbs",
    "post graduate",
    "under graduate",
    "pwbd",
    "faculty",
]


# ============================================================
# UTILITY FUNCTIONS
# ============================================================

def clean_filename(name):
    name = unquote(name)
    name = re.sub(r"\s+", " ", name)
    name = re.sub(r'[<>:"/\\|?*]', "_", name)
    name = name.strip(" ._")
    return name[:180] if name else "document"


def url_hash(url):
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:8]


def get_page(url):
    for attempt in range(1, RETRIES + 1):
        try:
            r = requests.get(
                url,
                headers=HEADERS,
                timeout=TIMEOUT,
                verify=True
            )
            r.raise_for_status()
            return r.text
        except Exception as e:
            print(f"  Page attempt {attempt} failed: {url} -> {e}")
            time.sleep(2)

    return None


def get_filename_from_url(url, fallback="document"):
    path = urlparse(url).path
    name = os.path.basename(path)

    if not name or "." not in name:
        name = fallback

    name = clean_filename(name)

    if not name.lower().endswith(".pdf"):
        name += ".pdf"

    return name


def is_pdf_url(url):
    lower = url.lower()
    return (
        ".pdf" in lower
        or "showfile" in lower
        or "getdocument" in lower
        or "download" in lower
    )


# ============================================================
# LINK EXTRACTION
# ============================================================

def extract_links(page_url, html):

    soup = BeautifulSoup(html, "html.parser")
    links = []

    for a in soup.find_all("a", href=True):

        href = a.get("href", "").strip()

        if not href:
            continue

        absolute = urljoin(page_url, href)

        if not absolute.startswith(("http://", "https://")):
            continue

        text = " ".join(a.stripped_strings)

        links.append({
            "url": absolute,
            "text": text,
        })

    return links


def collect_page_links():

    all_links = []

    for category, urls in PAGE_SOURCES.items():

        print("\n" + "=" * 70)
        print(f"SCANNING {category}")
        print("=" * 70)

        for page_url in urls:

            print(f"\nScanning: {page_url}")

            html = get_page(page_url)

            if not html:
                print("  FAILED TO READ PAGE")
                continue

            links = extract_links(page_url, html)

            print(f"  Found {len(links)} links")

            for item in links:

                url = item["url"]
                text = item["text"]

                # NMC requires filtering
                if category == "11_NMC_2002_2026":

                    combined = f"{text} {url}".lower()

                    if not any(k in combined for k in NMC_KEYWORDS):
                        continue

                # Keep likely documents
                if is_pdf_url(url):

                    all_links.append({
                        "category": category,
                        "url": url,
                        "text": text,
                    })

    return all_links


# ============================================================
# DOWNLOAD
# ============================================================

def download_file(category, url, suggested_name=None):

    folder = os.path.join(ROOT, category)
    os.makedirs(folder, exist_ok=True)

    if suggested_name:
        filename = clean_filename(suggested_name)

        if not filename.lower().endswith(".pdf"):
            filename += ".pdf"
    else:
        filename = get_filename_from_url(url)

    # Avoid collisions
    base, ext = os.path.splitext(filename)

    filename = f"{base}_{url_hash(url)}{ext}"

    output = os.path.join(folder, filename)

    if os.path.exists(output) and os.path.getsize(output) > 0:

        return {
            "status": "EXISTS",
            "category": category,
            "filename": filename,
            "url": url,
            "size": os.path.getsize(output),
        }

    for attempt in range(1, RETRIES + 1):

        try:

            r = requests.get(
                url,
                headers=HEADERS,
                timeout=TIMEOUT,
                stream=True,
                verify=True
            )

            r.raise_for_status()

            content_type = r.headers.get("Content-Type", "").lower()

            # Some official servers return octet-stream instead of PDF.
            # That's okay if URL suggests PDF/document.
            data = r.content

            if len(data) < 500:
                raise ValueError(
                    f"Downloaded content too small ({len(data)} bytes)"
                )

            with open(output, "wb") as f:
                f.write(data)

            return {
                "status": "DOWNLOADED",
                "category": category,
                "filename": filename,
                "url": url,
                "size": len(data),
                "content_type": content_type,
            }

        except Exception as e:

            if attempt == RETRIES:

                return {
                    "status": "FAILED",
                    "category": category,
                    "filename": filename,
                    "url": url,
                    "error": str(e),
                }

            time.sleep(2)


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 75)
    print("INDIA HEALTHCARE REGULATORY RAG")
    print("REMAINING DOCUMENT DOWNLOADER")
    print("=" * 75)

    records = []

    # --------------------------------------------------------
    # A. Crawl official pages
    # --------------------------------------------------------

    discovered = collect_page_links()

    print("\n")
    print("=" * 75)
    print(f"DISCOVERED PDF/DOCUMENT LINKS: {len(discovered)}")
    print("=" * 75)

    # Deduplicate URLs
    seen = set()
    unique_discovered = []

    for item in discovered:

        url = item["url"]

        if url in seen:
            continue

        seen.add(url)
        unique_discovered.append(item)

    print(f"UNIQUE DISCOVERED LINKS: {len(unique_discovered)}")

    # --------------------------------------------------------
    # B. Add direct documents
    # --------------------------------------------------------

    direct_items = []

    for category, docs in DIRECT_DOCUMENTS.items():

        for filename, url in docs:

            direct_items.append({
                "category": category,
                "url": url,
                "filename": filename,
                "text": filename,
            })

    # Deduplicate against discovered URLs
    discovered_urls = {x["url"] for x in unique_discovered}

    for item in direct_items:

        if item["url"] not in discovered_urls:

            unique_discovered.append({
                "category": item["category"],
                "url": item["url"],
                "text": item["text"],
                "filename": item["filename"],
            })

    print(f"TOTAL UNIQUE DOWNLOAD TARGETS: {len(unique_discovered)}")

    # --------------------------------------------------------
    # C. Download
    # --------------------------------------------------------

    futures = {}

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:

        for item in unique_discovered:

            suggested_name = item.get("filename")

            future = executor.submit(
                download_file,
                item["category"],
                item["url"],
                suggested_name
            )

            futures[future] = item

        for i, future in enumerate(as_completed(futures), 1):

            item = futures[future]

            try:
                result = future.result()

            except Exception as e:

                result = {
                    "status": "FAILED",
                    "category": item["category"],
                    "url": item["url"],
                    "error": str(e),
                }

            records.append(result)

            status = result.get("status")

            print(
                f"[{i}/{len(futures)}] "
                f"{status:10} | "
                f"{item['category']} | "
                f"{item['url']}"
            )

    # --------------------------------------------------------
    # D. Save index
    # --------------------------------------------------------

    index_path = os.path.join(
        ROOT,
        "remaining_documents_index.csv"
    )

    fields = [
        "status",
        "category",
        "filename",
        "url",
        "size",
        "content_type",
        "error",
    ]

    with open(
        index_path,
        "w",
        newline="",
        encoding="utf-8"
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=fields
        )

        writer.writeheader()

        for record in records:

            writer.writerow({
                field: record.get(field, "")
                for field in fields
            })

    # --------------------------------------------------------
    # E. Summary
    # --------------------------------------------------------

    downloaded = sum(
        r.get("status") == "DOWNLOADED"
        for r in records
    )

    existed = sum(
        r.get("status") == "EXISTS"
        for r in records
    )

    failed = sum(
        r.get("status") == "FAILED"
        for r in records
    )

    print("\n")
    print("=" * 75)
    print("DOWNLOAD COMPLETE")
    print("=" * 75)

    print(f"Targets       : {len(records)}")
    print(f"Downloaded    : {downloaded}")
    print(f"Already exist : {existed}")
    print(f"Failed        : {failed}")
    print(f"Root folder   : {ROOT}")
    print(f"Master index  : {index_path}")

    if failed:

        print("\nFAILED DOCUMENTS")
        print("-" * 75)

        for r in records:

            if r.get("status") == "FAILED":

                print(
                    f"{r.get('category')} | "
                    f"{r.get('url')} | "
                    f"{r.get('error')}"
                )


if __name__ == "__main__":
    main()