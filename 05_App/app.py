"""
Indian Regulatory Compliance Intelligence Assistant
Streamlit interface.

    streamlit run app.py

Requires: a built index (python ingest_corpus.py --rebuild) and 05_App/.env with
OPENAI_API_KEY. The plain earlier version is kept as app_working_27sep.py.

Three things here exist because of measured behaviour on 27 Sep, each documented
at its implementation:

  * Scope filters      - four Karnataka penalty circulars are character-identical
                         apart from the financial year and sit within 0.03 cosine;
                         ~20 CEA standards templates repeat clauses per facility
                         type. The distinguishing fact is categorical, so it
                         belongs in a filter, not a similarity score.
  * Adaptive reasoning - dropping reasoning_effort to "low" cut latency 60s -> 30s
                         AND improved the FY2023-24 answer, because less reasoning
                         meant less gap-filling. Effort is now chosen per query.
  * Answer cache       - embeddings run locally and cost nothing; generation is
                         the only API spend, so repeats should never be paid for
                         twice.

NOTE: none of this belongs in run_evaluation.py. The professor fixed the models,
and the RAG-vs-base comparison is only valid if both run identically.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import sys
import tarfile
import urllib.request
from pathlib import Path

# Chroma requires SQLite >= 3.35. Streamlit Community Cloud's image ships an
# older system sqlite3, so Chroma fails on first boot there every time. The
# standard remedy is to shim in pysqlite3 BEFORE anything imports chromadb.
# pysqlite3-binary is Linux-only and simply absent locally on Windows, where the
# system sqlite3 is new enough - hence the quiet pass.
try:                                                        # pragma: no cover
    __import__("pysqlite3")
    sys.modules["sqlite3"] = sys.modules.pop("pysqlite3")
except Exception:
    pass

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rag_query import (  # noqa: E402
    COLLECTION, EMBED_MODEL, INDEX_DIR, LLM_BASE_URL, LLM_MODEL,
    SYSTEM_PROMPT, format_sources, load_env, retrieve,
)

st.set_page_config(page_title="Regulatory Compliance Intelligence",
                   page_icon="📋", layout="wide",
                   initial_sidebar_state="expanded")

load_env()


def _secrets_into_env() -> None:
    """Streamlit Cloud has no .env - keys come from its Secrets manager.

    Reading st.secrets raises when no secrets file exists, which is the normal
    local case, so this must never be allowed to fail. .env wins where both
    exist, so local behaviour is unchanged.
    """
    for k in ("OPENAI_API_KEY", "TOGETHER_API_KEY",
              "LLM_REASONING_EFFORT", "INDEX_URL"):
        if os.getenv(k):
            continue
        try:
            if k in st.secrets:
                os.environ[k] = str(st.secrets[k])
        except Exception:
            return


_secrets_into_env()

BASE_EFFORT = os.getenv("LLM_REASONING_EFFORT", "low").strip()
CACHE_FILE = Path(__file__).resolve().parent / ".answer_cache.json"
INDEX_URL = os.getenv("INDEX_URL", "").strip()

CORPUS_DOCS, CORPUS_CHUNKS = 345, 25_955

TIER_BADGE = {
    "A": ("Core regulatory", "#0F6B3A", "binding"),
    "B": ("Sector regulation", "#1B4F8C", "binding within sector"),
    "C": ("Supporting guidance", "#9A6A12", "explanatory, not binding"),
    "D": ("Draft / proposed", "#B4521A", "NOT binding"),
    "E": ("Review", "#8C1123", "binding status unverified"),
}

DOMAIN_LABEL = {"Healthcare": "Healthcare", "RERA": "Real Estate / RERA"}
DOMAIN_BLURB = {
    "Healthcare": "Clinical establishments, NMC regulation and medical education, "
                  "PM-JAY, telemedicine, ABDM and health-data privacy.",
    "RERA": "The Real Estate (Regulation and Development) Act, 2016 and Karnataka "
            "RERA rules, circulars, notifications and orders.",
}

RERA_YEARS = ["All years", "2022-23", "2023-24", "2024-25", "2025-26", "2026-27"]

HEALTHCARE_FACILITIES = {
    "All establishment types": [],
    "Hospital — Level 2": ["hospital(level 2", "hospital (level 2"],
    "Hospital — Level 3": ["hospital(level 3", "hospital (level 3"],
    "Clinic / Polyclinic": ["clinic_polyclinic", "clinic/polyclinic"],
    "Mobile clinic": ["mobile clinic", "mobile dental van"],
    "Dental": ["dental lab", "mobile dental"],
    "Cosmetology / aesthetic": ["cosmetology", "hair transplant"],
    "Physiotherapy centre": ["physiotherapy"],
    "Blood centre": ["blood centre", "blood transfusion"],
    "Mortuary / sample collection": ["mortuary", "sample collection"],
}

SUGGESTED = {
    "RERA": [
        "What percentage of the amounts realised from allottees must a promoter "
        "deposit in a separate account, and what may it be used for?",
        "What penalty applies for non-submission of the annual audit report?",
        "When must a promoter submit quarterly progress reports?",
        "Which projects are exempt from registration under the Act?",
    ],
    "Healthcare": [
        "How long must a clinical establishment retain patient medical records?",
        "What are the requirements for empanelment of a hospital under PM-JAY?",
        "Which medicines may be prescribed under the Telemedicine Practice Guidelines?",
        "What qualifications are required for faculty in a medical institution?",
    ],
}


# ── resources ────────────────────────────────────────────────────────────────
@st.cache_resource(show_spinner=False)
def ensure_index() -> bool:
    """Make sure the Chroma index exists locally, fetching it if it does not.

    The index is ~290MB on disk. GitHub hard-rejects any single file over
    100MB, so it cannot live in the repository; it is published as a GitHub
    Release asset instead (2GB limit, no LFS quota) and pulled on first boot.
    Rebuilding it on the server is not an option - that is a six-hour job.

    Locally the directory already exists and this returns immediately.
    """
    if INDEX_DIR.exists() and any(INDEX_DIR.iterdir()):
        return True
    if not INDEX_URL:
        return False

    archive = INDEX_DIR.parent / "_index_download.tar.gz"
    try:
        with st.status("Fetching the corpus index (first run only)…",
                       expanded=True) as status:
            st.write(f"Downloading from {INDEX_URL.split('/')[2]} — about 150 MB.")
            urllib.request.urlretrieve(INDEX_URL, archive)   # noqa: S310
            st.write("Extracting…")
            with tarfile.open(archive, "r:gz") as tar:
                try:
                    tar.extractall(INDEX_DIR.parent, filter="data")
                except TypeError:          # filter= added in Python 3.12
                    tar.extractall(INDEX_DIR.parent)
            archive.unlink(missing_ok=True)
            status.update(label="Corpus index ready", state="complete",
                          expanded=False)
        return INDEX_DIR.exists() and any(INDEX_DIR.iterdir())
    except Exception as exc:                                 # noqa: BLE001
        archive.unlink(missing_ok=True)
        st.error(f"Could not fetch the corpus index: {exc}")
        return False


@st.cache_resource(show_spinner="Loading the corpus index…")
def get_store():
    # The "is there an index" check lives OUTSIDE this function on purpose:
    # @st.cache_resource would pin a None return for the life of the process.
    from langchain_chroma import Chroma
    from langchain_huggingface import HuggingFaceEmbeddings
    emb = HuggingFaceEmbeddings(model_name=EMBED_MODEL,
                                encode_kwargs={"normalize_embeddings": True})
    return Chroma(collection_name=COLLECTION, embedding_function=emb,
                  persist_directory=str(INDEX_DIR))


@st.cache_resource
def get_client():
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        return None
    from openai import OpenAI
    return OpenAI(api_key=key, base_url=LLM_BASE_URL) if LLM_BASE_URL else OpenAI(api_key=key)


@st.cache_resource
def answer_cache() -> dict:
    """Question -> answer, persisted to disk.

    Retrieval is local and free; generation is the only API spend, so a repeat
    should never be billed twice. Persisting to a file rather than session state
    means the cache survives a restart - which also allows warming it by running
    the demo questions once beforehand.
    """
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def cache_put(key: str, value: dict) -> None:
    store = answer_cache()
    store[key] = value
    try:
        CACHE_FILE.write_text(json.dumps(store, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass  # an unwritable cache must never break an answer


def cache_key(question: str, domain: str, scope: str, k: int,
              drafts: bool, review: bool, effort: str) -> str:
    raw = f"{LLM_MODEL}|{effort}|{domain}|{scope}|{k}|{drafts}|{review}|{question.strip().lower()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


# ── adaptive reasoning ───────────────────────────────────────────────────────
def choose_effort(results, question: str) -> tuple[str, list[str]]:
    """Pick reasoning effort from retrieval signals, and say why.

    Cost on a reasoning model scales with reasoning tokens, so this is the axis
    that actually drives spend. Routing EFFORT rather than switching MODELS
    keeps a single model, which matters: the professor fixed the models, and the
    RAG-vs-base comparison is only meaningful if both sides run identically.
    Multi-model escalation is a roadmap item, not something to smuggle in here.

    Authority tier is not a difficulty signal on its own - a tier-A Act can
    answer a trivial lookup. What actually predicts difficulty is weak
    similarity, many documents to reconcile, non-binding material needing
    careful handling, and mixed authority that may conflict.
    """
    if not results:
        return "low", ["no sources retrieved"]

    top = max(r["similarity"] for r in results)
    docs = len({r["source_file"] for r in results})
    tiers = {r["tier"] for r in results}

    score, why = 0, []
    if top < 0.75:
        score += 2; why.append(f"weak top match ({top:.2f})")
    elif top < 0.85:
        score += 1; why.append(f"moderate top match ({top:.2f})")
    if docs >= 4:
        score += 1; why.append(f"{docs} documents to reconcile")
    if tiers & {"D", "E"}:
        score += 2; why.append("non-binding material needs careful handling")
    if "A" in tiers and "C" in tiers:
        score += 1; why.append("binding and guidance both present")
    if len(question) > 180 or question.count("?") > 1:
        score += 1; why.append("multi-part question")

    effort = "minimal" if score == 0 else "low" if score <= 1 else \
             "medium" if score <= 3 else "high"
    if not why:
        why.append(f"strong single-source match ({top:.2f})")
    return effort, why


# ── styling ──────────────────────────────────────────────────────────────────
CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Source+Serif+4:opsz,wght@8..60,600;8..60,700&display=swap');

:root{
  --ink:#0E2439; --ink-2:#3C4F66; --muted:#6B7C93;
  --line:#E3E9F2; --paper:#F6F8FC; --card:#FFFFFF;
  --brand:#1B4F8C; --brand-2:#2E6FB7; --gold:#B8860B;
}
html, body, [class*="css"] { font-family:'Inter',-apple-system,'Segoe UI',sans-serif; }
.stApp { background:var(--paper); }
.block-container { padding-top:1.1rem; padding-bottom:3rem; max-width:1240px; }

/* hero */
.rc-hero{
  background:linear-gradient(135deg,#102D4D 0%,#1B4F8C 55%,#2E6FB7 100%);
  border-radius:14px; padding:1.5rem 1.7rem 1.3rem; color:#fff;
  box-shadow:0 10px 28px -14px rgba(16,45,77,.55); margin-bottom:1.1rem;
}
.rc-hero h1{
  font-family:'Source Serif 4',Georgia,serif; font-weight:700;
  font-size:1.75rem; line-height:1.18; margin:0 0 .35rem; letter-spacing:-.015em;
}
.rc-hero p{ margin:0; font-size:.85rem; line-height:1.55; color:#CFE0F2; max-width:76ch; }
.rc-stats{ display:flex; flex-wrap:wrap; gap:.45rem; margin-top:.95rem; }
.rc-stat{
  background:rgba(255,255,255,.12); border:1px solid rgba(255,255,255,.2);
  border-radius:999px; padding:.22rem .7rem; font-size:.71rem; font-weight:500;
  color:#EAF2FB; backdrop-filter:blur(2px);
}
.rc-stat b{ color:#fff; font-weight:700; }

/* section headings */
.rc-sec{
  font-family:'Source Serif 4',Georgia,serif; font-size:1.05rem; font-weight:700;
  color:var(--ink); margin:.2rem 0 .1rem;
}
.rc-sub{ color:var(--muted); font-size:.79rem; margin:0 0 .7rem; }
.rc-kicker{
  text-transform:uppercase; letter-spacing:.09em; font-size:.66rem;
  font-weight:700; color:var(--gold); margin-bottom:.15rem;
}

/* scope bar */
.rc-scope{
  background:var(--card); border:1px solid var(--line); border-left:3px solid var(--gold);
  border-radius:9px; padding:.55rem .85rem; font-size:.78rem; color:var(--ink-2);
  margin-bottom:.85rem; box-shadow:0 1px 2px rgba(16,45,77,.04);
}
.rc-scope b{ color:var(--ink); }

/* chips */
.rc-chip{
  display:inline-block; padding:2px 9px; border-radius:999px; font-size:.68rem;
  font-weight:650; color:#fff; white-space:nowrap; letter-spacing:.012em;
}
.rc-note{ color:var(--muted); font-size:.71rem; }
.rc-flag{
  display:inline-block; background:#F1F5FB; border:1px solid #D7E2F1;
  color:var(--brand); border-radius:999px; padding:1px 9px;
  font-size:.68rem; font-weight:600;
}

/* source cards */
.rc-src{
  background:var(--card); border:1px solid var(--line); border-radius:9px;
  padding:.75rem .9rem; margin-bottom:.6rem; transition:box-shadow .15s ease;
}
.rc-src:hover{ box-shadow:0 6px 18px -10px rgba(16,45,77,.35); }
.rc-src-t{ font-weight:640; font-size:.87rem; line-height:1.35; color:var(--ink); }
.rc-quote{
  font-family:ui-monospace,'Cascadia Mono',Consolas,monospace; font-size:.725rem;
  color:#3C4F66; white-space:pre-wrap; margin-top:.5rem; padding:.45rem .6rem;
  border-left:2px solid var(--line); background:#FAFCFF; border-radius:0 5px 5px 0;
}

/* buttons */
.stButton > button{
  background:var(--card); border:1px solid var(--line); border-radius:9px;
  color:var(--ink); font-weight:550; font-size:.82rem; text-align:left;
  padding:.6rem .85rem; width:100%; transition:all .14s ease;
  box-shadow:0 1px 2px rgba(16,45,77,.04);
}
.stButton > button:hover{
  border-color:var(--brand-2); color:var(--brand);
  box-shadow:0 6px 16px -9px rgba(27,79,140,.55); transform:translateY(-1px);
}
.stButton > button:focus:not(:active){ border-color:var(--brand); color:var(--brand); }

/* sidebar */
[data-testid="stSidebar"]{ background:#0E2439; }
[data-testid="stSidebar"] *{ color:#D9E4F1; }
[data-testid="stSidebar"] .stButton > button{
  background:rgba(255,255,255,.07); border:1px solid rgba(255,255,255,.16); color:#EAF2FB;
}
[data-testid="stSidebar"] .stButton > button:hover{
  background:rgba(255,255,255,.14); border-color:rgba(255,255,255,.3); color:#fff;
}
.rc-brand{
  font-family:'Source Serif 4',Georgia,serif; font-size:1.06rem; font-weight:700;
  color:#fff; line-height:1.2;
}
.rc-brand-sub{
  font-size:.68rem; color:#8FA8C4; letter-spacing:.05em;
  text-transform:uppercase; margin-top:.2rem;
}
.rc-side-h{
  text-transform:uppercase; letter-spacing:.1em; font-size:.65rem; font-weight:700;
  color:#7E99BA !important; margin:.15rem 0 .35rem;
}

/* tabs + metrics */
.stTabs [data-baseweb="tab-list"]{ gap:.3rem; border-bottom:1px solid var(--line); }
.stTabs [data-baseweb="tab"]{ font-size:.85rem; font-weight:560; }
[data-testid="stMetricValue"]{ font-size:1.5rem; color:var(--ink); font-weight:700; }
[data-testid="stMetricLabel"]{ font-size:.72rem; color:var(--muted); }
</style>
"""


def chip(tier: str, note: bool = True) -> str:
    label, colour, tail = TIER_BADGE[tier]
    out = f"<span class='rc-chip' style='background:{colour}'>{label}</span>"
    if note:
        out += f" <span class='rc-note'>{tail}</span>"
    return out


def evidence_strength(results) -> tuple[int, str]:
    """How much BINDING material backs this answer.

    Counts documents, not chunks, deliberately: six passages from one circular is
    one source, and calling that strong evidence would mislead in exactly the
    situation where someone is most likely to rely on it.
    """
    if not results:
        return 0, "No sources"
    binding = {r["source_file"] for r in results if r["tier"] in ("A", "B")}
    total = {r["source_file"] for r in results}
    pct = int(round(100 * len(binding) / max(1, len(total))))
    if len(binding) >= 3 and pct >= 75:
        return pct, "Strong — multiple binding instruments"
    if len(binding) >= 1:
        return pct, "Moderate — binding material present"
    return pct, "Weak — explanatory sources only"


def render_sources(results) -> None:
    with st.expander(f"Sources ({len(results)})", expanded=False):
        for i, r in enumerate(results, 1):
            # Raw PDF/OCR text contains stray <, > and & often enough that
            # injecting it unescaped silently swallows parts of the quotation.
            url = html.escape(r.get("url") or "", quote=True)
            link = (f" · <a href='{url}' target='_blank' rel='noopener'>open source</a>"
                    if url else "")
            body = html.escape(r["text"][:620].strip())
            if len(r["text"]) > 620:
                body += " …"
            edge = TIER_BADGE[r["tier"]][1]
            st.markdown(
                f"<div class='rc-src' style='border-left:3px solid {edge}'>"
                f"<div class='rc-src-t'>[{i}] {html.escape(str(r['title']))}</div>"
                f"<div style='margin:.35rem 0'>{chip(r['tier'])}</div>"
                f"<div class='rc-note'>{html.escape(str(r.get('bucket') or ''))} · cosine "
                f"{r['similarity']:.3f}{link}</div>"
                f"<div class='rc-quote'>{body}</div></div>", unsafe_allow_html=True)


def apply_scope(results, needles: list[str], limit: int):
    """Keep only documents whose TITLE matches the selected scope.

    A query naming "financial year 2023-24" put the 2024-25 circular first, the
    2022-23 second and the correct one third, all within 0.03 cosine - the
    embedding cannot see four characters of difference between otherwise
    identical documents. The same shape appears across CEA establishment
    templates. The distinguishing fact is categorical, not semantic, so it
    belongs in a filter rather than in a similarity score.
    """
    if not needles:
        return results[:limit], False
    keep = [r for r in results if any(n in (r["title"] or "").lower() for n in needles)]
    return (keep or results)[:limit], not keep


# ── generation ───────────────────────────────────────────────────────────────
def generate(client, question: str, domain: str, results, effort: str) -> str:
    user_msg = (f"Domain: {domain}\n\nQuestion: {question}\n\n"
                f"Retrieved sources:\n\n{format_sources(results)}")
    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_msg}]
    kwargs = {"model": LLM_MODEL, "messages": messages}
    if effort:
        kwargs["reasoning_effort"] = effort
    note, answer = st.empty(), None
    try:
        try:
            stream = client.chat.completions.create(**kwargs, stream=True)
        except Exception:
            if "reasoning_effort" not in kwargs:
                raise
            kwargs.pop("reasoning_effort")
            note.caption("_reasoning_effort not supported here — model default._")
            stream = client.chat.completions.create(**kwargs, stream=True)
        # A chunk can carry an empty choices list - the final usage-only chunk
        # does, and chunk.choices[0] would raise IndexError mid-answer.
        answer = st.write_stream(
            (c.choices[0].delta.content or "") for c in stream if c.choices)
    except Exception as exc:
        note.caption("_Streaming unavailable, generating…_")
        try:
            kwargs.pop("reasoning_effort", None)
            resp = client.chat.completions.create(**kwargs)
            answer = (resp.choices[0].message.content or "").strip()
            st.markdown(answer)
        except Exception as exc2:
            answer = (f"**Generation failed.** Streaming: `{exc}`  \n"
                      f"Non-streaming: `{exc2}`\n\nRetrieved sources are below.")
            st.error(answer)
    if not answer:
        answer = "_The model returned an empty response. Sources are below._"
        st.markdown(answer)
    return answer


# ── sidebar ──────────────────────────────────────────────────────────────────
st.markdown(CSS, unsafe_allow_html=True)

with st.sidebar:
    st.markdown("<div class='rc-brand'>Compliance Intelligence</div>"
                "<div class='rc-brand-sub'>IIM Bangalore · CCS</div>",
                unsafe_allow_html=True)
    st.divider()

    st.markdown("<div class='rc-side-h'>Domain</div>", unsafe_allow_html=True)
    domain = st.radio("Domain", ["Healthcare", "RERA"], label_visibility="collapsed",
                      format_func=lambda d: DOMAIN_LABEL[d])
    st.caption(DOMAIN_BLURB[domain])
    st.divider()

    st.markdown("<div class='rc-side-h'>Scope</div>", unsafe_allow_html=True)
    if domain == "RERA":
        fy = st.selectbox("Financial year", RERA_YEARS,
                          help="Karnataka penalty circulars differ only by year and are "
                               "near-identical to the embedding model. Scoping resolves it.")
        scope_needles = [] if fy == RERA_YEARS[0] else [fy]
        scope_label = "All years" if not scope_needles else f"FY {fy}"
    else:
        facility = st.selectbox("Establishment type", list(HEALTHCARE_FACILITIES),
                                help="CEA standards are published per facility type in "
                                     "near-identical templates. Scoping returns the "
                                     "standards that bind your establishment.")
        scope_needles = HEALTHCARE_FACILITIES[facility]
        scope_label = facility
    st.divider()

    st.markdown("<div class='rc-side-h'>Retrieval</div>", unsafe_allow_html=True)
    top_k = st.slider("Passages retrieved", 3, 12, 6, help="The cohort benchmark uses 6.")
    include_drafts = st.checkbox("Include draft / proposed (tier D)", value=False)
    include_review = st.checkbox("Include unverified (tier E)", value=False)
    st.divider()

    st.markdown("<div class='rc-side-h'>Cost controls</div>", unsafe_allow_html=True)
    adaptive = st.checkbox("Adaptive reasoning", value=True,
                           help="Choose reasoning effort per query from retrieval "
                                "signals. Reasoning tokens are what drive cost.")
    use_cache = st.checkbox("Reuse cached answers", value=True,
                            help="Retrieval is local and free; generation is the only "
                                 "API spend. Repeats are never paid for twice.")
    st.caption(f"{len(answer_cache())} answers cached")
    if st.button("Clear cache"):
        answer_cache().clear()
        try:
            CACHE_FILE.unlink(missing_ok=True)
        except Exception:
            pass
        st.rerun()
    st.divider()

    with st.expander("Authority tiers"):
        for t in "ABCDE":
            st.markdown(chip(t), unsafe_allow_html=True)
    st.caption(f"`{LLM_MODEL}` · `{EMBED_MODEL.split('/')[-1]}`")
    if st.button("Clear conversation"):
        st.session_state.messages = []
        st.rerun()

# ── preflight ────────────────────────────────────────────────────────────────
store = get_store() if ensure_index() else None
if store is None:
    st.error("No corpus index available.")
    st.caption("Locally, build it. On a deployment, set `INDEX_URL` in the app's "
               "secrets to a downloadable .tar.gz of `chroma_db/`.")
    st.code("cd 05_App\npython ingest_corpus.py --rebuild", language="bash")
    st.stop()

client = get_client()

st.markdown(
    "<div class='rc-hero'>"
    "<h1>Indian Regulatory Compliance Intelligence</h1>"
    "<p>Answers are grounded only in the project corpus. Where the corpus does not "
    "settle a question, the assistant says so rather than filling the gap. Draft and "
    "unverified material is labelled and never presented as binding law.</p>"
    f"<div class='rc-stats'>"
    f"<span class='rc-stat'><b>{CORPUS_DOCS}</b> documents</span>"
    f"<span class='rc-stat'><b>{CORPUS_CHUNKS:,}</b> passages</span>"
    f"<span class='rc-stat'><b>2</b> domains</span>"
    f"<span class='rc-stat'><b>5</b> authority tiers</span>"
    f"<span class='rc-stat'>authority-tiered retrieval</span>"
    "</div></div>", unsafe_allow_html=True)

if client is None:
    st.warning("`OPENAI_API_KEY` is not set — retrieval works, generation does not.")

tab_chat, tab_tools = st.tabs(["Assistant", f"{DOMAIN_LABEL[domain]} tools"])

# ── assistant ────────────────────────────────────────────────────────────────
with tab_chat:
    tiers_txt = "A–E" if include_review else ("A–D" if include_drafts else "A–C")
    st.markdown(f"<div class='rc-scope'><b>Scope</b> · {DOMAIN_LABEL[domain]} · "
                f"{scope_label} · top {top_k} passages · tiers {tiers_txt} · "
                f"reasoning {'adaptive' if adaptive else BASE_EFFORT or 'default'}"
                f"</div>", unsafe_allow_html=True)

    if "messages" not in st.session_state:
        st.session_state.messages = []

    if not st.session_state.messages:
        st.markdown("<div class='rc-kicker'>Start here</div>", unsafe_allow_html=True)
        cols = st.columns(2)
        for i, q in enumerate(SUGGESTED[domain]):
            if cols[i % 2].button(q, key=f"sug{i}"):
                st.session_state.pending_q = q
                st.rerun()

    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if msg.get("sources"):
                render_sources(msg["sources"])

    prompt = st.chat_input(
        "Ask about Karnataka RERA or the central Act…" if domain == "RERA"
        else "Ask about Indian healthcare regulation…")
    # One channel for button-raised questions from anywhere in the app. The
    # tools tab sets pending_q and reruns; the script re-executes top to bottom,
    # so this tab picks it up on the next pass.
    if not prompt:
        prompt = st.session_state.pop("pending_q", None)

    if prompt:
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"):
            with st.spinner("Searching the corpus…"):
                raw = retrieve(store, prompt, domain,
                               max(top_k * 4, 24) if scope_needles else top_k,
                               include_drafts, include_review)
                results, scope_missed = apply_scope(raw, scope_needles, top_k)

            if not results:
                answer = (f"The {DOMAIN_LABEL[domain]} corpus does not contain sources "
                          f"relevant to this question, so I cannot answer it on the "
                          f"evidence available.")
                st.markdown(answer)
                st.session_state.messages.append(
                    {"role": "assistant", "content": answer, "sources": []})
            else:
                if scope_missed:
                    st.info(f"No sources matched **{scope_label}** — showing the best "
                            f"available across all of {DOMAIN_LABEL[domain]}.")
                if any(r["tier"] == "D" for r in results):
                    st.warning("Includes **draft or proposed** material, which is not "
                               "binding regulation.")
                if any(r["tier"] == "E" for r in results):
                    st.warning("Includes material whose **binding status is unverified**.")

                effort, why = (choose_effort(results, prompt) if adaptive
                               else (BASE_EFFORT, ["fixed setting"]))
                pct, verdict = evidence_strength(results)
                key = cache_key(prompt, domain, scope_label, top_k,
                                include_drafts, include_review, effort)
                hit = answer_cache().get(key) if use_cache else None

                c1, c2, c3 = st.columns([1, 1, 2])
                c1.metric("Binding evidence", f"{pct}%")
                c2.metric("Reasoning", effort or "default")
                c3.markdown(
                    f"<div style='padding-top:.5rem'>"
                    f"<span class='rc-flag'>{'cached' if hit else 'generated'}</span> "
                    f"<span class='rc-note'>{verdict} · "
                    f"{len({r['source_file'] for r in results})} documents · "
                    f"{html.escape(', '.join(why))}</span></div>",
                    unsafe_allow_html=True)

                if hit:
                    answer = hit["answer"]
                    st.markdown(answer)
                elif client is None:
                    answer = "_Generation unavailable — `OPENAI_API_KEY` not set._"
                    st.markdown(answer)
                else:
                    answer = generate(client, prompt, domain, results, effort)
                    cache_put(key, {"answer": answer, "effort": effort,
                                    "question": prompt, "domain": domain})

                render_sources(results)
                st.session_state.messages.append(
                    {"role": "assistant", "content": answer, "sources": results})

# ── domain tools ─────────────────────────────────────────────────────────────
with tab_tools:
    if domain == "RERA":
        st.markdown("<div class='rc-kicker'>Obligations</div>"
                    "<div class='rc-sec'>Promoter duties under the Act and Karnataka Rules</div>"
                    "<p class='rc-sub'>Each button runs a grounded query against the corpus — "
                    "the assistant answers from the circulars. Nothing is hard-coded here.</p>",
                    unsafe_allow_html=True)
        items = [
            ("Separate bank account", "What are the promoter's obligations regarding the "
             "separate bank account under Section 4(2)(l)(D)?"),
            ("Quarterly progress reports", "When must a promoter submit quarterly progress "
             "reports and what happens on delay?"),
            ("Annual audit report", "What are the requirements and deadlines for the annual "
             "audit report?"),
            ("Project extension", "On what grounds and with what fee may a project "
             "registration be extended under Section 7?"),
            ("Agent registration", "What are the requirements for registration of a real "
             "estate agent?"),
            ("Appellate tribunal", "What are the fees and procedure for an appeal to the "
             "Karnataka Real Estate Appellate Tribunal?"),
        ]
        cols = st.columns(2)
        for i, (label, q) in enumerate(items):
            if cols[i % 2].button(label, key=f"ob{i}"):
                st.session_state.pending_q = q
                st.rerun()

        st.divider()
        st.markdown("<div class='rc-kicker'>Estimator</div>"
                    "<div class='rc-sec'>Annual audit report — penalty under Section 60</div>"
                    "<p class='rc-sub'>Non-submission or delay, Real Estate (Regulation and "
                    "Development) Act, 2016.</p>", unsafe_allow_html=True)
        a, b = st.columns(2)
        cost = a.number_input("Total estimated project cost (Rs crore)",
                              min_value=0.0, value=30.0, step=5.0)
        years = b.number_input("Financial years in default", min_value=1, value=1, step=1)
        if cost < 25:
            band, amount = "Less than Rs 25 crore", 20_000
        elif cost <= 50:
            band, amount = "Rs 25–50 crore", 25_000
        elif cost <= 100:
            band, amount = "Rs 50–100 crore", 50_000
        else:
            band, amount = "Above Rs 100 crore", 1_00_000
        m1, m2 = st.columns(2)
        m1.metric("Band", band)
        m2.metric("Estimated penalty", f"Rs {amount * int(years):,}")
        st.warning(
            "**Read this before relying on the figure.** This schedule comes from the "
            "circular *Imposing penalty … Annual Audit Report for the Financial Year "
            "2024-25*, whose own footnote states the first financial year ends "
            "31.03.2026. Whether it applies to earlier years is **not settled anywhere in "
            "this corpus** — the 2023-24 circular states a liability under Section 60 "
            "without specifying an amount. Confirm the applicable year's circular before "
            "acting. An estimator that hid this would be the exact failure mode a "
            "compliance tool cannot afford.")

    else:
        st.markdown("<div class='rc-kicker'>Standards</div>"
                    "<div class='rc-sec'>Clinical establishment requirements</div>"
                    f"<p class='rc-sub'>Scoped to <b>{html.escape(scope_label)}</b> in the "
                    f"sidebar. Each button runs a grounded query within that scope.</p>",
                    unsafe_allow_html=True)
        items = [
            ("Record retention", "How long must medical records and statistics be retained, "
             "and under which provision?"),
            ("Infrastructure requirements", "What are the minimum infrastructure and space "
             "requirements for this establishment type?"),
            ("Staffing requirements", "What are the minimum staffing and qualification "
             "requirements for this establishment type?"),
            ("Registration process", "What is the process and what documents are required to "
             "register this clinical establishment?"),
            ("Patient rights and privacy", "What obligations apply regarding patient "
             "confidentiality and data privacy?"),
            ("Emergency reporting", "What must be reported to the district authorities and in "
             "what form?"),
        ]
        cols = st.columns(2)
        for i, (label, q) in enumerate(items):
            if cols[i % 2].button(label, key=f"hc{i}"):
                st.session_state.pending_q = q
                st.rerun()

        st.divider()
        st.markdown("<div class='rc-kicker'>Insurance</div>"
                    "<div class='rc-sec'>PM-JAY hospital empanelment</div>"
                    "<p class='rc-sub'>Ayushman Bharat — empanelment, claims and beneficiary "
                    "processes.</p>", unsafe_allow_html=True)
        pm = [
            ("Empanelment criteria", "What are the criteria for empanelment of a hospital "
             "under PM-JAY?"),
            ("Claim settlement", "What is the process and timeline for claim settlement "
             "under PM-JAY?"),
            ("Beneficiary identification", "What is the process for beneficiary "
             "identification under PM-JAY?"),
            ("Pre-authorisation", "When is pre-authorisation required and what does the "
             "process involve?"),
        ]
        cols = st.columns(2)
        for i, (label, q) in enumerate(pm):
            if cols[i % 2].button(label, key=f"pm{i}"):
                st.session_state.pending_q = q
                st.rerun()
