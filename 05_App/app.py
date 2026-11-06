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

# bcrypt is used for password verification. Installed on Streamlit Cloud.
# Falls back gracefully so a missing package never breaks the app.
try:
    import bcrypt as _bcrypt
    _BCRYPT_OK = True
except ImportError:
    _BCRYPT_OK = False

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

# Jurisdiction filter for RERA. Scopes to Central RERA Act or Karnataka
# State RERA documents independently of the stakeholder role filter.
# Empty list means no jurisdiction constraint -- full corpus is searched.
RERA_JURISDICTIONS = {
    "All documents": [],
    "Central RERA Act only": [
        "real estate (regulation and development) act",
        "real estate regulation and development",
        "model real estate",
        "central act",
        "grievance redressal cell",
    ],
    "Karnataka State RERA only": [
        "karnataka",
        "k-rera",
        "k-reat",
        "ಕರ್ನಾಟಕ",
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
  border-radius:0; padding:2rem 2rem 1.6rem;
  margin-left:calc(-1 * var(--block-padding, 1rem));
  margin-right:calc(-1 * var(--block-padding, 1rem));
  margin-top:-1.1rem; color:#fff;
  box-shadow:0 8px 24px -10px rgba(16,45,77,.5); margin-bottom:1.4rem;
}
.rc-hero h1{
  font-family:'Source Serif 4',Georgia,serif; font-weight:700;
  font-size:1.7rem; line-height:1.18; margin:0 0 .35rem; letter-spacing:-.015em;
  text-align:center;
}
.rc-hero p{ margin:0 auto; font-size:.86rem; line-height:1.55; color:#CFE0F2; max-width:72ch; text-align:center; }
.rc-stats{ display:flex; flex-wrap:wrap; gap:.45rem; margin-top:.95rem; justify-content:center; }
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
.rc-hist-item{
  display:block; width:100%; text-align:left; padding:.28rem .55rem;
  font-size:.73rem; color:#CFE0F2; background:rgba(255,255,255,.05);
  border:1px solid rgba(255,255,255,.1); border-radius:6px; margin-bottom:.25rem;
  cursor:pointer; line-height:1.35; white-space:nowrap; overflow:hidden;
  text-overflow:ellipsis;
}
.rc-hist-item:hover{ background:rgba(255,255,255,.12); border-color:rgba(255,255,255,.25); }
/* Mobile: chips wrap cleanly and don't overflow */
@media (max-width: 640px) {
  .rc-stats{ gap:.3rem; }
  .rc-stat{ font-size:.65rem; padding:.18rem .5rem; }
  .block-container{ padding-left:.6rem !important; padding-right:.6rem !important; }
  .rc-hero{ padding:1.2rem 1rem 1rem; border-radius:0; }
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

/* 3. Sidebar — blanket dark; every element inherits unless overridden below */
[data-testid="stSidebar"],
[data-testid="stSidebar"] > div,
[data-testid="stSidebarContent"],
[data-testid="stSidebar"] * {
  background:#060F18 !important;
  color:#CFE0F2 !important;
  border-color:#1E3248 !important;
}
/* Re-apply accent colours that the wildcard above would flatten */
/* All button variants in sidebar including popover trigger */
[data-testid="stSidebar"] .stButton > button,
[data-testid="stSidebar"] [data-testid="stPopoverButton"],
[data-testid="stSidebar"] [data-testid="stPopoverButton"] > button,
[data-testid="stSidebar"] [data-testid="stBaseButton-secondary"],
[data-testid="stSidebar"] [data-testid="baseButton-secondary"],
[data-testid="stSidebar"] button[kind="secondary"],
[data-testid="stSidebar"] .stPopover > button,
[data-testid="stSidebar"] .stPopover button,
[data-testid="stSidebar"] [class*="stPopover"] button,
[data-testid="stSidebar"] [class*="popover"] button {
  background:#1A3352 !important; color:#D9E4F1 !important;
  border:1px solid #2E4E6E !important; border-radius:9px !important;
}
/* Popover trigger wrapper divs — the white pill container around "Demo User · User" */
[data-testid="stSidebar"] .stPopover,
[data-testid="stSidebar"] [data-testid="stPopover"],
[data-testid="stSidebar"] [class*="stPopover"],
[data-testid="stSidebar"] [data-testid="stPopoverButton"] > div,
[data-testid="stSidebar"] [data-testid="stPopover"] > div,
[data-testid="stSidebar"] div:has(> [data-testid="stPopoverButton"]) {
  background:#1A3352 !important;
  border:1px solid #2E4E6E !important;
  border-radius:9px !important;
}
[data-testid="stSidebar"] [data-testid="stPopoverButton"] p,
[data-testid="stSidebar"] [data-testid="stPopoverButton"] span,
[data-testid="stSidebar"] [class*="stPopover"] p,
[data-testid="stSidebar"] [class*="stPopover"] span {
  color:#D9E4F1 !important;
}
[data-testid="stSidebar"] .stButton > button:hover,
[data-testid="stSidebar"] [data-testid="stPopoverButton"]:hover,
[data-testid="stSidebar"] [data-testid="stPopoverButton"] > button:hover,
[data-testid="stSidebar"] .stPopover button:hover,
[data-testid="stSidebar"] [class*="stPopover"] button:hover {
  background:#1e3248 !important; border-color:#4A90D9 !important;
}
[data-testid="stSidebar"] .stButton > button[kind="primary"],
[data-testid="stSidebar"] [data-testid="stBaseButton-primary"] {
  background:#1E3A5F !important; border-color:#4A90D9 !important;
  color:#EAF4FF !important;
}
[data-testid="stSidebar"] .stDownloadButton > button {
  background:#1A3352 !important; color:#D9E4F1 !important;
  border:1px solid #2E4E6E !important; border-radius:9px !important;
  width:100% !important;
}
[data-testid="stSidebar"] .stDownloadButton > button:hover {
  background:#1e3248 !important; border-color:#4A90D9 !important;
}
[data-testid="stSidebar"] [data-baseweb="select"],
[data-testid="stSidebar"] [data-baseweb="select"] > div,
[data-testid="stSidebar"] [data-baseweb="select"] > div > div,
[data-testid="stSidebar"] [data-testid="stSelectbox"] > div > div {
  background:#1A3352 !important; border-color:#2E4E6E !important;
  color:#D9E4F1 !important;
}

/* 4. Main-area buttons */
.stButton > button,
[data-testid="stBaseButton-secondary"],
[data-testid="stBaseButton-primary"] {
  background:#152030 !important; color:#D9E8F5 !important;
  border-color:#253A50 !important;
}
.stButton > button:hover { background:#1e3248 !important; border-color:#4A90D9 !important; }
/* Download button in main area */
.stDownloadButton > button {
  background:#1A3352 !important; color:#D9E4F1 !important;
  border:1px solid #2E4E6E !important; border-radius:9px !important;
  width:100% !important;
}
.stDownloadButton > button:hover {
  background:#1e3248 !important; border-color:#4A90D9 !important;
}

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

/* 8. Expanders — all layers including nested content */
[data-testid="stExpander"],
[data-testid="stExpander"] summary,
[data-testid="stExpander"] > div,
[data-testid="stExpander"] details,
[data-testid="stExpander"] details > div,
[data-testid="stExpander"] [data-testid="stExpanderDetails"],
[data-testid="stExpander"] [data-testid="stVerticalBlock"],
[data-testid="stExpander"] [data-testid="stVerticalBlockBorderWrapper"],
[data-testid="stExpander"] .streamlit-expanderContent {
  background:#152030 !important;
  border-color:#253A50 !important;
}
/* Past sessions expander header — match dark navy button style */
[data-testid="stSidebar"] [data-testid="stExpander"] summary,
[data-testid="stSidebar"] [data-testid="stExpander"] > details > summary,
[data-testid="stSidebar"] details > summary {
  background:#1A3352 !important;
  border:1px solid #2E4E6E !important;
  border-radius:9px !important;
  color:#D9E4F1 !important;
}
[data-testid="stSidebar"] [data-testid="stExpander"] summary p,
[data-testid="stSidebar"] [data-testid="stExpander"] summary span,
[data-testid="stSidebar"] details > summary p,
[data-testid="stSidebar"] details > summary span {
  color:#D9E4F1 !important;
}
[data-testid="stSidebar"] [data-testid="stExpander"] summary svg,
[data-testid="stSidebar"] details > summary svg {
  fill:#D9E4F1 !important;
  color:#D9E4F1 !important;
}
/* Session buttons inside the Past sessions expander in the sidebar */
[data-testid="stSidebar"] [data-testid="stExpander"] .stButton > button,
[data-testid="stSidebar"] [data-testid="stExpander"] button {
  background:#1A3352 !important;
  color:#CFE0F2 !important;
  border:1px solid #2E4E6E !important;
  text-align:left !important;
}
[data-testid="stSidebar"] [data-testid="stExpander"] .stButton > button:hover,
[data-testid="stSidebar"] [data-testid="stExpander"] button:hover {
  background:#1e3a5a !important;
  border-color:#4A90D9 !important;
}
/* Any remaining white/light backgrounds anywhere in the sidebar */
[data-testid="stSidebar"] [data-testid="stExpanderDetails"],
[data-testid="stSidebar"] [data-testid="stExpander"] *:not(svg):not(path) {
  background:#060F18 !important;
  color:#CFE0F2 !important;
}
/* Session buttons — always dark navy, readable text */
[data-testid="stSidebar"] [data-testid="stExpander"] .stButton > button {
  background: #1A3352 !important;
  border: 1px solid #2E4E6E !important;
  font-size: .78rem !important;
  font-weight: 600 !important;
}
[data-testid="stSidebar"] [data-testid="stExpander"] .stButton > button p,
[data-testid="stSidebar"] [data-testid="stExpander"] .stButton > button span,
[data-testid="stSidebar"] [data-testid="stExpander"] .stButton > button div {
  color: #D9E4F1 !important;
  font-size: .78rem !important;
  font-weight: 600 !important;
}
[data-testid="stSidebar"] [data-testid="stExpander"] .stButton > button:hover {
  background: #1e3a5a !important;
  border-color: #4A90D9 !important;
}

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


# ── Voice / microphone helpers ────────────────────────────────────────────────
def _mic_widget(state_key: str, openai_key: str, widget_key: str) -> None:
    """Render a mic button. If new audio is recorded, transcribe with Whisper
    and store the transcript in session_state[state_key], then rerun."""
    try:
        from audio_recorder_streamlit import audio_recorder as _ar
    except ImportError:
        st.caption("🎙️ Install audio-recorder-streamlit for voice input")
        return

    import hashlib as _hl, io as _io_mic
    # Inject mic sound script once per session
    if not st.session_state.get("_mic_sound_injected"):
        st.session_state["_mic_sound_injected"] = True
        import streamlit.components.v1 as _cv1
        _cv1.html("""<script>
(function(){
  function beep(freq, dur, vol){
    try{
      var ac=new(window.AudioContext||window.webkitAudioContext)();
      var o=ac.createOscillator(), g=ac.createGain();
      o.connect(g); g.connect(ac.destination);
      o.frequency.value=freq; o.type='sine';
      g.gain.setValueAtTime(vol,ac.currentTime);
      g.gain.exponentialRampToValueAtTime(0.001,ac.currentTime+dur);
      o.start(); o.stop(ac.currentTime+dur);
    }catch(e){}
  }
  var was=false;
  setInterval(function(){
    try{
      var frames=parent.document.querySelectorAll('iframe');
      for(var i=0;i<frames.length;i++){
        var doc=frames[i].contentDocument;
        if(!doc) continue;
        var btn=doc.querySelector('button');
        if(!btn) continue;
        var paths=btn.querySelectorAll('path,circle,rect');
        var isRec=false;
        for(var j=0;j<paths.length;j++){
          var f=paths[j].getAttribute('fill')||'';
          if(f.toLowerCase().indexOf('e53935')>-1){isRec=true;break;}
        }
        if(isRec!==was){
          if(isRec) beep(880,0.12,0.35);   // start: short high ping
          else      beep(440,0.25,0.3);    // stop: softer lower tone
          was=isRec;
        }
      }
    }catch(e){}
  },150);
})();
</script>""", height=0)
    _mic_col = st.columns([10, 1])[1]
    with _mic_col:
        _audio = _ar(
            text="",
            recording_color="#e53935",
            neutral_color="#546e7a",
            icon_size="lg",
            pause_threshold=3.0,
            key=widget_key,
        )
    if _audio:
        _hash = _hl.md5(_audio).hexdigest()
        _seen_key = f"_mic_seen_{widget_key}"
        if st.session_state.get(_seen_key) != _hash:
            st.session_state[_seen_key] = _hash
            if not openai_key:
                st.warning("OPENAI_API_KEY not set — cannot transcribe audio.")
                return
            with st.spinner("Transcribing…"):
                try:
                    import openai as _oai_mic
                    _c = _oai_mic.OpenAI(api_key=openai_key)
                    _res = _c.audio.transcriptions.create(
                        model="whisper-1",
                        file=("audio.wav", _io_mic.BytesIO(_audio), "audio/wav"),
                    )
                    st.session_state[state_key] = _res.text
                except Exception as _e:
                    st.warning(f"Transcription failed: {_e}")
                    return
            st.rerun()


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
    _cu = _current_user()
    try:
        import requests as _req  # noqa: PLC0415
        _resp = _req.post(
            f"{url}/rest/v1/feedback",
            headers={"apikey": key, "Authorization": f"Bearer {key}",
                     "Content-Type": "application/json", "Prefer": "return=minimal"},
            json={"domain": domain, "question": question, "answer_snippet": answer[:300],
                  "confidence_score": round(confidence_score, 4),
                  "confidence_label": confidence_label, "feedback_type": feedback_type,
                  "feedback_reason": reason,
                  "user_id": _cu.get("id"),
                  "user_role": _cu.get("role")},
            timeout=5,
        )
        if not _resp.ok:
            st.session_state["_fb_err"] = f"HTTP {_resp.status_code}: {_resp.text[:200]}"
    except Exception as _log_err:
        st.session_state["_fb_err"] = str(_log_err)


def log_bookmark(domain: str, question: str, answer: str, sources: list) -> bool:
    """Save a Q&A pair to the bookmarks table. Returns True on success."""
    _cu = _current_user()
    if not _cu.get("id"):
        return False
    url = _secret("SUPABASE_URL")
    svc_key = _secret("SUPABASE_SERVICE_KEY")
    if not url or not svc_key:
        return False
    try:
        import requests as _req  # noqa: PLC0415
        import json as _json
        _resp = _req.post(
            f"{url}/rest/v1/bookmarks",
            headers={"apikey": svc_key, "Authorization": f"Bearer {svc_key}",
                     "Content-Type": "application/json", "Prefer": "return=minimal"},
            json={"user_id": _cu["id"], "domain": domain,
                  "question": question, "answer": answer,
                  "sources": sources},
            timeout=5,
        )
        return _resp.ok
    except Exception:
        return False


def fetch_bookmarks() -> list:
    """Fetch the current user's bookmarks from Supabase, newest first."""
    _cu = _current_user()
    if not _cu.get("id"):
        return []
    url = _secret("SUPABASE_URL")
    svc_key = _secret("SUPABASE_SERVICE_KEY")
    if not url or not svc_key:
        return []
    try:
        import requests as _req  # noqa: PLC0415
        _resp = _req.get(
            f"{url}/rest/v1/bookmarks",
            headers={"apikey": svc_key, "Authorization": f"Bearer {svc_key}"},
            params={"user_id": f"eq.{_cu['id']}",
                    "select": "id,domain,question,answer,sources,created_at",
                    "order": "created_at.desc",
                    "limit": "20"},
            timeout=5,
        )
        return _resp.json() if _resp.ok else []
    except Exception:
        return []


def delete_bookmark(bookmark_id: str) -> bool:
    """Delete one bookmark by id. Returns True on success."""
    url = _secret("SUPABASE_URL")
    svc_key = _secret("SUPABASE_SERVICE_KEY")
    if not url or not svc_key:
        return False
    try:
        import requests as _req  # noqa: PLC0415
        _resp = _req.delete(
            f"{url}/rest/v1/bookmarks",
            headers={"apikey": svc_key, "Authorization": f"Bearer {svc_key}",
                     "Prefer": "return=minimal"},
            params={"id": f"eq.{bookmark_id}"},
            timeout=5,
        )
        return _resp.ok
    except Exception:
        return False


def fetch_user_query_history(limit: int = 20) -> list[str]:
    """Fetch recent questions asked by the current user from Supabase queries table."""
    _cu = _current_user()
    if not _cu.get("id"):
        return []
    url = _secret("SUPABASE_URL")
    svc_key = _secret("SUPABASE_SERVICE_KEY")
    if not url or not svc_key:
        return []
    try:
        import requests as _req  # noqa: PLC0415
        _resp = _req.get(
            f"{url}/rest/v1/queries",
            headers={"apikey": svc_key, "Authorization": f"Bearer {svc_key}"},
            params={"user_id": f"eq.{_cu['id']}",
                    "select": "question,created_at",
                    "order": "created_at.desc",
                    "limit": str(limit)},
            timeout=5,
        )
        if _resp.ok:
            rows = _resp.json()
            # Deduplicate while preserving order
            seen, out = set(), []
            for r in rows:
                q = r.get("question", "").strip()
                if q and q not in seen:
                    seen.add(q)
                    out.append(q)
            return out
        return []
    except Exception:
        return []


def fetch_user_sessions(limit: int = 20) -> list[dict]:
    """Fetch the user's recent conversation sessions from Supabase, newest first."""
    _cu = _current_user()
    if not _cu.get("id"):
        return []
    url = _secret("SUPABASE_URL")
    svc_key = _secret("SUPABASE_SERVICE_KEY")
    if not url or not svc_key:
        return []
    try:
        import requests as _req  # noqa: PLC0415
        _resp = _req.get(
            f"{url}/rest/v1/sessions",
            headers={"apikey": svc_key, "Authorization": f"Bearer {svc_key}"},
            params={"user_id": f"eq.{_cu['id']}",
                    "select": "id,title,domain,updated_at,message_count",
                    "order": "updated_at.desc",
                    "limit": str(limit)},
            timeout=5,
        )
        return _resp.json() if _resp.ok else []
    except Exception:
        return []


def save_session(session_id: str | None, title: str, domain: str,
                 messages: list) -> str | None:
    """Create or update a conversation session. Returns the session id."""
    _cu = _current_user()
    if not _cu.get("id"):
        return None
    url = _secret("SUPABASE_URL")
    svc_key = _secret("SUPABASE_SERVICE_KEY")
    if not url or not svc_key:
        return None
    try:
        import requests as _req  # noqa: PLC0415
        hdrs = {"apikey": svc_key, "Authorization": f"Bearer {svc_key}",
                "Content-Type": "application/json", "Prefer": "return=representation"}
        _msg_count = sum(1 for m in messages if m.get("role") == "user")
        payload = {"title": title, "domain": domain,
                   "messages": messages, "updated_at": "now()",
                   "message_count": _msg_count}
        if session_id:
            r = _req.patch(f"{url}/rest/v1/sessions",
                           headers=hdrs,
                           params={"id": f"eq.{session_id}",
                                   "user_id": f"eq.{_cu['id']}"},
                           json=payload, timeout=5)
            if r.ok and r.json():
                return r.json()[0]["id"]
            return session_id
        else:
            payload["user_id"] = _cu["id"]
            r = _req.post(f"{url}/rest/v1/sessions",
                          headers=hdrs, json=payload, timeout=5)
            if r.ok and r.json():
                return r.json()[0]["id"]
            return None
    except Exception:
        return None


def load_session(session_id: str) -> dict | None:
    """Load full messages for a session."""
    _cu = _current_user()
    if not _cu.get("id"):
        return None
    url = _secret("SUPABASE_URL")
    svc_key = _secret("SUPABASE_SERVICE_KEY")
    if not url or not svc_key:
        return None
    try:
        import requests as _req  # noqa: PLC0415
        r = _req.get(f"{url}/rest/v1/sessions",
                     headers={"apikey": svc_key, "Authorization": f"Bearer {svc_key}"},
                     params={"id": f"eq.{session_id}",
                             "user_id": f"eq.{_cu['id']}",
                             "select": "id,title,domain,messages"},
                     timeout=5)
        if r.ok and r.json():
            return r.json()[0]
        return None
    except Exception:
        return None


def _extract_checklist(answer_text: str) -> list[str]:
    """Extract action items from an answer. Returns a list of checklist strings."""
    import re
    lines = answer_text.split("\n")
    checklist = []
    for line in lines:
        line = line.strip()
        # Pick up numbered items, bullets, or lines with imperative verbs
        if re.match(r"^(\d+[\.\)]|\*|-|•|–)\s+", line):
            cleaned = re.sub(r"^(\d+[\.\)]|\*|-|•|–)\s+", "", line).strip()
            if len(cleaned) > 10:
                checklist.append(cleaned)
    # If structured items weren't found, split on sentence boundaries
    if not checklist:
        action_verbs = (
            "ensure", "verify", "submit", "obtain", "register", "apply",
            "file", "provide", "maintain", "update", "check", "confirm",
            "notify", "appoint", "prepare", "comply", "disclose", "report",
            "review", "assess", "implement", "establish", "document", "conduct",
            "monitor", "obtain", "test", "train", "avoid", "consult",
        )
        for sent in re.split(r"(?<=[.!?])\s+", answer_text):
            sent = sent.strip()
            if len(sent) > 20 and any(sent.lower().startswith(v) for v in action_verbs):
                checklist.append(sent)
    # Last resort: extract sentences containing "must", "shall", "required to"
    if not checklist:
        obligation_re = re.compile(
            r"[^.!?]*\b(must|shall|required to|obligated to|need to|has to)\b[^.!?]*[.!?]",
            re.IGNORECASE)
        for m in obligation_re.finditer(answer_text):
            item = m.group(0).strip()
            if len(item) > 20:
                checklist.append(item)
    return checklist[:12]


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
    _cu = _current_user()
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
                "user_id": _cu.get("id"),
                "user_role": _cu.get("role"),
            },
            timeout=5,
        )
    except Exception:
        pass  # Never surface logging errors to the user


# ── auth ─────────────────────────────────────────────────────────────────────
_GUEST = {
    "id": None,
    "name": "Guest",
    "email": "guest@claritai.in",
    "role": "user",
}

def _auth_lookup(email: str, password: str) -> dict | None:
    """Check email+password against Supabase users table.

    Returns the user dict on success, None on failure.
    Silently fails open to guest rather than crashing the app.
    """
    url = _secret("SUPABASE_URL")
    key = _secret("SUPABASE_SERVICE_KEY")
    if not url or not key:
        return None
    try:
        import requests as _req
        resp = _req.get(
            f"{url}/rest/v1/users",
            headers={"apikey": key, "Authorization": f"Bearer {key}"},
            params={"email": f"eq.{email.strip().lower()}",
                    "is_active": "eq.true",
                    "select": "id,name,email,role,password_hash"},
            timeout=5,
        )
        if not resp.ok or not resp.json():
            return None
        row = resp.json()[0]
        pw_hash = row.get("password_hash", "")
        if _BCRYPT_OK:
            ok = _bcrypt.checkpw(password.encode(), pw_hash.encode())
        else:
            # Fallback: plain string compare for local dev only
            ok = (password == pw_hash)
        if not ok:
            return None
        # Record last_login
        _req.patch(
            f"{url}/rest/v1/users",
            headers={"apikey": key, "Authorization": f"Bearer {key}",
                     "Content-Type": "application/json", "Prefer": "return=minimal"},
            params={"id": f"eq.{row['id']}"},
            json={"last_login": datetime.now(timezone.utc).isoformat()},
            timeout=5,
        )
        return {"id": row["id"], "name": row["name"],
                "email": row["email"], "role": row["role"]}
    except Exception:
        return None


def _current_user() -> dict:
    """Always returns a user dict. Defaults to Guest."""
    return st.session_state.get("current_user") or _GUEST


def _demo_login(role: str) -> dict | None:
    """Quick demo login: fetch the first active user with the given role.
    No password check -- safe for judge/demo use only (service key required).
    """
    url = _secret("SUPABASE_URL")
    key = _secret("SUPABASE_SERVICE_KEY")
    if not url or not key:
        return None
    try:
        import requests as _req  # noqa: PLC0415
        resp = _req.get(
            f"{url}/rest/v1/users",
            headers={"apikey": key, "Authorization": f"Bearer {key}"},
            params={"role": f"eq.{role}", "is_active": "eq.true",
                    "select": "id,name,email,role", "limit": "1"},
            timeout=5,
        )
        if resp.ok and resp.json():
            row = resp.json()[0]
            return {"id": row["id"], "name": row["name"],
                    "email": row["email"], "role": row["role"]}
        return None
    except Exception:
        return None


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

# Generate a stable Document Q&A session ID at app startup (not inside the tab)
# so the sidebar can show Q&A history even before the tab is visited.
if "dq_session_id" not in st.session_state:
    import uuid as _uuid_startup
    st.session_state["dq_session_id"] = str(_uuid_startup.uuid4())

with st.sidebar:
    # ── Account popover — sits at the very top of the sidebar ─────────────────
    if "current_user" not in st.session_state:
        _auto = _demo_login("user")
        st.session_state["current_user"] = _auto

    _cu = _current_user()
    _role_labels = {
        "user": "User",
        "compliance_officer": "Compliance Officer",
        "admin": "Admin",
    }
    _role_badge = _role_labels.get(_cu["role"], _cu["role"].title())
    _role_colors = {
        "user": ("#1a3352", "#4A90D9"),
        "compliance_officer": ("#1a3d2a", "#34A853"),
        "admin": ("#3d1a1a", "#D94A4A"),
    }
    _rc_bg, _rc_bd = _role_colors.get(_cu["role"], ("#1a3352", "#4A90D9"))
    _sb_icon = "🟢" if _cu["id"] else "⚪"
    _sb_label = _role_labels.get(_cu["role"], "Guest")

    with st.popover(f"👤  {_cu['name']}  ·  {_sb_icon} {_sb_label}",
                    use_container_width=True):
        st.markdown(
            f"<div style='background:{_rc_bg};border:1px solid {_rc_bd};"
            f"border-radius:8px;padding:10px;text-align:center;margin-bottom:10px'>"
            f"<div style='font-size:1.6rem;margin-bottom:4px'>👤</div>"
            f"<div style='font-size:.6rem;color:#8FA8C4;margin-bottom:2px'>Signed in as</div>"
            f"<div style='font-size:.85rem;font-weight:700;color:#CFE0F2'>{_cu['name']}</div>"
            f"<div style='font-size:.65rem;color:{_rc_bd};font-weight:600'>{_sb_label}</div>"
            f"</div>"
            f"<p style='font-size:.6rem;color:#7E99BA;text-transform:uppercase;"
            f"letter-spacing:.07em;font-weight:700;text-align:center;margin:0 0 6px'>"
            f"Switch role</p>",
            unsafe_allow_html=True,
        )
        # _do_role_switch is defined just below — forward reference is fine
        # because the button callbacks run on next rerun after _do_role_switch is defined
        if st.button("👤 Guest", key="sb_rp_guest", use_container_width=True,
                     type="primary" if not _cu["id"] else "secondary"):
            st.session_state["_pending_role_switch"] = "guest"
            st.rerun()
        if st.button("🎖 Compliance Officer", key="sb_rp_co", use_container_width=True,
                     type="primary" if _cu["role"] == "compliance_officer" else "secondary"):
            st.session_state["_pending_role_switch"] = "compliance_officer"
            st.rerun()
        if st.button("🔑 Admin", key="sb_rp_admin", use_container_width=True,
                     type="primary" if _cu["role"] == "admin" else "secondary"):
            st.session_state["_pending_role_switch"] = "admin"
            st.rerun()
        st.divider()
        st.markdown("<p style='font-size:.65rem;color:#8FA8C4;margin:0 0 4px'>"
                    "Sign in with credentials</p>", unsafe_allow_html=True)
        _sb_email = st.text_input("Email", key="_sb_login_email",
                                  placeholder="you@claritai.in",
                                  label_visibility="collapsed")
        _sb_pw = st.text_input("Password", type="password", key="_sb_login_pw",
                               placeholder="Password",
                               label_visibility="collapsed")
        if st.button("Sign in", use_container_width=True, key="_sb_login_btn", type="primary"):
            _sb_found = _auth_lookup(_sb_email, _sb_pw)
            if _sb_found:
                st.session_state["current_user"] = _sb_found
                st.session_state["_auth_err"] = ""
                for _k in ("_db_query_history", "_cached_bookmarks", "_user_sessions",
                           "_active_session_id", "_active_session_title"):
                    st.session_state.pop(_k, None)
                st.session_state["messages"] = []
                st.session_state["feedback"] = {}
                st.rerun()
            else:
                st.session_state["_auth_err"] = "Incorrect email or password."
        if st.session_state.get("_auth_err"):
            st.error(st.session_state["_auth_err"])

    st.markdown(f"<div class='rc-brand' style='margin-top:.6rem'>"
                f"{DOMAIN_META[_default_domain]['title']}</div>"
                "<div class='rc-brand-sub'>IIM Bangalore · ClaritAI</div>",
                unsafe_allow_html=True)

    # _do_role_switch helper
    def _do_role_switch(role_key: str) -> None:
        _found = _demo_login(role_key) if role_key != "guest" else None
        st.session_state["current_user"] = _found
        st.session_state["_auth_err"] = ""
        for _k in ("_db_query_history", "_cached_bookmarks", "_user_sessions",
                   "_active_session_id", "_active_session_title"):
            st.session_state.pop(_k, None)
        st.session_state["messages"] = []
        st.session_state["feedback"] = {}
        st.rerun()

    # Handle role switch triggered from within the popover (buttons inside popovers
    # fire a rerun before the sidebar scope closes, so we process it here)
    if st.session_state.get("_pending_role_switch"):
        _pending = st.session_state.pop("_pending_role_switch")
        _do_role_switch(_pending)

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
        jurisdiction = st.selectbox(
            "Jurisdiction", list(RERA_JURISDICTIONS),
            help="Filter by Central RERA Act documents or Karnataka State RERA "
                 "documents. 'All documents' searches the full corpus.")
        scope_needles = RERA_ROLES[role]
        jurisdiction_needles = RERA_JURISDICTIONS[jurisdiction]
        scope_label = role if jurisdiction == "All documents" else f"{role} · {jurisdiction}"
        examples = (RERA_ROLE_EXAMPLES.get(role) or EXAMPLES["RERA"])
    else:
        facility = st.selectbox("Type of establishment", list(HEALTHCARE_FACILITIES),
                                help="Standards are published separately for each "
                                     "type of facility. Choosing one returns the "
                                     "standards that apply to it.")
        scope_needles = HEALTHCARE_FACILITIES[facility]
        jurisdiction_needles: list = []   # no jurisdiction split for Healthcare
        scope_label = facility
        examples = ([t.format(n=FACILITY_NOUN[facility]) for t in FACILITY_TEMPLATES]
                    if scope_needles else EXAMPLES["Healthcare"])

    n_scoped, n_general = scope_count(domain, scope_needles)
    _any_filter = bool(scope_needles or jurisdiction_needles)
    caption = scope_caption(n_scoped, "all" if not _any_filter else "some",
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
        st.session_state.pop("_active_session_id", None)
        st.session_state.pop("_active_session_title", None)
        # Refresh session list to show the just-saved session at top
        st.session_state.pop("_user_sessions", None)
        st.rerun()

    # ── Sessions (Claude-style conversation history) ──────────────────────────
    _cu_sess = _current_user()
    # Fetch on first load or after a role switch clears the cache
    if _cu_sess.get("id") and "_user_sessions" not in st.session_state:
        st.session_state["_user_sessions"] = fetch_user_sessions(20)

    # Filter sessions to the current domain so RERA never shows Healthcare sessions
    _all_sess = st.session_state.get("_user_sessions", []) if _cu_sess.get("id") else []
    _sess_list = [s for s in _all_sess if not LOCKED_DOMAIN or s.get("domain") == LOCKED_DOMAIN]

    st.divider()
    with st.expander("💬 Past sessions", expanded=False):
        if _sess_list:
            for _si, _sess in enumerate(_sess_list):
                _s_title   = _sess.get("title", "Conversation")
                _s_domain  = _sess.get("domain", "")
                _s_updated = _sess.get("updated_at", "")
                _s_q_count = _sess.get("message_count", 0) or 0
                _is_active = st.session_state.get("_active_session_id") == _sess["id"]
                _btn_style = "primary"   # always dark navy; active state shown via border in CSS

                # Parse timestamp to IST for display
                try:
                    _ts = _s_updated.rstrip("Z").split("+")[0]
                    _dt = datetime.fromisoformat(_ts).replace(tzinfo=timezone.utc).astimezone(IST)
                    _date_part = _dt.strftime("%-d %b")   # e.g. "3 Nov"
                    _time_part = _dt.strftime("%I:%M %p").lstrip("0")  # e.g. "9:42 AM"
                except Exception:
                    _date_part = ""
                    _time_part = ""

                # Session number (most recent = #1 relative to the list)
                _sess_num = _si + 1

                # Domain icon
                _dom_icon = "🏥" if _s_domain == "Healthcare" else "🏗️" if _s_domain == "RERA" else "💬"

                _q_label = f"{_s_q_count}Q" if _s_q_count > 0 else "—"
                _s_display = f"Session #{_sess_num}  ·  {_date_part}  ·  {_q_label}  {_dom_icon}"

                if st.button(_s_display, key=f"sess_btn_{_si}",
                             use_container_width=True,
                             type=_btn_style):
                    _loaded = load_session(_sess["id"])
                    if _loaded:
                        st.session_state["messages"] = _loaded.get("messages", [])
                        st.session_state["feedback"] = {}
                        st.session_state["_active_session_id"] = _sess["id"]
                        st.session_state["_active_session_title"] = _loaded.get("title", "")
                    st.rerun()
        else:
            if _cu_sess.get("id"):
                st.caption("Your conversations will appear here.")
            else:
                st.caption("Sign in to save and resume sessions.")

    # ── Document Q&A history (RERA only) ──────────────────────────────────────
    if LOCKED_DOMAIN == "RERA":
        # Fetch the most recent Q&A rows globally (no session_id filter) so
        # history is visible immediately on any fresh page load, not just
        # during the session that created it (session_state is ephemeral).
        if "_dqa_history_sidebar" not in st.session_state:
            try:
                import requests as _rq_dqa
                _sb_url_h = _secret("SUPABASE_URL")
                _sb_key_h = _secret("SUPABASE_SERVICE_KEY") or _secret("SUPABASE_KEY")
                if _sb_url_h and _sb_key_h:
                    _hr = _rq_dqa.get(
                        f"{_sb_url_h}/rest/v1/document_qa_history",
                        headers={"apikey": _sb_key_h, "Authorization": f"Bearer {_sb_key_h}"},
                        params={"order": "created_at.desc", "limit": "200"},
                        timeout=5,
                    )
                    _hrows = _hr.json() if _hr.ok else []
                    # Group by filename — each unique file = one "Document Q&A session"
                    _dqa_by_file: dict = {}
                    for _row in _hrows:
                        _fn = _row.get("filename") or "Unknown file"
                        _dqa_by_file.setdefault(_fn, []).append(_row)
                    st.session_state["_dqa_history_sidebar"] = _dqa_by_file
                else:
                    st.session_state["_dqa_history_sidebar"] = {}
            except Exception:
                st.session_state["_dqa_history_sidebar"] = {}

            _dqa_hist = st.session_state.get("_dqa_history_sidebar", {})
            if _dqa_hist:
                _dqa_active_file = st.session_state.get("_active_dqa_file")
                _dqa_expander_open = bool(_dqa_active_file)
                with st.expander("📄 Document Q&A", expanded=_dqa_expander_open):
                    for _dqa_si, (_dfname, _drows) in enumerate(_dqa_hist.items()):
                        # Date from the most-recent row for this file
                        try:
                            _dts = _drows[0].get("created_at", "").rstrip("Z").split("+")[0]
                            _ddt = datetime.fromisoformat(_dts).replace(tzinfo=timezone.utc).astimezone(IST)
                            _dqa_date = _ddt.strftime("%-d %b")
                        except Exception:
                            _dqa_date = ""
                        _dqa_qcount = len(_drows)
                        _dqa_label = f"Session #{_dqa_si + 1}  ·  {_dqa_date}  ·  {_dqa_qcount}Q  📄"
                        _is_dqa_active = _dqa_active_file == _dfname
                        if st.button(
                            _dqa_label,
                            key=f"dqa_sess_{_dqa_si}",
                            use_container_width=True,
                            type="primary" if not _is_dqa_active else "secondary",
                            help=_dfname,
                        ):
                            if _is_dqa_active:
                                # Clicking active session collapses it
                                st.session_state.pop("_active_dqa_file", None)
                                st.session_state.pop("dq_history", None)
                            else:
                                st.session_state["_active_dqa_file"] = _dfname
                                st.session_state["dq_history"] = [
                                    {"q": r["question"], "a": r["answer"], "followups": r.get("followups") or []}
                                    for r in _drows
                                ]
                            st.rerun()

                        # Show Q&A thread inline in sidebar when this session is active
                        if _is_dqa_active:
                            for _sr in reversed(_drows):
                                st.markdown(f"**Q:** {_sr['question']}")
                                _ans_preview = _sr['answer'][:300] + ("…" if len(_sr['answer']) > 300 else "")
                                st.caption(_ans_preview)
                                st.markdown("---")

    st.divider()
    st.toggle("🌙 Dark mode", key="dark_mode",
              help="Switch between light and dark theme")

    # Admin/compliance feedback dashboard — visible to admin and compliance_officer roles,
    # or via password fallback for sessions not yet logged in.
    _cu_role = _current_user().get("role", "user")
    _has_dashboard_access = _cu_role in ("admin", "compliance_officer")
    with st.expander("📊 Feedback analytics"):
        if not _has_dashboard_access:
            _admin_pw = st.text_input("Password", type="password",
                                      key="admin_pw", label_visibility="collapsed",
                                      placeholder="Admin password")
            _has_dashboard_access = (_admin_pw == _secret("ADMIN_PASSWORD", "") and bool(_admin_pw))
        else:
            _admin_pw = "bypass"  # sentinel so the block below runs
        if _has_dashboard_access:
            if True:  # was `if _admin_pw:` — keep indentation matching original
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

    # Saved Answers section -- only for signed-in users
    _cu_sidebar = _current_user()
    if _cu_sidebar.get("id"):
        st.divider()
        with st.expander("🔖 Saved Answers", expanded=False):
            if st.button("🔄 Refresh", key="bm_refresh", use_container_width=True):
                st.session_state.pop("_cached_bookmarks", None)
            _bm_list = st.session_state.get("_cached_bookmarks")
            if _bm_list is None:
                _bm_list = fetch_bookmarks()
                st.session_state["_cached_bookmarks"] = _bm_list
            if not _bm_list:
                st.caption("No saved answers yet. Click 🔖 Save on any answer to bookmark it.")
            else:
                for _bm in _bm_list:
                    _bm_raw  = _bm.get("created_at", "") or ""
                    try:
                        _bm_dt  = datetime.fromisoformat(_bm_raw.rstrip("Z").split("+")[0]).replace(tzinfo=timezone.utc).astimezone(IST)
                        _bm_date = _bm_dt.strftime("%d %b %Y")
                    except Exception:
                        _bm_date = _bm_raw[:10]
                    with st.expander(
                        f"[{_bm.get('domain','').upper()}] {_bm.get('question','')[:55]}...",
                        expanded=False
                    ):
                        st.markdown(f"**Q:** {_bm.get('question','')}")
                        st.markdown(_bm.get("answer", "")[:600] + ("…" if len(_bm.get("answer","")) > 600 else ""))
                        if _bm_date:
                            st.caption(f"Saved on {_bm_date}")
                        if st.button("🗑️ Delete", key=f"bm_del_{_bm['id']}",
                                     use_container_width=True):
                            if delete_bookmark(_bm["id"]):
                                st.session_state.pop("_cached_bookmarks", None)
                                st.toast("Bookmark deleted.", icon="🗑️")
                                st.rerun()

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

# ── Hero banner — always full width ──────────────────────────────────────────
st.markdown(
    "<div class='rc-hero'>"
    f"<h1>{meta['title']}</h1>"
    f"<p>{meta['lede']} Answers come only from these official documents. Where they "
    "do not settle a question, the assistant says so rather than guessing, and "
    "every answer shows the sources it relied on.</p>"
    "<div class='rc-stats'>"
    + "".join(f"<span class='rc-stat'>{f}</span>" for f in meta["facts"]) +
    "</div></div>", unsafe_allow_html=True)


# ── RERA extra tabs (K-RERA Lookup + Investment Signals + Document Q&A) ───────
if domain == "RERA":
    if _current_user()["role"] == "user":
        from user_flow import render_user_flow
        render_user_flow()
        st.stop()
    _tab_chat, _tab_lookup, _tab_invest, _tab_docqa = st.tabs([
        "💬 Compliance Chat",
        "🔍 K-RERA Project Lookup",
        "📊 Investment Signals",
        "📄 Document Q&A",
    ])
    # We render the chatbot into _tab_chat below by using a context switch.
    # The other three tabs are self-contained here.

    # ── Tab 2: K-RERA Lookup ───────────────────────────────────────────────
    with _tab_lookup:
        import requests as _req_lookup
        _SUPABASE_URL = _secret("SUPABASE_URL") or os.environ.get("SUPABASE_URL", "")
        _SUPABASE_KEY = _secret("SUPABASE_KEY") or os.environ.get("SUPABASE_KEY", "")

        st.subheader("Search Karnataka RERA Registered Projects")
        st.caption("10,096 projects as of November 2026 — data from rera.karnataka.gov.in")

        if not _SUPABASE_URL or not _SUPABASE_KEY:
            st.warning("SUPABASE_URL and SUPABASE_KEY are not set. Add them in Streamlit Secrets.")
        else:
            _lk_col_a, _lk_col_b = st.columns([3, 1])
            with _lk_col_a:
                _lk_query = st.text_input("Search projects", placeholder="e.g. Prestige, Brigade, Sobha, Whitefield…", key="lk_query")
            with _lk_col_b:
                _lk_field = st.selectbox("Search by", ["Project Name", "Builder / Promoter", "ACK Number", "PRM Number"], key="lk_field")

            _LK_COL_MAP = {"Project Name": "project_name", "Builder / Promoter": "promoter_name", "ACK Number": "ack_number", "PRM Number": "prm_number"}

            if _lk_query:
                _lk_col = _LK_COL_MAP.get(_lk_field, "project_name")
                _lk_select = (
                    "ack_number,prm_number,project_name,promoter_name,"
                    "status,district,taluk,project_type,total_units,sold_units,"
                    "completion_date,reg_date,lapse_date,land_area_sqft,detail_url,"
                    "pin_code,no_of_towers,open_parking,covered_parking,"
                    "project_sub_type,approving_authority,source_of_water,total_carpet_area_sqmt"
                )
                _lk_url = (f"{_SUPABASE_URL}/rest/v1/rera_projects"
                           f"?{_lk_col}=ilike.*{_req_lookup.utils.quote(_lk_query)}*"
                           f"&limit=20&select={_lk_select}")
                try:
                    _lk_r = _req_lookup.get(_lk_url, headers={"apikey": _SUPABASE_KEY, "Authorization": f"Bearer {_SUPABASE_KEY}"}, timeout=15)
                    _lk_r.raise_for_status()
                    _lk_results = _lk_r.json()
                except Exception as _lk_e:
                    st.error(f"Search error: {_lk_e}")
                    _lk_results = []

                if _lk_results:
                    st.success(f"Found {len(_lk_results)} project(s)")
                    _STATUS_COLOR = {"active": "🟢", "expired": "🔴", "lapsed": "🟠", "revoked": "⛔"}
                    for _lk_p in _lk_results:
                        _lk_status_raw = (_lk_p.get("status") or "").lower()
                        _lk_badge = _STATUS_COLOR.get(_lk_status_raw, "⬜")
                        _lk_title = (
                            f"{_lk_badge} **{_lk_p.get('project_name') or 'N/A'}**"
                            f"  |  {_lk_p.get('promoter_name') or 'N/A'}"
                        )
                        with st.expander(_lk_title):
                            # Row 1: Status + Location
                            _lk_r1a, _lk_r1b, _lk_r1c = st.columns(3)
                            _lk_r1a.metric("Status", (_lk_p.get("status") or "Not enriched yet").title())
                            _lk_r1b.metric("District", _lk_p.get("district") or "N/A")
                            _lk_r1c.metric("Project Type", _lk_p.get("project_type") or "N/A")

                            # Row 2: Units
                            _lk_r2a, _lk_r2b, _lk_r2c = st.columns(3)
                            _lk_total = _lk_p.get("total_units")
                            _lk_sold  = _lk_p.get("sold_units")
                            _lk_avail = (_lk_total - _lk_sold) if (_lk_total and _lk_sold is not None) else None
                            _lk_r2a.metric("Total Units", _lk_total if _lk_total else "N/A")
                            _lk_r2b.metric("Units Sold", _lk_sold if _lk_sold is not None else "N/A")
                            _lk_r2c.metric("Units Available", _lk_avail if _lk_avail is not None else "N/A")

                            # Row 3: Dates
                            _lk_r3a, _lk_r3b, _lk_r3c = st.columns(3)
                            _lk_r3a.metric("Registration Date", _lk_p.get("reg_date") or "N/A")
                            _lk_r3b.metric("Expected Completion", _lk_p.get("completion_date") or "N/A")
                            _lk_r3c.metric("Valid Until", _lk_p.get("lapse_date") or "N/A")

                            # Row 4: RERA Numbers
                            _lk_r4a, _lk_r4b = st.columns(2)
                            _lk_r4a.markdown(f"**ACK:** `{_lk_p.get('ack_number') or 'N/A'}`")
                            _lk_r4b.markdown(f"**PRM:** `{_lk_p.get('prm_number') or 'N/A'}`")

                            # Buyer guidance
                            if _lk_status_raw == "active":
                                st.success("This project is **Active** on K-RERA. The builder is currently compliant.")
                            elif _lk_status_raw in ("expired", "lapsed"):
                                st.error("Registration has **expired / lapsed**. Verify status directly with K-RERA before booking.")
                            elif _lk_status_raw == "revoked":
                                st.error("Registration has been **revoked** by K-RERA. Exercise extreme caution.")
                            else:
                                st.info("Detailed status not yet scraped. Click the portal link below to verify.")

                            # Portal link
                            _lk_portal_id = _lk_p.get("prm_number") or _lk_p.get("ack_number") or ""
                            if _lk_portal_id:
                                st.markdown(f"[View full details on K-RERA portal ↗](https://rera.karnataka.gov.in/projectViewDetails?id={_lk_portal_id})")

                            # ── Buyer Intelligence Brief ─────────────────────────────────
                            # Pull enrichment fields
                            _lk_pin     = _lk_p.get("pin_code")
                            _lk_towers  = _lk_p.get("no_of_towers")
                            _lk_open_pk = _lk_p.get("open_parking")
                            _lk_cvd_pk  = _lk_p.get("covered_parking")
                            _lk_subtype = _lk_p.get("project_sub_type")
                            _lk_auth    = _lk_p.get("approving_authority")
                            _lk_water   = _lk_p.get("source_of_water")
                            _lk_carpet  = _lk_p.get("total_carpet_area_sqmt")

                            _lk_has_enrich = any([
                                _lk_pin, _lk_towers,
                                _lk_open_pk is not None, _lk_cvd_pk is not None,
                                _lk_subtype, _lk_auth,
                            ])
                            if _lk_has_enrich:
                                st.markdown("---")
                                st.markdown(
                                    "<div style='font-size:.87rem;font-weight:700;"
                                    "color:var(--brand,#1B4F8C);margin-bottom:.6rem;"
                                    "letter-spacing:.04em;'>🧭 Buyer Intelligence Brief</div>",
                                    unsafe_allow_html=True)

                                # Quick-scan chips
                                _lk_chips = []
                                if _lk_pin:
                                    _lk_chips.append(f"📍 Pincode {_lk_pin}")
                                if _lk_towers:
                                    try:
                                        _tw_int = int(_lk_towers)
                                        _lk_chips.append(f"🏗️ {_tw_int} Tower{'s' if _tw_int > 1 else ''}")
                                    except (ValueError, TypeError):
                                        _lk_chips.append(f"🏗️ {_lk_towers}")
                                _lk_total_pk = None
                                try:
                                    _lk_opk_int = int(_lk_open_pk) if _lk_open_pk is not None else None
                                    _lk_cpk_int = int(_lk_cvd_pk)  if _lk_cvd_pk  is not None else None
                                except (ValueError, TypeError):
                                    _lk_opk_int = _lk_cpk_int = None
                                if _lk_opk_int is not None and _lk_cpk_int is not None:
                                    _lk_total_pk = _lk_opk_int + _lk_cpk_int
                                    _lk_chips.append(f"🅿️ {_lk_total_pk} parking spots")
                                elif _lk_opk_int is not None:
                                    _lk_chips.append(f"🅿️ {_lk_opk_int} open parking")
                                elif _lk_cpk_int is not None:
                                    _lk_chips.append(f"🏠 {_lk_cpk_int} covered parking")
                                if _lk_subtype:
                                    _lk_chips.append(f"🏘️ {_lk_subtype}")
                                if _lk_auth:
                                    _lk_chips.append(f"🏛️ {_lk_auth}")
                                if _lk_water:
                                    _lk_chips.append(f"💧 {_lk_water}")

                                _chip_html = "&nbsp;&nbsp;".join(
                                    f"<span style='background:var(--paper,#F0F4FA);"
                                    f"border:1px solid var(--line,#D0D9E8);border-radius:20px;"
                                    f"padding:3px 11px;font-size:.74rem;color:var(--ink,#0E2439);"
                                    f"white-space:nowrap;display:inline-block;margin-bottom:4px;'>{c}</span>"
                                    for c in _lk_chips
                                )
                                st.markdown(
                                    f"<div style='line-height:2.1;margin-bottom:.6rem;'>{_chip_html}</div>",
                                    unsafe_allow_html=True)

                                # Story cards: one per insight
                                _lk_stories = []

                                # Scale of the project
                                try:
                                    _tw_n = int(_lk_towers) if _lk_towers else None
                                    _tu_n = int(_lk_total)  if _lk_total  else None
                                except (ValueError, TypeError):
                                    _tw_n = _tu_n = None
                                if _tw_n and _tu_n:
                                    _upt = _tu_n // _tw_n
                                    if _tw_n == 1:
                                        _lk_stories.append(
                                            "This is a <b>single-tower project</b> with "
                                            f"{_tu_n} units. Smaller communities tend to have simpler "
                                            "maintenance and faster RWA formation once possession happens.")
                                    elif _tw_n <= 3:
                                        _lk_stories.append(
                                            f"A <b>boutique development</b> across {_tw_n} towers "
                                            f"with roughly {_upt} units each. These mid-sized societies "
                                            "are easier to govern and usually have tighter-knit neighbourhoods.")
                                    else:
                                        _lk_stories.append(
                                            f"This is a <b>large township</b> with {_tw_n} towers "
                                            f"and roughly {_upt} units per tower. Expect solid amenities "
                                            "and a lively community, but factor in higher maintenance costs.")

                                # Parking intelligence
                                if _lk_total_pk is not None and _tu_n:
                                    _pk_ratio = _lk_total_pk / _tu_n
                                    if _pk_ratio >= 1.0:
                                        _lk_stories.append(
                                            f"<b>Parking looks solid</b>: {_lk_total_pk} spots for "
                                            f"{_tu_n} units (ratio {_pk_ratio:.1f}x). Every flat "
                                            "is likely to get at least one dedicated spot.")
                                    elif _pk_ratio >= 0.7:
                                        _lk_stories.append(
                                            f"<b>Parking is moderate</b>: {_lk_total_pk} spots for "
                                            f"{_tu_n} units (ratio {_pk_ratio:.1f}x). Some flats may need "
                                            "to share. Ask the builder for the parking allotment plan in writing.")
                                    else:
                                        _lk_stories.append(
                                            f"<b>Parking is tight</b>: only {_lk_total_pk} spots for "
                                            f"{_tu_n} units (ratio {_pk_ratio:.1f}x). Clarify your "
                                            "parking rights in the sale agreement before signing anything.")
                                    if _lk_cpk_int and _lk_cpk_int > 0:
                                        _opk_val = _lk_opk_int if _lk_opk_int else 0
                                        _lk_stories.append(
                                            f"Of the {_lk_total_pk} spots, <b>{_lk_cpk_int} are covered</b> "
                                            f"(basement or stilt) and {_opk_val} are open. "
                                            "Covered spots protect vehicles from Bengaluru weather and reduce sun damage.")

                                # Approving authority context
                                if _lk_auth:
                                    _auth_l = _lk_auth.lower()
                                    if "bbmp" in _auth_l:
                                        _lk_stories.append(
                                            "Approved by <b>BBMP</b> (Bruhat Bengaluru Mahanagara Palike). "
                                            "The project is within Bengaluru's municipal limits, with piped water, "
                                            "drainage, and solid waste services managed by the city corporation.")
                                    elif "bda" in _auth_l:
                                        _lk_stories.append(
                                            "Approved by <b>BDA</b> (Bengaluru Development Authority). "
                                            "BDA-sanctioned plans follow structured layout norms with "
                                            "reserved parks and civic amenities.")
                                    elif "bmrda" in _auth_l:
                                        _lk_stories.append(
                                            "Approved by <b>BMRDA</b> (Bengaluru Metropolitan Region "
                                            "Development Authority). The project is in Bengaluru's extended "
                                            "metro region. Infrastructure growth here is active but uneven.")
                                    elif "gram panchayat" in _auth_l or "grama panchayat" in _auth_l:
                                        _lk_stories.append(
                                            "Approved by a <b>Gram Panchayat</b>. This project sits on "
                                            "the city outskirts where civic infrastructure can be limited. "
                                            "Confirm the Occupancy Certificate has been issued before making any payment.")
                                    else:
                                        _lk_stories.append(
                                            f"Approving authority: <b>{_lk_auth}</b>. "
                                            "Before final payment, confirm the Occupancy Certificate "
                                            "has been issued by this body.")

                                # Water source insight
                                if _lk_water:
                                    _wat_l = _lk_water.lower()
                                    if "bwssb" in _wat_l or "cauvery" in _wat_l:
                                        _lk_stories.append(
                                            f"Water comes from <b>{_lk_water}</b>. "
                                            "BWSSB / Cauvery piped supply is reliable and regulated. "
                                            "No borewell dependency keeps long-term water costs predictable.")
                                    elif "borewell" in _wat_l or "bore well" in _wat_l:
                                        _lk_stories.append(
                                            "Water source is <b>borewell</b>. Common on the outskirts "
                                            "but carries a risk if the water table drops over time. "
                                            "Ask the builder for a water quality test report and their contingency plan.")
                                    else:
                                        _lk_stories.append(
                                            f"Water source: <b>{_lk_water}</b>. Confirm adequate "
                                            "long-term supply with the builder, especially for upper floors.")

                                _story_style = (
                                    "font-size:.82rem;line-height:1.65;"
                                    "color:var(--ink,#0E2439);"
                                    "background:var(--paper,#F6F8FC);"
                                    "border-left:3px solid var(--brand,#1B4F8C);"
                                    "padding:.45rem .75rem;"
                                    "margin:.3rem 0;"
                                    "border-radius:0 6px 6px 0;"
                                )
                                for _st_txt in _lk_stories:
                                    st.markdown(
                                        f"<div style='{_story_style}'>{_st_txt}</div>",
                                        unsafe_allow_html=True)

                                # Carpet area footnote
                                if _lk_carpet:
                                    st.markdown(
                                        f"<div style='font-size:.76rem;color:var(--muted,#6B7C93);"
                                        f"margin-top:.5rem;'>Total carpet area on record: "
                                        f"<b>{_lk_carpet} sqmt</b></div>",
                                        unsafe_allow_html=True)
                else:
                    st.warning("No matching projects found. Try a shorter keyword.")
            else:
                st.info("Type a project name or builder name to search.")

            with st.expander("About this data"):
                st.markdown(
                    "Sourced from [K-RERA](https://rera.karnataka.gov.in/viewAllProjects) — all 10,096 RERA-registered Karnataka projects. "
                    "Rich details (status, location, units, dates) are scraped progressively from project detail pages and stored in our database. "
                    "Projects showing 'Not enriched yet' have not been detail-scraped yet. "
                    "Always cross-check critical decisions on the official portal."
                )

    # ── Tab 3: Investment Signals ──────────────────────────────────────────
    with _tab_invest:
        import pandas as _pd_invest
        import math as _math_invest

        # ── NoBroker 2026-anchored PSF data (real market rates, not estimates) ──────
        # Sources: NoBroker blog Nov 2026, 99acres Nov 2026, MagicBricks Nov 2026
        # For 17 directly anchored localities PSF is from live market data.
        # For remaining localities, zone-level scaling from Kaggle 2017 baseline is applied.
        # Zone scaling factors: Central ~1.90x | East ~2.02x | North ~1.73x | South ~1.85x | West ~1.78x
        _LOCALITIES = [
            # ── Central / Premium ──────────────────────────────────────────────────
            {"locality": "Indiranagar",          "pin": "560038", "avg_psf": 14000, "cagr_pct": 5.2, "risk": "Low",    "zone": "Central", "anchored": True},
            {"locality": "Koramangala",          "pin": "560034", "avg_psf": 13200, "cagr_pct": 5.0, "risk": "Low",    "zone": "Central", "anchored": True},
            {"locality": "Jayanagar",            "pin": "560041", "avg_psf": 12500, "cagr_pct": 4.8, "risk": "Low",    "zone": "Central", "anchored": False},
            {"locality": "JP Nagar",             "pin": "560078", "avg_psf": 10800, "cagr_pct": 5.5, "risk": "Low",    "zone": "South",   "anchored": False},
            {"locality": "Sadashivanagar",       "pin": "560080", "avg_psf": 15500, "cagr_pct": 4.5, "risk": "Low",    "zone": "Central", "anchored": False},
            # ── East corridor ──────────────────────────────────────────────────────
            {"locality": "Whitefield",           "pin": "560066", "avg_psf": 8500,  "cagr_pct": 8.1, "risk": "Low",    "zone": "East",    "anchored": True},
            {"locality": "Sarjapur Road",        "pin": "560035", "avg_psf": 8200,  "cagr_pct": 9.3, "risk": "Low",    "zone": "East",    "anchored": True},
            {"locality": "Marathahalli",         "pin": "560037", "avg_psf": 7800,  "cagr_pct": 7.8, "risk": "Low",    "zone": "East",    "anchored": True},
            {"locality": "HSR Layout",           "pin": "560102", "avg_psf": 11200, "cagr_pct": 6.8, "risk": "Low",    "zone": "East",    "anchored": True},
            {"locality": "KR Puram",             "pin": "560036", "avg_psf": 7200,  "cagr_pct": 8.5, "risk": "Medium", "zone": "East",    "anchored": False},
            {"locality": "Varthur",              "pin": "560087", "avg_psf": 6800,  "cagr_pct": 9.0, "risk": "Medium", "zone": "East",    "anchored": False},
            # ── North corridor ─────────────────────────────────────────────────────
            {"locality": "Hebbal",               "pin": "560024", "avg_psf": 9800,  "cagr_pct": 8.9, "risk": "Low",    "zone": "North",   "anchored": True},
            {"locality": "Yelahanka",            "pin": "560064", "avg_psf": 6200,  "cagr_pct": 10.2,"risk": "Medium", "zone": "North",   "anchored": True},
            {"locality": "Hennur",               "pin": "560043", "avg_psf": 6800,  "cagr_pct": 9.8, "risk": "Low",    "zone": "North",   "anchored": False},
            {"locality": "Devanahalli",          "pin": "562110", "avg_psf": 5600,  "cagr_pct": 12.5,"risk": "Medium", "zone": "North",   "anchored": False},
            {"locality": "Bagalur",              "pin": "562149", "avg_psf": 4200,  "cagr_pct": 15.0,"risk": "High",   "zone": "North",   "anchored": False},
            # ── South corridor ─────────────────────────────────────────────────────
            {"locality": "Electronic City",      "pin": "560100", "avg_psf": 5800,  "cagr_pct": 7.2, "risk": "Low",    "zone": "South",   "anchored": True},
            {"locality": "Bannerghatta Road",    "pin": "560076", "avg_psf": 7000,  "cagr_pct": 6.5, "risk": "Medium", "zone": "South",   "anchored": True},
            {"locality": "Kanakapura Road",      "pin": "560062", "avg_psf": 6200,  "cagr_pct": 8.2, "risk": "Medium", "zone": "South",   "anchored": False},
            {"locality": "Bommanahalli",         "pin": "560068", "avg_psf": 7500,  "cagr_pct": 7.0, "risk": "Low",    "zone": "South",   "anchored": False},
            # ── West corridor ──────────────────────────────────────────────────────
            {"locality": "Rajajinagar",          "pin": "560010", "avg_psf": 9200,  "cagr_pct": 5.8, "risk": "Low",    "zone": "West",    "anchored": False},
            {"locality": "Rajarajeshwari Nagar", "pin": "560098", "avg_psf": 6100,  "cagr_pct": 7.5, "risk": "Medium", "zone": "West",    "anchored": False},
            {"locality": "Mysore Road",          "pin": "560026", "avg_psf": 5500,  "cagr_pct": 7.8, "risk": "High",   "zone": "West",    "anchored": False},
            {"locality": "Tumkur Road",          "pin": "560073", "avg_psf": 5200,  "cagr_pct": 8.0, "risk": "High",   "zone": "West",    "anchored": False},
            {"locality": "Hoskote",              "pin": "562114", "avg_psf": 4500,  "cagr_pct": 13.0,"risk": "High",   "zone": "East",    "anchored": False},
        ]
        _RISK_ICON  = {"Low": "🟢", "Medium": "🟡", "High": "🔴"}

        st.subheader("Bengaluru Real Estate Investment Advisor")
        st.warning(
            "⚠️ PSF figures are sourced from NoBroker, 99acres and MagicBricks (Nov 2026). "
            "For unanchored localities, zone-level estimates are applied. "
            "Always verify with a registered property agent before any decision."
        )

        # ── Sub-tabs ───────────────────────────────────────────────────────────────
        _iv_sub1, _iv_sub2, _iv_sub3 = st.tabs([
            "🔍 Fair Price Check",
            "📊 Buy vs Rent vs Invest",
            "🗺️ Locality Explorer",
        ])

        # ════════════════════════════════════════════════════════════════════════════
        # SUB-TAB 1 — FAIR PRICE CHECKER
        # ════════════════════════════════════════════════════════════════════════════
        with _iv_sub1:
            st.markdown(
                "##### Is your builder quoting a fair price?\n"
                "Enter the locality, flat size, and what your builder is asking. "
                "We compare it against current market rates and tell you if you're overpaying."
            )
            _fpc_c1, _fpc_c2, _fpc_c3 = st.columns(3)
            with _fpc_c1:
                _fpc_locality = st.selectbox(
                    "Locality",
                    [p["locality"] for p in _LOCALITIES],
                    key="fpc_locality",
                )
            with _fpc_c2:
                _fpc_area = st.number_input(
                    "Flat size (sq ft)", min_value=300, max_value=5000, value=950, step=50, key="fpc_area"
                )
            with _fpc_c3:
                _fpc_quoted = st.number_input(
                    "Builder's asking price (₹ lakhs)", min_value=10, max_value=1000, value=120, step=5, key="fpc_quoted"
                )

            _fpc_data = next(p for p in _LOCALITIES if p["locality"] == _fpc_locality)
            _fpc_market_total_L = (_fpc_data["avg_psf"] * _fpc_area) / 100_000
            _fpc_quoted_psf     = (_fpc_quoted * 100_000) / _fpc_area
            _fpc_diff_L         = _fpc_quoted - _fpc_market_total_L
            _fpc_diff_pct       = (_fpc_diff_L / _fpc_market_total_L) * 100

            st.markdown("---")
            _r1, _r2, _r3 = st.columns(3)
            _r1.metric("Market rate (₹/sqft)", f"₹{_fpc_data['avg_psf']:,}", help="NoBroker/99acres Nov 2026 benchmark")
            _r2.metric("Your quoted rate (₹/sqft)", f"₹{_fpc_quoted_psf:,.0f}")
            _r3.metric(
                "Price difference",
                f"{'+'if _fpc_diff_L >= 0 else ''}₹{_fpc_diff_L:.1f}L",
                delta=f"{_fpc_diff_pct:+.1f}% vs market",
                delta_color="inverse",
            )

            if _fpc_diff_pct <= -10:
                st.success(
                    f"**Great deal.** The asking price is **{abs(_fpc_diff_pct):.1f}% below** market. "
                    f"At ₹{_fpc_quoted_psf:,.0f}/sqft vs market ₹{_fpc_data['avg_psf']:,}/sqft, "
                    f"you're saving ₹{abs(_fpc_diff_L):.1f}L. Verify construction quality and legal title."
                )
            elif _fpc_diff_pct <= 5:
                st.info(
                    f"**Fair price.** Within 5% of market benchmark. "
                    f"Market rate in {_fpc_locality} is ~₹{_fpc_data['avg_psf']:,}/sqft. "
                    f"Your effective rate is ₹{_fpc_quoted_psf:,.0f}/sqft — reasonable."
                )
            elif _fpc_diff_pct <= 15:
                st.warning(
                    f"**Slightly overpriced.** You're paying **₹{_fpc_diff_L:.1f}L more** than the market benchmark. "
                    f"This could reflect a newer building, amenities, or brand premium — or simply negotiation room. "
                    f"Try negotiating down to ₹{_fpc_market_total_L:.1f}L (₹{_fpc_data['avg_psf']:,}/sqft)."
                )
            else:
                st.error(
                    f"**Significantly overpriced.** You're being asked **{_fpc_diff_pct:.1f}% above** market — "
                    f"an extra ₹{_fpc_diff_L:.1f}L. Market benchmark for {_fpc_locality} is ₹{_fpc_data['avg_psf']:,}/sqft "
                    f"(total ~₹{_fpc_market_total_L:.1f}L for {_fpc_area} sqft). "
                    f"Negotiate hard or consider comparable localities."
                )

            if not _fpc_data["anchored"]:
                st.caption(
                    f"ℹ️ {_fpc_locality} PSF is a zone-level estimate ({_fpc_data['zone']} zone). "
                    "For anchored localities with live market data, check Whitefield, Sarjapur Road, HSR Layout, "
                    "Indiranagar, Koramangala, Hebbal, Yelahanka, Electronic City, Bannerghatta Road or Marathahalli."
                )

        # ════════════════════════════════════════════════════════════════════════════
        # SUB-TAB 2 — BUY vs RENT vs INVEST
        # ════════════════════════════════════════════════════════════════════════════
        with _iv_sub2:
            st.markdown(
                "##### Should you buy, or rent and invest the difference?\n"
                "We model your net wealth at the end of your chosen horizon across four paths: "
                "buy on EMI, rent and invest in Mutual Funds, invest in Gold, or park it in an FD."
            )

            _bri_c1, _bri_c2 = st.columns(2)
            with _bri_c1:
                st.markdown("**Property details**")
                _bri_price     = st.number_input("Property price (₹ lakhs)", 30, 500, 100, step=5, key="bri_price")
                _bri_down_pct  = st.slider("Down payment (%)", 10, 40, 20, step=5, key="bri_down")
                _bri_tenure    = st.slider("Loan tenure (years)", 5, 30, 20, step=5, key="bri_tenure")
                _bri_rate      = st.slider("Home loan rate (%)", 7.0, 11.0, 8.75, step=0.25, key="bri_rate")
                _bri_locality  = st.selectbox(
                    "Locality (for appreciation rate)",
                    [p["locality"] for p in _LOCALITIES],
                    index=5,
                    key="bri_locality",
                )
            with _bri_c2:
                st.markdown("**Your current rental situation**")
                _bri_rent      = st.number_input("Monthly rent you pay today (₹)", 5000, 150000, 35000, step=1000, key="bri_rent")
                _bri_rent_inc  = st.slider("Annual rent increase (%)", 3, 10, 5, step=1, key="bri_rent_inc")
                st.markdown("**Investment return assumptions**")
                _bri_mf_ret    = st.slider("Mutual Fund / Nifty CAGR (%)", 8, 18, 12, step=1, key="bri_mf")
                _bri_gold_ret  = st.slider("Gold CAGR (%)", 5, 15, 8, step=1, key="bri_gold")
                _bri_fd_ret    = st.slider("FD / RD interest rate (%)", 5, 9, 7, step=1, key="bri_fd")
                _bri_horizon   = st.slider("Analysis horizon (years)", 5, 25, 10, step=5, key="bri_horizon")

            # ── Calculations ───────────────────────────────────────────────────────
            _bri_loc_data     = next(p for p in _LOCALITIES if p["locality"] == _bri_locality)
            _bri_prop_cagr    = _bri_loc_data["cagr_pct"] / 100
            _bri_price_rs     = _bri_price * 1e5
            _bri_down_rs      = _bri_price_rs * (_bri_down_pct / 100)
            _bri_loan_rs      = _bri_price_rs - _bri_down_rs
            _bri_monthly_rate = (_bri_rate / 100) / 12
            _bri_n_months     = _bri_tenure * 12

            # Monthly EMI
            if _bri_monthly_rate > 0:
                _bri_emi = _bri_loan_rs * _bri_monthly_rate * (1 + _bri_monthly_rate)**_bri_n_months / \
                           ((1 + _bri_monthly_rate)**_bri_n_months - 1)
            else:
                _bri_emi = _bri_loan_rs / _bri_n_months

            # Path A: Buy on EMI
            # Net wealth = property value at horizon - outstanding loan
            _bri_h_months = _bri_horizon * 12
            _bri_prop_val_H = _bri_price_rs * (1 + _bri_prop_cagr) ** _bri_horizon
            # Outstanding loan after h years (if h < tenure)
            if _bri_horizon < _bri_tenure:
                _bri_paid_months = _bri_h_months
                _bri_outstanding = _bri_loan_rs * (1 + _bri_monthly_rate)**_bri_paid_months - \
                                   _bri_emi * ((1 + _bri_monthly_rate)**_bri_paid_months - 1) / _bri_monthly_rate
                _bri_outstanding = max(_bri_outstanding, 0)
            else:
                _bri_outstanding = 0
            _bri_buy_wealth = (_bri_prop_val_H - _bri_outstanding) / 1e5  # in lakhs

            # Path B: Rent + invest in MF
            # Invest the down payment as lump sum, invest (EMI - rent) monthly if EMI > rent
            _bri_lump_mf     = _bri_down_rs * (1 + _bri_mf_ret / 100) ** _bri_horizon
            _bri_monthly_sip = max(_bri_emi - _bri_rent, 0)  # extra cash freed up vs buying
            _bri_mf_monthly_r = (_bri_mf_ret / 100) / 12
            if _bri_mf_monthly_r > 0:
                _bri_sip_corpus = _bri_monthly_sip * ((1 + _bri_mf_monthly_r)**_bri_h_months - 1) / _bri_mf_monthly_r
            else:
                _bri_sip_corpus = _bri_monthly_sip * _bri_h_months
            # Also account for cumulative rent paid
            _bri_total_rent_paid = sum(
                _bri_rent * (1 + _bri_rent_inc / 100)**y * 12
                for y in range(_bri_horizon)
            )
            _bri_mf_wealth = (_bri_lump_mf + _bri_sip_corpus) / 1e5  # gross corpus, rent is a liability
            _bri_mf_net    = _bri_mf_wealth - (_bri_total_rent_paid / 1e5)  # subtract rent paid

            # Path C: Gold
            _bri_gold_wealth = (_bri_down_rs * (1 + _bri_gold_ret / 100) ** _bri_horizon) / 1e5
            _bri_gold_net    = _bri_gold_wealth - (_bri_total_rent_paid / 1e5)

            # Path D: FD
            _bri_fd_wealth = (_bri_down_rs * (1 + _bri_fd_ret / 100) ** _bri_horizon) / 1e5
            _bri_fd_net    = _bri_fd_wealth - (_bri_total_rent_paid / 1e5)

            st.markdown("---")
            st.markdown(f"#### Results at **{_bri_horizon}-year** horizon")

            _bri_m1, _bri_m2, _bri_m3, _bri_m4 = st.columns(4)
            _bri_best = max(_bri_buy_wealth, _bri_mf_net, _bri_gold_net, _bri_fd_net)

            def _bri_badge(val, best):
                return "🏆 " if abs(val - best) < 0.5 else ""

            _bri_m1.metric(
                f"{_bri_badge(_bri_buy_wealth, _bri_best)}🏠 Buy (EMI)",
                f"₹{_bri_buy_wealth:.1f}L",
                help=f"Property value ₹{_bri_prop_val_H/1e5:.1f}L minus outstanding loan ₹{_bri_outstanding/1e5:.1f}L"
            )
            _bri_m2.metric(
                f"{_bri_badge(_bri_mf_net, _bri_best)}📈 Rent + MF",
                f"₹{_bri_mf_net:.1f}L",
                help=f"MF corpus ₹{_bri_mf_wealth:.1f}L minus total rent paid ₹{_bri_total_rent_paid/1e5:.1f}L"
            )
            _bri_m3.metric(
                f"{_bri_badge(_bri_gold_net, _bri_best)}🥇 Rent + Gold",
                f"₹{_bri_gold_net:.1f}L",
                help=f"Gold corpus ₹{_bri_gold_wealth:.1f}L minus total rent paid ₹{_bri_total_rent_paid/1e5:.1f}L"
            )
            _bri_m4.metric(
                f"{_bri_badge(_bri_fd_net, _bri_best)}🏦 Rent + FD",
                f"₹{_bri_fd_net:.1f}L",
                help=f"FD corpus ₹{_bri_fd_wealth:.1f}L minus total rent paid ₹{_bri_total_rent_paid/1e5:.1f}L"
            )

            # Monthly cashflow comparison
            st.markdown("#### Monthly cashflow")
            _bri_cf1, _bri_cf2 = st.columns(2)
            _bri_cf1.info(
                f"**Buying route:** Monthly EMI = **₹{_bri_emi:,.0f}**  \n"
                f"Down payment upfront: ₹{_bri_down_rs/1e5:.1f}L"
            )
            _bri_cf2.info(
                f"**Rent route:** Monthly rent = **₹{_bri_rent:,}**  \n"
                f"Monthly surplus vs buying: ₹{max(_bri_emi-_bri_rent,0):,.0f} (invested in MF/Gold/FD)"
            )

            if _bri_emi > _bri_rent:
                st.caption(
                    f"EMI (₹{_bri_emi:,.0f}) > rent (₹{_bri_rent:,}) by ₹{_bri_emi-_bri_rent:,.0f}/month. "
                    f"If you rent, you save ₹{((_bri_emi-_bri_rent)*12)/1e5:.1f}L/year that gets invested."
                )
            else:
                st.caption(
                    f"Rent (₹{_bri_rent:,}) > EMI (₹{_bri_emi:,.0f}) by ₹{_bri_rent-_bri_emi:,.0f}/month. "
                    f"Buying actually saves you money monthly compared to renting in this scenario."
                )

            # Key insight box
            _winner = max(
                [("Buy on EMI", _bri_buy_wealth), ("Rent + MF", _bri_mf_net),
                 ("Rent + Gold", _bri_gold_net), ("Rent + FD", _bri_fd_net)],
                key=lambda x: x[1]
            )
            st.success(
                f"**{_bri_horizon}-year winner: {_winner[0]}** at ₹{_winner[1]:.1f}L net wealth. "
                f"Property appreciation assumed at {_bri_loc_data['cagr_pct']}% CAGR for {_bri_locality}. "
                f"Adjust sliders above to model your actual scenario."
            )
            st.caption(
                "Note: Buy wealth = property value minus outstanding loan (not cash in hand until sold). "
                "Rent+invest wealth = investment corpus minus all rent paid over the horizon. "
                "Tax implications, maintenance costs and registration charges not included."
            )

        # ════════════════════════════════════════════════════════════════════════════
        # SUB-TAB 3 — LOCALITY EXPLORER
        # ════════════════════════════════════════════════════════════════════════════
        with _iv_sub3:
            st.caption(
                "PSF from NoBroker/99acres Nov 2026 (anchored ✓) or zone-level estimate. "
                "CAGR is 9-year historical (2017-2026) based on Kaggle 2017 baseline."
            )
            _iv_c1, _iv_c2 = st.columns(2)
            with _iv_c1:
                _iv_budget = st.slider("Your budget (₹ lakhs)", 30, 300, 100, step=5, key="iv_budget")
            with _iv_c2:
                _iv_area = st.slider("Desired size (sq ft)", 400, 2000, 950, step=50, key="iv_area")

            _iv_target_psf = (_iv_budget * 100_000) / _iv_area
            st.info(f"Your budget implies **₹{_iv_target_psf:,.0f} / sq ft** for a {_iv_area} sq ft flat at ₹{_iv_budget}L")

            _iv_within  = sorted(
                [p for p in _LOCALITIES if p["avg_psf"] <= _iv_target_psf],
                key=lambda x: abs(x["avg_psf"] - _iv_target_psf)
            )
            _iv_stretch = sorted(
                [p for p in _LOCALITIES if _iv_target_psf < p["avg_psf"] <= _iv_target_psf * 1.20],
                key=lambda x: x["avg_psf"] - _iv_target_psf
            )

            if not _iv_within and not _iv_stretch:
                st.warning("No localities match this budget and size. Try increasing budget or reducing size.")
            else:
                if _iv_within:
                    st.markdown("#### ✅ Within your budget")
                    for _iv_p in _iv_within[:8]:
                        _iv_col1, _iv_col2, _iv_col3, _iv_col4 = st.columns([3, 2, 2, 3])
                        _iv_col1.markdown(f"**{_iv_p['locality']}** `{_iv_p['pin']}`{'  ✓' if _iv_p['anchored'] else ''}")
                        _iv_col2.markdown(f"₹{_iv_p['avg_psf']:,} / sqft")
                        _iv_col3.markdown(f"+{_iv_p['cagr_pct']}% CAGR")
                        _iv_col4.markdown(
                            f"{_RISK_ICON[_iv_p['risk']]} **{_iv_p['risk']}** · {_iv_p['zone']}  \n"
                            f"<small>₹{(_iv_target_psf - _iv_p['avg_psf']):,.0f}/sqft headroom</small>",
                            unsafe_allow_html=True,
                        )
                if _iv_stretch:
                    st.markdown("#### 🔶 Slight stretch (up to 20% over budget)")
                    for _iv_p in _iv_stretch[:4]:
                        _iv_col1, _iv_col2, _iv_col3, _iv_col4 = st.columns([3, 2, 2, 3])
                        _iv_col1.markdown(f"**{_iv_p['locality']}** `{_iv_p['pin']}`{'  ✓' if _iv_p['anchored'] else ''}")
                        _iv_col2.markdown(f"₹{_iv_p['avg_psf']:,} / sqft")
                        _iv_col3.markdown(f"+{_iv_p['cagr_pct']}% CAGR")
                        _iv_col4.markdown(
                            f"{_RISK_ICON[_iv_p['risk']]} **{_iv_p['risk']}** · {_iv_p['zone']}  \n"
                            f"<small style='color:#c0392b'>₹{(_iv_p['avg_psf'] - _iv_target_psf):,.0f}/sqft over</small>",
                            unsafe_allow_html=True,
                        )

            st.markdown("---")
            st.markdown("#### Full locality table")
            _iv_df = _pd_invest.DataFrame(_LOCALITIES)
            _iv_df = _iv_df[["locality","pin","avg_psf","cagr_pct","risk","zone","anchored"]]
            _iv_df.columns = ["Locality","PIN","Avg PSF (₹)","CAGR %","Risk","Zone","Anchored"]
            _iv_df["Est. cost (₹L)"] = (_iv_df["Avg PSF (₹)"] * _iv_area / 100_000).round(1)
            _iv_df["vs. Budget"] = (_iv_df["Avg PSF (₹)"] - _iv_target_psf).round(0).astype(int)
            _iv_df = _iv_df.sort_values("vs. Budget", key=lambda s: s.abs()).reset_index(drop=True)
            st.dataframe(
                _iv_df,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Locality":       st.column_config.TextColumn("Locality",      width="medium"),
                    "PIN":            st.column_config.TextColumn("PIN",           width="small"),
                    "Avg PSF (₹)":   st.column_config.NumberColumn("Avg PSF (₹)", width="small", format="₹%d"),
                    "CAGR %":        st.column_config.NumberColumn("CAGR %",       width="small", format="%.1f%%"),
                    "Risk":          st.column_config.TextColumn("Risk",           width="small"),
                    "Zone":          st.column_config.TextColumn("Zone",           width="small"),
                    "Anchored":      st.column_config.CheckboxColumn("Live data",  width="small"),
                    "Est. cost (₹L)":st.column_config.NumberColumn("Est. cost (₹L)", width="small", format="%.1f L"),
                    "vs. Budget":    st.column_config.NumberColumn("vs. Budget (₹/sqft)", width="small", format="%+d"),
                },
            )

    # ── Tab 4: Document Q&A ────────────────────────────────────────────────
    with _tab_docqa:
        import io as _io_docqa
        import requests as _req_docqa
        import numpy as _np_docqa

        st.markdown("### 📄 Sales Deed / Agreement Analyzer")
        st.markdown(
            "Upload your **sales deed, builder-buyer agreement, or any real estate document** (PDF). "
            "The tool will answer your questions by reading your document *and* cross-checking against "
            "Karnataka RERA regulations — flagging anything that looks non-compliant."
        )

        _OPENAI_KEY = _secret("OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY", "")
        _EMBED_MODEL = "text-embedding-3-small"
        _QA_MODEL    = _secret("LLM_MODEL") or os.environ.get("LLM_MODEL", "gpt-4o-mini")

        st.subheader("Ask questions about your RERA document")
        st.markdown("Upload any RERA-related PDF — sale agreement, allotment letter, project brochure — and ask questions in plain language.")

        # ── Past-session read-only view (no file needed) ──────────────────────
        _dq_past_file = st.session_state.get("_active_dqa_file")
        _dq_past_hist = st.session_state.get("dq_history", [])
        if _dq_past_file and _dq_past_hist and not st.session_state.get("dq_pdf"):
            st.info(f"📄 Viewing past session: **{_dq_past_file}**  — upload the same file below to ask new questions.")
            for _phi, _phentry in enumerate(reversed(_dq_past_hist)):
                with st.chat_message("user"):
                    st.markdown(_phentry["q"])
                with st.chat_message("assistant"):
                    if "COMPLIANCE CONCERN" in _phentry["a"]:
                        st.warning("One or more compliance concerns found in your document. See details below.")
                    st.markdown("### Analysis")
                    st.markdown(_phentry["a"])
                    st.caption("Sources: your uploaded document + Karnataka RERA regulatory corpus.")
            if st.button("✕ Close past session", key="dq_close_past"):
                st.session_state.pop("_active_dqa_file", None)
                st.session_state.pop("dq_history", None)
                st.rerun()
            st.markdown("---")

        if not _OPENAI_KEY:
            st.error("OPENAI_API_KEY not set. Add it to Streamlit Secrets.")
        else:
            _dq_uploaded = st.file_uploader("Upload PDF", type=["pdf"], key="dq_pdf")

            if _dq_uploaded:
                _dq_raw = _dq_uploaded.read()

                def _dq_extract_chunks(file_bytes, chunk_words=400):
                    import pdfplumber as _pdfplumber
                    _chunks = []
                    with _pdfplumber.open(_io_docqa.BytesIO(file_bytes)) as _pdf:
                        for _pg_num, _pg in enumerate(_pdf.pages, 1):
                            _text = _pg.extract_text() or ""
                            _words = _text.split()
                            for _i in range(0, len(_words), chunk_words):
                                _chunk = " ".join(_words[_i : _i + chunk_words])
                                if _chunk.strip():
                                    _chunks.append({"text": _chunk, "page": _pg_num})
                    return _chunks

                def _dq_embed(texts):
                    _r = _req_docqa.post(
                        "https://api.openai.com/v1/embeddings",
                        headers={"Authorization": f"Bearer {_OPENAI_KEY}"},
                        json={"model": _EMBED_MODEL, "input": texts},
                        timeout=30,
                    )
                    _r.raise_for_status()
                    return [d["embedding"] for d in _r.json()["data"]]

                def _dq_top_k(q_vec, doc_vecs, k=3):
                    _q = _np_docqa.array(q_vec)
                    _D = _np_docqa.array(doc_vecs)
                    _scores = _D @ _q / (_np_docqa.linalg.norm(_D, axis=1) * _np_docqa.linalg.norm(_q) + 1e-9)
                    return _scores.argsort()[::-1][:k].tolist()

                def _dq_ask(doc_context, rera_context, question, pages):
                    _sys = (
                        "You are a RERA compliance expert helping a first-time home buyer in Karnataka. "
                        "You have two sources: (1) the buyer's uploaded document, and (2) Karnataka RERA regulations. "
                        "Answer the question using both sources. "
                        "If a clause in the document contradicts or falls short of RERA requirements, "
                        "clearly flag it as: 'COMPLIANCE CONCERN: ...' "
                        "Cite page numbers from the uploaded document where relevant."
                    )
                    _usr = (
                        f"UPLOADED DOCUMENT (pages {', '.join(pages)}):\n{doc_context}\n\n"
                        f"KARNATAKA RERA REGULATIONS:\n{rera_context}\n\n"
                        f"Question: {question}"
                    )
                    _r = _req_docqa.post(
                        "https://api.openai.com/v1/chat/completions",
                        headers={"Authorization": f"Bearer {_OPENAI_KEY}"},
                        json={"model": _QA_MODEL, "messages": [{"role": "system", "content": _sys}, {"role": "user", "content": _usr}], "temperature": 0.2},
                        timeout=45,
                    )
                    _r.raise_for_status()
                    return _r.json()["choices"][0]["message"]["content"]

                # dq_session_id is initialised at app startup; just read it here
                _dq_sid = st.session_state["dq_session_id"]

                # ── Supabase helpers for Document Q&A history ──────────────────
                def _dq_sb_save(session_id, filename, question, answer, followups):
                    """Persist one Q&A turn to document_qa_history."""
                    try:
                        import requests as _rq
                        _sb_url = _secret("SUPABASE_URL")
                        _sb_key = _secret("SUPABASE_SERVICE_KEY") or _secret("SUPABASE_KEY")
                        if not _sb_url or not _sb_key:
                            return
                        _rq.post(
                            f"{_sb_url}/rest/v1/document_qa_history",
                            headers={
                                "apikey": _sb_key,
                                "Authorization": f"Bearer {_sb_key}",
                                "Content-Type": "application/json",
                                "Prefer": "return=minimal",
                            },
                            json={
                                "session_id": session_id,
                                "filename": filename,
                                "question": question,
                                "answer": answer,
                                "followups": followups,
                            },
                            timeout=10,
                        )
                    except Exception:
                        pass  # history persistence is best-effort

                def _dq_sb_load(session_id, filename):
                    """Load prior Q&A turns for this session + file from Supabase."""
                    try:
                        import requests as _rq
                        _sb_url = _secret("SUPABASE_URL")
                        _sb_key = _secret("SUPABASE_SERVICE_KEY") or _secret("SUPABASE_KEY")
                        if not _sb_url or not _sb_key:
                            return []
                        _resp = _rq.get(
                            f"{_sb_url}/rest/v1/document_qa_history",
                            headers={
                                "apikey": _sb_key,
                                "Authorization": f"Bearer {_sb_key}",
                            },
                            params={
                                "session_id": f"eq.{session_id}",
                                "filename": f"eq.{filename}",
                                "order": "created_at.desc",
                                "limit": "50",
                            },
                            timeout=10,
                        )
                        rows = _resp.json() if _resp.ok else []
                        return [{"q": r["question"], "a": r["answer"], "followups": r.get("followups") or []} for r in rows]
                    except Exception:
                        return []

                # ── Cache chunks + embeddings in session_state so they survive tab switches
                _dq_cache_key   = f"dq_vecs_{_dq_uploaded.name}_{len(_dq_raw)}"
                _dq_chunks_key  = f"dq_chunks_{_dq_uploaded.name}_{len(_dq_raw)}"
                _dq_fname_key   = "dq_current_file"

                # If a new file was uploaded, clear in-memory history and reload from Supabase
                if st.session_state.get(_dq_fname_key) != _dq_uploaded.name:
                    st.session_state[_dq_fname_key] = _dq_uploaded.name
                    st.session_state["dq_history"] = _dq_sb_load(_dq_sid, _dq_uploaded.name)
                    st.session_state.pop("dq_question", None)

                if _dq_chunks_key not in st.session_state:
                    with st.spinner("Reading and indexing your document…"):
                        _chunks_tmp = _dq_extract_chunks(_dq_raw)
                        if not _chunks_tmp:
                            st.error("Could not extract text — this PDF may be a scanned image. Try a text-based PDF.")
                            st.stop()
                        st.session_state[_dq_chunks_key] = _chunks_tmp
                        st.session_state[_dq_cache_key]  = _dq_embed([c["text"] for c in _chunks_tmp])

                _dq_chunks = st.session_state[_dq_chunks_key]
                _dq_vecs   = st.session_state[_dq_cache_key]

                if "dq_history" not in st.session_state:
                    st.session_state["dq_history"] = []

                st.success(f"Indexed {len(_dq_chunks)} sections from **{_dq_uploaded.name}**")
                st.caption("Questions are answered using your document AND Karnataka RERA regulations combined.")

                # Quick-check suggestion buttons
                _dq_suggestions = [
                    "Is the RERA registration number mentioned?",
                    "What is the promised possession date?",
                    "What penalty applies if builder delays possession?",
                    "Is the carpet area clearly defined?",
                    "Are there any clauses that may violate RERA?",
                ]
                # Pre-fill from follow-up button click (must happen before input renders)
                if "_dq_pending" in st.session_state:
                    st.session_state["dq_question"] = st.session_state.pop("_dq_pending")

                # Render full session history (oldest at top, newest at bottom)
                for _hi, _hentry in enumerate(reversed(st.session_state.get("dq_history", []))):
                    with st.chat_message("user"):
                        st.markdown(_hentry["q"])
                    with st.chat_message("assistant"):
                        if "COMPLIANCE CONCERN" in _hentry["a"]:
                            st.warning("One or more compliance concerns found in your document. See details below.")
                        st.markdown("### Analysis")
                        st.markdown(_hentry["a"])
                        st.caption("Sources: your uploaded document + Karnataka RERA regulatory corpus.")

                        # Follow-up suggestions per history entry
                        if _hentry.get("followups"):
                            st.markdown("---")
                            st.markdown("**Dig deeper — suggested follow-ups:**")
                            _fq_cols = st.columns(len(_hentry["followups"]))
                            _rev_idx = len(st.session_state["dq_history"]) - 1 - _hi
                            for _fi, _fq in enumerate(_hentry["followups"]):
                                if _fq_cols[_fi].button(_fq, key=f"dq_fup_{_rev_idx}_{_fi}"):
                                    st.session_state["_dq_pending"] = _fq
                                    st.rerun()

                st.markdown("---")

                # Quick-check buttons — shown below history so user can keep going
                if not st.session_state.get("dq_history"):
                    st.markdown("**Quick checks to get started:**")
                else:
                    st.markdown("**Ask another question:**")
                _dq_cols = st.columns(len(_dq_suggestions))
                for _dq_i, _dq_sq in enumerate(_dq_suggestions):
                    if _dq_cols[_dq_i].button(_dq_sq, key=f"dq_sq_{_dq_i}", use_container_width=True):
                        st.session_state["dq_question"] = _dq_sq

                _mic_widget("dq_question", _OPENAI_KEY, "mic_docqa")
                _dq_question = st.chat_input(
                    "Ask a question about your document…",
                    key="dq_text_input",
                ) or st.session_state.pop("dq_question", None)

                if _dq_question:
                    # Avoid re-running the same question already at the top of history
                    _dq_last_q = st.session_state["dq_history"][0]["q"] if st.session_state["dq_history"] else ""
                    if _dq_question != _dq_last_q:
                        with st.spinner("Searching document and RERA regulations…"):
                            _dq_q_vec  = _dq_embed([_dq_question])[0]
                            _dq_top    = _dq_top_k(_dq_q_vec, _dq_vecs, k=4)
                            _dq_ctx    = "\n\n---\n\n".join(_dq_chunks[_i]["text"] for _i in _dq_top)
                            _dq_pgs    = [str(_dq_chunks[_i]["page"]) for _i in _dq_top]
                            try:
                                _dq_rera_hits = retriever.invoke(_dq_question)
                                _dq_rera_ctx  = "\n\n---\n\n".join(d.page_content for d in _dq_rera_hits[:3])
                            except Exception:
                                _dq_rera_ctx  = "RERA corpus not available in this session."
                            _dq_ans = _dq_ask(_dq_ctx, _dq_rera_ctx, _dq_question, _dq_pgs)
                        _dq_followups = get_contextual_followups("RERA", _dq_question, _dq_ans)
                        # Prepend to history (newest first internally)
                        st.session_state["dq_history"].insert(0, {
                            "q": _dq_question,
                            "a": _dq_ans,
                            "followups": _dq_followups,
                        })
                        # Persist to Supabase (best-effort, non-blocking)
                        _dq_sb_save(_dq_sid, _dq_uploaded.name, _dq_question, _dq_ans, _dq_followups)
                        # Invalidate sidebar Q&A history cache so it refreshes on next render
                        st.session_state.pop("_dqa_history_sidebar", None)
                        # Clear input ready for next question
                        st.session_state.pop("dq_question", None)
                        st.rerun()

                # Clear history button
                if st.session_state.get("dq_history"):
                    if st.button("🗑️ Clear Q&A history", key="dq_clear_hist"):
                        st.session_state["dq_history"] = []
                        st.rerun()
            else:
                st.info("Upload your sales deed or builder-buyer agreement PDF to get started.")

    # ── Tab 1: Compliance Chat (all original chatbot code runs inside this tab)
    _rera_tab_ctx = _tab_chat
else:
    # Healthcare: no tabs, use a no-op context manager
    import contextlib
    _rera_tab_ctx = contextlib.nullcontext()

with _rera_tab_ctx:

    if client is None:
        st.warning("Answer generation is not configured. Source search still works.")

    _scope_tail = ""
    if n_scoped is not None and _any_filter:
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

                # Bookmark + Checklist action row
                _bk_col, _cl_col, _ = st.columns([1, 1, 6])
                _bk_saved_key = f"_bm_saved_{msg_idx}"
                if _bk_col.button(
                    "🔖 Save" if not st.session_state.get(_bk_saved_key) else "✅ Saved",
                    key=f"bm_btn_{msg_idx}",
                    help="Save this answer to your bookmarks",
                    disabled=st.session_state.get(_bk_saved_key, False),
                ):
                    _cu_now = _current_user()
                    if not _cu_now.get("id"):
                        st.toast("Sign in to save bookmarks.", icon="🔐")
                    else:
                        _ok = log_bookmark(domain, _prev_q, msg["content"],
                                           msg.get("sources", []))
                        if _ok:
                            st.session_state[_bk_saved_key] = True
                            st.toast("Answer saved to bookmarks!", icon="🔖")
                            st.rerun()
                        else:
                            st.toast("Could not save bookmark. Try again.", icon="⚠️")

                _cl_key = f"_show_cl_{msg_idx}"
                _cl_role = _current_user().get("role", "user")
                _cl_allowed = _cl_role in ("compliance_officer", "admin")
                _cl_label = "📋 Hide Checklist" if st.session_state.get(_cl_key) else "📋 Checklist"
                if _cl_allowed and _cl_col.button(_cl_label, key=f"cl_btn_{msg_idx}",
                                  help="Convert answer to a compliance checklist"):
                    st.session_state[_cl_key] = not st.session_state.get(_cl_key, False)

                if _cl_allowed and st.session_state.get(_cl_key):
                    _items = _extract_checklist(msg["content"])
                    st.markdown(
                        "<div style='font-size:.78rem;font-weight:600;color:#CFE0F2;"
                        "margin:.5rem 0 .3rem'>📋 Compliance Action Checklist</div>",
                        unsafe_allow_html=True)
                    if _items:
                        for _ci, _item in enumerate(_items, 1):
                            st.checkbox(f"{_ci}. {_item}", key=f"cl_check_{msg_idx}_{_ci}")
                    else:
                        st.info("No specific action items found in this answer. "
                                "Try asking a more targeted compliance question, "
                                "for example: 'What steps must a telemedicine platform follow?'",
                                icon="ℹ️")

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

    _mic_widget("pending_q", _secret("OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY", ""), "mic_chat")
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

            # Apply jurisdiction filter on top of role filter. If a jurisdiction
            # is selected, keep only titles that match its needles. When the role
            # filter is empty (all stakeholders), jurisdiction alone drives the list.
            if jurisdiction_needles:
                universe = scope_universe(domain)
                jur_set = {t for t in universe
                           if any(n in t.lower() for n in jurisdiction_needles)}
                if scope_titles:
                    scope_titles = sorted(t for t in scope_titles if t in jur_set)
                else:
                    scope_titles = sorted(jur_set)

            scope_empty = bool(scope_needles or jurisdiction_needles) and not scope_titles

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
                    log_query(domain, prompt, answer, best_sim, results, True)  # 3A-patch-applied
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
                    st.caption(
                        "⚠️ This answer is AI-generated from regulatory documents. "
                        "Always verify with the official source before acting on it."
                    )

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
                        f"border-radius:10px;'>{badge_icon} {badge_label}"
                        f"<span style='font-weight:400;opacity:.75;margin-left:6px'>"
                        f"(score: {best_sim:.2f})</span></span>",
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

                # Auto-save this conversation as a session for signed-in users
                _sess_cu = _current_user()
                if _sess_cu.get("id"):
                    _sess_msgs = st.session_state.messages
                    # Title = first user question (trimmed)
                    _first_q = next((m["content"] for m in _sess_msgs if m["role"] == "user"), "Conversation")
                    _sess_title = (_first_q[:80] + "…") if len(_first_q) > 80 else _first_q
                    _existing_sid = st.session_state.get("_active_session_id")
                    _new_sid = save_session(_existing_sid, _sess_title, domain, _sess_msgs)
                    if _new_sid:
                        st.session_state["_active_session_id"] = _new_sid
                        st.session_state["_active_session_title"] = _sess_title
                        # Refresh session list so sidebar shows latest
                        st.session_state["_user_sessions"] = fetch_user_sessions(20)

                # Action row: copy + share + feedback + bookmark buttons
                if not low_confidence and client is not None and daily_usage() < DAILY_LIMIT:
                    render_copy_button(answer, f"new_{new_msg_idx}")
                    render_share_button(prompt, answer, results, f"new_{new_msg_idx}")
                    fb_cols = st.columns([1, 1, 1, 7])
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
                    # Save bookmark — available immediately after answer is generated
                    _new_bk_key = f"_bm_saved_{new_msg_idx}"
                    if fb_cols[2].button(
                        "🔖 Save" if not st.session_state.get(_new_bk_key) else "✅ Saved",
                        key=f"bm_btn_new_{new_msg_idx}",
                        help="Save this answer to your benchmarks",
                        disabled=st.session_state.get(_new_bk_key, False),
                    ):
                        _cu_now = _current_user()
                        if not _cu_now.get("id"):
                            st.toast("Sign in to save benchmarks.", icon="🔐")
                        else:
                            _ok = log_bookmark(domain, prompt, answer, results)
                            if _ok:
                                st.session_state[_new_bk_key] = True
                                st.toast("Answer saved to benchmarks!", icon="🔖")
                                st.rerun()
                            else:
                                st.toast("Could not save benchmark. Try again.", icon="⚠️")

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
