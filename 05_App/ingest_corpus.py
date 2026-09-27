"""
Indian Regulatory Compliance Intelligence Assistant
Corpus ingestion and indexing.

Builds a Chroma vector index over 02_Corpus/ only, carrying the authority tier and
the resolved document title into chunk metadata so the retriever can filter by domain,
rank by authority, and cite a real title instead of a filename.

Four corpus properties this script exists to handle:
  1. Most Karnataka RERA PDFs are scanned images with no text layer. Their OCR text
     lives in 02_Corpus/_ocr_text/. Without the fallback, ~87% of the RERA side
     indexes as empty and retrieval silently returns nothing.
  2. The authority bucket is only a folder name. It is attached here as metadata,
     because after chunking a Draft/Proposed passage is otherwise indistinguishable
     from an Act.
  3. Many filenames are opaque (e.g. 2022051039_be62469a.pdf). Titles come from the
     mapping CSVs in 03_Metadata/.
  4. 01_Benchmarks/ holds expected answers. Indexing it would let the system retrieve
     its own answer key and invalidate the base-vs-RAG comparison. Asserted against.

Usage:
    python ingest_corpus.py                 # build the index
    python ingest_corpus.py --dry-run       # report only, write nothing
    python ingest_corpus.py --rebuild       # delete and rebuild the index

Writes:
    05_App/chroma_db/                       the persisted index
    03_Metadata/ingestion_report.csv        one row per source document
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import logging
import shutil
import sys
import time
import warnings
from collections import Counter
from pathlib import Path

# Government PDFs are full of minor structural defects and embedded Type1 fonts.
# pypdf recovers from all of it but is extremely loud about it - thousands of lines of
# "fontTools is required..." and "Ignoring wrong pointing object" that bury the report.
# None of it affects extraction. Silence it.
logging.getLogger("pypdf").setLevel(logging.CRITICAL)
logging.getLogger("pypdf._reader").setLevel(logging.CRITICAL)
logging.getLogger("pypdf.generic").setLevel(logging.CRITICAL)
logging.getLogger("pypdf._cmap").setLevel(logging.CRITICAL)
warnings.filterwarnings("ignore", module="pypdf")

# ── configuration ────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent   # 05_App/ -> project root
CORPUS       = PROJECT_ROOT / "02_Corpus"
OCR_TEXT     = CORPUS / "_ocr_text"
METADATA     = PROJECT_ROOT / "03_Metadata"
INDEX_DIR    = PROJECT_ROOT / "05_App" / "chroma_db"
REPORT       = METADATA / "ingestion_report.csv"

EMBED_MODEL  = "BAAI/bge-small-en-v1.5"     # 384d, matches the cohort setup
COLLECTION   = "regulatory_corpus"
CHUNK_SIZE   = 1000
CHUNK_OVERLAP = 150
MIN_TEXT_CHARS = 300                        # below this, treat the PDF as image-only

FORBIDDEN = ("01_Benchmarks", "08_Archive", "_new_sources")

# bucket -> authority tier. See README section 2.
TIER = {
    "01_Core_Regulatory":              "A",
    "02_Core_PMJAY_Insurance":         "A",
    "01_Central_RERA":                 "A",
    "02_Karnataka_RERA":               "A",
    "03_Medical_Regulation_Education": "B",
    "04_Supporting_Guidance":          "C",   # Healthcare
    "03_Supporting_Guidance":          "C",   # RERA
    "05_Draft_Proposed":               "D",
    "06_Review":                       "E",   # Healthcare
    "04_Review":                       "E",   # RERA
}
TIER_LABEL = {
    "A": "Core regulatory - citable as binding",
    "B": "Sector regulation - binding within its sector",
    "C": "Supporting guidance - explanatory, not binding",
    "D": "Draft or proposed - NEVER present as binding",
    "E": "Review - binding status unverified, excluded from default retrieval",
}


# ── title lookup ─────────────────────────────────────────────────────────────
def load_titles() -> dict[str, dict]:
    """filename -> {title, url, bucket} from the mapping CSVs."""
    out: dict[str, dict] = {}
    hc = METADATA / "Healthcare" / "healthcare_corpus_mapping.csv"
    if hc.exists():
        for r in csv.DictReader(hc.open(encoding="utf-8-sig")):
            if r.get("CorpusStatus", "in corpus") != "in corpus":
                continue
            t = (r.get("ResolvedTitle") or r.get("DocumentTitle") or "").strip()
            if r.get("FileName"):
                out[r["FileName"]] = {"title": t, "url": "", "bucket": r.get("Bucket", "")}
    else:
        print(f"  ! missing {hc} - healthcare citations will fall back to filenames")

    rr = METADATA / "RERA" / "rera_corpus_mapping.csv"
    if rr.exists():
        for r in csv.DictReader(rr.open(encoding="utf-8-sig")):
            t = (r.get("DocumentTitle") or "").strip()
            if r.get("FileName"):
                out[r["FileName"]] = {"title": t, "url": r.get("SourceUrl", "").strip(),
                                      "bucket": r.get("Bucket", "")}
    else:
        print(f"  ! missing {rr} - RERA citations will fall back to filenames")
    return out


# ── text extraction, with the OCR sidecar fallback ───────────────────────────
def ocr_sidecar_for(pdf: Path) -> Path:
    """Sidecar path for a PDF: <stem cut to 60> + "_" + sha1(full stem)[:8] + ".txt"

    The hash suffix is not decoration. An earlier version of this function used
    pdf.stem[:70] with no disambiguator, and three distinct tier-A Karnataka
    circulars -

        Imposing penalty ... Annual Audit Report for the Financial Year 2022-23
        Imposing penalty ... Annual Audit Report for the Financial Year 2023-24
        Imposing penalty ... Annual Audit Report for the Financial Year 2024-25

    - are character-identical up to position 70. All three resolved to a single
    sidecar, so two of them were indexed carrying a different year's penalty
    figures: a wrong answer the tier system cannot catch, because the authority
    metadata was correct and only the text was another document's. Any truncating
    scheme must be disambiguated by a hash of the FULL stem.
    """
    digest = hashlib.sha1(pdf.stem.encode("utf-8")).hexdigest()[:8]
    return OCR_TEXT / f"{pdf.stem[:60]}_{digest}.txt"


def extract_text(pdf: Path) -> tuple[str, str]:
    """Return (text, source) where source is 'pdf', 'ocr_sidecar' or 'none'."""
    text = ""
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(pdf))
        text = "\n".join((p.extract_text() or "") for p in reader.pages)
    except Exception as exc:                       # corrupt or not a PDF
        print(f"  ! unreadable: {pdf.name} ({exc})")
        text = ""

    if len(text.strip()) >= MIN_TEXT_CHARS:
        return text, "pdf"

    side = ocr_sidecar_for(pdf)
    if side.exists():
        ocr = side.read_text(encoding="utf-8", errors="ignore")
        if len(ocr.strip()) >= MIN_TEXT_CHARS:
            return ocr, "ocr_sidecar"

    return "", "none"


def is_real_pdf(p: Path) -> bool:
    """Guard against HTML pages saved with a .pdf extension."""
    try:
        with p.open("rb") as fh:
            return fh.read(5) == b"%PDF-"
    except Exception:
        return False


def clear_index() -> bool:
    """Delete the existing index. Called BEFORE extraction, deliberately.

    On 27 Sep a --rebuild ran the full 40-minute extraction, produced 25,947
    chunks, and only then called shutil.rmtree, which died with

        PermissionError: [WinError 5] Access is denied: ...\\chroma_db\\<uuid>

    because something (OneDrive syncing the 231MB index, which lives inside the
    OneDrive tree) held a handle on the folder. rmtree deletes as it walks, so
    it had already emptied the vector-segment directory before raising: the
    index was left with its sqlite metadata and no vectors, and 40 minutes of
    extraction was thrown away.

    Failing here costs seconds. Failing after extraction costs the whole run.
    """
    if not INDEX_DIR.exists():
        return True
    for attempt in (1, 2, 3):
        try:
            shutil.rmtree(INDEX_DIR)
            print("existing index removed.")
            return True
        except PermissionError as exc:
            if attempt < 3:
                print(f"  index locked, retrying in 3s ({attempt}/3)...")
                time.sleep(3)
                continue
            print()
            print("=" * 74)
            print("ERROR: cannot delete the existing index - something holds a handle on it.")
            print(f"  {exc}")
            print()
            print("Stopping now, before the 40-minute extraction, so no work is wasted.")
            print("Fix, in order of likelihood:")
            print("  1. Close every Python / Streamlit process and any Explorer window")
            print("     sitting in chroma_db.")
            print("  2. Pause OneDrive sync (right-click the cloud icon -> Pause syncing).")
            print("     The index lives inside the OneDrive tree; OneDrive locks files it")
            print("     is uploading. This is the usual cause.")
            print(f"  3. Delete it by hand:  Remove-Item -Recurse -Force \"{INDEX_DIR}\"")
            print("=" * 74)
            return False
    return False


# ── main ─────────────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="report only, build nothing")
    ap.add_argument("--rebuild", action="store_true", help="delete the existing index first")
    args = ap.parse_args()

    if not CORPUS.exists():
        print(f"ERROR: corpus not found at {CORPUS}")
        return 1

    print(f"corpus : {CORPUS}")
    print(f"index  : {INDEX_DIR}")
    print()

    # Clear the index FIRST, not after extraction. See clear_index().
    if args.rebuild and not args.dry_run:
        if not clear_index():
            return 1

    titles = load_titles()
    print(f"titles loaded: {len(titles)}")

    total_pdfs = sum(1 for _ in CORPUS.glob("*/*/*.pdf"))
    print(f"documents to process: {total_pdfs}")
    print("\nextracting text (the slow part - progress below)\n")

    # ── collect documents ────────────────────────────────────────────────────
    rows, docs = [], []
    done = 0
    splitter = None
    if not args.dry_run:
        from langchain_text_splitters import RecursiveCharacterTextSplitter
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP,
            separators=["\n\n", "\n", ". ", " ", ""],
        )
        from langchain_core.documents import Document

    for domain_dir in sorted(d for d in CORPUS.iterdir() if d.is_dir() and not d.name.startswith("_")):
        domain = domain_dir.name
        for bucket_dir in sorted(b for b in domain_dir.iterdir() if b.is_dir()):
            bucket = bucket_dir.name
            tier = TIER.get(bucket)
            if tier is None:
                print(f"  ! unknown bucket '{bucket}' - skipped (add it to TIER)")
                continue

            for pdf in sorted(bucket_dir.glob("*.pdf")):
                # requirement 4: nothing outside 02_Corpus may ever be indexed
                assert not any(f in str(pdf) for f in FORBIDDEN), f"FORBIDDEN PATH: {pdf}"

                rec = {"domain": domain, "bucket": bucket, "tier": tier,
                       "file": pdf.name, "title": "", "text_source": "", "chars": 0,
                       "chunks": 0, "status": ""}

                if not is_real_pdf(pdf):
                    rec.update(status="SKIPPED - not a PDF (HTML or corrupt)")
                    rows.append(rec)
                    continue

                done += 1
                print(f"  [{done:3d}/{total_pdfs}] {bucket[:22]:24s} {pdf.name[:46]:48s}",
                      end="", flush=True)

                text, src = extract_text(pdf)
                meta = titles.get(pdf.name, {})
                title = meta.get("title") or pdf.stem
                rec.update(title=title, text_source=src, chars=len(text.strip()))
                tag = {"pdf": "text", "ocr_sidecar": "OCR ", "none": "EMPTY"}.get(src, src)
                print(f" {tag} {len(text.strip()):7d}ch", flush=True)

                if not text.strip():
                    rec["status"] = "SKIPPED - no text (image-only, no OCR sidecar)"
                    rows.append(rec)
                    continue

                rec["status"] = "indexed"
                if not args.dry_run:
                    chunks = splitter.split_text(text)
                    rec["chunks"] = len(chunks)
                    for i, chunk in enumerate(chunks):
                        docs.append(Document(
                            page_content=chunk,
                            metadata={
                                "domain": domain,
                                "bucket": bucket,
                                "tier": tier,
                                "tier_label": TIER_LABEL[tier],
                                "title": title,
                                "source_file": pdf.name,
                                "source_url": meta.get("url", ""),
                                "text_source": src,
                                "chunk_index": i,
                            },
                        ))
                rows.append(rec)

    # ── report ───────────────────────────────────────────────────────────────
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    with REPORT.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, ["domain", "bucket", "tier", "file", "title",
                                "text_source", "chars", "chunks", "status"])
        w.writeheader()
        w.writerows(rows)

    indexed = [r for r in rows if r["status"] == "indexed"]
    skipped = [r for r in rows if r["status"] != "indexed"]

    print()
    print("=" * 74)
    print(f"documents found   : {len(rows)}")
    print(f"  indexed         : {len(indexed)}")
    print(f"  skipped         : {len(skipped)}")
    print()
    print("text source:")
    for k, v in Counter(r["text_source"] for r in indexed).items():
        print(f"  {k or '(none)':16s} {v:4d}")
    print()
    print("by domain and tier:")
    for (d, t), v in sorted(Counter((r["domain"], r["tier"]) for r in indexed).items()):
        print(f"  {d:12s} tier {t}  {v:4d} docs")

    if skipped:
        print()
        print("SKIPPED - these contribute nothing to retrieval:")
        for r in skipped:
            print(f"  [{r['bucket'][:26]:28s}] {r['file'][:52]:54s} {r['status']}")

    print()
    print(f"report written: {REPORT}")

    if args.dry_run:
        print("\ndry run - no index built.")
        return 0

    # ── build the index ──────────────────────────────────────────────────────
    print(f"\nchunks to embed: {len(docs)}")
    if not docs:
        print("ERROR: nothing to index.")
        return 1

    # The index was already cleared at the top of main(), before extraction.
    # Anything here now would be a folder recreated during the run.
    if args.rebuild and INDEX_DIR.exists() and not clear_index():
        return 1

    print(f"loading embedding model {EMBED_MODEL} (first run downloads ~130MB)...")
    from langchain_huggingface import HuggingFaceEmbeddings
    from langchain_chroma import Chroma

    embeddings = HuggingFaceEmbeddings(
        model_name=EMBED_MODEL,
        encode_kwargs={"normalize_embeddings": True},   # required for BGE cosine similarity
    )

    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    print("embedding and persisting (this is the slow part)...")
    store = Chroma.from_documents(
        documents=docs,
        embedding=embeddings,
        collection_name=COLLECTION,
        persist_directory=str(INDEX_DIR),
    )

    count = store._collection.count()
    print(f"\nindexed {count} chunks into {INDEX_DIR}")

    # ── verification ─────────────────────────────────────────────────────────
    print("\nverification")
    print("-" * 74)
    checks = [
        ("Healthcare tier A", {"$and": [{"domain": "Healthcare"}, {"tier": "A"}]}),
        ("RERA tier A",       {"$and": [{"domain": "RERA"}, {"tier": "A"}]}),
        ("RERA Karnataka",    {"bucket": "02_Karnataka_RERA"}),
        ("Draft / proposed",  {"tier": "D"}),
    ]
    for label, flt in checks:
        n = len(store.get(where=flt, limit=100000)["ids"])
        print(f"  {label:22s} {n:6d} chunks")

    hits = store.similarity_search(
        "penalty for late submission of quarterly progress reports",
        k=3, filter={"domain": "RERA"})
    print("\n  sample RERA retrieval:")
    for h in hits:
        print(f"    [tier {h.metadata['tier']}] {h.metadata['title'][:62]}")

    print("\ndone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
