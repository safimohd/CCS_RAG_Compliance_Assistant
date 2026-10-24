"""
System C: Agentic RAG
Within-domain, multi-step retrieval with query decomposition and reflection.

Core agentic behaviours:
  1. Query decomposition  -- splits multi-part or complex queries into focused sub-queries
  2. Sequential retrieval -- each sub-query gets its own top-k retrieval pass
  3. Synthesis            -- combines all retrieved passages before answering
  4. Reflection loop      -- if the first answer flags insufficient evidence,
                             the agent reformulates and retrieves once more

This sits on top of the existing ChromaDB index and gpt-5-nano, so no new
infrastructure is required - it runs as soon as the ingest is done.

Usage:
    python agentic_rag.py --domain Healthcare "What are the minimum staffing
    requirements for a 30-bed hospital and what are the renewal penalties?"
"""

from __future__ import annotations

import json
import os
import sys
import textwrap
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "05_App"))

# ── constants (mirror rag_query.py) ─────────────────────────────────────────
EMBED_MODEL  = "BAAI/bge-small-en-v1.5"
COLLECTION   = "regulatory_corpus"
LLM_MODEL    = os.getenv("LLM_MODEL", "gpt-5-nano")
INDEX_DIR    = PROJECT_ROOT / "05_App" / "chroma_db"
TOP_K        = 6
CANDIDATE_K  = 24
TIER_WEIGHT  = {"A": 1.00, "B": 0.95, "C": 0.80, "D": 0.55, "E": 0.35}

# ── decomposition prompt ─────────────────────────────────────────────────────
DECOMPOSE_SYSTEM = """\
You are a query analyser for an Indian regulatory compliance assistant.
Your job is to break a complex user query into independent, focused sub-queries
that can each be answered by searching a regulatory document index.

Rules:
- If the query is simple and atomic, return it unchanged as a single sub-query.
- If it contains multiple questions or requires connecting several facts, split it.
- Each sub-query must be self-contained and searchable.
- Do not invent questions the user did not ask.
- Return ONLY a JSON array of strings, no other text.

Examples:
  Input:  "What are the registration fees for a RERA project and the penalty for delay?"
  Output: ["What are the registration fees for a RERA project?",
           "What is the penalty for delay in RERA project registration?"]

  Input:  "How long must indoor patient records be kept?"
  Output: ["How long must indoor patient records be kept?"]
"""

# ── reflection prompt ────────────────────────────────────────────────────────
REFLECT_SYSTEM = """\
You are a quality-checker for an Indian regulatory compliance assistant.
Given an answer draft, decide whether it is sufficient or needs more retrieval.
A draft is insufficient if it explicitly says the corpus does not contain the
information, or if it is shorter than two sentences without citing a source.

Respond with ONLY valid JSON:
  {"sufficient": true}
  or
  {"sufficient": false, "refined_query": "<a better search query to try>"}
"""

# ── final answer prompt ───────────────────────────────────────────────────────
ANSWER_SYSTEM = """\
You are an Indian regulatory compliance assistant. Answer ONLY from the
retrieved sources supplied. Structure every answer as:
1. A direct answer to the question.
2. Sources relied on, cited by document TITLE (never a filename).
3. The specific regulatory provision (section, rule, clause).
4. A concise compliance takeaway.

Hard rules:
- If sources do not contain sufficient evidence, say so explicitly and stop.
- DRAFT material is not binding; flag it plainly.
- Never invent figures, section numbers or citations.
- Correct false premises before answering.
"""


def _load_store():
    if not INDEX_DIR.exists():
        sys.exit(f"ERROR: no index at {INDEX_DIR}. Run ingest_corpus.py first.")
    from langchain_huggingface import HuggingFaceEmbeddings
    from langchain_chroma import Chroma
    emb = HuggingFaceEmbeddings(
        model_name=EMBED_MODEL, encode_kwargs={"normalize_embeddings": True})
    return Chroma(collection_name=COLLECTION, embedding_function=emb,
                  persist_directory=str(INDEX_DIR))


def _retrieve(store, query: str, domain: str, k: int = TOP_K) -> list[dict]:
    """Authority-weighted retrieval for one sub-query."""
    try:
        space = ((store._collection.metadata or {}).get("hnsw:space") or "l2").lower()
    except Exception:
        space = "l2"

    results = store.similarity_search_with_relevance_scores(
        query, k=CANDIDATE_K, filter={"domain": domain})

    scored = []
    for doc, raw in results:
        md = doc.metadata or {}
        if md.get("tier", "E") == "E":
            continue
        if space.startswith("cosine"):
            cos = raw
        else:
            cos = 1.0 - (1.0 - raw) ** 2
        weighted = cos * TIER_WEIGHT.get(md.get("tier", "C"), 0.80)
        scored.append({
            "content":  doc.page_content,
            "title":    md.get("title", "Unknown"),
            "tier":     md.get("tier", "?"),
            "source":   md.get("source_url", ""),
            "weighted": weighted,
        })

    scored.sort(key=lambda x: x["weighted"], reverse=True)
    return scored[:k]


def _passages_text(passages: list[dict]) -> str:
    lines = []
    for i, p in enumerate(passages, 1):
        lines.append(
            f"[{i}] Title: {p['title']} | Tier: {p['tier']}\n{p['content']}")
    return "\n\n".join(lines)


def _llm_call(client, system: str, user: str, temperature: float = 0.0) -> str:
    rsp = client.chat.completions.create(
        model=LLM_MODEL,
        temperature=temperature,
        messages=[
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
    )
    return rsp.choices[0].message.content.strip()


def decompose_query(client, query: str) -> list[str]:
    """Return a list of focused sub-queries (may be length 1 for simple queries)."""
    raw = _llm_call(client, DECOMPOSE_SYSTEM, query)
    try:
        subs = json.loads(raw)
        if isinstance(subs, list) and all(isinstance(s, str) for s in subs):
            return [s.strip() for s in subs if s.strip()]
    except (json.JSONDecodeError, ValueError):
        pass
    # Fallback: return original query intact
    return [query]


def reflect(client, original_query: str, draft_answer: str) -> dict:
    """Check if the draft is sufficient; return refinement if not."""
    user_msg = (
        f"Original question: {original_query}\n\n"
        f"Draft answer:\n{draft_answer}"
    )
    raw = _llm_call(client, REFLECT_SYSTEM, user_msg)
    try:
        result = json.loads(raw)
        if isinstance(result, dict):
            return result
    except (json.JSONDecodeError, ValueError):
        pass
    return {"sufficient": True}


def agentic_answer(
    query: str,
    domain: str,
    store=None,
    client=None,
    verbose: bool = False,
) -> dict[str, Any]:
    """
    Full agentic pipeline for one query.

    Returns:
        {
          "query":        str,
          "domain":       str,
          "sub_queries":  list[str],
          "passages":     list[dict],   # all retrieved chunks, de-duplicated
          "answer":       str,
          "reflection":   dict,         # was a second pass needed?
          "steps":        list[str],    # log of reasoning steps
        }
    """
    if store is None:
        store = _load_store()
    if client is None:
        from openai import OpenAI
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            sys.exit("ERROR: OPENAI_API_KEY not set")
        client = OpenAI(api_key=api_key)

    steps = []

    # ── Step 1: decompose ────────────────────────────────────────────────────
    sub_queries = decompose_query(client, query)
    steps.append(f"Decomposed into {len(sub_queries)} sub-quer{'y' if len(sub_queries)==1 else 'ies'}: {sub_queries}")

    # ── Step 2: retrieve for each sub-query ──────────────────────────────────
    seen_chunks: set[str] = set()
    all_passages: list[dict] = []
    for sq in sub_queries:
        passages = _retrieve(store, sq, domain)
        for p in passages:
            key = p["content"][:120]
            if key not in seen_chunks:
                seen_chunks.add(key)
                all_passages.append(p)
    steps.append(f"Retrieved {len(all_passages)} unique passages across {len(sub_queries)} sub-queries")

    # ── Step 3: first-pass answer ────────────────────────────────────────────
    context = _passages_text(all_passages)
    user_msg = (
        f"Domain: {domain}\n\n"
        f"Retrieved sources:\n{context}\n\n"
        f"Question: {query}"
    )
    draft = _llm_call(client, ANSWER_SYSTEM, user_msg)
    steps.append("Generated first-pass answer")

    # ── Step 4: reflect and optionally re-retrieve ───────────────────────────
    reflection = reflect(client, query, draft)
    final_answer = draft

    if not reflection.get("sufficient", True):
        refined_q = reflection.get("refined_query", query)
        steps.append(f"Reflection: insufficient. Re-querying with: '{refined_q}'")

        extra_passages = _retrieve(store, refined_q, domain)
        for p in extra_passages:
            key = p["content"][:120]
            if key not in seen_chunks:
                seen_chunks.add(key)
                all_passages.append(p)

        context2 = _passages_text(all_passages)
        user_msg2 = (
            f"Domain: {domain}\n\n"
            f"Retrieved sources (expanded):\n{context2}\n\n"
            f"Question: {query}"
        )
        final_answer = _llm_call(client, ANSWER_SYSTEM, user_msg2)
        steps.append("Generated final answer after re-retrieval")
    else:
        steps.append("Reflection: answer is sufficient, no re-retrieval needed")

    if verbose:
        for s in steps:
            print(f"  [agent] {s}")
        print()

    return {
        "query":       query,
        "domain":      domain,
        "sub_queries": sub_queries,
        "passages":    all_passages,
        "answer":      final_answer,
        "reflection":  reflection,
        "steps":       steps,
    }


# ── CLI for quick testing ─────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="System C: Agentic RAG")
    parser.add_argument("--domain", choices=["Healthcare", "RERA"], required=True)
    parser.add_argument("query", nargs="+")
    args = parser.parse_args()
    q = " ".join(args.query)
    result = agentic_answer(q, args.domain, verbose=True)
    print("=" * 72)
    print(f"QUERY: {result['query']}")
    print(f"DOMAIN: {result['domain']}")
    print(f"SUB-QUERIES: {result['sub_queries']}")
    print(f"PASSAGES USED: {len(result['passages'])}")
    print()
    print("ANSWER:")
    print(textwrap.fill(result["answer"], width=80))
