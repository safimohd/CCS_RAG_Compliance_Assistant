"""
Indian Regulatory Compliance Intelligence Assistant
Streamlit chat interface.

    streamlit run app.py

Requires: a built index (python ingest_corpus.py) and 05_App/.env with OPENAI_API_KEY.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rag_query import (  # noqa: E402
    COLLECTION, EMBED_MODEL, INDEX_DIR, LLM_BASE_URL, LLM_MODEL,
    SYSTEM_PROMPT, TIER_NAME, format_sources, load_env, retrieve,
)

st.set_page_config(page_title="Indian Regulatory Compliance Assistant",
                   page_icon="📋", layout="wide")

TIER_BADGE = {
    "A": ("Core regulatory", "#1a7f37", "binding"),
    "B": ("Sector regulation", "#1f6feb", "binding within sector"),
    "C": ("Supporting guidance", "#9a6700", "explanatory, not binding"),
    "D": ("DRAFT / PROPOSED", "#bc4c00", "NOT binding"),
    "E": ("Review", "#82061e", "binding status unverified"),
}

# Load .env here, not only inside get_client(), so settings read at import time
# (below) actually see it.
load_env()

# Reasoning effort for the demo. "minimal" or "low" cuts the ~60s first-token
# latency of gpt-5-nano substantially. Set LLM_REASONING_EFFORT in .env to
# override, or to an empty value to use the model's default.
REASONING_EFFORT = os.getenv("LLM_REASONING_EFFORT", "low").strip()

DOMAIN_BLURB = {
    "Healthcare": "Clinical establishments, NMC regulation and medical education, "
                  "PM-JAY and health insurance, telemedicine, ABDM and health-data privacy.",
    "RERA": "The Real Estate (Regulation and Development) Act, 2016 and Karnataka RERA "
            "rules, circulars, notifications and orders.",
}


@st.cache_resource(show_spinner="Loading the index…")
def get_store():
    # The "is there an index" check lives OUTSIDE this function on purpose.
    # @st.cache_resource would pin a None return for the life of the process,
    # so an app opened while the index was still building would go on claiming
    # there is no index long after it finished.
    from langchain_chroma import Chroma
    from langchain_huggingface import HuggingFaceEmbeddings
    emb = HuggingFaceEmbeddings(model_name=EMBED_MODEL,
                                encode_kwargs={"normalize_embeddings": True})
    return Chroma(collection_name=COLLECTION, embedding_function=emb,
                  persist_directory=str(INDEX_DIR))


@st.cache_resource
def get_client():
    load_env()
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        return None
    from openai import OpenAI
    return OpenAI(api_key=key, base_url=LLM_BASE_URL) if LLM_BASE_URL else OpenAI(api_key=key)


def tier_chip(tier: str) -> str:
    label, colour, note = TIER_BADGE[tier]
    return (f"<span style='background:{colour};color:#fff;padding:2px 8px;"
            f"border-radius:10px;font-size:0.72rem;font-weight:600;white-space:nowrap'>"
            f"{label}</span> <span style='color:#666;font-size:0.72rem'>{note}</span>")


# ── sidebar ──────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("📋 Compliance Assistant")
    st.caption("IIM Bangalore CCS · Indian Regulatory Compliance Intelligence")

    domain = st.radio("**Domain**", ["Healthcare", "RERA"],
                      format_func=lambda d: "Real Estate / RERA" if d == "RERA" else d)
    st.caption(DOMAIN_BLURB[domain])
    st.divider()

    st.markdown("**Retrieval**")
    top_k = st.slider("Passages retrieved", 3, 12, 6,
                      help="The cohort benchmark uses 6.")
    include_drafts = st.checkbox("Include draft / proposed material", value=False,
                                 help="Tier D. Always labelled as non-binding in the answer.")
    include_review = st.checkbox("Include unverified material", value=False,
                                 help="Tier E. Binding status not confirmed by a human.")

    st.divider()
    st.markdown("**Authority tiers**")
    for t in "ABCDE":
        st.markdown(tier_chip(t), unsafe_allow_html=True)

    st.divider()
    st.caption(f"Model: `{LLM_MODEL}`  ·  Embeddings: `{EMBED_MODEL.split('/')[-1]}`"
               + (f"  ·  reasoning: `{REASONING_EFFORT}`" if REASONING_EFFORT else ""))
    # No use_container_width= here: Streamlit deprecated it in favour of
    # width= and newer releases reject it outright, which would kill the whole
    # sidebar on load. Not worth a full-width button.
    if st.button("Clear conversation"):
        st.session_state.messages = []
        st.rerun()

# ── preflight ────────────────────────────────────────────────────────────────
store = get_store() if INDEX_DIR.exists() else None
if store is None:
    st.error("No index found.")
    st.code("cd 05_App\npython ingest_corpus.py", language="bash")
    st.stop()

client = get_client()
if client is None:
    st.warning("`OPENAI_API_KEY` is not set — retrieval works, answer generation does not.")
    st.code("copy .env.example .env    # then put your key in .env", language="bash")

# ── header ───────────────────────────────────────────────────────────────────
st.title("Indian Regulatory Compliance Intelligence Assistant")
st.caption(
    "Answers are grounded only in the project corpus. Where the corpus does not settle a "
    "question, the assistant says so rather than filling the gap. Draft and unverified "
    "material is labelled and never presented as binding law."
)

if "messages" not in st.session_state:
    st.session_state.messages = []

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg.get("sources"):
            with st.expander(f"Sources ({len(msg['sources'])})"):
                for i, s in enumerate(msg["sources"], 1):
                    st.markdown(f"**[{i}] {s['title']}**", unsafe_allow_html=True)
                    st.markdown(tier_chip(s["tier"]), unsafe_allow_html=True)
                    meta = f"`{s['bucket']}` · similarity {s['similarity']:.3f}"
                    if s.get("url"):
                        meta += f" · [source]({s['url']})"
                    st.caption(meta)
                    st.text(s["text"][:600] + ("…" if len(s["text"]) > 600 else ""))
                    st.divider()

# ── chat ─────────────────────────────────────────────────────────────────────
placeholder = ("Ask about Karnataka RERA or the central Act…" if domain == "RERA"
               else "Ask about Indian healthcare regulation…")

if prompt := st.chat_input(placeholder):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("Searching the corpus…"):
            results = retrieve(store, prompt, domain, top_k, include_drafts, include_review)

        if not results:
            answer = (f"The {domain} corpus does not contain sources relevant to this "
                      f"question, so I cannot answer it on the evidence available. "
                      f"Try rephrasing, or check whether the topic falls in the other domain.")
            st.markdown(answer)
            st.session_state.messages.append(
                {"role": "assistant", "content": answer, "sources": []})
        else:
            if any(r["tier"] == "D" for r in results):
                st.warning("Retrieved material includes **draft or proposed** documents. "
                           "These are not binding regulation.")
            if any(r["tier"] == "E" for r in results):
                st.warning("Retrieved material includes documents whose **binding status is "
                           "unverified**.")

            if client is None:
                answer = "_Answer generation is unavailable — `OPENAI_API_KEY` is not set. " \
                         "The retrieved sources are shown below._"
                st.markdown(answer)
            else:
                user_msg = (f"Domain: {domain}\n\nQuestion: {prompt}\n\n"
                            f"Retrieved sources:\n\n{format_sources(results)}")
                messages = [{"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": user_msg}]
                answer = None
                kwargs = {"model": LLM_MODEL, "messages": messages}
                # gpt-5-nano is a reasoning model: it spends reasoning tokens
                # before emitting the first visible one, so streaming does not
                # hide the wait. Default effort cost ~60s per answer, which is
                # six minutes of dead air across a demo. Lowering the effort
                # cuts that sharply. Dropped automatically if the model or the
                # installed client does not accept the parameter.
                if REASONING_EFFORT:
                    kwargs["reasoning_effort"] = REASONING_EFFORT
                note = st.empty()
                try:
                    try:
                        stream = client.chat.completions.create(**kwargs, stream=True)
                    except Exception:
                        if "reasoning_effort" not in kwargs:
                            raise
                        kwargs.pop("reasoning_effort")
                        note.caption("_reasoning_effort not supported here — "
                                     "using the model default._")
                        stream = client.chat.completions.create(**kwargs, stream=True)
                    # A chunk can carry an empty choices list - the final
                    # usage-only chunk does, and chunk.choices[0] would raise
                    # IndexError mid-answer.
                    answer = st.write_stream(
                        (c.choices[0].delta.content or "")
                        for c in stream if c.choices
                    )
                except Exception as exc:
                    # Streaming is refused for some models and some
                    # unverified organisations. Fall back to one blocking call
                    # rather than showing the examiner a stack trace.
                    note.caption("_Streaming unavailable, generating…_")
                    try:
                        kwargs.pop("reasoning_effort", None)
                        resp = client.chat.completions.create(**kwargs)
                        answer = (resp.choices[0].message.content or "").strip()
                        st.markdown(answer)
                    except Exception as exc2:
                        answer = (f"**Generation failed.** Streaming: `{exc}`  \n"
                                  f"Non-streaming: `{exc2}`\n\n"
                                  f"The retrieved sources are still shown below.")
                        st.error(answer)
                if not answer:
                    answer = ("_The model returned an empty response. The retrieved "
                              "sources are below._")
                    st.markdown(answer)

            with st.expander(f"Sources ({len(results)})", expanded=True):
                for i, s in enumerate(results, 1):
                    st.markdown(f"**[{i}] {s['title']}**")
                    st.markdown(tier_chip(s["tier"]), unsafe_allow_html=True)
                    meta = f"`{s['bucket']}` · similarity {s['similarity']:.3f}"
                    if s.get("url"):
                        meta += f" · [source]({s['url']})"
                    st.caption(meta)
                    st.text(s["text"][:600] + ("…" if len(s["text"]) > 600 else ""))
                    st.divider()

            st.session_state.messages.append(
                {"role": "assistant", "content": answer, "sources": results})
