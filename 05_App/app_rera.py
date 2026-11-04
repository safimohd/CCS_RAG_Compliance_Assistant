"""
RERA AI Compliance Assistant
IIM Bangalore CCS Project

Three tabs:
  1. Document Q&A     — upload a RERA PDF and ask questions (OpenAI RAG)
  2. K-RERA Lookup    — search 10,000+ registered K-RERA projects (Supabase)
  3. Investment Signals — Bengaluru locality benchmarks for 40-80L buyers

Secrets — add to 05_App/.env (already gitignored):
  OPENAI_API_KEY   (already present)
  SUPABASE_URL
  SUPABASE_KEY
"""

import os, io, re
import requests
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="RERA AI Assistant",
    page_icon="🏠",
    layout="wide",
    initial_sidebar_state="collapsed",
)

OPENAI_KEY   = os.environ.get("OPENAI_API_KEY", "")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
LLM_MODEL    = os.environ.get("LLM_MODEL", "gpt-5-nano")
EMBED_MODEL  = "text-embedding-3-small"   # cheapest OpenAI embedding, free tier eligible

# ---------------------------------------------------------------------------
# PDF helpers
# ---------------------------------------------------------------------------
@st.cache_resource(show_spinner=False)
def _pdfplumber():
    import pdfplumber
    return pdfplumber

def extract_chunks(file_bytes: bytes, chunk_words: int = 400) -> list[dict]:
    pdfplumber = _pdfplumber()
    chunks = []
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for page_num, page in enumerate(pdf.pages, 1):
            text = page.extract_text() or ""
            words = text.split()
            for i in range(0, len(words), chunk_words):
                chunk = " ".join(words[i : i + chunk_words])
                if chunk.strip():
                    chunks.append({"text": chunk, "page": page_num})
    return chunks

def embed_texts(texts: list[str]) -> list[list[float]]:
    resp = requests.post(
        "https://api.openai.com/v1/embeddings",
        headers={"Authorization": f"Bearer {OPENAI_KEY}"},
        json={"model": EMBED_MODEL, "input": texts},
        timeout=30,
    )
    resp.raise_for_status()
    return [d["embedding"] for d in resp.json()["data"]]

def cosine_top_k(query_vec, doc_vecs, k=3):
    import numpy as np
    q = np.array(query_vec)
    D = np.array(doc_vecs)
    scores = D @ q / (np.linalg.norm(D, axis=1) * np.linalg.norm(q) + 1e-9)
    return scores.argsort()[::-1][:k].tolist()

def ask_llm(context: str, question: str, pages: list[str]) -> str:
    system = (
        "You are a RERA compliance expert helping a first-time home buyer in Karnataka. "
        "Answer using ONLY the document excerpts provided. "
        "If the answer is not in the excerpts, say: "
        "'This information is not found in the uploaded document.' "
        f"Cite the relevant page numbers at the end of your answer."
    )
    user = f"Document excerpts (pages {', '.join(pages)}):\n{context}\n\nQuestion: {question}"
    resp = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {OPENAI_KEY}"},
        json={
            "model": LLM_MODEL,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ],
            "temperature": 0.2,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]

# ---------------------------------------------------------------------------
# K-RERA Supabase search
# ---------------------------------------------------------------------------
def search_projects(query: str, field: str, limit: int = 20) -> list[dict]:
    col_map = {
        "Project Name":      "project_name",
        "Builder / Promoter": "promoter_name",
        "ACK Number":        "ack_number",
        "PRM Number":        "prm_number",
    }
    col = col_map.get(field, "project_name")
    url = (
        f"{SUPABASE_URL}/rest/v1/rera_projects"
        f"?{col}=ilike.*{requests.utils.quote(query)}*"
        f"&limit={limit}"
        f"&select=ack_number,prm_number,project_name,promoter_name"
    )
    r = requests.get(url, headers={
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
    }, timeout=15)
    r.raise_for_status()
    return r.json()

# ---------------------------------------------------------------------------
# Investment signal data (Bengaluru, 2026)
# ---------------------------------------------------------------------------
LOCALITIES = [
    {"locality": "Whitefield",          "pin": "560066", "avg_psf": 7200,  "trend": "+8% YoY",  "risk": "Low"},
    {"locality": "Sarjapur Road",        "pin": "560035", "avg_psf": 6800,  "trend": "+11% YoY", "risk": "Low"},
    {"locality": "Electronic City",      "pin": "560100", "avg_psf": 5400,  "trend": "+6% YoY",  "risk": "Low"},
    {"locality": "Hebbal",               "pin": "560024", "avg_psf": 8100,  "trend": "+9% YoY",  "risk": "Low"},
    {"locality": "Marathahalli",         "pin": "560037", "avg_psf": 6500,  "trend": "+7% YoY",  "risk": "Low"},
    {"locality": "Bannerghatta Road",    "pin": "560076", "avg_psf": 5900,  "trend": "+5% YoY",  "risk": "Medium"},
    {"locality": "Hennur",               "pin": "560043", "avg_psf": 5600,  "trend": "+10% YoY", "risk": "Low"},
    {"locality": "Devanahalli",          "pin": "562110", "avg_psf": 4800,  "trend": "+14% YoY", "risk": "Medium"},
    {"locality": "Kanakapura Road",      "pin": "560062", "avg_psf": 5100,  "trend": "+9% YoY",  "risk": "Medium"},
    {"locality": "JP Nagar",             "pin": "560078", "avg_psf": 7800,  "trend": "+6% YoY",  "risk": "Low"},
    {"locality": "Koramangala",          "pin": "560034", "avg_psf": 10200, "trend": "+5% YoY",  "risk": "Low"},
    {"locality": "Indiranagar",          "pin": "560038", "avg_psf": 11500, "trend": "+4% YoY",  "risk": "Low"},
    {"locality": "HSR Layout",           "pin": "560102", "avg_psf": 9100,  "trend": "+7% YoY",  "risk": "Low"},
    {"locality": "Yelahanka",            "pin": "560064", "avg_psf": 5300,  "trend": "+12% YoY", "risk": "Medium"},
    {"locality": "Tumkur Road",          "pin": "560073", "avg_psf": 4500,  "trend": "+8% YoY",  "risk": "High"},
    {"locality": "Mysore Road",          "pin": "560026", "avg_psf": 4700,  "trend": "+7% YoY",  "risk": "High"},
    {"locality": "Hoskote",              "pin": "562114", "avg_psf": 3900,  "trend": "+15% YoY", "risk": "High"},
    {"locality": "Bagalur",              "pin": "562149", "avg_psf": 3600,  "trend": "+18% YoY", "risk": "High"},
    {"locality": "KR Puram",             "pin": "560036", "avg_psf": 5800,  "trend": "+9% YoY",  "risk": "Medium"},
    {"locality": "Rajarajeshwari Nagar", "pin": "560098", "avg_psf": 5200,  "trend": "+6% YoY",  "risk": "Medium"},
]
RISK_ICON = {"Low": "🟢", "Medium": "🟡", "High": "🔴"}

# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------
st.title("🏠 RERA AI Compliance Assistant")
st.caption("Karnataka | First-time buyers in the ₹40–80 lakh range")

tab1, tab2, tab3 = st.tabs([
    "📄 Document Q&A",
    "🔍 K-RERA Project Lookup",
    "📊 Investment Signals",
])

# ── TAB 1: Document Q&A ────────────────────────────────────────────────────
with tab1:
    st.subheader("Ask questions about your RERA document")
    st.markdown(
        "Upload any RERA-related PDF — sale agreement, allotment letter, "
        "project brochure — and ask questions in plain language."
    )

    if not OPENAI_KEY:
        st.error("OPENAI_API_KEY not found. Add it to 05_App/.env")
        st.stop()

    uploaded = st.file_uploader("Upload PDF", type=["pdf"])

    if uploaded:
        raw = uploaded.read()

        with st.spinner("Reading and indexing document…"):
            chunks = extract_chunks(raw)
            if not chunks:
                st.error("Could not extract text from this PDF — it may be a scanned image.")
                st.stop()

            # Embed all chunks (cached in session so re-runs are free)
            cache_key = f"vecs_{uploaded.name}_{len(raw)}"
            if cache_key not in st.session_state:
                texts = [c["text"] for c in chunks]
                st.session_state[cache_key] = embed_texts(texts)

        vecs = st.session_state[cache_key]
        st.success(f"Indexed {len(chunks)} sections from **{uploaded.name}**")

        # Suggested questions
        suggestions = [
            "What is the RERA registration number?",
            "What is the promised possession date?",
            "What penalty applies for delayed possession?",
            "What amenities are included?",
            "What is the carpet area of the flat?",
        ]
        st.markdown("**Quick questions:**")
        cols = st.columns(len(suggestions))
        for i, q in enumerate(suggestions):
            if cols[i].button(q, key=f"sq_{i}", use_container_width=True):
                st.session_state["q_text"] = q

        question = st.text_input(
            "Or type your own question",
            value=st.session_state.get("q_text", ""),
            placeholder="e.g. What happens if the builder delays possession?",
        )

        if question:
            with st.spinner("Searching document…"):
                q_vec   = embed_texts([question])[0]
                top_idx = cosine_top_k(q_vec, vecs, k=3)
                context = "\n\n---\n\n".join(chunks[i]["text"] for i in top_idx)
                pages   = [str(chunks[i]["page"]) for i in top_idx]
                answer  = ask_llm(context, question, pages)

            st.markdown("### Answer")
            st.markdown(answer)
    else:
        st.info("Upload a PDF above to get started.")


# ── TAB 2: K-RERA Lookup ───────────────────────────────────────────────────
with tab2:
    st.subheader("Search Karnataka RERA Registered Projects")
    st.caption("10,096 projects as of November 2026 — data from rera.karnataka.gov.in")

    if not SUPABASE_URL or not SUPABASE_KEY:
        st.error("SUPABASE_URL or SUPABASE_KEY not set in .env")
    else:
        col_a, col_b = st.columns([3, 1])
        with col_a:
            query = st.text_input(
                "Search",
                placeholder="e.g. Prestige, Brigade, Sobha, Whitefield…",
            )
        with col_b:
            field = st.selectbox(
                "Search by",
                ["Project Name", "Builder / Promoter", "ACK Number", "PRM Number"],
            )

        if query:
            with st.spinner("Searching K-RERA database…"):
                try:
                    results = search_projects(query, field)
                except Exception as e:
                    st.error(f"Search error: {e}")
                    results = []

            if results:
                st.success(f"Found {len(results)} project(s) — showing first {len(results)}")
                for r in results:
                    with st.expander(
                        f"**{r.get('project_name') or 'N/A'}** — {r.get('promoter_name') or 'N/A'}"
                    ):
                        c1, c2 = st.columns(2)
                        c1.metric("ACK Number", r.get("ack_number") or "N/A")
                        c2.metric("PRM Number", r.get("prm_number") or "N/A")
                        ack = r.get("ack_number", "")
                        if ack:
                            st.markdown(
                                f"[View full details on K-RERA portal ↗](https://rera.karnataka.gov.in/projectViewDetails?id={ack})"
                            )
            else:
                st.warning("No matching projects found. Try a shorter keyword.")
        else:
            st.info("Type a project name or builder name to search.")

        with st.expander("About this data"):
            st.markdown(
                "Scraped from [rera.karnataka.gov.in](https://rera.karnataka.gov.in/viewAllProjects). "
                "All 10,096 RERA-registered Karnataka projects as of November 2026."
            )


# ── TAB 3: Investment Signals ──────────────────────────────────────────────
with tab3:
    st.subheader("Bengaluru Locality Investment Signals")
    st.caption("Price benchmarks for the ₹40–80 lakh budget range")

    c1, c2 = st.columns(2)
    with c1:
        budget = st.slider("Your budget (₹ lakhs)", 30, 100, 60, step=5)
    with c2:
        area = st.slider("Desired size (sq ft)", 400, 1500, 900, step=50)

    target_psf = (budget * 100_000) / area
    st.info(f"Your budget implies **₹{target_psf:,.0f} / sq ft** for a {area} sq ft flat at ₹{budget}L")

    st.markdown("#### Localities that fit your budget")
    matches = [p for p in LOCALITIES if p["avg_psf"] <= target_psf * 1.10]
    matches.sort(key=lambda x: abs(x["avg_psf"] - target_psf))

    if matches:
        for p in matches[:10]:
            diff = p["avg_psf"] - target_psf
            fit  = "within budget" if diff <= 0 else f"₹{diff:,.0f}/sqft over budget"
            c1, c2, c3, c4 = st.columns([3, 2, 2, 2])
            c1.markdown(f"**{p['locality']}** `{p['pin']}`")
            c2.markdown(f"₹{p['avg_psf']:,} / sqft")
            c3.markdown(p["trend"])
            c4.markdown(f"{RISK_ICON[p['risk']]} {p['risk']} — {fit}")
    else:
        st.warning("No localities match this budget. Try increasing your budget or reducing the size.")

    st.markdown("---")
    st.markdown("#### Full locality table")
    import pandas as pd
    df = pd.DataFrame(LOCALITIES)
    df.columns = ["Locality", "PIN", "Avg PSF (₹)", "YoY Trend", "Risk"]
    df["Est. cost at target size (₹L)"] = (df["Avg PSF (₹)"] * area / 100_000).round(1)
    st.dataframe(df, use_container_width=True, hide_index=True)

    st.caption(
        "PSF figures are indicative 2026 market benchmarks. "
        "Consult a registered property agent before making investment decisions."
    )
