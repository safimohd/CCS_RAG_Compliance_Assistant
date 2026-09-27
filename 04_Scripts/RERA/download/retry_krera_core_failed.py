import csv
import time
import requests
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(
    r"C:\Users\rahma\OneDrive - Indian Institute of Management"
    r"\Desktop\Term 5\CCS\RAG_Project\Real Estate"
)

DOCS = ROOT / "KRERA_Documents"
METADATA = DOCS / "krera_metadata.csv"
RETRY_LOG = DOCS / "krera_core_retry_results.csv"


# ============================================================
# CORE RERA KEYWORDS
# ============================================================

CORE_KEYWORDS = [
    "registration",
    "extension of registration",
    "extension of project",
    "extension of time",
    "force majeure",
    "section 7",
    "section 8",
    "section 11",
    "section 12",
    "section 13",
    "section 14",
    "section 15",
    "section 18",
    "section 19",
    "section 31",
    "section 40",
    "section 59",
    "section 60",
    "section 61",
    "section 63",
    "section 64",
    "section 70",
    "section 71",
    "section 72",
    "section 81",
    "project registration",
    "promoter",
    "allottee",
    "quarterly progress",
    "qpr",
    "annual audit",
    "audit report",
    "rera bank account",
    "designated bank account",
    "70% account",
    "penalty",
    "recovery",
    "arrears",
    "monetary relief",
    "non-monetary relief",
    "complaint",
    "complaints",
    "adjudicating officer",
    "appeal",
    "appellate tribunal",
    "agreement for sale",
    "model form",
    "allotment letter",
    "advertisement",
    "advertising",
    "ongoing projects",
    "ongoing project",
    "change of promoter",
    "transfer of promoter",
    "assignment of promoter",
    "land owner",
    "landowners",
    "project name",
    "project approval",
    "fees",
    "fee",
    "renewal",
    "approval",
    "rules",
    "regulations",
    "notification",
    "circular",
    "guidelines",
    "corrigendum",
    "delegation"
]


# ============================================================
# TITLES THAT SHOULD NOT BE RETRIED
# ============================================================

EXCLUDE_KEYWORDS = [
    "applications are invited",
    "application are invited",
    "vacancy",
    "vacant post",
    "recruitment",
    "job",
    "legal advisor",
    "legal adviser",
    "standing counsel",
    "lok adalat",
    "lok-adalat",
    "azadi ka amrit",
    "tiranga",
    "press release",
    "office shifting",
    "shifting of office",
    "volunteer",
    "event",
    "awareness programme"
]


def is_core(title):

    t = title.lower()

    if any(x in t for x in EXCLUDE_KEYWORDS):
        return False

    return any(x in t for x in CORE_KEYWORDS)


# ============================================================
# LOAD ORIGINAL KRERA METADATA
# ============================================================

if not METADATA.exists():
    print("ERROR: krera_metadata.csv not found:")
    print(METADATA)
    raise SystemExit(1)


with METADATA.open(
    "r",
    encoding="utf-8-sig",
    newline=""
) as f:

    rows = list(csv.DictReader(f))


# ============================================================
# IDENTIFY FAILED CORE DOCUMENTS
# ============================================================

core_failed = []

for row in rows:

    status = str(
        row.get("status", "")
    ).strip().lower()

    title = (
        row.get("title")
        or row.get("Title")
        or row.get("document_name")
        or ""
    ).strip()

    if status != "downloaded" and is_core(title):
        core_failed.append(row)


print()
print("=" * 70)
print("KRERA — RETRY FAILED CORE DOCUMENTS")
print("=" * 70)
print()

print(
    f"Failed documents identified as CORE: "
    f"{len(core_failed)}"
)

print()

for i, row in enumerate(core_failed, 1):

    title = (
        row.get("title")
        or row.get("Title")
        or row.get("document_name")
        or ""
    )

    print(f"{i:02d}. {title}")

print()


# ============================================================
# HTTP SESSION
# ============================================================

session = requests.Session()

session.headers.update({
    "User-Agent":
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "Chrome/153.0 Safari/537.36"
})


results = []


# ============================================================
# DOWNLOAD
# ============================================================

for i, row in enumerate(core_failed, 1):

    title = (
        row.get("title")
        or row.get("Title")
        or row.get("document_name")
        or "KRERA Document"
    ).strip()

    url = (
        row.get("url")
        or row.get("URL")
        or row.get("document_url")
        or ""
    ).strip()

    # IMPORTANT:
    # Never use the long title as the Windows filename.
    safe_filename = f"KRERA_CORE_RETRY_{i:02d}.pdf"

    destination = DOCS / safe_filename

    print("=" * 70)
    print(f"[{i}/{len(core_failed)}]")
    print(f"TITLE: {title}")
    print(f"FILE : {safe_filename}")

    if not url:

        print("FAILED - No URL found")

        results.append({
            "title": title,
            "url": url,
            "filename": safe_filename,
            "status": "FAILED",
            "reason": "No URL found"
        })

        continue


    # --------------------------------------------------------
    # Retry up to 5 times
    # --------------------------------------------------------

    success = False
    reason = ""

    for attempt in range(1, 6):

        print(f"Attempt {attempt}/5")

        try:

            response = session.get(
                url,
                timeout=(20, 90),
                allow_redirects=True
            )

            content = response.content

            is_pdf = content[:5] == b"%PDF-"

            if response.status_code == 200 and is_pdf:

                destination.write_bytes(content)

                size = destination.stat().st_size

                if size > 1000:

                    print(
                        f"SUCCESS - {size:,} bytes"
                    )

                    results.append({
                        "title": title,
                        "url": url,
                        "filename": safe_filename,
                        "status": "DOWNLOADED",
                        "reason": ""
                    })

                    success = True
                    break

            reason = (
                f"HTTP {response.status_code}; "
                f"valid_pdf={is_pdf}"
            )

            print("FAILED:", reason)

        except Exception as e:

            reason = str(e)
            print("ERROR:", reason)

        if attempt < 5:
            time.sleep(2 * attempt)


    if not success:

        print("FINAL STATUS: FAILED")

        results.append({
            "title": title,
            "url": url,
            "filename": safe_filename,
            "status": "FAILED",
            "reason": reason
        })


# ============================================================
# SAVE RETRY LOG
# ============================================================

with RETRY_LOG.open(
    "w",
    encoding="utf-8-sig",
    newline=""
) as f:

    writer = csv.DictWriter(
        f,
        fieldnames=[
            "title",
            "url",
            "filename",
            "status",
            "reason"
        ]
    )

    writer.writeheader()
    writer.writerows(results)


# ============================================================
# SUMMARY
# ============================================================

downloaded = sum(
    1 for r in results
    if r["status"] == "DOWNLOADED"
)

failed = sum(
    1 for r in results
    if r["status"] == "FAILED"
)


print()
print("=" * 70)
print("RETRY COMPLETE")
print("=" * 70)
print()

print(
    f"Core documents identified : {len(core_failed)}"
)

print(
    f"Downloaded                : {downloaded}"
)

print(
    f"Still failed              : {failed}"
)

print()
print("Retry log:")
print(RETRY_LOG)
print()