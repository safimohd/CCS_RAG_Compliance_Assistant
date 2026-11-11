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

# ── helpers ───────────────────────────────────────────────────────────────────

def _secret(key: str) -> str:
    return os.environ.get(key) or st.secrets.get(key, "")


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
in Karnataka, India. You help people understand:
- Which government health schemes cover a treatment (AB-ArK, Arogya Sanjeevini, PM-JAY)
- How much a procedure costs under each scheme
- Which hospitals near them are empanelled (covered) for cashless treatment

Schemes overview:
- AB-ArK (Arogya Karnataka): Karnataka state scheme, covers all 31 districts. \
  Three ward types: General, Semi-Private, Private. Rates set by the state government.
- Arogya Sanjeevini (Tier 2 NABH): Karnataka scheme for 5 cities: \
  Belagavi, Dharwad, Mangalore, Mysore, Kalaburgi.
- PM-JAY / HBP (Ayushman Bharat): National scheme, one flat package rate.

IMPORTANT guidelines:
- Always use the search_healthcare_packages tool when a user asks about costs, \
  rates, or coverage. Do not guess rates from memory.
- Always use find_empanelled_hospitals when asked about where to get treated. \
  For Bangalore / IIM Bangalore / pincode 560076, pass district="Bangalore". \
  Our dataset now includes hospitals in Bangalore Urban district. \
  List them with name, address, phone (if available), and specialities. \
  Always mention that for the most up-to-date full list, they can also check \
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

def render_user_chat():
    """
    Clean, single-purpose chat for the 'user' role (Healthcare domain only).
    No tabs, no jargon. Just a question box with guided examples.
    """
    st.markdown(_HC_CSS, unsafe_allow_html=True)

    st.markdown(
        "<div class='sc-hero'>"
        "<h2>Your Healthcare Guide</h2>"
        "<p>Ask about treatment costs, which government schemes cover a procedure, "
        "or find cashless hospitals near you in Karnataka.</p>"
        "</div>",
        unsafe_allow_html=True,
    )

    st.markdown(
        "<div class='sc-hint'>"
        "Rates shown are from official Karnataka government scheme documents. "
        "Actual hospital billing may vary. Always confirm with the hospital."
        "</div>",
        unsafe_allow_html=True,
    )

    hk = "sc_hc_history"
    if hk not in st.session_state:
        st.session_state[hk] = []

    history: list[dict] = st.session_state[hk]

    prefill = None
    if not history:
        st.markdown("**Try one of these:**")
        prefill = _example_chips(_EXAMPLES_HEALTHCARE, "usr_hc")

    for msg in history:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    prompt = prefill or st.chat_input(
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
            client = OpenAI(api_key=_secret("OPENAI_API_KEY"))
            answer = _agentic_generate(
                client,
                history[:-1] + [{"role": "user", "content": prompt}],
                TOOLS_HEALTHCARE,
                _SYS_HEALTHCARE,
            )
        except Exception as e:
            answer = f"Something went wrong. Please try again. ({e})"
            st.markdown(answer)

    history.append({"role": "assistant", "content": answer})

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
