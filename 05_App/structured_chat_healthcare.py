"""
structured_chat_healthcare.py  --  Data-augmented chat for the Healthcare domain only.

Provides two entry points:
  render_user_chat()   -- clean, guided view for the 'user' role
  render_smart_tab()   -- power tab for compliance_officer / admin roles

The LLM uses OpenAI tool-calling to pull real data from Supabase:
  search_healthcare_packages  --  AB-ArK / Arogya Sanjeevini / PM-JAY package rates
  find_empanelled_hospitals   --  PM-JAY empanelled hospitals by Karnataka district

No RERA, property, or market-rates code lives here.
"""

from __future__ import annotations

import json
import os

import requests as _req
import streamlit as st

_USER_RAG_STORE = None

# ── helpers ───────────────────────────────────────────────────────────────────

def _secret(key: str) -> str:
    return os.environ.get(key) or st.secrets.get(key, "")


@st.cache_data(ttl=120)
def _fetch_hc_feedback_stats() -> dict:
    """Return aggregate feedback counts for Healthcare domain only. Cached 2 min."""
    url = _secret("SUPABASE_URL")
    key = _secret("SUPABASE_KEY")
    if not url or not key:
        return {}
    try:
        resp = _req.get(
            f"{url}/rest/v1/feedback",
            headers={"apikey": key, "Authorization": f"Bearer {key}"},
            params={"select": "feedback_type", "domain": "eq.Healthcare"},
            timeout=5,
        )
        if not resp.ok:
            return {}
        rows = resp.json() or []
        total = len(rows)
        ups = sum(1 for r in rows if r.get("feedback_type") == "up")
        pct = int(ups / total * 100) if total else 0
        return {"total": total, "ups": ups, "downs": total - ups, "pct": pct}
    except Exception:
        return {}


def _log_hc_feedback(question: str, answer: str, conf_label: str,
                     conf_score: float, feedback_type: str) -> None:
    """Write a feedback row to Supabase for the Healthcare domain."""
    url = _secret("SUPABASE_URL")
    key = _secret("SUPABASE_KEY")
    if not url or not key:
        return
    try:
        _req.post(
            f"{url}/rest/v1/feedback",
            headers={
                "apikey": key,
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "Prefer": "return=minimal",
            },
            json={
                "domain": "Healthcare",
                "question": question[:1000],
                "answer": answer[:2000],
                "conf_label": conf_label,
                "conf_score": round(conf_score, 4),
                "feedback_type": feedback_type,
                "user_id": None,
                "user_email": "guest@claritai.in",
            },
            timeout=5,
        )
    except Exception:
        pass


def _supa(table: str, params: dict, limit: int = 10) -> list[dict]:
    """Generic Supabase REST GET. Returns a list of row dicts or []."""
    url   = _secret("SUPABASE_URL")
    key   = _secret("SUPABASE_KEY")
    if not url or not key:
        return []
    headers = {
        "apikey":        key,
        "Authorization": f"Bearer {key}",
        "Accept":        "application/json",
    }
    params.setdefault("limit", str(limit))
    try:
        r = _req.get(
            f"{url}/rest/v1/{table}",
            params=params, headers=headers, timeout=12
        )
        data = r.json()
        return data if isinstance(data, list) else []
    except Exception:
        return []


# ── Supabase query functions (Healthcare only) ────────────────────────────────

def search_healthcare_packages(
    procedure_name: str,
    scheme: str = "any",
    specialty: str | None = None,
    city: str | None = None,
) -> list[dict]:
    """
    Query packages_master for procedures matching the given name.
    Returns up to 10 rows with rate information.
    """
    params: dict = {
        "select": "scheme,specialty,procedure_code,procedure_name,"
                  "hospital_type,general_ward_rate,semi_private_rate,"
                  "private_rate,geography_scope",
        "procedure_name": f"ilike.*{procedure_name}*",
        "order":          "general_ward_rate.asc",
    }
    if scheme and scheme not in ("any", "Any", ""):
        params["scheme"] = f"eq.{scheme}"
    if specialty:
        params["specialty"] = f"ilike.*{specialty}*"

    rows = _supa("packages_master", params, limit=10)

    # city filter client-side (geography_scope is free-text)
    if city and rows:
        city_lc = city.lower()
        filtered = [
            r for r in rows
            if city_lc in (r.get("geography_scope") or "").lower()
            or "all" in (r.get("geography_scope") or "").lower()
        ]
        if filtered:
            rows = filtered

    return rows


def find_empanelled_hospitals(district: str, scheme: str = "PM-JAY") -> list[dict]:
    """
    Return PM-JAY empanelled hospitals in a Karnataka district.
    Normalises common Bangalore/Bengaluru spelling variants and falls back
    to a broader search if the exact district returns nothing.
    """
    _DISTRICT_ALIASES: list[tuple[str, str]] = [
        # Bangalore / Bengaluru variants
        ("bengaluru", "Bangalore"),
        ("bangalore", "Bangalore"),
        ("blr",       "Bangalore"),
        ("560",       "Bangalore"),
        ("iimb",      "Bangalore"),
        ("iim bang",  "Bangalore"),
        ("bannerghatta", "Bangalore"),
        ("koramangala",  "Bangalore"),
        ("whitefield",   "Bangalore"),
        ("electronic city", "Bangalore"),
        ("indiranagar",  "Bangalore"),
        ("jayanagar",    "Bangalore"),
        ("jp nagar",     "Bangalore"),
        ("btm layout",   "Bangalore"),
        ("hsr layout",   "Bangalore"),
        ("yelahanka",    "Bangalore"),
        ("sarjapur",     "Bangalore"),
        # Other Karnataka districts
        ("mysuru",      "Mysore"),
        ("mysore",      "Mysore"),
        ("hubballi",    "Hubli"),
        ("hubli",       "Hubli"),
        ("dharwad",     "Dharwad"),
        ("belagavi",    "Belgaum"),
        ("belgaum",     "Belgaum"),
        ("mangaluru",   "Mangalore"),
        ("mangalore",   "Mangalore"),
        ("kalaburagi",  "Gulbarga"),
        ("gulbarga",    "Gulbarga"),
        ("shivamogga",  "Shimoga"),
        ("shimoga",     "Shimoga"),
        ("davanagere",  "Davanagere"),
        ("davangere",   "Davanagere"),
        ("tumakuru",    "Tumkur"),
        ("tumkur",      "Tumkur"),
        ("udupi",       "Udupi"),
        ("hassan",      "Hassan"),
        ("ballari",     "Bellary"),
        ("bellary",     "Bellary"),
        ("vijayapura",  "Bijapur"),
        ("bijapur",     "Bijapur"),
        ("raichur",     "Raichur"),
        ("bidar",       "Bidar"),
        ("koppal",      "Koppal"),
        ("gadag",       "Gadag"),
        ("bagalkot",    "Bagalkot"),
        ("chitradurga", "Chitradurga"),
        ("chikkamagaluru", "Chikmagalur"),
        ("chikmagalur", "Chikmagalur"),
        ("kodagu",      "Coorg"),
        ("coorg",       "Coorg"),
        ("mandya",      "Mandya"),
        ("chamarajanagar", "Chamarajanagar"),
        ("ramanagara",  "Ramanagara"),
        ("kolar",       "Kolar"),
        ("chikkaballapur", "Chikkaballapur"),
        ("chikballapur",   "Chikkaballapur"),
        ("yadgir",      "Yadgir"),
        ("vijayapur",   "Bijapur"),
    ]

    district_lc = district.lower().strip()
    search_term = district
    for fragment, canonical in _DISTRICT_ALIASES:
        if fragment in district_lc:
            search_term = canonical
            break

    table  = "pmjay_hospitals_karnataka" if "IRDAI" not in scheme else "irdai_hospitals_karnataka"
    select = ("name,address,district,phone,hospital_type,care_level,"
              "empanelment_scheme,cashless,specialities,services")

    rows = _supa(table, {"select": select, "district": f"ilike.*{search_term}*"}, limit=12)

    if not rows and search_term == "Bangalore":
        rows = _supa(table, {"select": select, "district": "ilike.*Bangalore Urban*"}, limit=12)
    if not rows and search_term == "Bangalore":
        rows = _supa(table, {"select": select, "district": "ilike.*Bengaluru*"}, limit=12)
    elif not rows and search_term == "Mysore":
        rows = _supa(table, {"select": select, "district": "ilike.*Mysuru*"}, limit=12)
    elif not rows and search_term == "Hubli":
        rows = _supa(table, {"select": select, "district": "ilike.*Hubballi*"}, limit=12)
    elif not rows and search_term == "Mangalore":
        rows = _supa(table, {"select": select, "district": "ilike.*Mangaluru*"}, limit=12)
    elif not rows and search_term == "Belgaum":
        rows = _supa(table, {"select": select, "district": "ilike.*Belagavi*"}, limit=12)
    elif not rows and search_term == "Gulbarga":
        rows = _supa(table, {"select": select, "district": "ilike.*Kalaburagi*"}, limit=12)

    if not rows:
        rows = _supa(table, {"select": select}, limit=10)

    return rows


# ── OpenAI tool schemas (Healthcare only) ────────────────────────────────────

TOOLS_HEALTHCARE = [
    {
        "type": "function",
        "function": {
            "name": "search_healthcare_packages",
            "description": (
                "Look up treatment or procedure costs under Karnataka government "
                "health schemes: AB-ArK (all Karnataka districts), Arogya Sanjeevini "
                "(Belagavi, Dharwad, Mangalore, Mysore, Kalaburgi), and PM-JAY / HBP "
                "(national). Call this whenever the user asks about treatment costs, "
                "which scheme covers a procedure, package rates, or affordability."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "procedure_name": {
                        "type": "string",
                        "description": "Name or partial name of the medical procedure or treatment",
                    },
                    "scheme": {
                        "type": "string",
                        "enum": ["AB-ArK", "HBP_National", "Sanjeevini", "any"],
                        "description": "Which government scheme to check. Use 'any' if the user has not specified.",
                    },
                    "specialty": {
                        "type": "string",
                        "description": "Medical specialty such as Cardiology, Oncology, Orthopaedics (optional)",
                    },
                    "city": {
                        "type": "string",
                        "description": "City in Karnataka if the user specified one (optional)",
                    },
                },
                "required": ["procedure_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_empanelled_hospitals",
            "description": (
                "Find government-empanelled hospitals in a Karnataka district "
                "where patients can avail PM-JAY / Ayushman Bharat treatment "
                "without out-of-pocket payment. Call this when the user asks "
                "where to get treated, which hospitals accept the scheme, or "
                "which hospitals are covered in a district."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "district": {
                        "type": "string",
                        "description": "Karnataka district name or locality (e.g. Bangalore, Whitefield, Mysore)",
                    },
                    "scheme": {
                        "type": "string",
                        "enum": ["PM-JAY", "GIC / IRDAI", "Both"],
                        "description": "Which empanelment scheme",
                    },
                },
                "required": ["district"],
            },
        },
    },
]

# ── System prompt (Healthcare only) ──────────────────────────────────────────

_SYS_HEALTHCARE = """\
You are a friendly, plain-language healthcare assistant for patients and families \
in Karnataka, India. Help with general health information using only the official \
document excerpts supplied with the current question, scheme and package lookups, \
and empanelled hospital records.

IMPORTANT guidelines:
- Treat the supplied document excerpts as the only authority for clinical guidance. \
  Do not diagnose, prescribe a personal treatment, or fill gaps from general memory. \
  If no relevant official excerpt was found, say so plainly and suggest contacting \
  a qualified health professional. For urgent or severe symptoms, advise immediate \
  in-person medical care without trying to triage a diagnosis.
- Explain scheme acronyms in simple words the first time they matter. Do not imply \
  someone is eligible based only on chat. Explain how to verify through the official \
  beneficiary process when the supplied sources support it.
- Never call district results "nearest" or rank them by distance. The hospital data \
  does not provide verified distance coordinates. Say "matching hospitals in this area".
- Cite clinical or scheme guidance by the supplied document title. Never invent a source.
- Always use the search_healthcare_packages tool when a user asks about costs, \
  rates, or coverage. Do not guess rates from memory.
- Always use find_empanelled_hospitals when asked about where to get treated. \
  For Bangalore / IIM Bangalore / pincode 560076, pass district="Bangalore". \
  Our dataset now includes hospitals in Bangalore Urban district. \
  List only returned hospitals with name, address, phone (if available), \
  specialities, and the scheme shown in the record. Never assume a hospital offers \
  cashless care for every treatment or that the patient is eligible; advise confirming \
  both with the hospital's scheme desk. Do not rank results by distance. \
  For the most up-to-date full PM-JAY list, users can also check \
  https://hospitals.pmjay.gov.in (search Bengaluru Urban).
- When you have tool results, present the most relevant 3-5 results clearly. \
  Format rates as Indian Rupees with commas (e.g. Rs. 12,000).
- If rates differ by ward type, explain the difference in simple terms.
- Never recommend a specific doctor or give medical diagnosis.
- Keep answers short, warm, and jargon-free. The user is a patient or family member.
- If the tool returns hospitals, list their name, address, and contact number. \
  Always mention that these hospitals offer cashless treatment under PM-JAY / AB-ArK.
- Do not use em dashes anywhere in your response.\
"""

_SYS_POWER_HC = """\
You are a data-augmented healthcare compliance assistant with live access to \
Karnataka's healthcare regulatory databases. You can query:
- 9,249 healthcare package rates across AB-ArK, Sanjeevini, and PM-JAY schemes
- 497+ PM-JAY empanelled hospitals across Karnataka (including 29 in Bangalore Urban)

Always use the available tools to fetch real data before answering factual questions \
about rates or hospital empanelment. Synthesise the tool output into a clear, \
actionable response. Present data in tables where it helps readability.
Do not use em dashes anywhere in your response.\
"""

# ── Tool dispatcher ───────────────────────────────────────────────────────────

_TOOL_FNS = {
    "search_healthcare_packages": search_healthcare_packages,
    "find_empanelled_hospitals":  find_empanelled_hospitals,
}


def _run_tool_call(tool_call) -> str:
    name = tool_call.function.name
    fn   = _TOOL_FNS.get(name)
    if not fn:
        return f"[unknown tool: {name}]"
    try:
        args = json.loads(tool_call.function.arguments or "{}")
        result = fn(**args)
        return json.dumps(result, default=str)
    except Exception as e:
        return f"[tool error: {e}]"


# ── Agentic generate (tool-calling loop) ─────────────────────────────────────

def _agentic_generate(
    client,
    messages: list[dict],
    tools: list[dict],
    system_prompt: str,
) -> str:
    """
    Run the OpenAI tool-calling loop:
      1. Send messages to LLM.
      2. If the LLM calls a tool, execute it and loop back.
      3. When the LLM produces a final text answer, stream it and return.
    """
    model   = os.environ.get("LLM_MODEL", "gpt-5-nano")
    history = [{"role": "system", "content": system_prompt}] + messages

    for _ in range(5):
        kwargs: dict = {
            "model":    model,
            "messages": history,
        }
        if tools:
            kwargs["tools"]       = tools
            kwargs["tool_choice"] = "auto"

        resp = client.chat.completions.create(**kwargs)
        msg  = resp.choices[0].message

        if not msg.tool_calls:
            answer_text = msg.content or ""
            if answer_text:
                st.markdown(answer_text)
            return answer_text

        history.append(msg)
        for tc in msg.tool_calls:
            result_str = _run_tool_call(tc)
            history.append({
                "role":         "tool",
                "tool_call_id": tc.id,
                "content":      result_str,
            })

    answer = "I looked up the data but could not form a complete answer. Please try rephrasing."
    st.markdown(answer)
    return answer


# ── Example questions ─────────────────────────────────────────────────────────

_EXAMPLES_HEALTHCARE = [
    "How much does a knee replacement cost under AB-ArK?",
    "Which hospitals in Mysore are covered under PM-JAY?",
    "Does Arogya Sanjeevini cover dialysis in Mangalore?",
    "What is the rate for a C-section under government schemes?",
    "Compare cataract surgery rates across all three schemes",
]


def _example_chips(examples: list[str], key_prefix: str) -> str | None:
    """Render clickable example chips. Returns the clicked question or None."""
    cols = st.columns(len(examples))
    for i, (col, q) in enumerate(zip(cols, examples)):
        with col:
            if st.button(
                q,
                key=f"{key_prefix}_ex_{i}",
                use_container_width=True,
                type="secondary",
            ):
                return q
    return None


# ── CSS ───────────────────────────────────────────────────────────────────────

_HC_CSS = """
<style>
.sc-hero {
    background: linear-gradient(135deg, #064e3b 0%, #059669 100%);
    border-radius: 12px;
    padding: 20px 24px 16px;
    color: #fff;
    margin-bottom: 16px;
}
.sc-hero h2 { margin: 0 0 4px; font-size: 1.3rem; font-weight: 700; }
.sc-hero p  { margin: 0; font-size: .88rem; color: #a7f3d0; line-height: 1.5; }
.sc-hint    {
    background: #f0fdf4;
    border-left: 4px solid #22c55e;
    border-radius: 6px;
    padding: 8px 14px;
    font-size: .82rem;
    color: #166534;
    margin-bottom: 10px;
}
</style>
"""


# ── User chat view (clean, guided) ────────────────────────────────────────────

def render_user_chat(store=None):
    """
    Clean, single-purpose chat for the 'user' role (Healthcare domain only).
    No tabs, no jargon. Just a question box with guided examples.
    """
    st.markdown(_HC_CSS, unsafe_allow_html=True)
    store = _USER_RAG_STORE if store is None else store

    st.markdown("### What do you need help with?")
    st.caption(
        "Ask in your own words. Health guidance uses official sources; scheme and "
        "hospital details are checked against the available records."
    )

    hk = "sc_hc_history"
    if hk not in st.session_state:
        st.session_state[hk] = []
    if "sc_hc_feedback" not in st.session_state:
        st.session_state["sc_hc_feedback"] = {}

    history: list[dict] = st.session_state[hk]

    # Feedback stats bar — always visible above chat
    _fb = _fetch_hc_feedback_stats()
    if _fb.get("total", 0) > 0:
        st.markdown(
            f"<div style='font-size:.72rem;color:#6B7C93;margin-bottom:.4rem;'>"
            f"📊 Community feedback: "
            f"<b style='color:#1a7f4b'>{_fb['ups']} 👍</b> "
            f"out of <b>{_fb['total']}</b> ratings "
            f"<span style='color:#1a7f4b;font-weight:600'>({_fb['pct']}% helpful)</span>"
            f"</div>",
            unsafe_allow_html=True,
        )

    if not history:
        prompt_options = [
            "I have a health question",
            "Find hospitals near my area",
            "Explain government health schemes",
            "Check help with treatment costs",
        ]
        prompt_map = {
            prompt_options[0]: "I have a health question: ",
            prompt_options[1]: "Help me find hospitals near ",
            prompt_options[2]: "Explain the government health schemes available in Karnataka, who may qualify, and how to verify eligibility.",
            prompt_options[3]: "Check government scheme coverage and package costs for ",
        }
        cols = st.columns(2)
        for i, label in enumerate(prompt_options):
            with cols[i % 2]:
                if st.button(label, key=f"usr_hc_help_{i}", use_container_width=True):
                    st.session_state["sc_input_healthcare"] = prompt_map[label]

    for idx, msg in enumerate(history):
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if msg.get("sources"):
                with st.expander("Sources used"):
                    for source in msg["sources"]:
                        title = source.get("title", "Official source")
                        url = source.get("url")
                        st.markdown(f"- [{title}]({url})" if url else f"- {title}")
            # Confidence badge + feedback buttons for past assistant messages
            if msg["role"] == "assistant" and msg.get("conf_label"):
                _clabel = msg["conf_label"]
                _cscore = msg.get("conf_score", 0.0)
                _badge_colors = {"High": ("#d1fae5", "#065f46"), "Medium": ("#fef9c3", "#713f12"), "Low": ("#fee2e2", "#991b1b")}
                _bg, _fg = _badge_colors.get(_clabel, ("#f3f4f6", "#374151"))
                st.markdown(
                    f"<span style='background:{_bg};color:{_fg};font-size:.7rem;"
                    f"font-weight:600;padding:2px 8px;border-radius:999px;'>"
                    f"{_clabel} confidence ({_cscore:.2f})</span>",
                    unsafe_allow_html=True,
                )
                _fb_key = f"hist_fb_{idx}"
                if not st.session_state["sc_hc_feedback"].get(_fb_key):
                    _c1, _c2, _ = st.columns([1, 1, 8])
                    if _c1.button("👍", key=f"up_{_fb_key}"):
                        _log_hc_feedback(
                            history[idx - 1]["content"] if idx > 0 else "",
                            msg["content"], _clabel, _cscore, "up",
                        )
                        st.session_state["sc_hc_feedback"][_fb_key] = "up"
                        st.rerun()
                    if _c2.button("👎", key=f"dn_{_fb_key}"):
                        _log_hc_feedback(
                            history[idx - 1]["content"] if idx > 0 else "",
                            msg["content"], _clabel, _cscore, "down",
                        )
                        st.session_state["sc_hc_feedback"][_fb_key] = "down"
                        st.rerun()
                else:
                    _given = st.session_state["sc_hc_feedback"][_fb_key]
                    st.caption("Thanks for the feedback! 👍" if _given == "up" else "Thanks — we'll work to improve. 👎")

    prompt = st.chat_input(
        "Ask anything..." if not history else "Ask a follow-up...",
        key="sc_input_healthcare",
    )

    if not prompt:
        return

    history.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        try:
            from openai import OpenAI
            from rag_query import format_sources, retrieve

            try:
                source_rows = retrieve(store, prompt, "Healthcare", 5) if store is not None else []
                # Apply threshold on converted cosine similarity, not on the raw L2-based score.
                # Raw score of 0.35 is equivalent to cosine ~0.578, which is too strict for
                # conversational user questions. Using cosine >= 0.45 recovers regulatory
                # policy questions that are phrased informally (e.g. "I'm 72, do I get separate
                # cover?") while still filtering genuinely unrelated chunks.
                source_rows = [row for row in source_rows
                               if (1.0 - (1.0 - row.get("similarity", 0)) ** 2) >= 0.45]
                retrieval_failed = False
            except Exception:
                source_rows = []
                retrieval_failed = True
            source_context = format_sources(source_rows) if source_rows else ""
            grounded_system = _SYS_HEALTHCARE + (
                "\n\nOFFICIAL DOCUMENT EXCERPTS FOR THIS QUESTION:\n" + source_context
                if source_context else
                "\n\n" + (
                    "Document search is currently unavailable. Do not answer clinical "
                    "or scheme-process questions from memory; say the official source "
                    "search is unavailable. " if retrieval_failed else
                    "No official document excerpt matched this question. Do not answer "
                    "clinical or scheme-process questions from memory; say the sources "
                    "available here did not verify the answer. "
                ) +
                "Package and hospital tools may still be used for their respective factual lookups."
            )
            client = OpenAI(api_key=_secret("OPENAI_API_KEY"))
            answer = _agentic_generate(
                client,
                history[:-1] + [{"role": "user", "content": prompt}],
                TOOLS_HEALTHCARE,
                grounded_system,
            )
        except Exception as e:
            answer = f"Something went wrong. Please try again. ({e})"
            st.markdown(answer)

    _src_rows = locals().get("source_rows", [])
    source_refs = [
        {"title": row.get("title"), "url": row.get("url")}
        for row in _src_rows
        if row.get("title")
    ]

    # Compute confidence badge
    _best_sim = max((_src_rows[i].get("similarity", 0) for i in range(len(_src_rows))), default=0.0)
    if _best_sim >= 0.70:
        _conf_label = "High"
    elif _best_sim >= 0.45:
        _conf_label = "Medium"
    else:
        _conf_label = "Low"

    history.append({
        "role": "assistant",
        "content": answer,
        "sources": source_refs,
        "conf_label": _conf_label,
        "conf_score": round(_best_sim, 4),
    })

    # Show confidence badge for new answer
    with st.chat_message("assistant"):
        _badge_colors = {"High": ("#d1fae5", "#065f46"), "Medium": ("#fef9c3", "#713f12"), "Low": ("#fee2e2", "#991b1b")}
        _bg, _fg = _badge_colors.get(_conf_label, ("#f3f4f6", "#374151"))
        st.markdown(
            f"<span style='background:{_bg};color:{_fg};font-size:.7rem;"
            f"font-weight:600;padding:2px 8px;border-radius:999px;'>"
            f"{_conf_label} confidence ({_best_sim:.2f})</span>",
            unsafe_allow_html=True,
        )
        if source_refs:
            with st.expander("Sources used"):
                for source in source_refs:
                    title = source.get("title", "Official source")
                    url = source.get("url")
                    st.markdown(f"- [{title}]({url})" if url else f"- {title}")

        # Feedback buttons for new answer
        _new_fb_key = f"new_{len(history)}"
        if not st.session_state["sc_hc_feedback"].get(_new_fb_key):
            _c1, _c2, _ = st.columns([1, 1, 8])
            if _c1.button("👍", key=f"up_new_{len(history)}"):
                _log_hc_feedback(prompt, answer, _conf_label, _best_sim, "up")
                st.session_state["sc_hc_feedback"][_new_fb_key] = "up"
                st.rerun()
            if _c2.button("👎", key=f"dn_new_{len(history)}"):
                _log_hc_feedback(prompt, answer, _conf_label, _best_sim, "down")
                st.session_state["sc_hc_feedback"][_new_fb_key] = "down"
                st.rerun()

    if history:
        st.button(
            "Clear chat",
            key="sc_clear_healthcare",
            on_click=lambda: st.session_state.update({hk: []}),
        )


# ── Smart Data Chat tab (compliance_officer / admin) ─────────────────────────

def render_smart_tab():
    """
    Power chat tab for compliance_officer and admin roles (Healthcare only).
    Uses all healthcare tools and shows raw data cards where relevant.
    """
    st.markdown(
        "<div style='background:#fafafa;border:1px solid #e5e7eb;border-radius:8px;"
        "padding:12px 16px;margin-bottom:12px;font-size:.85rem;color:#374151'>"
        "<b>Smart Data Chat</b> has live access to all healthcare databases: "
        "9,249 package rates across AB-ArK, Sanjeevini and PM-JAY, "
        "plus 497+ empanelled hospitals across Karnataka (Bangalore Urban included). "
        "Ask questions that require looking up real numbers."
        "</div>",
        unsafe_allow_html=True,
    )

    st.markdown("**Quick queries:**")
    examples = [
        "List all AB-ArK packages for cardiac procedures",
        "Compare dialysis rates across AB-ArK and Sanjeevini",
        "Show PM-JAY hospitals in Belagavi district",
        "What is the AB-ArK rate for hip replacement?",
    ]

    hk = "sc_power_healthcare_history"
    if hk not in st.session_state:
        st.session_state[hk] = []

    history: list[dict] = st.session_state[hk]

    prefill = _example_chips(examples, "pw_healthcare") if not history else None

    for msg in history:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    prompt = prefill or st.chat_input(
        "Query the healthcare databases...",
        key="sc_power_input_healthcare",
    )

    if not prompt:
        return

    history.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        try:
            from openai import OpenAI
            client = OpenAI(api_key=_secret("OPENAI_API_KEY"))
            answer = _agentic_generate(
                client,
                history[:-1] + [{"role": "user", "content": prompt}],
                TOOLS_HEALTHCARE,
                _SYS_POWER_HC,
            )
        except Exception as e:
            answer = f"Something went wrong. ({e})"
            st.markdown(answer)

    history.append({"role": "assistant", "content": answer})

    if history:
        st.button(
            "Clear chat",
            key="sc_power_clear_healthcare",
            on_click=lambda: st.session_state.update({hk: []}),
        )
