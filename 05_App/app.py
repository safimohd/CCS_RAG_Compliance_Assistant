"""
Regulatory Compliance Assistant — Streamlit interface.

    streamlit run app.py

Deployed twice from this one file, once per domain. `APP_DOMAIN` in the app's
secrets ("Healthcare" or "RERA") locks the deployment to a single domain and
hides the domain selector, so a reviewer only ever sees their own subject.
Unset locally, so the selector appears for development.

Audience note, 9 Oct: this is shared with domain experts, not with the people
who built it. Retrieval diagnostics — similarity scores, passage counts, tier
letters, reasoning effort, internal folder names — were useful while debugging
and are noise to a reader checking a regulation. They have been removed from
the interface and kept in the code. What a reviewer needs instead is whether a
source binds them, what the document is, and the ability to read the passage
itself.

Behaviour that exists because of measured failures (27 Sep):
  * Scope filters      - four Karnataka penalty circulars are character-identical
                         apart from the financial year and sit within 0.03 cosine;
                         ~20 CEA standards templates repeat clauses per facility
                         type. The distinguishing fact is categorical, so it
                         belongs in a filter, not a similarity score.
  * Adaptive reasoning - lowering reasoning_effort cut latency 60s -> 30s AND
                         improved the FY2023-24 answer; less reasoning meant less
                         gap-filling. Chosen per query, not shown to the user.
  * Answer cache       - retrieval is local and free, generation is the only API
                         spend, so repeats are never billed twice.
  * Daily cap          - the link is forwarded onward without warning and runs on
                         a key we do not own. See daily_usage().

NOTE: none of this belongs in run_evaluation.py. The models are fixed, and the
RAG-vs-base comparison is only valid if both sides run identically.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import sys
import tarfile
import urllib.request
from datetime import date, datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))
from pathlib import Path

# Chroma requires SQLite >= 3.35. Streamlit Community Cloud's image ships an
# older system sqlite3, so Chroma fails on first boot there every time. Shim in
# pysqlite3 BEFORE anything imports chromadb. pysqlite3-binary is Linux-only and
# absent locally on Windows, where system sqlite3 is new enough - hence the pass.
try:                                                        # pragma: no cover
    __import__("pysqlite3")
    sys.modules["sqlite3"] = sys.modules.pop("pysqlite3")
except Exception:
    pass

import streamlit as st
import streamlit.components.v1 as _stc

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rag_query import (  # noqa: E402
    COLLECTION, EMBED_MODEL, INDEX_DIR, LLM_BASE_URL, LLM_MODEL,
    SYSTEM_PROMPT, format_sources, index_titles, is_narrow_doc, load_env,
    retrieve,
)
from rag_query import resolve_titles as rag_query_resolve  # noqa: E402

load_env()


def _secret(key: str, default: str = "") -> str:
    """.env locally, Streamlit Secrets when deployed. .env wins where both exist.

    Reading st.secrets raises when no secrets file exists, which is the normal
    local case, so this must never be allowed to fail.
    """
    val = os.getenv(key)
    if val:
        return val.strip()
    try:
        if key in st.secrets:
            return str(st.secrets[key]).strip()
    except Exception:
        pass
    return default


# ── deployment configuration ─────────────────────────────────────────────────
DOMAINS = ("Healthcare", "RERA")
_locked = _secret("APP_DOMAIN")
LOCKED_DOMAIN = _locked if _locked in DOMAINS else None

BASE_EFFORT = _secret("LLM_REASONING_EFFORT", "low")
INDEX_URL = _secret("INDEX_URL")
# The link is forwarded onward without warning, on a key we do not own, so the
# worst case has to be a known number rather than an open tap.
DAILY_LIMIT = int(_secret("DAILY_QUESTION_LIMIT", "100") or 100)
CONTACT = _secret("CONTACT_EMAIL")

# Cosine similarity threshold below which results are treated as too weak to
# generate a confident answer. Value is the cosine (after to_cosine conversion),
# so 0.0 means orthogonal vectors and 1.0 means identical. Empirically, queries
# that are genuinely on-topic score 0.40+; off-topic queries tend to score below
# 0.30 even for the best chunk returned.
LOW_CONFIDENCE_THRESHOLD = 0.35

APP_DIR = Path(__file__).resolve().parent
CACHE_FILE = APP_DIR / ".answer_cache.json"
USAGE_FILE = APP_DIR / ".usage.json"
METADATA_REPORT = APP_DIR.parent / "03_Metadata" / "ingestion_report.csv"

DOMAIN_META = {
    "Healthcare": {
        "title": "Healthcare Regulation Assistant",
        "lede": "Clinical establishment standards, medical education and NMC regulation, "
                "PM-JAY, telemedicine, and health-data privacy.",
        "docs": 259,
        "facts": ["259 source documents", "Central regulation", "Official sources only"],
        "placeholder": "Ask about Indian healthcare regulation…",
    },
    "RERA": {
        "title": "Karnataka RERA Assistant",
        "lede": "Your compliance guide for Karnataka's real estate sector. Covers the "
                "Real Estate (Regulation and Development) Act 2016, Karnataka RERA Rules 2017, "
                "K-RERA circulars, notifications and guidelines. Ask as a promoter, "
                "home buyer, agent, or legal professional.",
        "docs": 91,
        "facts": ["91 official documents", "K-RERA · Central RERA Act", "Promoters · Buyers · Agents"],
        "placeholder": "Ask about project registration, refunds, penalties, timelines…",
    },
}

# Plain language. A reviewer needs to know whether a source binds them - not
# which tier letter we filed it under.
STATUS = {
    "A": ("Binding regulation", "#0F6B3A"),
    "B": ("Binding — sector rules", "#1B4F8C"),
    "C": ("Guidance — not binding", "#9A6A12"),
    "D": ("Draft — not in force", "#B4521A"),
    "E": ("Status unverified", "#8C1123"),
}

# Role-based scope filter for RERA. Each entry maps a stakeholder role to the
# title substrings that identify documents specific to that role. The empty
# list means "no filter — search the whole corpus." The same needles drive
# both the scope count shown in the sidebar and the Chroma title filter.
RERA_ROLES = {
    "All stakeholders": [],
    "Home Buyer / Allottee": [
        "allotment",
        "conciliation",
        "monetary",
        "national building code",
        "ಪುನರಾವೃತ",          # Kannada FAQ documents
    ],
    "Promoter / Developer": [
        "quarterly",
        "annual audit",
        "audit report",
        "bank account",
        "extension",
        "penalty",
        "promoter",
        "advertisement",
        "change of project",
        "transferring",
        "real estate agent",
        "ongoing project",
        "fee",
    ],
    "Legal & Dispute Resolution": [
        "appellate",
        "conciliation",
        "monetary",
        "k-reat",
    ],
}

HEALTHCARE_FACILITIES = {
    "Any establishment": [],
    "Hospital — Level 2": ["hospital(level 2", "hospital (level 2"],
    "Hospital — Level 3": ["hospital(level 3", "hospital (level 3"],
    "Clinic or polyclinic": ["clinic_polyclinic", "clinic/polyclinic"],
    "Mobile clinic": ["mobile clinic", "mobile dental van"],
    "Dental": ["dental lab", "mobile dental"],
    "Cosmetology or aesthetic": ["cosmetology", "hair transplant"],
    "Physiotherapy centre": ["physiotherapy"],
    "Blood centre": ["blood centre", "blood transfusion"],
    "Mortuary or sample collection": ["mortuary", "sample collection"],
}

# What "narrowing the search" means - FACILITY_MARKERS and is_narrow_doc() -
# lives in rag_query.py, because it is a fact about the corpus rather than about
# the interface, and measure_scope.py and audit_scope.py read the same copy.


# ── display ──────────────────────────────────────────────────────────────────
_KB_RE = re.compile(r"\s*\(\s*[\d.]+\s*[KMG]B\s*\)\s*$")
_LEAD_RE = re.compile(r"^(?:\(?[ivx]+\)|\d+(?:\.[a-z])?\.?)\s*[|.)]?\s+", re.I)
# Page furniture inside a pipe-fragmented title: a bare number, a page marker,
# or a numbered heading such as "1. What is chikungunya".
_FURNITURE_RE = re.compile(r"^(?:\d+\s*[.)]?\s|\d+$|page\b|contents?\b|annexure\b)", re.I)


_GAZETTE_RE = re.compile(r"^\s*(\d{3,5})\s+GI/(\d{4})\b")
_HASH_SUFFIX_RE = re.compile(r"_[0-9a-f]{6,}$", re.I)
_GENERIC = {"preamble", "introduction", "contents", "content", "background",
            "foreword", "annexure", "index", "facts"}


def _from_filename(source_file: str) -> str:
    """A readable name from the filename, when there is one in there.

    Most source filenames are content hashes and useless. A few are the real
    document name. Only return one that looks like words.
    """
    stem = re.sub(r"\.(pdf|docx?|txt)$", "", (source_file or ""), flags=re.I)
    stem = _HASH_SUFFIX_RE.sub("", stem).replace("_", " ").strip()
    stem = _LEAD_RE.sub("", stem).strip()
    letters = sum(c.isalpha() for c in stem)
    if len(stem) < 12 or " " not in stem or letters < len(stem) * 0.6:
        return ""
    return stem


def clean_title(title: str, source_file: str = "") -> str:
    """Tidy a document title for display.

    Titles come from PDF metadata and OCR, so they carry artefacts a reviewer
    should never see: a trailing file size ("Hospital(Level 2) (832.09 KB )",
    12 documents), pipe runs where OCR broke a cover page into fragments
    ("1 | OPERATIONAL | GUIDELINES FOR | CLINICAL | ESTABLISHMENTS ACT"), and
    leading enumerators from a directory listing ("(i) ", "2.a. ").

    The underlying title is never changed - only what is shown. Retrieval,
    scoping and the authority tiers all still key on the real string.
    """
    t = (title or "").strip()
    t = _KB_RE.sub("", t)

    # Gazette notifications first, before the leading number is stripped: these
    # titles are OCR'd Hindi mastheads and unreadable either way, but the
    # gazette number IS the official citation, so show that instead of soup.
    gz = _GAZETTE_RE.match(t)
    if gz:
        return f"Gazette of India notification {gz.group(1)} GI/{gz.group(2)}"

    t = _LEAD_RE.sub("", t)
    if "|" in t:
        # Take the LEADING fragments, not the longest one. Tried longest first
        # and it was worse than doing nothing: on the CEA operational
        # guidelines it picked a table-of-contents line, and on the chikungunya
        # factsheet a sentence from the body. A fragmented cover page puts the
        # title at the top, so read from the start and stop at the first
        # fragment that is page furniture (a bare number, a numbered heading)
        # or long enough to be prose.
        parts = [p.strip() for p in t.split("|") if p.strip()]
        kept: list[str] = []
        for p in parts:
            if _FURNITURE_RE.match(p) or len(p) > 60:
                break
            kept.append(p)
            if sum(len(x) + 1 for x in kept) > 90:
                break
        t = " ".join(kept) if kept else parts[0]
    t = re.sub(r"\s{2,}", " ", t).strip(" .,-–—|")

    # A cover page whose first fragment is boilerplate ("Preamble") leaves a
    # title that says nothing. The filename sometimes carries the real name.
    if len(t) < 15 or t.lower() in _GENERIC:
        alt = _from_filename(source_file)
        if alt:
            return alt
    return t or (title or "").strip()

EXAMPLES = {
    "Healthcare": [
        "How long must a clinical establishment retain patient medical records?",
        "What are the requirements for a hospital to be empanelled under PM-JAY?",
        "Which medicines may be prescribed in a telemedicine consultation?",
        "What are the minimum staffing requirements for a Level 2 hospital?",
        "What must be reported to the district authorities, and how often?",
        "What qualifications are required for faculty in a medical college?",
    ],
    "RERA": [
        "What share of money collected from allottees must go into a separate account, "
        "and what can it be spent on?",
        "What penalty applies if the annual audit report is not submitted?",
        "When must a promoter file quarterly progress reports?",
        "Which projects do not need to be registered under the Act?",
        "On what grounds can a project registration be extended?",
        "How does a buyer file a complaint, and what does it cost?",
    ],
}

# When a scope is chosen, the examples follow it. Narrowing the search used to
# change nothing you could see until you had already asked something, which made
# the control feel inert. These questions also happen to be the headings the CEA
# standards are organised under, so the answers genuinely differ by facility.
FACILITY_NOUN = {
    "Hospital — Level 2": "a Level 2 hospital",
    "Hospital — Level 3": "a Level 3 hospital",
    "Clinic or polyclinic": "a clinic or polyclinic",
    "Mobile clinic": "a mobile clinic",
    "Dental": "a dental lab or mobile dental van",
    "Cosmetology or aesthetic": "a cosmetology or hair transplant centre",
    "Physiotherapy centre": "a physiotherapy centre",
    "Blood centre": "a blood centre",
    "Mortuary or sample collection": "a mortuary or sample collection centre",
}

FACILITY_TEMPLATES = [
    "What are the minimum infrastructure and space requirements for {n}?",
    "What staff must {n} employ, and with what qualifications?",
    "What equipment must {n} have?",
    "How long must {n} keep patient records?",
    "How is {n} registered under the Clinical Establishments Act?",
    "What must {n} report to the district authorities?",
]

# Per-role example questions for RERA. Shown when a role is selected so the
# question buttons feel relevant rather than generic. The "All stakeholders"
# bucket reuses the EXAMPLES["RERA"] list defined above.
RERA_ROLE_EXAMPLES = {
    "Home Buyer / Allottee": [
        "What refund am I entitled to if the promoter delays giving possession?",
        "How do I file a complaint against a promoter with K-RERA?",
        "What must the allotment letter include under K-RERA rules?",
        "How does the K-RERA conciliation process work for buyer-promoter disputes?",
        "What project information must a promoter display on the K-RERA portal?",
        "What building-code standards must a residential project meet?",
    ],
    "Promoter / Developer": [
        "When must quarterly progress reports be submitted, and what must they contain?",
        "What penalty applies if the annual audit report is not submitted on time?",
        "What share of money collected from allottees must go into the separate bank account?",
        "On what grounds can a project registration be extended under K-RERA?",
        "What advertising guidelines apply before a project is registered?",
        "What is required to register as a real estate agent under K-RERA?",
    ],
    "Legal & Dispute Resolution": [
        "How is a complaint filed before K-RERA, and what are the filing fees?",
        "What is the procedure for recovering monetary relief from a defaulting promoter?",
        "How does the K-RERA Conciliation and Dispute Resolution Cell operate?",
        "On what grounds can an appeal be filed before K-REAT?",
        "What penalties can K-RERA impose on a promoter for non-compliance?",
        "What is the process for transferring a promoter's rights and liabilities?",
    ],
}


# ── resources ────────────────────────────────────────────────────────────────
@st.cache_resource(show_spinner=False)
def ensure_index() -> bool:
    """Fetch the Chroma index if it is not already on disk.

    The index is ~290MB. GitHub rejects files over 100MB, so it cannot live in
    the repository; it is published as a Release asset and pulled on first boot.
    Rebuilding on the server is not an option - that is a six-hour job.
    """
    if INDEX_DIR.exists() and any(INDEX_DIR.iterdir()):
        return True
    if not INDEX_URL:
        return False
    archive = INDEX_DIR.parent / "_index_download.tar.gz"
    try:
        import requests as _req
        with _req.get(INDEX_URL, stream=True, timeout=(15, 300),
                      allow_redirects=True) as r:
            r.raise_for_status()
            with open(archive, "wb") as fh:
                for chunk in r.iter_content(chunk_size=1 << 20):
                    fh.write(chunk)
        with tarfile.open(archive, "r:gz") as tar:
            try:
                tar.extractall(INDEX_DIR.parent, filter="data")
            except TypeError:          # filter= added in Python 3.12
                tar.extractall(INDEX_DIR.parent)
        archive.unlink(missing_ok=True)
        return INDEX_DIR.exists() and any(INDEX_DIR.iterdir())
    except Exception as exc:                                 # noqa: BLE001
        archive.unlink(missing_ok=True)
        st.error(f"The document library could not be loaded: {exc}")
        return False


@st.cache_resource(show_spinner="Preparing the document library…")
def get_store():
    # The existence check lives OUTSIDE this function on purpose:
    # @st.cache_resource would pin a None return for the life of the process.
    from langchain_chroma import Chroma
    from langchain_huggingface import HuggingFaceEmbeddings
    emb = HuggingFaceEmbeddings(model_name=EMBED_MODEL,
                                encode_kwargs={"normalize_embeddings": True})
    return Chroma(collection_name=COLLECTION, embedding_function=emb,
                  persist_directory=str(INDEX_DIR))


@st.cache_resource
def get_client():
    key = _secret("OPENAI_API_KEY")
    if not key:
        return None
    from openai import OpenAI
    return OpenAI(api_key=key, base_url=LLM_BASE_URL) if LLM_BASE_URL else OpenAI(api_key=key)


@st.cache_resource
def corpus_titles() -> dict:
    """Document titles per domain, retrievable tiers only.

    Used to say how many documents a scope selection actually covers. A control
    whose effect you cannot see is a control nobody trusts - and when the honest
    answer is "one document", saying so is better than implying a wide search.
    Degrades to no counts at all if the report is missing.
    """
    import csv
    out = {"Healthcare": [], "RERA": []}
    try:
        with METADATA_REPORT.open(encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                if row.get("tier") in ("A", "B", "C") and (row.get("chunks") or "0") != "0":
                    if row.get("domain") in out:
                        out[row["domain"]].append((row.get("title") or "").lower())
    except Exception:
        pass
    return out


@st.cache_resource(show_spinner=False)
def scope_universe(domain: str) -> list[str]:
    """The document titles retrieval can actually match, from the index itself.

    The scope filter is now a metadata constraint inside the Chroma query, so
    the titles it is built from have to be the exact strings the index holds.
    Falls back to the ingestion report only if the index cannot be read, in
    which case the count may be shown but no scope filter is applied.
    """
    try:
        if ensure_index():
            return index_titles(get_store(), domain)
    except Exception:                                           # noqa: BLE001
        pass
    return []


def resolve_scope(domain: str, needles: list) -> tuple[list[str], list[str]]:
    """Scope selection -> (documents specific to it, documents that always apply).

    Both halves are in scope. The second half is why scoping to a financial
    year no longer hides the Act.
    """
    if not needles:
        return [], []
    return rag_query_resolve(scope_universe(domain), needles,
                             lambda t: is_narrow_doc(domain, t))


def scope_count(domain: str, needles: list) -> tuple[int | None, int]:
    """(documents specific to the selection, documents that always apply).

    Counted from the same title list the filter is built from, so the number
    shown and the documents searched can never disagree.
    """
    universe = scope_universe(domain)
    if not universe:                       # index unreadable - fall back to CSV
        titles = corpus_titles().get(domain) or []
        if not titles:
            return None, 0
        if not needles:
            return len(titles), 0
        return sum(1 for t in titles if any(n in t for n in needles)), 0
    if not needles:
        return len(universe), 0
    specific, general = resolve_scope(domain, needles)
    return len(specific), len(general)


def scope_caption(n: int | None, whole: str, general: int = 0) -> str:
    """One plain sentence. No counts a reader cannot act on.

    An earlier version said "4 documents cover this. 143 more apply
    throughout." The 143 is true and useless: it is an artefact of how the
    corpus is organised, not something a reviewer can do anything with.
    """
    if n is None:
        return ""
    if whole == "all":
        return f"Searching all {n} documents."
    plus = " Plus the rules that apply to every case." if general else ""
    if n == 0:
        if general:
            return "Nothing is written specifically for this. " \
                   "Searching the rules that apply to every case."
        return "No documents in the library cover this. Choose another option."
    if n == 1:
        return f"1 document is specific to this.{plus}"
    return f"{n} documents are specific to this.{plus}"


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_json(path: Path, data: dict) -> None:
    try:
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass  # an unwritable file must never break an answer


@st.cache_resource
def answer_cache() -> dict:
    """Question -> answer, persisted to disk so it survives a restart."""
    return _read_json(CACHE_FILE)


def cache_put(key: str, value: dict) -> None:
    store = answer_cache()
    store[key] = value
    _write_json(CACHE_FILE, store)


def cache_key(question: str, domain: str, scope: str, drafts: bool,
              review: bool, effort: str) -> str:
    raw = (f"{LLM_MODEL}|{effort}|{domain}|{scope}|{drafts}|{review}"
           f"|{question.strip().lower()}")
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def daily_usage() -> int:
    """Generations charged to the key today.

    This link gets forwarded on without us being told, and it runs on the
    professor's OpenAI key. A public URL with no ceiling is an open tap; this
    makes the worst case a number we chose. Cached answers cost nothing and are
    deliberately not counted.
    """
    data = _read_json(USAGE_FILE)
    return int(data.get(date.today().isoformat(), 0))


def bump_usage() -> None:
    today = date.today().isoformat()
    data = _read_json(USAGE_FILE)
    data = {today: int(data.get(today, 0)) + 1}   # keep only today
    _write_json(USAGE_FILE, data)


# ── adaptive reasoning (internal; never shown) ───────────────────────────────
def choose_effort(results, question: str) -> str:
    """Pick reasoning effort from retrieval signals.

    Cost on a reasoning model scales with reasoning tokens, so this is the axis
    that drives spend. Routing EFFORT rather than switching MODELS keeps one
    model, which matters: the models are fixed, and the RAG-vs-base comparison
    is only meaningful if both sides run identically.

    Authority is not a difficulty signal on its own - a binding Act can answer a
    trivial lookup. What predicts difficulty is weak similarity, many documents
    to reconcile, non-binding material needing careful handling, and mixed
    authority that may conflict.
    """
    if not results:
        return "low"
    top = max(r["similarity"] for r in results)
    docs = len({r["source_file"] for r in results})
    tiers = {r["tier"] for r in results}

    score = 0
    if top < 0.75:
        score += 2
    elif top < 0.85:
        score += 1
    if docs >= 4:
        score += 1
    if tiers & {"D", "E"}:
        score += 2
    if "A" in tiers and "C" in tiers:
        score += 1
    if len(question) > 180 or question.count("?") > 1:
        score += 1
    return ("minimal" if score == 0 else "low" if score <= 1
            else "medium" if score <= 3 else "high")


# ── presentation ─────────────────────────────────────────────────────────────
CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Source+Serif+4:opsz,wght@8..60,600;8..60,700&display=swap');
:root{
  --ink:#0E2439; --muted:#6B7C93; --line:#E3E9F2; --paper:#F6F8FC;
  --card:#FFFFFF; --brand:#1B4F8C; --brand-2:#2E6FB7; --gold:#B8860B;
}
html, body, [class*="css"] { font-family:'Inter',-apple-system,'Segoe UI',sans-serif; }
.stApp { background:var(--paper); }
.block-container { padding-top:1.1rem; padding-bottom:3rem; max-width:1100px; }

.rc-hero{
  background:linear-gradient(135deg,#102D4D 0%,#1B4F8C 55%,#2E6FB7 100%);
  border-radius:14px; padding:1.5rem 1.7rem 1.3rem; color:#fff;
  box-shadow:0 10px 28px -14px rgba(16,45,77,.55); margin-bottom:1.1rem;
}
.rc-hero h1{
  font-family:'Source Serif 4',Georgia,serif; font-weight:700;
  font-size:1.7rem; line-height:1.18; margin:0 0 .35rem; letter-spacing:-.015em;
}
.rc-hero p{ margin:0; font-size:.86rem; line-height:1.55; color:#CFE0F2; max-width:72ch; }
.rc-stats{ display:flex; flex-wrap:wrap; gap:.45rem; margin-top:.95rem; }
.rc-stat{
  background:rgba(255,255,255,.12); border:1px solid rgba(255,255,255,.2);
  border-radius:999px; padding:.22rem .7rem; font-size:.71rem; font-weight:500; color:#EAF2FB;
}
.rc-kicker{
  text-transform:uppercase; letter-spacing:.09em; font-size:.66rem;
  font-weight:700; color:var(--gold); margin:.2rem 0 .45rem;
}
.rc-scope{
  background:var(--card); border:1px solid var(--line); border-left:3px solid var(--gold);
  border-radius:9px; padding:.55rem .85rem; font-size:.79rem; color:#3C4F66;
  margin-bottom:.85rem;
}
.rc-chip{
  display:inline-block; padding:2px 9px; border-radius:999px; font-size:.68rem;
  font-weight:650; color:#fff; white-space:nowrap;
}
.rc-note{ color:var(--muted); font-size:.74rem; }
.rc-basis{
  color:#3C4F66; font-size:.82rem; padding:.45rem 0 .1rem;
}
.rc-src{
  background:var(--card); border:1px solid var(--line); border-radius:9px;
  padding:.75rem .9rem; margin-bottom:.6rem; transition:box-shadow .15s ease;
}
.rc-src:hover{ box-shadow:0 6px 18px -10px rgba(16,45,77,.35); }
.rc-src-t{ font-weight:640; font-size:.88rem; line-height:1.35; color:var(--ink); }
.rc-quote{
  font-size:.78rem; color:#3C4F66; white-space:pre-wrap; margin-top:.5rem;
  padding:.5rem .65rem; border-left:2px solid var(--line); background:#FAFCFF;
  border-radius:0 5px 5px 0; line-height:1.5;
}
.stButton > button{
  background:var(--card); border:1px solid var(--line); border-radius:9px;
  color:var(--ink); font-weight:500; font-size:.82rem; text-align:left;
  padding:.65rem .85rem; width:100%; transition:all .14s ease; line-height:1.4;
}
.stButton > button:hover{
  border-color:var(--brand-2); color:var(--brand);
  box-shadow:0 6px 16px -9px rgba(27,79,140,.55); transform:translateY(-1px);
}
[data-testid="stSidebar"]{ background:#0E2439; }
[data-testid="stSidebar"] *{ color:#D9E4F1; }
/* Sidebar selectbox — match the dark navy sidebar in light mode */
[data-testid="stSidebar"] [data-baseweb="select"],
[data-testid="stSidebar"] [data-baseweb="select"] > div,
[data-testid="stSidebar"] [data-baseweb="select"] > div > div,
[data-testid="stSidebar"] [data-baseweb="select"] > div > div > div,
[data-testid="stSidebar"] [data-testid="stSelectbox"] > div > div,
[data-testid="stSidebar"] [data-testid="stSelectbox"] > div > div > div {
  background:#1A3352 !important;
  border-color:#2E4E6E !important;
  color:#D9E4F1 !important;
}
[data-testid="stSidebar"] [data-baseweb="select"] span,
[data-testid="stSidebar"] [data-baseweb="select"] div,
[data-testid="stSidebar"] [data-baseweb="select"] svg { color:#D9E4F1 !important; fill:#D9E4F1 !important; }
/* Sidebar "Start a new conversation" button — white text on visible dark bg */
[data-testid="stSidebar"] .stButton > button{
  background:#1A3352 !important; color:#D9E4F1 !important;
  border:1px solid #2E4E6E !important; border-radius:9px !important;
}
.rc-brand{
  font-family:'Source Serif 4',Georgia,serif; font-size:1.02rem; font-weight:700;
  color:#fff; line-height:1.25;
}
.rc-brand-sub{
  font-size:.67rem; color:#8FA8C4; letter-spacing:.05em;
  text-transform:uppercase; margin-top:.2rem;
}
.rc-side-h{
  text-transform:uppercase; letter-spacing:.1em; font-size:.65rem; font-weight:700;
  color:#7E99BA !important; margin:.15rem 0 .35rem;
}
.rc-how-to{
  background:rgba(255,255,255,.07); border:1px solid rgba(255,255,255,.13);
  border-radius:8px; padding:.6rem .8rem; margin-bottom:.6rem;
  font-size:.78rem; line-height:1.55; color:#CFE0F2;
}
.rc-how-to b{ color:#EAF2FB; }
.rc-clear-row{
  display:flex; justify-content:flex-end; margin-bottom:.4rem;
}
.rc-fb-thanks{
  font-size:.72rem; color:#1a7f4b; font-weight:600; margin-left:.3rem;
  padding:2px 6px; background:#e6f4ec; border-radius:6px; vertical-align:middle;
}
/* Mobile: chips wrap cleanly and don't overflow */
@media (max-width: 640px) {
  .rc-stats{ gap:.3rem; }
  .rc-stat{ font-size:.65rem; padding:.18rem .5rem; }
  .block-container{ padding-left:.6rem !important; padding-right:.6rem !important; }
  .rc-hero{ padding:1rem 1rem .9rem; }
  .rc-hero h1{ font-size:1.3rem; }
  .rc-scope{ font-size:.74rem; }
  [data-testid="stHorizontalBlock"] > div{ min-width:0 !important; }
}
</style>
"""

DARK_CSS = """
<style>
:root{
  --ink:#D9E8F5; --muted:#8FA8C4; --line:#253A50; --paper:#0D1B2A;
  --card:#152030; --brand:#4A90D9; --brand-2:#6BAEE8; --gold:#D4A843;
}
/* 1. Paint the root dark — everything else becomes transparent so this shows through */
html, body, #root { background:#0D1B2A !important; }
.stApp { background:#0D1B2A !important; }

/* 2. Make ALL Streamlit layout containers transparent */
[data-testid="stAppViewContainer"],
[data-testid="stMain"],
[data-testid="stMainBlockContainer"],
[data-testid="stVerticalBlock"],
[data-testid="stHorizontalBlock"],
[data-testid="stHeader"],
[data-testid="stDecoration"],
[data-testid="stToolbar"],
.block-container,
section.main { background:transparent !important; }

/* 3. Sidebar stays its own dark shade */
[data-testid="stSidebar"],
[data-testid="stSidebar"] > div,
[data-testid="stSidebarContent"] { background:#060F18 !important; }

/* 4. Buttons */
.stButton > button,
[data-testid="stBaseButton-secondary"],
[data-testid="stBaseButton-primary"] {
  background:#152030 !important; color:#D9E8F5 !important;
  border-color:#253A50 !important;
}
.stButton > button:hover { background:#1e3248 !important; border-color:#4A90D9 !important; }

/* 5. Chat messages */
[data-testid="stChatMessage"] { background:#152030 !important; border-color:#253A50 !important; }

/* 6. Chat input — target every layer Streamlit wraps around the textarea */
[data-testid="stChatInput"],
[data-testid="stChatInput"] > div,
[data-testid="stChatInputContainer"],
[data-testid="stBottom"],
[data-testid="stBottom"] > div,
.stChatFloatingInputContainer,
.stChatFloatingInputContainer > div,
div:has(> [data-testid="stChatInput"]),
textarea {
  background:#152030 !important;
  color:#D9E8F5 !important;
  border-color:#253A50 !important;
}
/* The sticky bottom bar that holds the input */
[data-testid="stBottomBlockContainer"],
[data-testid="stBottomBlockContainer"] > div {
  background:#0D1B2A !important;
  border-color:#253A50 !important;
}

/* 7. Selectbox / dropdowns — all layers */
[data-baseweb="select"],
[data-baseweb="select"] > div,
[data-baseweb="select"] > div > div,
[data-baseweb="select"] input,
[data-baseweb="input"] > div,
[data-baseweb="popover"],
[data-baseweb="menu"],
[data-testid="stSelectbox"] > div > div {
  background:#152030 !important;
  border-color:#253A50 !important;
  color:#D9E8F5 !important;
}
[data-baseweb="select"] span,
[data-baseweb="select"] div,
[data-baseweb="input"] input { color:#D9E8F5 !important; }
/* Dropdown option list */
[role="listbox"], [role="option"] {
  background:#152030 !important; color:#D9E8F5 !important;
}
/* Placeholder text in chat input */
[data-testid="stChatInput"] textarea::placeholder,
textarea::placeholder { color:#8FA8C4 !important; opacity:1 !important; }

/* 8. Expanders */
[data-testid="stExpander"] summary,
[data-testid="stExpander"] > div { background:#152030 !important; border-color:#253A50 !important; }

/* 9. Text — target only Streamlit markdown, not everything */
[data-testid="stMarkdownContainer"] p,
[data-testid="stMarkdownContainer"] li,
[data-testid="stMarkdownContainer"] span,
.stMarkdown p, .stMarkdown li,
label, .stSelectbox label, .stToggle label,
[data-testid="stWidgetLabel"] { color:#D9E8F5 !important; }

/* 10. Custom classes */
.rc-scope { background:#152030 !important; color:#8FA8C4 !important; border-color:#253A50 !important; }
.rc-basis  { color:#8FA8C4 !important; }
.rc-src    { background:#152030 !important; border-color:#253A50 !important; }
.rc-src-t  { color:#D9E8F5 !important; }
.rc-quote  { background:#1A2D40 !important; color:#B0C8E0 !important; border-color:#253A50 !important; }
.rc-fb-thanks { color:#4ade80 !important; background:#052e16 !important; }

/* 11. Hero card keeps its gradient */
.rc-hero, .rc-hero * { color:#fff !important; }
.rc-hero p { color:#CFE0F2 !important; }

/* 12. Caption / small text */
[data-testid="stCaptionContainer"] p,
small, .stCaption { color:#8FA8C4 !important; }
</style>
"""


def chip(tier: str) -> str:
    label, colour = STATUS[tier]
    return f"<span class='rc-chip' style='background:{colour}'>{label}</span>"


def basis_sentence(results) -> str:
    """Say what the answer rests on, in words rather than a percentage.

    Counts documents, not passages: six extracts from one circular is one
    source, and presenting that as six would mislead precisely where someone is
    most likely to rely on it.
    """
    if not results:
        return ""
    by_doc = {}
    for r in results:
        by_doc.setdefault(r["source_file"], r["tier"])
    binding = sum(1 for t in by_doc.values() if t in ("A", "B"))
    other = len(by_doc) - binding

    def plural(n, word):
        return f"{n} {word}" + ("" if n == 1 else "s")

    if binding and other:
        return (f"Based on {plural(binding, 'binding regulation')} and "
                f"{plural(other, 'guidance document')}.")
    if binding:
        return f"Based on {plural(binding, 'binding regulation')}."
    return (f"Based on {plural(other, 'guidance document')} only — no binding "
            f"regulation was found for this question.")


def render_sources(results, domain: str = "") -> None:
    # Show the top 3 source titles inline so the reader sees provenance at a
    # glance without having to open the expander. Full passages are inside.
    if results:
        seen: set[str] = set()
        preview_lines: list[str] = []
        for r in results:
            t = clean_title(str(r["title"]), r.get("source_file", ""))
            if t not in seen:
                seen.add(t)
                colour = STATUS[r["tier"]][1]
                preview_lines.append(
                    f"<span style='color:{colour};font-weight:600'>•</span> "
                    f"<span style='font-size:.8rem;color:#3C4F66'>{html.escape(t)}</span>")
            if len(preview_lines) == 3:
                break
        remaining = len({r["source_file"] for r in results}) - len(preview_lines)
        if remaining > 0:
            preview_lines.append(
                f"<span style='font-size:.78rem;color:#6B7C93'>"
                f"+ {remaining} more source{'s' if remaining > 1 else ''}</span>")
        st.markdown(
            "<div style='margin:.35rem 0 .5rem;line-height:1.8'>"
            + "<br>".join(preview_lines) + "</div>",
            unsafe_allow_html=True)

    n = len(results)
    domain_tag = f" {domain}" if domain else ""
    expander_label = (f"View {n}{domain_tag} source{'s' if n != 1 else ''} behind this answer"
                      if n else "Sources")
    with st.expander(expander_label):
        for i, r in enumerate(results, 1):
            # Raw PDF and OCR text contains stray <, > and & often enough that
            # injecting it unescaped silently swallows parts of the quotation.
            url = html.escape(r.get("url") or "", quote=True)
            link = (f" · <a href='{url}' target='_blank' rel='noopener'>"
                    f"open the official source</a>" if url else "")
            body = html.escape(r["text"][:620].strip())
            if len(r["text"]) > 620:
                body += " …"
            edge = STATUS[r["tier"]][1]
            st.markdown(
                f"<div class='rc-src' style='border-left:3px solid {edge}'>"
                f"<div class='rc-src-t'>{i}. "
                f"{html.escape(clean_title(str(r['title']), r.get('source_file', '')))}"
                f"</div>"
                f"<div style='margin:.4rem 0'>{chip(r['tier'])}"
                f"<span class='rc-note'>{link}</span></div>"
                f"<div class='rc-quote'>{body}</div></div>", unsafe_allow_html=True)


# apply_scope() was removed on 11 Oct 2026.
#
# It took the top CANDIDATE_K results by similarity and kept the ones whose
# title matched the chosen scope - and when none matched, returned the unscoped
# list instead: `return (keep or results)[:limit], not keep`.
#
# Measured consequence: "What equipment must be available?" scoped to
# Physiotherapy centre returned a Yoga standards DRAFT. The physiotherapy
# document is in the corpus, but ~20 near-identical CEA facility templates
# outrank it on a generic question - and generic questions are exactly what the
# dropdown exists to serve. 3 of 4 fallbacks in that run were not the control
# case; they were this defect.
#
# The scope is now a metadata constraint passed into the Chroma query
# (rag_query.retrieve(..., titles=...)), so the search cannot leave the scope,
# and an empty scope is reported rather than widened.


def render_copy_button(text: str, key: str) -> None:
    """Render a clipboard copy button using an iframe so Streamlit's HTML
    sanitizer cannot strip the onclick handler."""
    safe = html.escape(text, quote=True)
    _stc.html(
        f"""<button id="cb_{key}"
            onclick="navigator.clipboard.writeText(document.getElementById('ct_{key}').innerText)
                     .then(()=>{{document.getElementById('cb_{key}').innerText='✅ Copied';
                                setTimeout(()=>{{document.getElementById('cb_{key}').innerText='📋 Copy answer'}},1500)}})"
            style="background:none;border:1px solid #d1d5db;border-radius:6px;
                   padding:3px 10px;font-size:13px;cursor:pointer;color:#4B5563;
                   font-family:sans-serif;">
            📋 Copy answer
        </button>
        <div id="ct_{key}" style="display:none">{safe}</div>""",
        height=36,
    )


def render_share_button(question: str, answer: str, sources: list, key: str) -> None:
    """Render a row of share buttons: WhatsApp, Gmail, Outlook and Copy."""
    source_lines = []
    for r in sources:
        title = clean_title(str(r.get("title", "")), r.get("source_file", ""))
        source_lines.append(f"  - {title}")
    sources_block = "\n".join(source_lines) if source_lines else "  (none)"
    share_text = (
        f"Q: {question}\n\n"
        f"A: {answer}\n\n"
        f"Sources:\n{sources_block}\n\n"
        f"-- via CCS Regulatory Compliance Assistant (IIM Bangalore)"
    )
    safe_js = share_text.replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")
    enc_js  = (
        "encodeURIComponent(`" + safe_js + "`)"
    )

    _stc.html(
        f"""<style>
  .sr-row-{key}{{display:flex;align-items:center;gap:6px;font-family:sans-serif;flex-wrap:wrap}}
  .sr-lbl-{key}{{font-size:12px;color:#9CA3AF;margin-right:2px}}
  .sr-a-{key}{{display:inline-flex;align-items:center;gap:4px;padding:3px 9px;
              font-size:12px;border:1px solid #e5e7eb;border-radius:6px;
              text-decoration:none;color:#374151;background:#fff;cursor:pointer;
              white-space:nowrap}}
  .sr-a-{key}:hover{{background:#f3f4f6;border-color:#d1d5db}}
</style>
<div class="sr-row-{key}">
  <span class="sr-lbl-{key}">Share via:</span>
  <a class="sr-a-{key}" id="wa_{key}" href="#" target="_blank" rel="noopener">
    <svg width="13" height="13" viewBox="0 0 24 24" fill="#25D366"><path d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347m-5.421 7.403h-.004a9.87 9.87 0 01-5.031-1.378l-.361-.214-3.741.982.998-3.648-.235-.374a9.86 9.86 0 01-1.51-5.26c.001-5.45 4.436-9.884 9.888-9.884 2.64 0 5.122 1.03 6.988 2.898a9.825 9.825 0 012.893 6.994c-.003 5.45-4.437 9.884-9.885 9.884m8.413-18.297A11.815 11.815 0 0012.05 0C5.495 0 .16 5.335.157 11.892c0 2.096.547 4.142 1.588 5.945L.057 24l6.305-1.654a11.882 11.882 0 005.683 1.448h.005c6.554 0 11.89-5.335 11.893-11.893a11.821 11.821 0 00-3.48-8.413z"/></svg>
    WhatsApp
  </a>
  <a class="sr-a-{key}" id="gm_{key}" href="#" target="_blank" rel="noopener">
    <svg width="13" height="13" viewBox="0 0 24 24"><path d="M24 5.457v13.909c0 .904-.732 1.636-1.636 1.636h-3.819V11.73L12 16.64l-6.545-4.91v9.273H1.636A1.636 1.636 0 010 19.366V5.457c0-2.023 2.309-3.178 3.927-1.964L12 9.641l8.073-6.148C21.69 2.28 24 3.434 24 5.457z" fill="#EA4335"/></svg>
    Gmail
  </a>
  <a class="sr-a-{key}" id="ol_{key}" href="#" target="_blank" rel="noopener">
    <svg width="13" height="13" viewBox="0 0 24 24" fill="#0078D4"><rect x="2" y="4" width="20" height="16" rx="2"/><path d="M2 8l10 7 10-7" stroke="#fff" stroke-width="1.5" fill="none"/></svg>
    Outlook
  </a>
  <button class="sr-a-{key}" id="cp_{key}"
    onclick="(function(){{var t=`{safe_js}`;navigator.clipboard.writeText(t).then(function(){{var b=document.getElementById('cp_{key}');b.textContent='✅ Copied';setTimeout(function(){{b.textContent='📋 Copy'}},1500)}})}})()"
    style="border:1px solid #e5e7eb;background:#fff">📋 Copy
  </button>
</div>
<script>
(function(){{
  var enc = {enc_js};
  document.getElementById('wa_{key}').href = 'https://api.whatsapp.com/send?text=' + enc;
  document.getElementById('gm_{key}').href = 'https://mail.google.com/mail/?view=cm&body=' + enc;
  document.getElementById('ol_{key}').href = 'https://outlook.live.com/mail/0/deeplink/compose?body=' + enc;
}})();
</script>""",
        height=44,
    )


_DOMAIN_FOLLOWUPS: dict[str, list[str]] = {
    "Healthcare": [
        "What are the minimum staff requirements for this type of establishment?",
        "What records and documentation must be maintained on site?",
        "What are the penalties for non-compliance with these standards?",
    ],
    "RERA": [
        "What documents must a promoter submit for project registration?",
        "What are the timelines and penalties for project delays?",
        "What rights does a home buyer have if the promoter defaults?",
    ],
}

_HEALTHCARE_FOLLOWUP_POOLS: dict[str, list[str]] = {
    "staff":        ["What qualifications must the medical officer in charge hold?",
                     "What are the minimum nurse-to-patient ratios required?",
                     "How often must staff training and competency checks be conducted?"],
    "record":       ["For how long must patient records be retained under CEA?",
                     "What information must be recorded in the admission register?",
                     "Who is responsible for records when a facility changes ownership?"],
    "equipment":    ["What maintenance schedule is required for medical equipment?",
                     "Which equipment must be calibrated and at what intervals?",
                     "Are there storage standards specified for medical devices?"],
    "penalty":      ["What is the penalty for operating without a valid CEA registration?",
                     "Can a facility appeal a cancellation of its registration?",
                     "What is the process for a compliance audit under CEA?"],
    "registr":      ["What documents are required to register a clinical establishment?",
                     "How is a clinical establishment classified for registration?",
                     "How often must a CEA registration be renewed?"],
    "infra":        ["What are the minimum space requirements per bed?",
                     "What ventilation and sanitation standards apply to wards?",
                     "Are there specifications for wheelchair access and ramps?"],
}

_RERA_FOLLOWUP_POOLS: dict[str, list[str]] = {
    "registr":      ["What happens if a project exceeds its registered completion date?",
                     "Can a project be de-registered, and on what grounds?",
                     "How are changes to an approved plan notified to RERA?"],
    "buyer":        ["Can a buyer cancel the agreement and get a full refund?",
                     "How does a buyer file a complaint with the RERA authority?",
                     "What compensation is a buyer entitled to for project delays?"],
    "delay":        ["What is the process for applying for a project extension?",
                     "Can force majeure events excuse a promoter from delay penalties?",
                     "What compensation must a promoter pay for delayed possession?"],
    "agent":        ["What are the obligations of a RERA-registered real estate agent?",
                     "What penalty does an unregistered agent face?",
                     "How often must a real estate agent renew their registration?"],
    "penalty":      ["What is the process for enforcing a RERA order against a promoter?",
                     "Can criminal action be taken against a promoter under RERA?",
                     "What are the monetary limits for penalties under Karnataka RERA rules?"],
    "escrow":       ["What percentage of collections must be kept in escrow?",
                     "Can escrow funds be withdrawn before project completion?",
                     "Who audits the escrow account and at what frequency?"],
    "complaint":    ["What is the timeline for adjudicating a RERA complaint?",
                     "Can a complaint be filed online with Karnataka RERA?",
                     "What documents must accompany a home buyer's complaint?"],
}


def get_contextual_followups(domain: str, question: str, answer: str) -> list[str]:
    """Return 3 follow-up questions tailored to what was just asked.
    No LLM call -- keyword-match on question + answer text."""
    combined = (question + " " + answer).lower()
    pool_map = (_HEALTHCARE_FOLLOWUP_POOLS if domain == "Healthcare"
                else _RERA_FOLLOWUP_POOLS)
    for kw, suggestions in pool_map.items():
        if kw in combined:
            return suggestions
    return _DOMAIN_FOLLOWUPS.get(domain, [])


# Suggested rephrased questions shown when an answer is low-confidence.
_LOW_CONF_SUGGESTIONS: dict[str, list[str]] = {
    "Healthcare": [
        "What staffing requirements apply to a hospital under CEA standards?",
        "What records must a clinical establishment maintain and for how long?",
        "What are the penalties for non-compliance with CEA regulations?",
    ],
    "RERA": [
        "What documents must a promoter submit to register a project?",
        "What are a buyer's rights if the promoter delays the project?",
        "What penalties apply if a project is not registered under RERA?",
    ],
}

# Regulation currency dates shown as a footer on every answer.
_REG_DATE: dict[str, str] = {
    "Healthcare": "March 2024",
    "RERA": "September 2024",
}


def log_feedback(domain: str, question: str, answer: str,
                 confidence_label: str, confidence_score: float,
                 feedback_type: str, reason: str = "") -> None:
    """Write one feedback row to Supabase. Silently skips if not configured."""
    url = _secret("SUPABASE_URL")
    key = _secret("SUPABASE_KEY")
    if not url or not key:
        return
    try:
        import requests as _req  # noqa: PLC0415
        _resp = _req.post(
            f"{url}/rest/v1/feedback",
            headers={"apikey": key, "Authorization": f"Bearer {key}",
                     "Content-Type": "application/json", "Prefer": "return=minimal"},
            json={"domain": domain, "question": question, "answer_snippet": answer[:300],
                  "confidence_score": round(confidence_score, 4),
                  "confidence_label": confidence_label, "feedback_type": feedback_type,
                  "feedback_reason": reason},
            timeout=5,
        )
        if not _resp.ok:
            st.session_state["_fb_err"] = f"HTTP {_resp.status_code}: {_resp.text[:200]}"
    except Exception as _log_err:
        st.session_state["_fb_err"] = str(_log_err)


def log_query(bot: str, question: str, answer: str,
              confidence: float, results, flagged: bool) -> None:
    """Log every answered query to the Supabase queries table.

    Uses the service role key (SUPABASE_SERVICE_KEY) to bypass RLS.
    Silently swallows all errors so a logging hiccup never breaks the UX.
    """
    url = _secret("SUPABASE_URL")
    svc_key = _secret("SUPABASE_SERVICE_KEY")
    if not url or not svc_key:
        return
    try:
        import requests as _req  # noqa: PLC0415
        source_docs = [r.get("title") or r.get("source", "") for r in (results or [])]
        _req.post(
            f"{url}/rest/v1/queries",
            headers={
                "apikey": svc_key,
                "Authorization": f"Bearer {svc_key}",
                "Content-Type": "application/json",
                "Prefer": "return=minimal",
            },
            json={
                "bot": bot,
                "question": question,
                "answer_snippet": (answer or "")[:300],
                "confidence": round(confidence, 4),
                "source_docs": source_docs,
                "session_id": st.session_state.get("session_id", ""),
                "flagged_low_conf": flagged,
            },
            timeout=5,
        )
    except Exception:
        pass  # Never surface logging errors to the user


def generate(client, question: str, domain: str, results, effort: str) -> str:
    user_msg = (f"Domain: {domain}\n\nQuestion: {question}\n\n"
                f"Retrieved sources:\n\n{format_sources(results)}")
    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_msg}]
    kwargs = {"model": LLM_MODEL, "messages": messages}
    if effort:
        kwargs["reasoning_effort"] = effort
    answer = None
    try:
        try:
            stream = client.chat.completions.create(**kwargs, stream=True)
        except Exception:
            if "reasoning_effort" not in kwargs:
                raise
            kwargs.pop("reasoning_effort")
            stream = client.chat.completions.create(**kwargs, stream=True)
        # A chunk can carry an empty choices list - the final usage-only chunk
        # does, and chunk.choices[0] would raise IndexError mid-answer.
        answer = st.write_stream(
            (c.choices[0].delta.content or "") for c in stream if c.choices)
    except Exception:
        # Streaming is refused for some models and some unverified
        # organisations. Fall back to one blocking call rather than showing a
        # reviewer a stack trace.
        try:
            kwargs.pop("reasoning_effort", None)
            resp = client.chat.completions.create(**kwargs)
            answer = (resp.choices[0].message.content or "").strip()
            st.markdown(answer)
        except Exception:
            answer = ("Sorry — the answer could not be generated just now. "
                      "The sources found for your question are shown below.")
            st.warning(answer)
    if not answer:
        answer = "No answer was returned. The sources found are shown below."
        st.markdown(answer)
    return answer


# ── page ─────────────────────────────────────────────────────────────────────
_default_domain = LOCKED_DOMAIN or "Healthcare"
st.set_page_config(page_title=DOMAIN_META[_default_domain]["title"],
                   page_icon="📋", layout="wide",
                   initial_sidebar_state="expanded")
st.markdown(CSS, unsafe_allow_html=True)
if st.session_state.get("dark_mode"):
    st.markdown(DARK_CSS, unsafe_allow_html=True)

with st.sidebar:
    st.markdown(f"<div class='rc-brand'>{DOMAIN_META[_default_domain]['title']}</div>"
                "<div class='rc-brand-sub'>IIM Bangalore</div>",
                unsafe_allow_html=True)
    st.divider()

    if LOCKED_DOMAIN:
        domain = LOCKED_DOMAIN
    else:
        st.markdown("<div class='rc-side-h'>Domain</div>", unsafe_allow_html=True)
        domain = st.radio("Domain", list(DOMAINS), label_visibility="collapsed",
                          format_func=lambda d: DOMAIN_META[d]["title"])
        st.divider()

    # "How to use" pinned prominently above the filters so first-time
    # users see it before they start typing.
    _no_msgs = not st.session_state.get("messages")
    with st.expander("ℹ️ How to use this assistant", expanded=False):
        st.markdown(
            "**Ask in plain language.** No legal terms needed.\n\n"
            "**Use the filter** below to narrow results to your role or "
            "facility type.\n\n"
            "**Check the sources.** Every answer lists the exact documents "
            "and passages it relied on.\n\n"
            "**Confidence badge** — a warning appears when the match is weak.\n\n"
            "**Not legal advice.** Always verify with a professional before acting.")

    st.markdown("<div class='rc-side-h'>Narrow the search</div>",
                unsafe_allow_html=True)
    if domain == "RERA":
        role = st.selectbox("I am a…", list(RERA_ROLES),
                            help="Narrows the search to documents most relevant "
                                 "to your role. The foundational Act and Karnataka "
                                 "Rules always appear alongside role-specific circulars.")
        scope_needles = RERA_ROLES[role]
        scope_label = role
        examples = (RERA_ROLE_EXAMPLES.get(role) or EXAMPLES["RERA"])
    else:
        facility = st.selectbox("Type of establishment", list(HEALTHCARE_FACILITIES),
                                help="Standards are published separately for each "
                                     "type of facility. Choosing one returns the "
                                     "standards that apply to it.")
        scope_needles = HEALTHCARE_FACILITIES[facility]
        scope_label = facility
        examples = ([t.format(n=FACILITY_NOUN[facility]) for t in FACILITY_TEMPLATES]
                    if scope_needles else EXAMPLES["Healthcare"])

    n_scoped, n_general = scope_count(domain, scope_needles)
    caption = scope_caption(n_scoped, "all" if not scope_needles else "some",
                            n_general)
    if caption:
        st.caption(caption)

    # Kannada language toggle -- RERA only (corpus has Kannada FAQ docs)
    kannada_mode = False
    if domain == "RERA":
        kannada_mode = st.toggle("ಕನ್ನಡ / Kannada documents",
                                 help="Include Kannada-language FAQ documents in the search.")

    with st.expander("More options"):
        include_drafts = st.checkbox("Include draft and proposed rules", value=False,
                                     help="Clearly labelled, and never presented as "
                                          "law in force.")
        include_review = st.checkbox("Include documents we have not verified",
                                     value=False,
                                     help="Material whose legal status we have not "
                                          "confirmed.")

    st.divider()
    if st.button("Start a new conversation"):
        st.session_state.messages = []
        st.session_state.feedback = {}
        st.rerun()

    # Export conversation button
    if st.session_state.get("messages"):
        lines = []
        for m in st.session_state.messages:
            role = "You" if m["role"] == "user" else "Assistant"
            lines.append(f"**{role}:** {m['content']}")
            if m.get("sources"):
                for r in m["sources"]:
                    lines.append(f"  Source: {clean_title(str(r['title']), r.get('source_file',''))}")
            lines.append("")
        export_text = "\n".join(lines)
        st.download_button("Download conversation",
                           data=export_text,
                           file_name="compliance_conversation.txt",
                           mime="text/plain",
                           use_container_width=True)

    st.divider()
    st.toggle("🌙 Dark mode", key="dark_mode",
              help="Switch between light and dark theme")

    # Admin feedback dashboard (hidden behind a password)
    with st.expander("📊 Feedback analytics"):
        _admin_pw = st.text_input("Password", type="password",
                                  key="admin_pw", label_visibility="collapsed",
                                  placeholder="Admin password")
        if _admin_pw == _secret("ADMIN_PASSWORD", ""):
            if _admin_pw:  # Don't show stats if no password is set at all
                _sb_url = _secret("SUPABASE_URL")
                _sb_key = _secret("SUPABASE_KEY")
                if not _sb_url or not _sb_key:
                    st.info("Supabase not configured — no feedback data yet.")
                else:
                    try:
                        import requests as _req  # noqa: PLC0415
                        _fb_resp = _req.get(
                            f"{_sb_url}/rest/v1/feedback",
                            headers={"apikey": _sb_key,
                                     "Authorization": f"Bearer {_sb_key}"},
                            params={"select": "*"},
                            timeout=10,
                        )
                        _fb_resp.raise_for_status()
                        _rows = _fb_resp.json() or []
                        _total = len(_rows)
                        _ups = sum(1 for r in _rows if r.get("feedback_type") == "up")
                        _dns = _total - _ups
                        _pct = int(_ups / _total * 100) if _total else 0
                        st.markdown(f"**Total ratings:** {_total}")
                        st.markdown(f"**👍 Helpful:** {_ups} ({_pct}%)  |  **👎 Not helpful:** {_dns}")
                        _dn_rows = [r for r in _rows if r.get("feedback_type") == "down"]
                        if _dn_rows:
                            st.markdown("**Questions that got thumbs down:**")
                            for _r in _dn_rows[-10:][::-1]:
                                st.markdown(
                                    f"- [{_r.get('domain','')}] {_r.get('question','')[:80]}")
                    except Exception as _e:
                        st.error(f"Could not load feedback: {_e}")
            else:
                st.caption("Enter admin password above to view analytics.")

    st.divider()
    st.markdown(
        "<div style='font-size:.72rem;color:#8896A5;line-height:1.5'>"
        "This assistant provides regulatory information only, not legal advice. "
        "Always verify with a qualified professional before acting on any answer."
        "</div>", unsafe_allow_html=True)

meta = DOMAIN_META[domain]

store = get_store() if ensure_index() else None
if store is None:
    st.error("The document library is unavailable. Please try again shortly.")
    st.stop()

client = get_client()

st.markdown(
    "<div class='rc-hero'>"
    f"<h1>{meta['title']}</h1>"
    f"<p>{meta['lede']} Answers come only from these official documents. Where they "
    "do not settle a question, the assistant says so rather than guessing, and "
    "every answer shows the sources it relied on.</p>"
    "<div class='rc-stats'>"
    + "".join(f"<span class='rc-stat'>{f}</span>" for f in meta["facts"]) +
    "</div></div>", unsafe_allow_html=True)

if client is None:
    st.warning("Answer generation is not configured. Source search still works.")

_scope_tail = ""
if n_scoped is not None and scope_needles:
    if n_scoped:
        _scope_tail = (f" — {n_scoped} document" + ("" if n_scoped == 1 else "s")
                       + " specific to this, plus the general rules")
    elif n_general:
        _scope_tail = " — the general rules only"
    else:
        _scope_tail = " — no documents cover this"
st.markdown(f"<div class='rc-scope'>Searching <b>{html.escape(scope_label)}</b>"
            f"{html.escape(_scope_tail)}</div>", unsafe_allow_html=True)

if "messages" not in st.session_state:
    st.session_state.messages = []
if "feedback" not in st.session_state:
    # Maps message index → "up" | "down"
    st.session_state.feedback = {}

# Clear-chat shortcut in the main area (complement to sidebar button)
if st.session_state.get("messages"):
    _cc_cols = st.columns([9, 1])
    if _cc_cols[1].button("🗑️", key="main_clear", help="Clear conversation"):
        st.session_state.messages = []
        st.session_state.feedback = {}
        st.rerun()

# Examples sit directly above the chat and ask the question in place. An earlier
# version put them behind a tab; the answer rendered in a tab the reader was not
# looking at, so the buttons appeared to do nothing. Reviewed 9 Oct.
if not st.session_state.messages:
    st.markdown("<div class='rc-kicker'>Try one of these</div>", unsafe_allow_html=True)
    cols = st.columns(2)
    for i, q in enumerate(examples):
        # The key carries the scope, so changing the dropdown gives Streamlit new
        # widgets rather than stale ones wearing new labels.
        if cols[i % 2].button(q, key=f"ex{scope_label}{i}"):
            st.session_state.pending_q = q
            st.rerun()

for msg_idx, msg in enumerate(st.session_state.messages):
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg.get("sources"):
            render_sources(msg["sources"], domain)
        if msg["role"] == "assistant" and msg.get("rated"):
            # Confidence badge from stored similarity scores
            hist_sim = max((r["similarity"] for r in msg.get("sources", [])), default=0.0)
            if hist_sim >= 0.65:
                h_color, h_label, h_icon = "#1a7f4b", "High confidence", "✅"
            elif hist_sim >= 0.45:
                h_color, h_label, h_icon = "#b45309", "Medium confidence", "🟡"
            else:
                h_color, h_label, h_icon = "#6B7C93", "Low confidence", "⚠️"
            st.markdown(
                f"<span style='font-size:.75rem;color:{h_color};"
                f"font-weight:600;background:{h_color}18;padding:2px 8px;"
                f"border-radius:10px;'>{h_icon} {h_label}</span>",
                unsafe_allow_html=True)

            # Copy button
            render_copy_button(msg["content"], f"hist_{msg_idx}")

            # Share answer button
            _prev_q = ""
            for _pi in range(msg_idx - 1, -1, -1):
                if st.session_state.messages[_pi]["role"] == "user":
                    _prev_q = st.session_state.messages[_pi]["content"]
                    break
            render_share_button(_prev_q, msg["content"],
                                msg.get("sources", []), f"hist_{msg_idx}")

        # Timestamp -- shown for any assistant message that has one
        if msg["role"] == "assistant":
            _ts = msg.get("timestamp", "")
            if _ts:
                st.markdown(
                    f"<div style='font-size:.68rem;color:#9CA3AF;margin-top:.2rem;'>"
                    f"🕐 Answered {_ts}</div>",
                    unsafe_allow_html=True)

        # Feedback buttons for rated answers with sources
        if msg["role"] == "assistant" and msg.get("sources") and msg.get("rated"):
            existing = st.session_state.feedback.get(msg_idx)
            _fb_thanks = st.session_state.get("_fb_thanks", set())
            fb_cols = st.columns([1, 1, 8])
            up_type = "primary" if existing == "up" else "secondary"
            dn_type = "primary" if existing == "down" else "secondary"
            if fb_cols[0].button("👍", key=f"fb_up_{msg_idx}",
                                 type=up_type, help="This answer was helpful"):
                new_val = None if existing == "up" else "up"
                st.session_state.feedback[msg_idx] = new_val
                if new_val is not None:
                    # Find the preceding user question for this answer
                    _q = ""
                    for _pi in range(msg_idx - 1, -1, -1):
                        if st.session_state.messages[_pi]["role"] == "user":
                            _q = st.session_state.messages[_pi]["content"]
                            break
                    log_feedback(domain, _q, msg["content"],
                                 msg.get("conf_label", ""), msg.get("conf_score", 0.0),
                                 "up")
                    st.session_state.setdefault("_fb_thanks", set()).add(msg_idx)
                st.rerun()
            if fb_cols[1].button("👎", key=f"fb_dn_{msg_idx}",
                                 type=dn_type, help="This answer needs improvement"):
                new_val = None if existing == "down" else "down"
                st.session_state.feedback[msg_idx] = new_val
                if new_val is not None:
                    _q = ""
                    for _pi in range(msg_idx - 1, -1, -1):
                        if st.session_state.messages[_pi]["role"] == "user":
                            _q = st.session_state.messages[_pi]["content"]
                            break
                    log_feedback(domain, _q, msg["content"],
                                 msg.get("conf_label", ""), msg.get("conf_score", 0.0),
                                 "down")
                    st.session_state.setdefault("_fb_thanks", set()).add(msg_idx)
                st.rerun()
            # Show visual confirmation after feedback
            if msg_idx in _fb_thanks:
                fb_cols[2].markdown(
                    "<span class='rc-fb-thanks'>Thanks for your feedback!</span>",
                    unsafe_allow_html=True)

            # Surface any feedback-logging error
            if st.session_state.get("_fb_err"):
                st.warning(f"Feedback not saved: {st.session_state.pop('_fb_err')}")

        # Follow-up questions -- only show for the last assistant message
        if (msg["role"] == "assistant"
                and msg_idx == len(st.session_state.messages) - 1
                and msg.get("followups")):
            st.markdown(
                "<div style='font-size:.8rem;color:#6B7C93;margin-top:.6rem;"
                "margin-bottom:.25rem;font-weight:600'>💡 You might also ask:</div>",
                unsafe_allow_html=True)
            for i, fq in enumerate(msg["followups"]):
                if st.button(fq, key=f"hist_followup_{msg_idx}_{i}",
                             use_container_width=True):
                    st.session_state["pending_q"] = fq
                    st.rerun()

prompt = st.chat_input(meta["placeholder"])
if not prompt:
    prompt = st.session_state.pop("pending_q", None)

if prompt:
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        # Resolve the scope to exact titles BEFORE searching, so the constraint
        # goes into the query. An empty scope is an answer ("we hold nothing on
        # this"), never a reason to search outside it.
        if scope_needles:
            specific, general = resolve_scope(domain, scope_needles)
        else:
            specific, general = [], []
        scope_titles = sorted(set(specific) | set(general))
        scope_empty = bool(scope_needles) and not scope_titles

        if specific == [] and general and scope_needles:
            st.info(f"No document is specific to **{scope_label}**. Answering "
                    f"from the rules that apply throughout.")

        if scope_empty:
            answer = (f"The library holds no documents specific to "
                      f"**{scope_label}**, so there is nothing here to answer "
                      f"from. Nothing outside that selection was searched. "
                      f"Change the selection in the sidebar to search more widely.")
            st.markdown(answer)
            st.session_state.messages.append(
                {"role": "assistant", "content": answer, "sources": []})
            results = []
        else:
            with st.spinner("Searching the documents…"):
                # For Kannada mode, prepend a language hint to improve retrieval
                # of bilingual/Kannada documents without changing the core query
                search_prompt = prompt
                if domain == "RERA" and kannada_mode:
                    search_prompt = f"[Kannada/ಕನ್ನಡ preferred] {prompt}"
                results = retrieve(store, search_prompt, domain, 6,
                                   include_drafts, include_review,
                                   titles=scope_titles or None)

        if not results and not scope_empty:
            scoped_note = (f" within {scope_label}" if scope_titles else "")
            answer = (f"Nothing in these documents addresses that question{scoped_note}, "
                      "so there is no basis here for an answer. It may fall outside "
                      "what this collection covers.")
            st.markdown(answer)
            st.session_state.messages.append(
                {"role": "assistant", "content": answer, "sources": []})
        elif results:
            # Low-confidence guard: if the best-scoring chunk is below the
            # threshold the question is likely outside what the collection covers.
            # We still show the sources (they are the closest we found) but warn
            # the reader that the match is weak rather than silently generating
            # a potentially misleading answer.
            best_sim = max((r["similarity"] for r in results), default=0.0)
            low_confidence = best_sim < LOW_CONFIDENCE_THRESHOLD
            if any(r["tier"] == "D" for r in results):
                st.warning("Some of this comes from **draft or proposed** rules, "
                           "which are not in force.")
            if any(r["tier"] == "E" for r in results):
                st.warning("Some of this comes from documents whose **legal status "
                           "has not been verified**.")

            if low_confidence:
                st.info(
                    "The documents found are only loosely related to your question "
                    "so this answer may be incomplete or off-target. "
                    "The closest passages are shown below; consider rephrasing or "
                    "checking whether the topic falls within this collection.",
                    icon="ℹ️")
                # Show clickable rephrasing suggestions
                _suggestions = _LOW_CONF_SUGGESTIONS.get(domain, [])
                if _suggestions:
                    st.markdown(
                        "<div style='font-size:.8rem;color:#6B7C93;margin-top:.5rem;"
                        "margin-bottom:.25rem;font-weight:600'>💬 Try one of these instead:</div>",
                        unsafe_allow_html=True)
                    for _si, _sq in enumerate(_suggestions):
                        if st.button(_sq, key=f"lc_suggest_{new_msg_idx}_{_si}",
                                     use_container_width=True):
                            st.session_state["pending_q"] = _sq
                            st.rerun()

            effort = choose_effort(results, prompt)
            key = cache_key(prompt, domain, scope_label, include_drafts,
                            include_review, effort)
            hit = answer_cache().get(key)

            st.markdown(f"<div class='rc-basis'>{basis_sentence(results)}</div>",
                        unsafe_allow_html=True)

            if low_confidence:
                # Skip LLM call for low-confidence matches; show raw passages only.
                answer = ("The question does not closely match the documents in "
                          "this collection. The nearest passages are shown in the "
                          "sources panel; please review them directly or try a "
                          "more specific query.")
                st.markdown(answer)
            elif hit:
                answer = hit["answer"]
                st.markdown(answer)
            elif client is None:
                answer = "Answer generation is not configured; see the sources below."
                st.markdown(answer)
            elif daily_usage() >= DAILY_LIMIT:
                answer = ("This demonstration has reached its limit of questions for "
                          "today. The sources found for your question are shown "
                          "below, and the service resets tomorrow.")
                if CONTACT:
                    answer += f" For access, contact {CONTACT}."
                st.warning(answer)
            else:
                answer = generate(client, prompt, domain, results, effort)
                bump_usage()
                cache_put(key, {"answer": answer, "question": prompt,
                                "domain": domain})
                log_query(domain, prompt, answer, best_sim, results, low_confidence)

            # Confidence badge
            if not low_confidence:
                if best_sim >= 0.65:
                    badge_color, badge_label = "#1a7f4b", "High confidence"
                    badge_icon = "✅"
                elif best_sim >= 0.45:
                    badge_color, badge_label = "#b45309", "Medium confidence"
                    badge_icon = "🟡"
                else:
                    badge_color, badge_label = "#6B7C93", "Low confidence"
                    badge_icon = "⚠️"
                st.markdown(
                    f"<span style='font-size:.75rem;color:{badge_color};"
                    f"font-weight:600;background:{badge_color}18;padding:2px 8px;"
                    f"border-radius:10px;'>{badge_icon} {badge_label}</span>",
                    unsafe_allow_html=True)
                # Regulation currency footer
                _reg_date = _REG_DATE.get(domain, "2024")
                st.markdown(
                    f"<div style='font-size:.7rem;color:#9CA3AF;margin-top:.3rem;'>"
                    f"📅 Based on regulations current as of {_reg_date}</div>",
                    unsafe_allow_html=True)

                # Timestamp
                _now_ts = datetime.now(tz=IST).strftime("%b %d, %Y  %I:%M %p IST")
                st.markdown(
                    f"<div style='font-size:.68rem;color:#9CA3AF;margin-top:.2rem;'>"
                    f"🕐 Answered {_now_ts}</div>",
                    unsafe_allow_html=True)

            # Generate follow-ups before appending so they are stored with the message
            followups: list[str] = []
            if not low_confidence:
                followups = get_contextual_followups(domain, prompt, answer)

            render_sources(results, domain)
            new_msg_idx = len(st.session_state.messages)
            _conf_label = badge_label if not low_confidence else "Low confidence"
            st.session_state.messages.append(
                {"role": "assistant", "content": answer, "sources": results,
                 "rated": not low_confidence, "followups": followups,
                 "conf_label": _conf_label, "conf_score": best_sim,
                 "timestamp": datetime.now(tz=IST).strftime("%b %d, %Y  %I:%M %p IST")})

            # Action row: copy + share + feedback buttons
            if not low_confidence and client is not None and daily_usage() < DAILY_LIMIT:
                render_copy_button(answer, f"new_{new_msg_idx}")
                render_share_button(prompt, answer, results, f"new_{new_msg_idx}")
                fb_cols = st.columns([1, 1, 8])
                if fb_cols[0].button("👍", key=f"fb_up_{new_msg_idx}",
                                     type="secondary", help="This answer was helpful"):
                    st.session_state.feedback[new_msg_idx] = "up"
                    st.session_state.setdefault("_fb_thanks", set()).add(new_msg_idx)
                    log_feedback(domain, prompt, answer, _conf_label, best_sim, "up")
                    st.rerun()
                if fb_cols[1].button("👎", key=f"fb_dn_{new_msg_idx}",
                                     type="secondary", help="This answer needs improvement"):
                    st.session_state.feedback[new_msg_idx] = "down"
                    st.session_state.setdefault("_fb_thanks", set()).add(new_msg_idx)
                    log_feedback(domain, prompt, answer, _conf_label, best_sim, "down")
                    st.rerun()

            # Surface any feedback-logging error as a warning
            if st.session_state.get("_fb_err"):
                st.warning(f"Feedback not saved: {st.session_state.pop('_fb_err')}")

            # Suggested follow-up questions (rendered from stored list)
            if followups:
                st.markdown(
                    "<div style='font-size:.8rem;color:#6B7C93;margin-top:.6rem;"
                    "margin-bottom:.25rem;font-weight:600'>💡 You might also ask:</div>",
                    unsafe_allow_html=True)
                for i, fq in enumerate(followups):
                    if st.button(fq, key=f"followup_{new_msg_idx}_{i}",
                                 use_container_width=True):
                        st.session_state["pending_q"] = fq
                        st.rerun()
