"""
RERA AI Compliance Assistant
IIM Bangalore CCS Project — Group [your group]

Tabs:
  1. Document Q&A     — upload a RERA agreement/brochure PDF and ask questions
  2. K-RERA Lookup    — search 10,000+ registered projects by name or builder
  3. Investment Signals — area-wise price benchmarks for Bengaluru localities

Secrets required (st.secrets or environment variables):
  SUPABASE_URL        — Supabase project URL
  SUPABASE_KEY        — Supabase service role key
  GEMINI_API_KEY      — Google Gemini API key (free tier)
"""

import os
import re
import io
import requests
import streamlit as st

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="RERA AI Assistant",
    page_icon="🏠",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ---------------------------------------------------------------------------
# Secrets helper — works with st.secrets AND plain env vars
# ---------------------------------------------------------------------------
def get_secret(key: str) -> str:
    try:
        return st.secrets[key]
    except Exception:
        val = os.environ.get(key, "")
        return val


SUPABASE_URL = get_secret("SUPABASE_URL")
SUPABASE_KEY = get_secret("SUPABASE_KEY")
GEMINI_KEY   = get_secret("GEMINI_API_KEY")

# ---------------------------------------------------------------------------
# Lazy imports (heavy libs — only load when their tab is used)
# ---------------------------------------------------------------------------
@st.cache_resource(show_spinner=False)
def _load_pdf_libs():
    import pdfplumber
    import chromadb
    from chromadb.utils.embedding_functions import GoogleGenerativeAiEmbeddingFunction
    return pdfplumber, chromadb, GoogleGenerativeAiEmbeddingFunction


# ---------------------------------------------------------------------------
# Tab 1 helpers
# ---------------------------------------------------------------------------
def extract_pdf_chunks(file_bytes: bytes, chunk_size: int = 400) -> list[dict]:
    """Extract text from PDF, split into ~400-word chunks with page metadata."""
    pdfplumber, _, _ = _load_pdf_libs()
    chunks = []
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for page_num, page in enumerate(pdf.pages, 1):
            text = page.extract_text() or ""
            words = text.split()
            for i in range(0, len(words), chunk_size):
                chunk = " ".join(words[i : i + chunk_size])
                if chunk.strip():
                    chunks.append({"text": chunk, "page": page_num})
    return chunks


@st.cache_resource(show_spinner=False)
def _get_chroma_client():
    _, chromadb, _ = _load_pdf_libs()
    return chromadb.Client()


def build_vector_store(chunks: list[dict], collection_name: str):
    """Embed chunks and store in in-memory ChromaDB collection."""
    _, chromadb, GoogleGenerativeAiEmbeddingFunction = _load_pdf_libs()
    client = _get_chroma_client()

    # Drop and recreate so re-uploads are fresh
    try:
        client.delete_collection(collection_name)
    except Exception:
        pass

    ef = GoogleGenerativeAiEmbeddingFunction(api_key=GEMINI_KEY)
    col = client.create_collection(collection_name, embedding_function=ef)
    col.add(
        documents=[c["text"] for c in chunks],
        metadatas=[{"page": c["page"]} for c in chunks],
        ids=[f"chunk_{i}" for i in range(len(chunks))],
    )
    return col


def answer_question(collection, question: str) -> str:
    """Retrieve top-3 chunks, then ask Gemini to answer from them."""
    results = collection.query(query_texts=[question], n_results=3)
    context = "\n\n---\n\n".join(results["documents"][0])
    pages   = [str(m["page"]) for m in results["metadatas"][0]]

    prompt = f"""You are a RERA compliance expert helping a first-time home buyer in Karnataka.
Answer the question using ONLY the document excerpts below.
If the answer is not in the excerpts, say "This information is not found in the uploaded document."
Cite the page numbers at the end of your answer.

Document excerpts (pages {', '.join(pages)}):
{context}

Question: {question}

Answer:"""

    resp = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_KEY}",
        json={"contents": [{"parts": [{"text": prompt}]}]},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["candidates"][0]["content"]["parts"][0]["text"]


# ---------------------------------------------------------------------------
# Tab 2 helpers
# ---------------------------------------------------------------------------
def search_rera_projects(query: str, field: str, limit: int = 20) -> list[dict]:
    """Search rera_projects table in Supabase using ilike."""
    col_map = {
        "Project Name": "project_name",
        "Builder / Promoter": "promoter_name",
        "ACK Number": "ack_number",
        "PRM Number": "prm_number",
    }
    col = col_map.get(field, "project_name")
    url = (
        f"{SUPABASE_URL}/rest/v1/rera_projects"
        f"?{col}=ilike.*{requests.utils.quote(query)}*"
        f"&limit={limit}"
        f"&select=ack_number,prm_number,project_name,promoter_name"
    )
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
    }
    r = requests.get(url, headers=headers, timeout=15)
    r.raise_for_status()
    return r.json()


# ---------------------------------------------------------------------------
# Tab 3 helpers — static seed data (Bengaluru localities)
# ---------------------------------------------------------------------------
PRICE_DATA = [
    {"locality": "Whitefield",        "pin": "560066", "avg_psf": 7200,  "trend": "+8% YoY",  "risk": "Low"},
    {"locality": "Sarjapur Road",      "pin": "560035", "avg_psf": 6800,  "trend": "+11% YoY", "risk": "Low"},
    {"locality": "Electronic City",    "pin": "560100", "avg_psf": 5400,  "trend": "+6% YoY",  "risk": "Low"},
    {"locality": "Hebbal",             "pin": "560024", "avg_psf": 8100,  "trend": "+9% YoY",  "risk": "Low"},
    {"locality": "Marathahalli",       "pin": "560037", "avg_psf": 6500,  "trend": "+7% YoY",  "risk": "Low"},
    {"locality": "Bannerghatta Road",  "pin": "560076", "avg_psf": 5900,  "trend": "+5% YoY",  "risk": "Medium"},
    {"locality": "Hennur",             "pin": "560043", "avg_psf": 5600,  "trend": "+10% YoY", "risk": "Low"},
    {"locality": "Devanahalli",        "pin": "562110", "avg_psf": 4800,  "trend": "+14% YoY", "risk": "Medium"},
    {"locality": "Kanakapura Road",    "pin": "560062", "avg_psf": 5100,  "trend": "+9% YoY",  "risk": "Medium"},
    {"locality": "JP Nagar",           "pin": "560078", "avg_psf": 7800,  "trend": "+6% YoY",  "risk": "Low"},
    {"locality": "Koramangala",        "pin": "560034", "avg_psf": 10200, "trend": "+5% YoY",  "risk": "Low"},
    {"locality": "Indiranagar",        "pin": "560038", "avg_psf": 11500, "trend": "+4% YoY",  "risk": "Low"},
    {"locality": "HSR Layout",         "pin": "560102", "avg_psf": 9100,  "trend": "+7% YoY",  "risk": "Low"},
    {"locality": "Yelahanka",          "pin": "560064", "avg_psf": 5300,  "trend": "+12% YoY", "risk": "Medium"},
    {"locality": "Tumkur Road",        "pin": "560073", "avg_psf": 4500,  "trend": "+8% YoY",  "risk": "High"},
    {"locality": "Mysore Road",        "pin": "560026", "avg_psf": 4700,  "trend": "+7% YoY",  "risk": "High"},
    {"locality": "Hoskote",            "pin": "562114", "avg_psf": 3900,  "trend": "+15% YoY", "risk": "High"},
    {"locality": "Bagalur",            "pin": "562149", "avg_psf": 3600,  "trend": "+18% YoY", "risk": "High"},
    {"locality": "KR Puram",           "pin": "560036", "avg_psf": 5800,  "trend": "+9% YoY",  "risk": "Medium"},
    {"locality": "Rajarajeshwari Nagar","pin": "560098","avg_psf": 5200,  "trend": "+6% YoY",  "risk": "Medium"},
]

RISK_BADGE = {"Low": "🟢", "Medium": "🟡", "High": "🔴"}

def budget_signal(budget_lakhs: float, area_sqft: float) -> dict:
    """Find localities where budget fits at current avg PSF."""
    target_psf = (budget_lakhs * 100_000) / area_sqft
    matches = [
        p for p in PRICE_DATA
        if p["avg_psf"] <= target_psf * 1.10  # 10% tolerance
    ]
    matches.sort(key=lambda x: abs(x["avg_psf"] - target_psf))
    return matches


# ---------------------------------------------------------------------------
# App UI
# ---------------------------------------------------------------------------
st.title("🏠 RERA AI Compliance Assistant")
st.caption("Karnataka | For first-time buyers in the ₹40–80 lakh range")

tab1, tab2, tab3 = st.tabs([
    "📄 Document Q&A",
    "🔍 K-RERA Project Lookup",
    "📊 Investment Signals",
])


# ============================================================
# TAB 1 — PDF Upload + Q&A
# ============================================================
with tab1:
    st.subheader("Ask questions about your RERA agreement or project brochure")
    st.markdown(
        "Upload any RERA-related document — sale agreement, allotment letter, "
        "project brochure — and ask anything about it."
    )

    if not GEMINI_KEY:
        st.error("GEMINI_API_KEY is not set. Add it to .streamlit/secrets.toml or as an env var.")
        st.stop()

    uploaded = st.file_uploader("Upload a PDF", type=["pdf"], key="pdf_upload")

    if uploaded:
        file_bytes = uploaded.read()
        collection_name = "rera_doc"

        with st.spinner("Reading and indexing document…"):
            chunks = extract_pdf_chunks(file_bytes)
            if not chunks:
                st.error("Could not extract text from this PDF. It may be scanned/image-based.")
                st.stop()
            collection = build_vector_store(chunks, collection_name)

        st.success(f"Indexed {len(chunks)} sections from {uploaded.name}")

        # Suggested questions
        st.markdown("**Suggested questions:**")
        suggestions = [
            "What is the RERA registration number?",
            "What is the possession date promised?",
            "What penalty applies if possession is delayed?",
            "What amenities are promised in the agreement?",
            "What is the carpet area of the flat?",
        ]
        cols = st.columns(len(suggestions))
        for i, q in enumerate(suggestions):
            if cols[i].button(q, key=f"sug_{i}", use_container_width=True):
                st.session_state["doc_question"] = q

        question = st.text_input(
            "Or type your own question",
            value=st.session_state.get("doc_question", ""),
            placeholder="e.g. What happens if the builder delays possession?",
            key="doc_q_input",
        )

        if question:
            with st.spinner("Searching document and generating answer…"):
                try:
                    answer = answer_question(collection, question)
                    st.markdown("### Answer")
                    st.markdown(answer)
                except Exception as e:
                    st.error(f"Error generating answer: {e}")
    else:
        st.info("Upload a PDF above to get started.")


# ============================================================
# TAB 2 — K-RERA Project Lookup
# ============================================================
with tab2:
    st.subheader("Search Karnataka RERA Registered Projects")
    st.caption(f"Database: 10,096 projects as of November 2026")

    if not SUPABASE_URL or not SUPABASE_KEY:
        st.error("SUPABASE_URL or SUPABASE_KEY not set.")
    else:
        col_a, col_b = st.columns([3, 1])
        with col_a:
            search_query = st.text_input(
                "Search",
                placeholder="e.g. Prestige, Brigade, Sobha, Whitefield…",
                key="rera_search",
            )
        with col_b:
            search_field = st.selectbox(
                "Search by",
                ["Project Name", "Builder / Promoter", "ACK Number", "PRM Number"],
                key="rera_field",
            )

        if search_query:
            with st.spinner("Searching K-RERA database…"):
                try:
                    results = search_rera_projects(search_query, search_field)
                except Exception as e:
                    st.error(f"Search failed: {e}")
                    results = []

            if results:
                st.success(f"Found {len(results)} project(s)")
                for r in results:
                    with st.expander(
                        f"**{r.get('project_name', 'N/A')}** — {r.get('promoter_name', 'N/A')}"
                    ):
                        c1, c2 = st.columns(2)
                        c1.metric("ACK Number", r.get("ack_number", "N/A"))
                        c2.metric("PRM Number", r.get("prm_number", "N/A"))
                        ack = r.get("ack_number", "")
                        if ack:
                            rera_url = f"https://rera.karnataka.gov.in/viewProject?id={ack}"
                            st.markdown(f"[View on K-RERA portal ↗]({rera_url})")
            else:
                st.warning("No projects found. Try a shorter keyword.")
        else:
            st.info("Type a project name or builder name above to search.")

        # Quick stats
        with st.expander("About this dataset"):
            st.markdown(
                "Data scraped directly from [rera.karnataka.gov.in](https://rera.karnataka.gov.in/viewAllProjects). "
                "Includes all 10,096 RERA-registered projects in Karnataka as of November 2026. "
                "Refreshed manually by running the scraper."
            )


# ============================================================
# TAB 3 — Investment Signals
# ============================================================
with tab3:
    st.subheader("Bengaluru Locality Investment Signals")
    st.caption("Price benchmarks for the ₹40–80 lakh budget range")

    st.markdown("#### Filter by budget and size")
    col1, col2 = st.columns(2)
    with col1:
        budget = st.slider("Budget (₹ lakhs)", 30, 100, 60, step=5)
    with col2:
        area = st.slider("Desired area (sq ft)", 400, 1500, 900, step=50)

    target_psf = (budget * 100_000) / area
    st.markdown(f"**Target price:** ₹{target_psf:,.0f} / sq ft at {area} sq ft for ₹{budget}L")

    st.markdown("---")
    st.markdown("#### Matching localities")

    matches = budget_signal(budget, area)
    if matches:
        for p in matches[:10]:
            badge = RISK_BADGE.get(p["risk"], "")
            diff  = p["avg_psf"] - target_psf
            fit   = "within budget" if diff <= 0 else f"₹{diff:,.0f}/sqft over budget"
            with st.container():
                c1, c2, c3, c4 = st.columns([3, 2, 2, 2])
                c1.markdown(f"**{p['locality']}** `{p['pin']}`")
                c2.markdown(f"₹{p['avg_psf']:,}/sqft")
                c3.markdown(p["trend"])
                c4.markdown(f"{badge} {p['risk']} risk — {fit}")
    else:
        st.warning("No localities match this budget. Try increasing the budget or reducing the size.")

    st.markdown("---")
    st.markdown("#### All Bengaluru localities in database")
    import pandas as pd
    df = pd.DataFrame(PRICE_DATA)
    df.columns = ["Locality", "PIN", "Avg PSF (₹)", "YoY Trend", "Risk"]
    df["Total at target area (₹L)"] = (df["Avg PSF (₹)"] * area / 100_000).round(1)
    st.dataframe(df, use_container_width=True, hide_index=True)

    st.caption(
        "PSF figures are indicative benchmarks based on 2026 market data. "
        "Consult a registered property agent before making investment decisions."
    )
