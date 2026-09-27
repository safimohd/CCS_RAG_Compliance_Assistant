import os
import re
import csv
import time
import hashlib
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse, unquote
from concurrent.futures import ThreadPoolExecutor, as_completed


# ============================================================
# INDIA HEALTHCARE REGULATORY RAG
# REMAINING DOCUMENT DOWNLOADER - V2
#
# Purpose:
#   Download only remaining authoritative healthcare-regulatory
#   documents from official Indian Government / regulator sources.
#
# Safe:
#   - Resumes existing downloads
#   - Validates PDF magic bytes
#   - Does NOT save HTML as .pdf
#   - Filters NMC "What's New"
#   - Does NOT crawl ABDM again
#   - Does NOT hammer the NACO page
# ============================================================


BASE_DIR = "Healthcare_RAG_Raw"
OUTPUT_ROOT = os.path.join(BASE_DIR, "08_Remaining_Official")

MAX_WORKERS = 6
TIMEOUT = 40
RETRIES = 3

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/153.0 Safari/537.36"
    )
}


# ============================================================
# FOLDER STRUCTURE
# ============================================================

FOLDERS = {
    "CEA": "01_CEA",
    "NMC": "02_NMC",
    "DPDP": "03_DPDP",
    "HIV_AIDS": "04_HIV_AIDS",
    "MENTAL_HEALTHCARE": "05_Mental_Healthcare",
    "MTP_PCPNDT": "06_MTP_PCPNDT",
    "ART_SURROGACY_THOTA": "07_ART_Surrogacy_THOTA",
    "NCAHP": "08_NCAHP",
    "BIOMEDICAL_WASTE": "09_Biomedical_Waste",
    "AERB": "10_AERB",
}


for folder in FOLDERS.values():
    os.makedirs(os.path.join(OUTPUT_ROOT, folder), exist_ok=True)


# ============================================================
# HELPERS
# ============================================================

def clean_filename(name):
    name = unquote(name)

    name = re.sub(r"[<>:\"/\\|?*]", "_", name)
    name = re.sub(r"\s+", " ", name)
    name = name.strip(" .")

    if not name:
        name = "document"

    return name[:180]


def filename_from_url(url, fallback="document.pdf"):
    path = urlparse(url).path
    name = os.path.basename(path)

    if not name:
        name = fallback

    name = clean_filename(name)

    if not name.lower().endswith(".pdf"):
        name += ".pdf"

    return name


def unique_filename(url, original_name):
    """
    Prevent collisions such as multiple Download.pdf files.
    """
    stem, ext = os.path.splitext(original_name)

    short_hash = hashlib.sha1(url.encode("utf-8")).hexdigest()[:10]

    return f"{stem}_{short_hash}{ext}"


def is_pdf(content):
    """
    Real PDFs begin with %PDF.
    This prevents HTML pages being saved as .pdf.
    """
    return content[:5] == b"%PDF-"


def fetch(url, timeout=TIMEOUT):
    for attempt in range(1, RETRIES + 1):

        try:
            r = requests.get(
                url,
                headers=HEADERS,
                timeout=timeout,
                allow_redirects=True
            )

            r.raise_for_status()

            return r

        except Exception as e:

            if attempt < RETRIES:
                print(
                    f"    Retry {attempt}/{RETRIES - 1}: "
                    f"{str(e)[:120]}"
                )
                time.sleep(2)

            else:
                print(
                    f"    FAILED: {url}\n"
                    f"    Reason: {str(e)[:200]}"
                )

    return None


def save_pdf(url, category, source_title=None):

    folder = os.path.join(
        OUTPUT_ROOT,
        FOLDERS[category]
    )

    os.makedirs(folder, exist_ok=True)

    original_name = filename_from_url(
        url,
        fallback="document.pdf"
    )

    filename = unique_filename(
        url,
        original_name
    )

    filepath = os.path.join(
        folder,
        filename
    )

    # Resume-safe
    if os.path.exists(filepath):

        if os.path.getsize(filepath) > 1000:

            with open(filepath, "rb") as f:
                first_bytes = f.read(5)

            if first_bytes == b"%PDF-":

                return {
                    "status": "EXISTING",
                    "category": category,
                    "filename": filename,
                    "url": url,
                    "size": os.path.getsize(filepath),
                    "source_title": source_title or ""
                }

    r = fetch(url)

    if r is None:
        return {
            "status": "FAILED",
            "category": category,
            "filename": filename,
            "url": url,
            "size": 0,
            "source_title": source_title or ""
        }

    content = r.content

    # CRITICAL:
    # Do not save HTML pages with .pdf extensions.
    if not is_pdf(content):

        return {
            "status": "INVALID_NOT_PDF",
            "category": category,
            "filename": filename,
            "url": url,
            "size": len(content),
            "source_title": source_title or ""
        }

    with open(filepath, "wb") as f:
        f.write(content)

    return {
        "status": "DOWNLOADED",
        "category": category,
        "filename": filename,
        "url": url,
        "size": len(content),
        "source_title": source_title or ""
    }


# ============================================================
# PDF LINK EXTRACTION
# ============================================================

def extract_pdf_links(page_url, html, keyword_filter=None):

    soup = BeautifulSoup(html, "html.parser")

    results = []

    for a in soup.find_all("a", href=True):

        href = a.get("href", "").strip()

        if not href:
            continue

        full_url = urljoin(page_url, href)

        text = a.get_text(" ", strip=True)

        combined = f"{text} {href}".lower()

        # Must look like a PDF
        looks_pdf = (
            ".pdf" in full_url.lower()
            or "getdocument" in full_url.lower()
            or "download" in full_url.lower()
        )

        if not looks_pdf:
            continue

        if keyword_filter:

            if not any(
                keyword.lower() in combined
                for keyword in keyword_filter
            ):
                continue

        results.append(
            (
                full_url,
                text
            )
        )

    # Deduplicate
    seen = set()
    unique = []

    for url, title in results:

        if url not in seen:

            seen.add(url)

            unique.append(
                (
                    url,
                    title
                )
            )

    return unique


# ============================================================
# CRAWL A PAGE
# ============================================================

def crawl_page(
    page_url,
    category,
    keyword_filter=None
):

    print()
    print(f"Scanning: {page_url}")

    r = fetch(page_url)

    if r is None:

        print("  FAILED TO READ PAGE")
        return []

    content_type = r.headers.get(
        "Content-Type",
        ""
    ).lower()

    # If the URL itself is a PDF
    if is_pdf(r.content):

        print("  Page itself is a PDF")

        return [
            {
                "url": page_url,
                "title": os.path.basename(
                    urlparse(page_url).path
                ),
                "category": category
            }
        ]

    links = extract_pdf_links(
        page_url,
        r.text,
        keyword_filter
    )

    print(f"  PDF links found: {len(links)}")

    return [
        {
            "url": url,
            "title": title,
            "category": category
        }
        for url, title in links
    ]


# ============================================================
# DIRECT DOCUMENTS
# ============================================================

DIRECT_DOCUMENTS = [

    # --------------------------------------------------------
    # NMC
    # --------------------------------------------------------

    (
        "NMC",
        "NMC Code of Medical Ethics Regulations 2002",
        "https://www.nmc.org.in/wp-content/uploads/2017/10/Ethics-Regulations-2002.pdf"
    ),

    # --------------------------------------------------------
    # DPDP
    # --------------------------------------------------------

    (
        "DPDP",
        "Digital Personal Data Protection Act 2023",
        "https://www.meity.gov.in/static/uploads/2024/02/Digital-Personal-Data-Protection-Act-2023-1.pdf"
    ),

    (
        "DPDP",
        "Digital Personal Data Protection Rules 2025",
        "https://www.meity.gov.in/static/uploads/2025/11/53450e6e5dc0bfa85ebd78686cadad39.pdf"
    ),

    (
        "DPDP",
        "Establishment of Data Protection Board of India",
        "https://www.meity.gov.in/static/uploads/2025/11/cc217843dc3bcb37b2b05bcc3b4e031f.pdf"
    ),

    # --------------------------------------------------------
    # HIV / AIDS
    # --------------------------------------------------------

    (
        "HIV_AIDS",
        "HIV AIDS Central Government Rules 2018",
        "https://naco.mohfw.gov.in/files/Centre%20Rules.pdf"
    ),

    (
        "HIV_AIDS",
        "HIV AIDS Act 2017 English",
        "https://naco.gov.in/sites/default/files/HIV%20and%20AIDS%20Act-%20English.pdf"
    ),

    # --------------------------------------------------------
    # MENTAL HEALTHCARE
    # --------------------------------------------------------

    (
        "MENTAL_HEALTHCARE",
        "Mental Healthcare Act 2017",
        "https://www.mohfw.gov.in/sites/default/files/Mental%20Healthcare%20Act%2C%202017_0.pdf"
    ),

    # --------------------------------------------------------
    # MTP
    # --------------------------------------------------------

    (
        "MTP_PCPNDT",
        "Medical Termination of Pregnancy Act 1971",
        "https://www.indiacode.nic.in/bitstream/123456789/1593/1/197134.pdf"
    ),

    # --------------------------------------------------------
    # PCPNDT
    # --------------------------------------------------------

    (
        "MTP_PCPNDT",
        "PCPNDT Act 1994",
        "https://www.indiacode.nic.in/bitstream/123456789/1937/1/199457.pdf"
    ),

    # --------------------------------------------------------
    # NCAHP
    # --------------------------------------------------------

    (
        "NCAHP",
        "National Commission for Allied and Healthcare Professions Act 2021",
        "https://www.indiacode.nic.in/bitstream/123456789/16824/1/a2021-14.pdf"
    ),

    # --------------------------------------------------------
    # BIOMEDICAL WASTE
    # --------------------------------------------------------

    (
        "BIOMEDICAL_WASTE",
        "Biomedical Waste Management Amendment Rules 2018",
        "https://upload.indiacode.nic.in/showfile?actid=AC_CEN_16_18_00011_198629_1517807327582&filename=Bio+medical+waste+management+%28amendment%29183847.pdf&type=rule"
    ),

    # --------------------------------------------------------
    # AERB
    # --------------------------------------------------------

    (
        "AERB",
        "Medical Diagnostic X Ray Safety Code",
        "https://www.aerb.gov.in/storage/uploads/documents/regdocMS24h.pdf"
    ),
]


# ============================================================
# PAGE SOURCES
# ============================================================

PAGE_SOURCES = [

    # --------------------------------------------------------
    # CEA
    # --------------------------------------------------------

    (
        "CEA",
        "CEA Official Notifications",
        "https://clinicalestablishments.mohfw.gov.in/notification",
        [
            "notification",
            "amendment",
            "clinical establishment",
            "diagnostic",
            "gazette",
            "rules",
        ]
    ),

    # --------------------------------------------------------
    # NMC
    # --------------------------------------------------------

    (
        "NMC",
        "NMC Rules and Regulations",
        "https://nmc.org.in/page/rules-regulations-rules-regulations-nmc",
        [
            "regulation",
            "regulations",
            "amendment",
            "guideline",
            "guidelines",
            "advisory",
            "registration",
            "licence",
            "license",
            "professional conduct",
            "medical qualification",
            "minimum standards",
            "medical education",
            "medical institution",
            "faculty",
            "FMGL",
            "foreign medical graduate",
            "NExT",
            "PGME",
            "CBME",
            "recognition",
        ]
    ),

    (
        "NMC",
        "NMC What's New",
        "https://nmc.org.in/whats-new/",
        [
            "regulation",
            "regulations",
            "amendment",
            "gazette",
            "guideline",
            "guidelines",
            "advisory",
            "notification",
            "professional conduct",
            "registration",
            "licence",
            "license",
            "medical ethics",
            "minimum standards",
            "medical education",
            "clinical",
            "patient safety",
            "telemedicine",
            "digital health",
            "ABDM",
            "HMIS",
            "registered medical practitioner",
            "stem cell",
            "infection prevention",
            "safe injection",
        ]
    ),

    # --------------------------------------------------------
    # INDIA CODE
    # --------------------------------------------------------

    (
        "ART_SURROGACY_THOTA",
        "ART Act 2021",
        "https://www.indiacode.nic.in/indiacode/handle/123456789/17031?view_type=browse",
        [
            "assisted reproductive",
            "reproductive technology",
            "act",
            "rules",
        ]
    ),

    (
        "ART_SURROGACY_THOTA",
        "Surrogacy Act 2021",
        "https://www.indiacode.nic.in/indiacode/handle/123456789/17046?view_type=browse",
        [
            "surrogacy",
            "act",
            "rules",
        ]
    ),

    (
        "ART_SURROGACY_THOTA",
        "Transplantation of Human Organs and Tissues Act",
        "https://www.indiacode.nic.in/indiacode/handle/123456789/1962?view_type=browse",
        [
            "transplantation",
            "human organ",
            "human tissue",
            "act",
            "rules",
        ]
    ),

    # --------------------------------------------------------
    # AERB / DAE
    # --------------------------------------------------------

    (
        "AERB",
        "Department of Atomic Energy Acts and Rules",
        "https://dae.gov.in/acts-rules/page/3/",
        [
            "radiation protection",
            "radiation",
            "radioactive",
            "safe disposal",
            "atomic energy",
        ]
    ),

    (
        "AERB",
        "AERB Rules",
        "https://www.aerb.gov.in/english/acts-regulations/rules",
        [
            "radiation",
            "diagnostic",
            "x-ray",
            "medical",
            "safety",
            "rules",
        ]
    ),

    # --------------------------------------------------------
    # BIOMEDICAL WASTE
    # --------------------------------------------------------

    (
        "BIOMEDICAL_WASTE",
        "India Code Biomedical Waste",
        "https://www.indiacode.nic.in/handle/123456789/1362?view_type=browse",
        [
            "bio-medical",
            "biomedical",
            "waste",
            "management",
        ]
    ),
]


# ============================================================
# NMC EXCLUSION FILTER
# ============================================================

NMC_EXCLUDE = [

    "recruitment",
    "recruitment rules",
    "vacancy",
    "consultant",
    "deputation",
    "senior advisor",
    "career",
    "job",
    "quiz",
    "viksit bharat",
    "event",
    "webinar",
    "celebration",
    "holiday",
    "seat matrix",
    "students admitted",
    "admission counselling",
]


def nmc_allowed(title, url):

    combined = (
        f"{title} {url}"
    ).lower()

    # Exclude obvious administrative noise
    for word in NMC_EXCLUDE:

        if word in combined:

            return False

    return True


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 78)
    print("INDIA HEALTHCARE REGULATORY RAG")
    print("REMAINING DOCUMENT DOWNLOADER - V2")
    print("=" * 78)

    all_documents = []

    # --------------------------------------------------------
    # 1. DIRECT DOCUMENTS
    # --------------------------------------------------------

    print()
    print("=" * 70)
    print("1. DIRECT OFFICIAL DOCUMENTS")
    print("=" * 70)

    for category, title, url in DIRECT_DOCUMENTS:

        all_documents.append(
            {
                "url": url,
                "title": title,
                "category": category,
            }
        )

        print(
            f"[DIRECT] {category}: {title}"
        )

    # --------------------------------------------------------
    # 2. CRAWL OFFICIAL PAGES
    # --------------------------------------------------------

    print()
    print("=" * 70)
    print("2. CRAWLING OFFICIAL REGULATORY PAGES")
    print("=" * 70)

    for (
        category,
        title,
        page_url,
        keywords
    ) in PAGE_SOURCES:

        # Special handling for NMC What's New
        docs = crawl_page(
            page_url,
            category,
            keyword_filter=keywords
        )

        for doc in docs:

            if category == "NMC":

                if not nmc_allowed(
                    doc["title"],
                    doc["url"]
                ):
                    continue

            all_documents.append(doc)

    # --------------------------------------------------------
    # 3. DEDUPLICATE
    # --------------------------------------------------------

    print()
    print("=" * 70)
    print("3. DEDUPLICATING")
    print("=" * 70)

    unique = {}

    for doc in all_documents:

        url = doc["url"]

        if url not in unique:

            unique[url] = doc

    all_documents = list(
        unique.values()
    )

    print(
        f"Unique documents identified: "
        f"{len(all_documents)}"
    )

    # --------------------------------------------------------
    # 4. DOWNLOAD
    # --------------------------------------------------------

    print()
    print("=" * 70)
    print("4. DOWNLOADING")
    print("=" * 70)

    results = []

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:

        future_map = {}

        for doc in all_documents:

            future = executor.submit(
                save_pdf,
                doc["url"],
                doc["category"],
                doc.get("title", "")
            )

            future_map[future] = doc

        for future in as_completed(
            future_map
        ):

            doc = future_map[future]

            try:

                result = future.result()

            except Exception as e:

                result = {
                    "status": "FAILED_EXCEPTION",
                    "category": doc["category"],
                    "filename": "",
                    "url": doc["url"],
                    "size": 0,
                    "source_title": doc.get(
                        "title",
                        ""
                    ),
                }

                print(
                    f"[EXCEPTION] "
                    f"{doc['url']}\n"
                    f"  {e}"
                )

            results.append(result)

            status = result["status"]

            if status == "DOWNLOADED":

                print(
                    f"[OK] "
                    f"{result['category']} | "
                    f"{result['filename']} | "
                    f"{result['size']:,} bytes"
                )

            elif status == "EXISTING":

                print(
                    f"[EXISTS] "
                    f"{result['category']} | "
                    f"{result['filename']}"
                )

            elif status == "INVALID_NOT_PDF":

                print(
                    f"[NOT PDF] "
                    f"{result['category']} | "
                    f"{result['url']}"
                )

            else:

                print(
                    f"[FAILED] "
                    f"{result['category']} | "
                    f"{result['url']}"
                )

    # --------------------------------------------------------
    # 5. MASTER INDEX
    # --------------------------------------------------------

    print()
    print("=" * 70)
    print("5. WRITING MASTER INDEX")
    print("=" * 70)

    index_path = os.path.join(
        OUTPUT_ROOT,
        "remaining_master_index.csv"
    )

    with open(
        index_path,
        "w",
        newline="",
        encoding="utf-8-sig"
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=[
                "status",
                "category",
                "filename",
                "size",
                "source_title",
                "url",
            ]
        )

        writer.writeheader()

        for result in sorted(
            results,
            key=lambda x: (
                x["category"],
                x["filename"]
            )
        ):

            writer.writerow(result)

    # --------------------------------------------------------
    # 6. SUMMARY
    # --------------------------------------------------------

    downloaded = sum(
        1
        for r in results
        if r["status"] == "DOWNLOADED"
    )

    existing = sum(
        1
        for r in results
        if r["status"] == "EXISTING"
    )

    failed = sum(
        1
        for r in results
        if r["status"]
        in [
            "FAILED",
            "FAILED_EXCEPTION",
        ]
    )

    invalid = sum(
        1
        for r in results
        if r["status"] == "INVALID_NOT_PDF"
    )

    print()
    print("=" * 78)
    print("DOWNLOAD SUMMARY")
    print("=" * 78)

    print(
        f"Unique documents attempted : "
        f"{len(all_documents)}"
    )

    print(
        f"Downloaded                  : "
        f"{downloaded}"
    )

    print(
        f"Already existed             : "
        f"{existing}"
    )

    print(
        f"Failed                      : "
        f"{failed}"
    )

    print(
        f"Invalid / not PDF           : "
        f"{invalid}"
    )

    print()
    print("Output folder:")
    print(
        os.path.abspath(OUTPUT_ROOT)
    )

    print()
    print("Master index:")
    print(
        os.path.abspath(index_path)
    )

    print()
    print("=" * 78)
    print("DONE")
    print("=" * 78)


if __name__ == "__main__":
    main()