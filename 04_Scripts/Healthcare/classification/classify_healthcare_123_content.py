import csv
import re
from pathlib import Path

from pypdf import PdfReader


ROOT = Path(
    r"C:\Users\rahma\OneDrive - Indian Institute of Management"
    r"\\Desktop\Term 5\CCS\RAG_Project"
)

MASTER = ROOT / "Healthcare_Master"

INPUT_FILE = MASTER / "healthcare_manual_review_classified.csv"
OUTPUT_FILE = MASTER / "healthcare_content_classification.csv"


def classify_text(text):
    t = text.lower()

    scores = {
        "Regulatory / Legal": 0,
        "Clinical / Healthcare": 0,
        "Digital Health / Data": 0,
        "PM-JAY / Insurance": 0,
        "Medical Education / NMC": 0,
        "Public Health": 0,
        "Informational": 0,
    }

    # Authorities
    if re.search(r"ministry of health|mohfw|government of india", t):
        scores["Regulatory / Legal"] += 3

    if re.search(r"national medical commission|\bnmc\b|medical council of india|\bmci\b", t):
        scores["Medical Education / NMC"] += 8
        scores["Regulatory / Legal"] += 5

    if re.search(r"national health authority|\bnha\b|ayushman bharat|pm[- ]?jay", t):
        scores["PM-JAY / Insurance"] += 8

    if re.search(r"cdsco|central drugs standard control|drug controller general", t):
        scores["Regulatory / Legal"] += 7
        scores["Clinical / Healthcare"] += 3

    # Legal / regulatory language
    if re.search(r"\bact\b|acts|statute|statutory|legislation|hereby", t):
        scores["Regulatory / Legal"] += 7

    if re.search(r"\brules?\b|regulations?|regulatory", t):
        scores["Regulatory / Legal"] += 7

    if re.search(r"notification|circular|gazette|government order|office memorandum", t):
        scores["Regulatory / Legal"] += 7

    if re.search(
        r"shall be|shall not|mandatory|required|prohibited|penalty|offence|punishable",
        t,
    ):
        scores["Regulatory / Legal"] += 3

    # Registration / licensing
    if re.search(
        r"registration|registered|licen[cs]e|licensing|renewal|recognition|approval|accreditation|empanelment",
        t,
    ):
        scores["Regulatory / Legal"] += 5

    # Standards
    if re.search(
        r"minimum standards|minimum requirements|standards|compliance|compliant|requirements|norms",
        t,
    ):
        scores["Regulatory / Legal"] += 5

    # Clinical establishments
    if re.search(
        r"clinical establishment|hospital|clinic|nursing home|healthcare facility|health facility",
        t,
    ):
        scores["Clinical / Healthcare"] += 5

    if re.search(
        r"laboratory|diagnostic|pathology|radiology|blood bank|pharmacy|medical facility",
        t,
    ):
        scores["Clinical / Healthcare"] += 4

    # Medical education
    if re.search(
        r"medical education|medical college|undergraduate|postgraduate|post-graduate|internship|residency|faculty|curriculum|neet",
        t,
    ):
        scores["Medical Education / NMC"] += 7

    if re.search(
        r"professional conduct|ethics|medical practitioner|registered medical practitioner",
        t,
    ):
        scores["Medical Education / NMC"] += 6
        scores["Regulatory / Legal"] += 4

    # Digital health
    if re.search(
        r"digital health|digital healthcare|abdm|abha|health id|electronic health record|\behr\b|\bemr\b",
        t,
    ):
        scores["Digital Health / Data"] += 7

    if re.search(r"telemedicine|tele-medicine|telehealth|tele-health", t):
        scores["Digital Health / Data"] += 7

    if re.search(
        r"personal data|health data|data protection|privacy|cybersecurity|information security",
        t,
    ):
        scores["Digital Health / Data"] += 7
        scores["Regulatory / Legal"] += 3

    # Insurance
    if re.search(
        r"health insurance|insurance claim|claims|beneficiary|package rate|package rates|pre-authorisation|preauthorization",
        t,
    ):
        scores["PM-JAY / Insurance"] += 6

    if re.search(r"empanelled hospital|empanelment|portability", t):
        scores["PM-JAY / Insurance"] += 5

    # Public health
    if re.search(
        r"public health|national health programme|national health program|health programme|health program",
        t,
    ):
        scores["Public Health"] += 6

    if re.search(
        r"surveillance|epidemic|pandemic|outbreak|disease control|immunization|vaccination",
        t,
    ):
        scores["Public Health"] += 5

    # Patient rights
    if re.search(
        r"patient rights|patient charter|grievance|grievance redressal|complaint|informed consent|consumer rights",
        t,
    ):
        scores["Regulatory / Legal"] += 6

    # Clinical guidance
    if re.search(
        r"clinical guideline|clinical guidelines|clinical management|treatment guideline|management guideline|clinical protocol|standard treatment",
        t,
    ):
        scores["Clinical / Healthcare"] += 5

    # Biomedical safety
    if re.search(
        r"biomedical waste|bio-medical waste|infection control|infection prevention|sterilization",
        t,
    ):
        scores["Clinical / Healthcare"] += 5
        scores["Regulatory / Legal"] += 4

    # Informational
    if re.search(
        r"annual report|annual-report|newsletter|bulletin|statistics|statistical report|survey report|dashboard",
        t,
    ):
        scores["Informational"] += 7

    if re.search(
        r"press release|public notice|poster|brochure|leaflet|awareness material",
        t,
    ):
        scores["Informational"] += 6

    draft = bool(re.search(r"\bdraft\b|proposed|consultation paper", t))

    category = max(scores, key=scores.get)
    score = scores[category]

    if score == 0:
        bucket = "MANUAL REVIEW"
        priority = "REVIEW"
        reason = "No strong content signal detected"

    elif draft and category == "Regulatory / Legal":
        bucket = "REVIEW - DRAFT / PROPOSED"
        priority = "MEDIUM"
        reason = "Contains draft/proposed language"

    elif category == "Informational" and score >= 7:
        bucket = "EXCLUDE - INFORMATIONAL"
        priority = "LOW"
        reason = "Appears to be report/statistical/informational material"

    elif category == "Regulatory / Legal" and score >= 7:
        bucket = "KEEP - CORE REGULATORY"
        priority = "HIGH"
        reason = "Strong legal/regulatory content detected"

    elif category == "Digital Health / Data" and score >= 7:
        bucket = "KEEP - CORE DIGITAL HEALTH"
        priority = "HIGH"
        reason = "Strong digital-health/data compliance content detected"

    elif category == "PM-JAY / Insurance" and score >= 7:
        bucket = "KEEP - CORE PM-JAY / INSURANCE"
        priority = "HIGH"
        reason = "Strong healthcare insurance/PM-JAY content detected"

    elif category == "Medical Education / NMC" and score >= 7:
        bucket = "KEEP - MEDICAL REGULATION / EDUCATION"
        priority = "HIGH"
        reason = "Strong medical education/professional regulatory content detected"

    elif score >= 5:
        bucket = "KEEP - SUPPORTING GUIDANCE"
        priority = "MEDIUM"
        reason = "Relevant official healthcare guidance detected"

    else:
        bucket = "REVIEW - NEEDS VERIFICATION"
        priority = "REVIEW"
        reason = "Some healthcare signal detected but insufficient confidence"

    return category, bucket, priority, reason


def find_pdf(row):
    candidates = []

    original = row.get("OriginalPath", "").strip()
    filename = row.get("FileName", "").strip()

    if original:
        candidates.append(Path(original))

    if filename:
        candidates.append(MASTER / filename)

    for candidate in candidates:
        if candidate.exists():
            return candidate

    if filename:
        matches = list(MASTER.rglob(filename))
        if matches:
            return matches[0]

    return None


rows = []

with INPUT_FILE.open("r", encoding="utf-8-sig", newline="") as f:
    reader = csv.DictReader(f)

    for row in reader:
        if row.get("SuggestedBucket", "").strip() == "MANUAL REVIEW":
            rows.append(row)

print("=" * 60)
print("HEALTHCARE PDF CONTENT CLASSIFICATION")
print("=" * 60)
print()
print(f"PDFs to inspect: {len(rows)}")
print()

results = []

for i, row in enumerate(rows, 1):

    filename = row.get("FileName", "")
    print(f"[{i}/{len(rows)}] {filename}")

    pdf_path = find_pdf(row)

    if pdf_path is None:
        results.append({
            "FileName": filename,
            "FilePath": "",
            "DocumentTitle": "",
            "Authority": "",
            "Year": "",
            "SuggestedCategory": "",
            "SuggestedBucket": "ERROR - FILE NOT FOUND",
            "SuggestedPriority": "REVIEW",
            "Reason": "PDF could not be located",
        })
        continue

    try:
        reader = PdfReader(str(pdf_path))

        # First 3 pages are enough for title / authority / document type
        text_parts = []

        for page in reader.pages[:3]:
            try:
                text_parts.append(page.extract_text() or "")
            except Exception:
                pass

        text = "\n".join(text_parts)

        if not text.strip():
            text = filename

        # Preview
        lines = [
            x.strip()
            for x in text.splitlines()
            if x.strip()
        ]

        title = " | ".join(lines[:12])[:1000]

        # Year
        years = re.findall(r"\b(?:19|20)\d{2}\b", text)
        year = years[0] if years else ""

        # Authority
        authority = ""

        authorities = [
            "Ministry of Health and Family Welfare",
            "Government of India",
            "National Medical Commission",
            "Medical Council of India",
            "National Health Authority",
            "Ayushman Bharat",
            "PM-JAY",
            "Central Drugs Standard Control Organisation",
            "CDSCO",
            "Directorate General of Health Services",
            "Indian Council of Medical Research",
            "ICMR",
            "Bureau of Indian Standards",
            "BIS",
        ]

        text_lower = text.lower()

        for a in authorities:
            if a.lower() in text_lower:
                authority = a
                break

        category, bucket, priority, reason = classify_text(text)

        results.append({
            "FileName": filename,
            "FilePath": str(pdf_path),
            "DocumentTitle": title,
            "Authority": authority,
            "Year": year,
            "SuggestedCategory": category,
            "SuggestedBucket": bucket,
            "SuggestedPriority": priority,
            "Reason": reason,
        })

    except Exception as e:

        results.append({
            "FileName": filename,
            "FilePath": str(pdf_path),
            "DocumentTitle": "",
            "Authority": "",
            "Year": "",
            "SuggestedCategory": "",
            "SuggestedBucket": "ERROR - EXTRACTION FAILED",
            "SuggestedPriority": "REVIEW",
            "Reason": str(e),
        })


fieldnames = [
    "FileName",
    "FilePath",
    "DocumentTitle",
    "Authority",
    "Year",
    "SuggestedCategory",
    "SuggestedBucket",
    "SuggestedPriority",
    "Reason",
]

with OUTPUT_FILE.open("w", encoding="utf-8-sig", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(results)


from collections import Counter

bucket_counts = Counter(
    r["SuggestedBucket"] for r in results
)

priority_counts = Counter(
    r["SuggestedPriority"] for r in results
)

print()
print("=" * 60)
print("CONTENT CLASSIFICATION COMPLETE")
print("=" * 60)
print()
print(f"Documents processed: {len(results)}")
print()
print("BUCKET SUMMARY")
print("-" * 60)

for bucket, count in bucket_counts.most_common():
    print(f"{bucket:<45} {count:>5}")

print()
print("PRIORITY SUMMARY")
print("-" * 60)

for priority, count in sorted(priority_counts.items()):
    print(f"{priority:<15} {count:>5}")

print()
print("Output:")
print(OUTPUT_FILE)
print()