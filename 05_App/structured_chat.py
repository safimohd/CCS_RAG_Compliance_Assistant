"""
structured_chat.py  --  Data-augmented chat for the compliance chatbot

Provides two entry points:
  render_user_chat(domain)   -- clean, guided view for the 'user' role
  render_smart_tab(domain)   -- power tab for compliance_officer / admin roles

The LLM uses OpenAI tool-calling to pull real data from Supabase:
  Healthcare domain  ->  search_healthcare_packages, find_empanelled_hospitals
  RERA domain        ->  check_rera_project, get_property_estimate, check_rera_project
  Both               ->  lookup_market_rates (gold / benchmark assets)
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


# ── Supabase query functions ──────────────────────────────────────────────────

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
    # Normalise Bangalore variants to what the DB uses
    _BANGALORE_ALIASES = {
        "bengaluru", "bengaluru urban", "bangalore", "bangalore urban",
        "bengaluru rural", "bangalore rural", "blr", "560076", "560001",
        "iim bangalore", "iimb", "bannerghatta", "koramangala",
        "jayanagar", "indiranagar", "whitefield", "electronic city",
    }
    district_lc = district.lower().strip()
    if any(alias in district_lc for alias in _BANGALORE_ALIASES):
        search_term = "Bangalore"
    else:
        search_term = district

    table  = "pmjay_hospitals_karnataka" if "IRDAI" not in scheme else "irdai_hospitals_karnataka"
    select = ("hospital_name,address,district,scheme,empanelment_status,"
              "contact_number,specialities")

    # Primary search: by normalised district
    rows = _supa(table, {"select": select, "district": f"ilike.*{search_term}*"}, limit=10)

    # Fallback 1: try "Urban" suffix
    if not rows and search_term == "Bangalore":
        rows = _supa(table, {"select": select, "district": f"ilike.*Bengaluru*"}, limit=10)

    # Fallback 2: return any hospitals (no district filter) so user gets something
    if not rows:
        rows = _supa(table, {"select": select}, limit=10)

    return rows


def check_rera_project(query: str) -> list[dict]:
    """
    Look up a Karnataka RERA project by ACK number, project name, or promoter name.
    """
    select = ("project_name,promoter_name,status,district,"
              "ack_number,prm_number,completion_date,lapse_date,"
              "total_units,sold_units,approving_authority,pin_code")

    # Try ACK / RERA number pattern first
    if any(x in query.upper() for x in ["PRM/", "PR/KA", "PRM/KA", "/RERA/"]):
        rows = _supa(
            "rera_projects",
            {"select": select, "ack_number": f"ilike.*{query}*"},
            limit=5
        )
        if rows:
            return rows

    # Project name
    rows = _supa(
        "rera_projects",
        {"select": select, "project_name": f"ilike.*{query}*"},
        limit=5
    )
    if rows:
        return rows

    # Promoter name fallback
    return _supa(
        "rera_projects",
        {"select": select, "promoter_name": f"ilike.*{query}*"},
        limit=5
    )


def get_property_estimate(locality: str) -> list[dict]:
    """
    Return 2026 price-per-sqft estimate for a Bangalore locality.
    """
    return _supa(
        "property_prices_estimated",
        {
            "select": "locality,city,zone,estimated_ppsf_2026,"
                      "ppsf_min_2026,ppsf_max_2026",
            "locality": f"ilike.*{locality}*",
        },
        limit=5
    )


def lookup_market_rates(asset_type: str = "gold_24k", period: str = "current") -> list[dict]:
    """Return benchmark market rate for an asset (gold, etc.)."""
    params: dict = {
        "select": "asset_type,value_type,period,value,notes,fetched_at",
        "asset_type": f"eq.{asset_type}",
    }
    if period:
        params["period"] = f"eq.{period}"
    return _supa("market_rates", params, limit=5)


# ── OpenAI tool schemas ───────────────────────────────────────────────────────

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
                        "description": "Karnataka district name",
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

TOOLS_RERA = [
    {
        "type": "function",
        "function": {
            "name": "check_rera_project",
            "description": (
                "Look up a Karnataka RERA registered project by its project name, "
                "promoter name, or RERA registration number. Use this whenever the "
                "user asks whether a project is registered, what the registration "
                "status is, the completion date, or anything about a specific builder "
                "or project."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Project name, promoter/builder name, or RERA registration number",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_property_estimate",
            "description": (
                "Get 2026 property price estimates (per square foot) for a "
                "Bangalore locality. Use when the user asks about property "
                "prices, fair value, price bands, or whether a quoted price "
                "is reasonable for a specific area."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "locality": {
                        "type": "string",
                        "description": "Bangalore locality name, e.g. Whitefield, Sarjapur Road, HSR Layout",
                    }
                },
                "required": ["locality"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lookup_market_rates",
            "description": (
                "Get current or historical market rates for benchmark assets like gold. "
                "Use when the user asks about gold prices or investment benchmarks."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "asset_type": {
                        "type": "string",
                        "enum": ["gold_24k", "gold_22k"],
                        "description": "Asset type",
                    },
                    "period": {
                        "type": "string",
                        "description": "Period: 'current' for today, or 'YYYY-MM' for a past month",
                    },
                },
                "required": [],
            },
        },
    },
]

# ── System prompts ────────────────────────────────────────────────────────────

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
  For Bangalore / IIM Bangalore / pincode 560076, pass district="Bangalore".
- When you have tool results, present the most relevant 3-5 results clearly. \
  Format rates as Indian Rupees with commas (e.g. Rs. 12,000).
- If rates differ by ward type, explain the difference in simple terms.
- Never recommend a specific doctor or give medical diagnosis.
- Keep answers short, warm, and jargon-free. The user is a patient or family member.
- If the tool returns hospitals, list their name, address, and contact number. \
  Always mention that these hospitals offer cashless treatment under PM-JAY / AB-ArK.
- Do not use em dashes anywhere in your response.\
"""

_SYS_RERA = """\
You are a friendly, plain-language property and RERA assistant for homebuyers \
in Karnataka, India. You help people:
- Check if a Karnataka builder/project is RERA registered and what the status is
- Understand fair property prices in Bangalore localities
- Know their rights under the RERA Act 2016

Key RERA rules every buyer must know:
- Section 13: Builder cannot collect more than 10% before a registered Agreement for Sale.
- RERA number must appear on every advertisement and booking document.
- Builder must keep 70% of collected funds in a separate escrow account.
- A project whose RERA expiry is before the promised possession date is a red flag.

IMPORTANT guidelines:
- Always use check_rera_project when a user mentions a project name, builder, or RERA number.
- Always use get_property_estimate when asked about price, value, or affordability in Bangalore.
- When you have tool results, present them clearly. For price estimates, state the \
  min-max band and what a "fair" quoted price looks like (within plus/minus 15%).
- Keep answers short and clear. The user is a regular homebuyer.
- Do not use em dashes anywhere in your response.\
"""

_SYS_POWER = """\
You are a data-augmented compliance assistant with live access to Karnataka's \
regulatory databases. You can query:
- 9,249 healthcare package rates across AB-ArK, Sanjeevini, and PM-JAY schemes
- 536 PM-JAY empanelled hospitals across Karnataka
- 10,096 K-RERA registered projects (live data)
- 1,292 Bangalore locality property price estimates

Always use the available tools to fetch real data before answering factual questions \
about rates, registrations, or prices. Synthesise the tool output into a clear, \
actionable response. Present data in tables where it helps readability.
Do not use em dashes anywhere in your response.\
"""

# ── Tool dispatcher ───────────────────────────────────────────────────────────

_TOOL_FNS = {
    "search_healthcare_packages": search_healthcare_packages,
    "find_empanelled_hospitals":  find_empanelled_hospitals,
    "check_rera_project":         check_rera_project,
    "get_property_estimate":      get_property_estimate,
    "lookup_market_rates":        lookup_market_rates,
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
    from openai import OpenAI  # noqa: F401 (already imported via client)

    model   = os.environ.get("LLM_MODEL", "gpt-5-nano")
    history = [{"role": "system", "content": system_prompt}] + messages

    for _ in range(5):   # max 5 tool-call rounds
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
            # Final text answer: stream it
            answer_text = msg.content or ""
            if answer_text:
                st.markdown(answer_text)
            return answer_text

        # Execute all tool calls in this round
        history.append(msg)
        for tc in msg.tool_calls:
            result_str = _run_tool_call(tc)
            history.append({
                "role":         "tool",
                "tool_call_id": tc.id,
                "content":      result_str,
            })

    # Fallback if loop exhausted
    answer = "I looked up the data but could not form a complete answer. Please try rephrasing."
    st.markdown(answer)
    return answer


# ── Example questions per domain ─────────────────────────────────────────────

_EXAMPLES_HEALTHCARE = [
    "How much does a knee replacement cost under AB-ArK?",
    "Which hospitals in Mysore are covered under PM-JAY?",
    "Does Arogya Sanjeevini cover dialysis in Mangalore?",
    "What is the rate for a C-section under government schemes?",
    "Compare cataract surgery rates across all three schemes",
]

_EXAMPLES_RERA = [
    "Is Prestige Lakeside Habitat RERA registered?",
    "What is a fair price per sqft in Whitefield right now?",
    "Check RERA status of Puravankara Provident Housing",
    "What price should I expect in HSR Layout?",
    "Can a builder ask for more than 10% before signing?",
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


# ── CSS for the user chat view ────────────────────────────────────────────────

_USER_CSS = """
<style>
.sc-hero {
    background: linear-gradient(135deg, #1a4fa0 0%, #2563eb 100%);
    border-radius: 12px;
    padding: 20px 24px 16px;
    color: #fff;
    margin-bottom: 16px;
}
.sc-hero h2 {
    margin: 0 0 4px;
    font-size: 1.3rem;
    font-weight: 700;
}
.sc-hero p {
    margin: 0;
    font-size: .88rem;
    color: #bdd4f9;
    line-height: 1.5;
}
.sc-hint {
    background: #f0f9ff;
    border-left: 4px solid #3b82f6;
    border-radius: 6px;
    padding: 8px 14px;
    font-size: .82rem;
    color: #1e40af;
    margin-bottom: 10px;
}
</style>
"""

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

def render_user_chat(domain: str = "Healthcare"):
    """
    Clean, single-purpose chat for the 'user' role.
    No tabs, no jargon. Just a question box with guided examples.
    """
    is_hc = (domain == "Healthcare")
    css   = _HC_CSS if is_hc else _USER_CSS

    st.markdown(css, unsafe_allow_html=True)

    # Hero banner
    if is_hc:
        st.markdown(
            "<div class='sc-hero'>"
            "<h2>Your Healthcare Guide</h2>"
            "<p>Ask about treatment costs, which government schemes cover a procedure, "
            "or find cashless hospitals near you in Karnataka.</p>"
            "</div>",
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            "<div class='sc-hero'>"
            "<h2>Your Property Assistant</h2>"
            "<p>Ask about RERA registration, fair property prices in Bangalore, "
            "or your rights as a homebuyer under Karnataka RERA.</p>"
            "</div>",
            unsafe_allow_html=True,
        )

    # Hint strip
    if is_hc:
        st.markdown(
            "<div class='sc-hint'>"
            "Rates shown are from official Karnataka government scheme documents. "
            "Actual hospital billing may vary. Always confirm with the hospital."
            "</div>",
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            "<div class='sc-hint'>"
            "RERA data is from rera.karnataka.gov.in (10,096 projects). "
            "Price estimates are 2026 locality benchmarks."
            "</div>",
            unsafe_allow_html=True,
        )

    # State key for this chat
    hk = "sc_hc_history" if is_hc else "sc_rera_history"
    if hk not in st.session_state:
        st.session_state[hk] = []

    history: list[dict] = st.session_state[hk]

    # Example chips (only shown when chat is empty)
    prefill = None
    if not history:
        st.markdown("**Try one of these:**")
        examples = _EXAMPLES_HEALTHCARE if is_hc else _EXAMPLES_RERA
        prefill  = _example_chips(examples, "usr_hc" if is_hc else "usr_rera")

    # Render conversation history
    for msg in history:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # Input box (or prefilled chip click)
    prompt = prefill or st.chat_input(
        "Ask anything..." if not history else "Ask a follow-up...",
        key=f"sc_input_{domain.lower()}",
    )

    if not prompt:
        return

    # Append user message
    history.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # Generate answer with tool calls
    with st.chat_message("assistant"):
        try:
            from openai import OpenAI
            client = OpenAI(api_key=_secret("OPENAI_API_KEY"))
            tools  = TOOLS_HEALTHCARE if is_hc else TOOLS_RERA
            sysp   = _SYS_HEALTHCARE  if is_hc else _SYS_RERA
            answer = _agentic_generate(client, history[:-1] + [{"role": "user", "content": prompt}], tools, sysp)
        except Exception as e:
            answer = f"Something went wrong. Please try again. ({e})"
            st.markdown(answer)

    history.append({"role": "assistant", "content": answer})

    # Clear button
    if history:
        st.button(
            "Clear chat",
            key=f"sc_clear_{domain.lower()}",
            on_click=lambda: st.session_state.update({hk: []}),
        )


# ── Smart Data Chat tab (compliance_officer / admin) ─────────────────────────

def render_smart_tab(domain: str = "Healthcare"):
    """
    Power chat tab for compliance_officer and admin roles.
    Uses all available tools and shows raw data cards where relevant.
    """
    is_hc = (domain == "Healthcare")
    tools = (TOOLS_HEALTHCARE + TOOLS_RERA) if not is_hc else TOOLS_HEALTHCARE

    st.markdown(
        "<div style='background:#fafafa;border:1px solid #e5e7eb;border-radius:8px;"
        "padding:12px 16px;margin-bottom:12px;font-size:.85rem;color:#374151'>"
        "<b>Smart Data Chat</b> has live access to all structured databases: "
        "9,249 healthcare package rates, 536 empanelled hospitals, "
        "10,096 K-RERA projects, and 1,292 Bangalore locality price estimates. "
        "Ask questions that require looking up real numbers."
        "</div>",
        unsafe_allow_html=True,
    )

    # Examples for compliance/admin
    st.markdown("**Quick queries:**")
    if is_hc:
        examples = [
            "List all AB-ArK packages for cardiac procedures",
            "Compare dialysis rates across AB-ArK and Sanjeevini",
            "Show PM-JAY hospitals in Belagavi district",
            "What is the AB-ArK rate for hip replacement?",
        ]
    else:
        all_examples = [
            "Compare property prices in Whitefield vs HSR Layout",
            "Check RERA registration for Sobha City Mysore",
            "List all expired RERA projects in Bangalore",
            "What is the AB-ArK rate for angioplasty?",
        ]
        examples = all_examples

    hk = f"sc_power_{domain.lower()}_history"
    if hk not in st.session_state:
        st.session_state[hk] = []

    history: list[dict] = st.session_state[hk]

    if not history:
        prefill = _example_chips(examples, f"pw_{domain.lower()}")
    else:
        prefill = None

    for msg in history:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    prompt = prefill or st.chat_input(
        "Query the databases...",
        key=f"sc_power_input_{domain.lower()}",
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
            # Power mode: give all tools regardless of domain
            all_tools = TOOLS_HEALTHCARE + TOOLS_RERA
            answer = _agentic_generate(client, history[:-1] + [{"role": "user", "content": prompt}], all_tools, _SYS_POWER)
        except Exception as e:
            answer = f"Something went wrong. ({e})"
            st.markdown(answer)

    history.append({"role": "assistant", "content": answer})

    if history:
        st.button(
            "Clear chat",
            key=f"sc_power_clear_{domain.lower()}",
            on_click=lambda: st.session_state.update({hk: []}),
        )
