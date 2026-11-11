"""
user_flow_rera.py  --  Standalone Karnataka RERA Property Due Diligence App

Deployment entry: app_rera.py execs this file directly.
Provides a chat interface with multi-PDF upload and URL context,
plus live K-RERA database lookup, price check, and buy-vs-rent analysis.

Healthcare app: app_healthcare.py + structured_chat.py (separate codebase, nothing shared)
"""

import streamlit as st
import streamlit.components.v1 as components
import os
import json
import io
import re

# ── page config (must be first Streamlit call) ─────────────────────────────────

st.set_page_config(
    page_title="Karnataka RERA Property Advisor",
    page_icon="🏠",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── lazy imports ──────────────────────────────────────────────────────────────

def _ensure(pkg, import_as=None):
    import importlib
    name = import_as or pkg
    try:
        return importlib.import_module(name)
    except ImportError:
        import subprocess, sys
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", pkg,
             "--break-system-packages", "-q"]
        )
        return importlib.import_module(name)


def _secret(key: str) -> str:
    try:
        return os.environ.get(key) or st.secrets.get(key, "")
    except Exception:
        return os.environ.get(key, "")


# ── locality price bands (Bangalore, Q2-Q3 2026) ─────────────────────────────

LOCALITIES = [
    {"locality": "Whitefield",              "pin": "560066", "avg_psf": 14000, "trend": "+12% YoY", "risk": "Low"},
    {"locality": "Sarjapur Road",           "pin": "560035", "avg_psf": 12000, "trend": "+5% YoY",  "risk": "Medium"},
    {"locality": "Electronic City",         "pin": "560100", "avg_psf": 8100,  "trend": "+7% YoY",  "risk": "Medium"},
    {"locality": "Hebbal",                  "pin": "560024", "avg_psf": 15000, "trend": "+18% YoY", "risk": "Low"},
    {"locality": "Marathahalli",            "pin": "560037", "avg_psf": 14500, "trend": "+15% YoY", "risk": "Medium"},
    {"locality": "Bannerghatta Road",       "pin": "560076", "avg_psf": 11000, "trend": "+7% YoY",  "risk": "Medium"},
    {"locality": "Hennur",                  "pin": "560043", "avg_psf": 10800, "trend": "+11% YoY", "risk": "Medium"},
    {"locality": "Devanahalli",             "pin": "562110", "avg_psf": 10500, "trend": "+8% YoY",  "risk": "High"},
    {"locality": "Kanakapura Road",         "pin": "560062", "avg_psf": 11400, "trend": "+8% YoY",  "risk": "Medium"},
    {"locality": "JP Nagar",                "pin": "560078", "avg_psf": 12200, "trend": "+14% YoY", "risk": "Low"},
    {"locality": "Koramangala",             "pin": "560034", "avg_psf": 23000, "trend": "+12% YoY", "risk": "Low"},
    {"locality": "Indiranagar",             "pin": "560038", "avg_psf": 18500, "trend": "+12% YoY", "risk": "Low"},
    {"locality": "HSR Layout",              "pin": "560102", "avg_psf": 17500, "trend": "+25% YoY", "risk": "Low"},
    {"locality": "Yelahanka",               "pin": "560064", "avg_psf": 10200, "trend": "+18% YoY", "risk": "Medium"},
    {"locality": "Tumkur Road",             "pin": "560073", "avg_psf": 8250,  "trend": "+10% YoY", "risk": "High"},
    {"locality": "Mysore Road",             "pin": "560026", "avg_psf": 8300,  "trend": "+8% YoY",  "risk": "Medium"},
    {"locality": "Hoskote",                 "pin": "562114", "avg_psf": 7000,  "trend": "+9% YoY",  "risk": "High"},
    {"locality": "Bagalur",                 "pin": "562149", "avg_psf": 9800,  "trend": "+8% YoY",  "risk": "High"},
    {"locality": "KR Puram",                "pin": "560036", "avg_psf": 11200, "trend": "+20% YoY", "risk": "Medium"},
    {"locality": "Rajarajeshwari Nagar",    "pin": "560098", "avg_psf": 11200, "trend": "+14% YoY", "risk": "Medium"},
]

# ── LLM field extraction ──────────────────────────────────────────────────────

EXTRACT_PROMPT = """Extract property details from the text below.
Return ONLY a JSON object with these keys (use null for missing values):
{
  "project_name": string,
  "promoter_name": string,
  "location": string,
  "district": string,
  "price_per_sqft": number,
  "total_price_lakh": number,
  "rera_number": string,
  "completion_date": string,
  "unit_type": string,
  "carpet_area_sqft": number
}
Notes:
- price_per_sqft: the per sq ft rate as a number
- total_price_lakh: total flat price in lakhs (if crores, multiply by 100)
- rera_number: Karnataka RERA patterns like PRM/KA/RERA/... or PR/KA/RERA/... etc.
Return only the JSON object, nothing else."""


def _extract_fields(text: str) -> dict:
    OpenAI = _ensure("openai", "openai").OpenAI
    client = OpenAI(api_key=_secret("OPENAI_API_KEY"))
    try:
        resp = client.chat.completions.create(
            model=os.environ.get("LLM_MODEL", "gpt-5-nano"),
            messages=[
                {"role": "system", "content": EXTRACT_PROMPT},
                {"role": "user", "content": text[:6000]},
            ],
            response_format={"type": "json_object"},
        )
        return json.loads(resp.choices[0].message.content)
    except Exception:
        try:
            raw = resp.choices[0].message.content
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            return json.loads(match.group()) if match else {}
        except Exception:
            return {}


def _pdf_to_text(pdf_bytes: bytes) -> str:
    pdfplumber = _ensure("pdfplumber")
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        return "\n".join(p.extract_text() or "" for p in pdf.pages[:12])


def _url_to_text(url: str) -> str:
    requests = _ensure("requests")
    BeautifulSoup = _ensure("beautifulsoup4", "bs4").BeautifulSoup
    try:
        r = requests.get(url, timeout=12, headers={"User-Agent": "Mozilla/5.0"})
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        return soup.get_text(separator=" ", strip=True)
    except Exception as e:
        return f"Error fetching URL: {e}"


# ── RERA live database lookup ─────────────────────────────────────────────────

def _rera_lookup(extracted: dict) -> list:
    requests = _ensure("requests")
    url_base = _secret("SUPABASE_URL")
    key = _secret("SUPABASE_KEY")
    if not url_base or not key:
        return []

    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    select = ("project_name,promoter_name,status,district,ack_number,prm_number,"
              "completion_date,lapse_date,total_units,sold_units,"
              "no_of_towers,approving_authority,pin_code,total_carpet_area_sqmt")

    def _get(params: str) -> list:
        try:
            r = requests.get(
                f"{url_base}/rest/v1/rera_projects?{params}&select={select}&limit=5",
                headers=headers, timeout=10
            )
            return r.json() if r.status_code == 200 and isinstance(r.json(), list) else []
        except Exception:
            return []

    rera_no = (extracted.get("rera_number") or "").strip()
    if rera_no:
        rows = _get(f"ack_number=eq.{requests.utils.quote(rera_no)}")
        if rows:
            return rows

    name = (extracted.get("project_name") or "").strip()
    if name:
        rows = _get(f"project_name=ilike.*{requests.utils.quote(name)}*")
        if rows:
            return rows

    promoter = (extracted.get("promoter_name") or "").strip()
    if promoter:
        return _get(f"promoter_name=ilike.*{requests.utils.quote(promoter)}*")

    return []


def _search_rera_db(query: str, search_by: str = "auto", limit: int = 5) -> list:
    """Search rera_projects by project name, promoter, pin code, or RERA number."""
    requests = _ensure("requests")
    url_base = _secret("SUPABASE_URL")
    key = _secret("SUPABASE_KEY")
    if not url_base or not key or not query.strip():
        return []

    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    select = ("project_name,promoter_name,status,district,ack_number,prm_number,"
              "completion_date,lapse_date,total_units,sold_units,"
              "no_of_towers,approving_authority,pin_code,total_carpet_area_sqmt")
    q = query.strip()

    def _get(params: str) -> list:
        try:
            r = requests.get(
                f"{url_base}/rest/v1/rera_projects?{params}&select={select}&limit={limit}",
                headers=headers, timeout=10
            )
            return r.json() if r.status_code == 200 and isinstance(r.json(), list) else []
        except Exception:
            return []

    if search_by == "rera" or (search_by == "auto" and re.search(r"(PRM|PR)/KA/RERA", q, re.I)):
        rows = _get(f"ack_number=ilike.*{requests.utils.quote(q)}*")
        if rows:
            return rows

    if search_by == "pin" or (search_by == "auto" and re.match(r"^\d{6}$", q)):
        return _get(f"pin_code=eq.{requests.utils.quote(q)}")

    if search_by == "promoter":
        return _get(f"promoter_name=ilike.*{requests.utils.quote(q)}*")

    if search_by == "name":
        return _get(f"project_name=ilike.*{requests.utils.quote(q)}*")

    # auto: try project name first, then promoter
    rows = _get(f"project_name=ilike.*{requests.utils.quote(q)}*")
    if rows:
        return rows
    return _get(f"promoter_name=ilike.*{requests.utils.quote(q)}*")


def _format_db_rows_for_llm(rows: list) -> str:
    if not rows:
        return ""
    lines = ["[Live K-RERA database results]"]
    for r in rows[:5]:
        sold = r.get("sold_units") or ""
        total = r.get("total_units") or ""
        units_str = f"{sold}/{total} units sold" if sold and total else ""
        parts = [
            r.get("project_name", "Unknown project"),
            f"Status: {r.get('status', 'unknown')}",
            f"Promoter: {r.get('promoter_name', '')}",
            f"RERA#: {r.get('ack_number', '')}",
            f"District: {r.get('district', '')}",
            f"PIN: {r.get('pin_code', '')}",
            f"Completion: {r.get('completion_date', '')}",
            f"Lapse date: {r.get('lapse_date', '')}",
        ]
        if units_str:
            parts.append(units_str)
        lines.append("  " + " | ".join(p for p in parts if p.split(": ", 1)[-1]))
    return "\n".join(lines)


def _detect_entity_in_message(msg: str) -> dict:
    if re.search(r"(PRM|PR)/KA/RERA", msg, re.I):
        m = re.search(r"((?:PRM|PR)/KA/RERA[^\s,]+)", msg, re.I)
        return {"query": m.group(1) if m else "", "type": "rera"}

    m = re.search(r"\b(5[0-9]{5})\b", msg)
    if m:
        return {"query": m.group(1), "type": "pin"}

    project_signals = [
        "project", "apartment", "flat", "society", "residency", "residences",
        "habitat", "gardens", "towers", "heights", "enclave", "villa", "layout",
        "is registered", "rera registered", "track record", "how many projects",
        "trustworthy", "reputed", "reliable", "developer", "builder", "promoter"
    ]
    msg_lower = msg.lower()
    if not any(sig in msg_lower for sig in project_signals):
        return {"query": "", "type": "none"}

    m = re.search(r'["\u2018\u2019\u201c\u201d]([^"\u201c\u201d]+)["\u2018\u2019\u201c\u201d]', msg)
    if m:
        return {"query": m.group(1).strip(), "type": "name"}

    builder_patterns = [
        r'\b(Prestige\s+\w+(?:\s+\w+)?)',
        r'\b(Sobha\s+\w+(?:\s+\w+)?)',
        r'\b(Brigade\s+\w+(?:\s+\w+)?)',
        r'\b(Godrej\s+\w+(?:\s+\w+)?)',
        r'\b(Puravankara\s+\w+(?:\s+\w+)?)',
        r'\b(Provident\s+\w+(?:\s+\w+)?)',
        r'\b(Mantri\s+\w+(?:\s+\w+)?)',
        r'\b(Embassy\s+\w+(?:\s+\w+)?)',
        r'\b(Salarpuria\s+\w+(?:\s+\w+)?)',
        r'\b(Shriram\s+\w+(?:\s+\w+)?)',
        r'\b(TVS\s+\w+(?:\s+\w+)?)',
    ]
    for pat in builder_patterns:
        m = re.search(pat, msg, re.I)
        if m:
            matched = m.group(1).strip()
            if any(w in msg_lower for w in ["how many", "track record", "trustworthy", "reliable", "builder", "developer", "promoter"]):
                builder_name = matched.split()[0]
                return {"query": builder_name, "type": "promoter"}
            return {"query": matched, "type": "name"}

    return {"query": "", "type": "none"}


# ── price check and buy-vs-rent ───────────────────────────────────────────────

def _price_verdict(extracted: dict) -> dict:
    ppsf = extracted.get("price_per_sqft")
    location = (extracted.get("location") or extracted.get("district") or "").strip().lower()

    if not ppsf:
        return {"verdict": "unavailable", "reason": "Price per sqft not found in document"}
    if not location:
        return {"verdict": "unavailable", "reason": "Location not found in document"}

    band = None
    for row in LOCALITIES:
        loc_name = (row.get("locality") or "").lower()
        if location in loc_name or loc_name in location:
            band = row
            break

    if band is None:
        return {"verdict": "unavailable", "reason": f"No price band data for '{location}'. Try uploading a Bengaluru property."}

    avg = band["avg_psf"]
    low, high = avg * 0.85, avg * 1.15
    if ppsf < low:
        verdict = "below_band"
        label = f"Below market band: quoted {ppsf:,.0f}/sqft vs {low:,.0f}-{high:,.0f} range"
    elif ppsf > high:
        verdict = "above_band"
        label = f"Above market band: quoted {ppsf:,.0f}/sqft vs {low:,.0f}-{high:,.0f} range"
    else:
        verdict = "in_range"
        label = f"Fair price: {ppsf:,.0f}/sqft is within the {low:,.0f}-{high:,.0f} range"

    return {
        "verdict": verdict,
        "label": label,
        "quoted": ppsf,
        "low": low,
        "high": high,
        "trend": band.get("trend"),
        "risk": band.get("risk"),
        "locality": band.get("locality"),
    }


def _buy_vs_rent(extracted: dict) -> dict:
    price_lakh = extracted.get("total_price_lakh")
    if not price_lakh:
        return {"verdict": "unavailable", "reason": "Total price not found in document"}

    price = price_lakh * 100_000
    loan = price * 0.80
    r = 0.085 / 12
    n = 240
    emi = loan * r * (1 + r) ** n / ((1 + r) ** n - 1)
    est_rent = price * 0.0025
    interest_total = emi * n - loan
    break_even = round(interest_total / (emi - est_rent) / 12, 1) if emi > est_rent else None

    return {
        "verdict": "buy" if break_even and break_even < 12 else "consider_renting",
        "emi": round(emi),
        "est_rent": round(est_rent),
        "break_even_years": break_even,
        "price_lakh": price_lakh,
    }


def _status_color(status: str):
    s = (status or "").lower()
    if any(x in s for x in ["active", "registered", "approved"]):
        return "#166534", "#f0fdf4"
    if any(x in s for x in ["lapsed", "expired", "revoked"]):
        return "#9f1239", "#fff1f2"
    return "#92400e", "#fffbeb"


# ── pipeline animation strip ──────────────────────────────────────────────────

DEMO_HTML = """
<style>
@keyframes fadein{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:translateY(0)}}
.uf-strip{display:flex;gap:6px;align-items:center;flex-wrap:wrap;padding:4px 0}
.uf-card{background:#eef2ff;border:1px solid #c7d2fe;border-radius:8px;
  padding:7px 12px;font-size:11px;color:#3730a3;text-align:center;min-width:92px;
  animation:fadein .4s ease both}
.uf-card b{display:block;font-size:12px;margin-bottom:2px;color:#312e81}
.uf-arr{color:#94a3b8;font-size:14px;flex-shrink:0}
.uf-card:nth-child(1){animation-delay:.0s}
.uf-card:nth-child(3){animation-delay:.25s}
.uf-card:nth-child(5){animation-delay:.5s}
.uf-card:nth-child(7){animation-delay:.75s}
.uf-card:nth-child(9){animation-delay:1.0s}
</style>
<div class="uf-strip">
  <div class="uf-card"><b>PDF / URL / Chat</b>your choice</div>
  <div class="uf-arr">&#8594;</div>
  <div class="uf-card"><b>Extract</b>project details</div>
  <div class="uf-arr">&#8594;</div>
  <div class="uf-card"><b>RERA check</b>live K-RERA data</div>
  <div class="uf-arr">&#8594;</div>
  <div class="uf-card"><b>Price check</b>vs locality band</div>
  <div class="uf-arr">&#8594;</div>
  <div class="uf-card"><b>Report</b>one clear verdict</div>
</div>
"""


# ── LLM system prompt ─────────────────────────────────────────────────────────

def _build_chat_system() -> str:
    loc_lines = "\n".join(
        f"  - {r['locality']}: avg {r['avg_psf']:,} psf, trend {r['trend']}, risk {r['risk']}"
        for r in LOCALITIES
    )
    return f"""You are a friendly, plain-language property advisor for homebuyers in Karnataka, India.
You help people understand Karnataka RERA, check whether a builder is registered, assess price fairness,
and decide whether to buy or rent in Bangalore.

Current Bangalore locality price benchmarks (average price per sq ft):
{loc_lines}

Key RERA rules every buyer must know:
- RERA Section 13: A builder CANNOT collect more than 10% of the flat cost before signing a registered Agreement for Sale. A booking receipt alone is NOT legally binding.
- Always insist on: (1) allotment letter, (2) registered Agreement for Sale with possession date, penalty clauses, and RERA number on the document, before paying anything beyond the initial token.
- RERA registration must be obtained BEFORE the builder advertises or takes bookings. "Approval in process" means the project is not legally cleared yet.
- A project whose RERA expiry date is before the promised possession date is a serious red flag.
- Under RERA, the builder must keep 70% of collected funds in a separate escrow account for construction only.

Guidelines:
- Keep answers short and clear. The user is a regular homebuyer, not a lawyer or finance professional.
- For price questions use the benchmark data above. A fair price is within plus or minus 15% of the average.
- When asked about a price without knowing sqft, reverse-calculate: state what sqft that price buys at the locality average, then give the verdict.
- For buy vs rent: if the estimated monthly EMI is less than 1.5x the expected market rent, buying usually makes sense over a 7-10 year horizon.
- If someone asks about a specific RERA number, guide them to visit rera.karnataka.gov.in or upload a brochure to auto-check.
- When someone asks about booking amounts or advance payments, always cite the 10% Section 13 rule AND the registered Agreement for Sale requirement in the same answer.
- Always remind users to verify RERA registration and legal documents independently before making any payment.
- Never use jargon without explaining it in plain words right after.
- Do not use em dashes anywhere in your response."""


# ── report card renderer ──────────────────────────────────────────────────────

def _render_report(extracted: dict, rera_rows: list, price: dict, bvr: dict):
    st.markdown("### Your property report")

    if rera_rows:
        row = rera_rows[0]
        status = row.get("status") or "Unknown"
        fg, bg = _status_color(status)
        sold = row.get("sold_units") or ""
        total = row.get("total_units") or ""
        units_str = f" | {sold}/{total} units sold" if sold and total else ""
        st.markdown(
            f"<div style='background:{bg};border-left:4px solid {fg};"
            f"border-radius:6px;padding:12px 16px;margin-bottom:10px'>"
            f"<div style='font-weight:700;font-size:.95rem;color:{fg}'>RERA Status: {status}</div>"
            f"<div style='font-size:.82rem;color:#374151;margin-top:4px'>"
            f"<b>{row.get('project_name','')}</b> | {row.get('promoter_name','')}</div>"
            f"<div style='font-size:.78rem;color:#6b7280;margin-top:3px'>"
            f"ACK: {row.get('ack_number','N/A')} | Completion: {row.get('completion_date','N/A')}{units_str}"
            f"</div></div>",
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            "<div style='background:#fef3c7;border-left:4px solid #d97706;"
            "border-radius:6px;padding:12px 16px;margin-bottom:10px'>"
            "<div style='font-weight:700;color:#92400e'>RERA: Not found in K-RERA database</div>"
            "<div style='font-size:.82rem;color:#78350f;margin-top:4px'>"
            "Could not match the project. Verify the RERA number directly at "
            "<a href='https://rera.karnataka.gov.in' target='_blank'>rera.karnataka.gov.in</a>."
            "</div></div>",
            unsafe_allow_html=True,
        )

    c1, c2 = st.columns(2)
    with c1:
        pv = price.get("verdict", "unavailable")
        if pv == "unavailable":
            color, icon = "#6b7280", "?"
            msg = price.get("reason", "Price data not available")
        elif pv == "in_range":
            color, icon = "#166534", "OK"
            msg = price.get("label", "")
        elif pv == "below_band":
            color, icon = "#1d4ed8", "LOW"
            msg = price.get("label", "")
        else:
            color, icon = "#9f1239", "HIGH"
            msg = price.get("label", "")

        st.markdown(
            f"<div style='border:1px solid #e5e7eb;border-radius:8px;padding:12px;height:100%'>"
            f"<div style='font-size:.7rem;color:#6b7280;text-transform:uppercase;letter-spacing:.05em'>Price Check</div>"
            f"<div style='font-size:1.4rem;font-weight:800;color:{color};margin:4px 0'>{icon}</div>"
            f"<div style='font-size:.8rem;color:#374151'>{msg}</div>"
            + (f"<div style='font-size:.75rem;color:#6b7280;margin-top:4px'>"
               f"Trend: {price.get('trend','')} | Area risk: {price.get('risk','')}</div>"
               if pv != "unavailable" else "")
            + "</div>",
            unsafe_allow_html=True,
        )

    with c2:
        bv = bvr.get("verdict", "unavailable")
        if bv == "unavailable":
            color, icon = "#6b7280", "?"
            msg = bvr.get("reason", "")
            detail = ""
        elif bv == "buy":
            color, icon = "#166534", "BUY"
            msg = f"EMI ({bvr['emi']:,}/mo) beats renting ({bvr['est_rent']:,}/mo)"
            detail = f"Break-even: {bvr.get('break_even_years','?')} yrs"
        else:
            color, icon = "#1d4ed8", "RENT"
            msg = f"EMI ({bvr.get('emi',0):,}/mo) is high for the area"
            detail = f"Break-even: {bvr.get('break_even_years','?')} yrs" if bvr.get("break_even_years") else ""

        st.markdown(
            f"<div style='border:1px solid #e5e7eb;border-radius:8px;padding:12px;height:100%'>"
            f"<div style='font-size:.7rem;color:#6b7280;text-transform:uppercase;letter-spacing:.05em'>Buy vs Rent</div>"
            f"<div style='font-size:1.4rem;font-weight:800;color:{color};margin:4px 0'>{icon}</div>"
            f"<div style='font-size:.8rem;color:#374151'>{msg}</div>"
            + (f"<div style='font-size:.75rem;color:#6b7280;margin-top:4px'>{detail}</div>" if detail else "")
            + "</div>",
            unsafe_allow_html=True,
        )

    with st.expander("Extracted property details", expanded=False):
        clean = {k: v for k, v in extracted.items() if v is not None}
        if clean:
            rows_md = "\n".join(
                f"| **{k.replace('_',' ').title()}** | {v} |"
                for k, v in clean.items()
            )
            st.markdown(f"| Field | Value |\n|---|---|\n{rows_md}")
        else:
            st.info("No structured fields could be extracted.")

    if len(rera_rows) > 1:
        with st.expander(f"Other RERA matches ({len(rera_rows) - 1} more)", expanded=False):
            for r in rera_rows[1:]:
                fg2, bg2 = _status_color(r.get("status", ""))
                st.markdown(
                    f"<div style='background:{bg2};border-radius:6px;padding:8px 12px;margin-bottom:6px'>"
                    f"<b style='color:{fg2}'>{r.get('project_name','')}</b>"
                    f" <span style='color:#6b7280;font-size:.8rem'>({r.get('status','')})</span><br>"
                    f"<span style='font-size:.78rem;color:#374151'>{r.get('promoter_name','')} | ACK: {r.get('ack_number','')}</span>"
                    f"</div>",
                    unsafe_allow_html=True,
                )


# ── sidebar: document upload ──────────────────────────────────────────────────

def _sidebar_document_context():
    """Sidebar: upload multiple PDFs or paste a URL as persistent chat context."""
    with st.sidebar:
        st.markdown("### Attach Documents")
        st.caption("Upload brochures or paste a URL. Chat will use them as context.")

        uploaded_files = st.file_uploader(
            "PDF brochures (multiple OK)",
            type=["pdf"],
            accept_multiple_files=True,
            key="sidebar_pdf_files",
        )
        url_input = st.text_input(
            "Or paste a property URL",
            placeholder="https://...",
            key="sidebar_url_input",
        )

        process_btn = st.button(
            "Load into chat", key="sidebar_process_btn", type="primary",
            use_container_width=True,
        )

        if process_btn:
            texts = []
            with st.spinner("Processing documents..."):
                if uploaded_files:
                    for f in uploaded_files:
                        try:
                            t = _pdf_to_text(f.read())
                            if t and t.strip():
                                texts.append(f"[From: {f.name}]\n{t.strip()}")
                        except Exception:
                            pass
                if url_input and url_input.startswith("http"):
                    try:
                        t = _url_to_text(url_input)
                        if t and t.strip():
                            texts.append(f"[From: {url_input}]\n{t.strip()}")
                    except Exception:
                        pass

            if texts:
                combined = "\n\n---\n\n".join(texts)
                st.session_state.uf_doc_context = combined

                try:
                    extracted = _extract_fields(texts[0])
                    rera_rows = _rera_lookup(extracted) if extracted else []
                    price = _price_verdict(extracted) if extracted else {}
                    bvr = _buy_vs_rent(extracted) if extracted else {}
                    source_names = []
                    for f in (uploaded_files or []):
                        source_names.append(f.name)
                    if url_input and url_input.startswith("http"):
                        source_names.append(url_input)
                    st.session_state.uf_report_data = {
                        "extracted": extracted,
                        "rera_rows": rera_rows,
                        "price": price,
                        "bvr": bvr,
                        "source_names": source_names,
                    }
                except Exception:
                    st.session_state.uf_report_data = None

                st.success(f"{len(texts)} document(s) loaded into chat.")
            else:
                st.warning("No text extracted. Try a different file or URL.")

        if st.session_state.get("uf_doc_context"):
            n = len(st.session_state.get("uf_report_data", {}).get("source_names", []))
            st.markdown(
                f"<div style='font-size:.78rem;color:#166534;background:#dcfce7;"
                f"border-radius:4px;padding:4px 8px;margin-top:4px'>"
                f"Context active: {n} doc{'s' if n!=1 else ''} loaded</div>",
                unsafe_allow_html=True,
            )
            if st.button("Clear documents", key="sidebar_clear_docs", type="secondary",
                         use_container_width=True):
                st.session_state.pop("uf_doc_context", None)
                st.session_state.pop("uf_report_data", None)
                st.rerun()


# ── sidebar: K-RERA project search ───────────────────────────────────────────

def _sidebar_project_search():
    with st.sidebar:
        st.markdown("### K-RERA Project Lookup")
        st.caption("Search 10,000+ registered projects directly.")

        search_query = st.text_input(
            "Project / Builder / PIN / RERA#",
            placeholder="e.g. Prestige Lakeside or 560066",
            key="sidebar_search_query"
        )
        search_btn = st.button(
            "Search K-RERA", key="sidebar_search_btn", type="primary",
            use_container_width=True,
        )

        if search_btn and search_query.strip():
            with st.spinner("Searching K-RERA..."):
                rows = _search_rera_db(search_query.strip(), search_by="auto", limit=8)

            if not rows:
                st.warning("No matching projects found.")
            else:
                st.success(f"Found {len(rows)} project(s)")
                for r in rows:
                    fg, bg = _status_color(r.get("status", ""))
                    sold = r.get("sold_units") or ""
                    total = r.get("total_units") or ""
                    units_str = f"{sold}/{total} units" if sold and total else ""
                    st.markdown(
                        f"<div style='background:{bg};border-radius:8px;padding:10px 12px;margin-bottom:8px'>"
                        f"<div style='font-weight:700;color:{fg};font-size:.88rem'>{r.get('project_name','')}</div>"
                        f"<div style='font-size:.78rem;color:#374151;margin-top:3px'>"
                        f"Status: <b>{r.get('status','')}</b></div>"
                        f"<div style='font-size:.75rem;color:#6b7280;margin-top:2px'>"
                        f"{r.get('promoter_name','')} | PIN: {r.get('pin_code','')}</div>"
                        f"<div style='font-size:.75rem;color:#6b7280'>"
                        f"RERA: {r.get('ack_number','')} | Completion: {r.get('completion_date','')}"
                        + (f" | {units_str}" if units_str else "")
                        + f"</div></div>",
                        unsafe_allow_html=True,
                    )


# ── main chat interface ───────────────────────────────────────────────────────

def _render_chat():
    if "uf_chat_history" not in st.session_state:
        st.session_state.uf_chat_history = []

    for msg in st.session_state.uf_chat_history:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    user_input = st.chat_input(
        "Ask about any Karnataka property: RERA status, fair price, buy vs rent, builder reputation..."
    )

    if user_input:
        st.session_state.uf_chat_history.append({"role": "user", "content": user_input})
        with st.chat_message("user"):
            st.markdown(user_input)

        with st.chat_message("assistant"):
            with st.spinner("Thinking..."):
                try:
                    from openai import OpenAI
                    client = OpenAI(api_key=_secret("OPENAI_API_KEY"))

                    entity = _detect_entity_in_message(user_input)
                    db_context = ""
                    if entity["type"] != "none" and entity["query"]:
                        db_rows = _search_rera_db(
                            entity["query"], search_by=entity["type"], limit=5
                        )
                        if db_rows:
                            db_context = _format_db_rows_for_llm(db_rows)

                    system_content = _build_chat_system()

                    doc_context = st.session_state.get("uf_doc_context", "")
                    if doc_context:
                        system_content = (
                            system_content
                            + "\n\n[Uploaded document context. Use this to answer questions about the property.]\n"
                            + doc_context[:8000]
                        )

                    if db_context:
                        system_content = (
                            system_content
                            + "\n\n"
                            + db_context
                            + "\n\nUse the above live K-RERA database results to answer accurately. "
                            "Quote RERA numbers, status, and completion dates from these results."
                        )

                    messages = (
                        [{"role": "system", "content": system_content}]
                        + st.session_state.uf_chat_history
                    )
                    resp = client.chat.completions.create(
                        model=os.environ.get("LLM_MODEL", "gpt-5-nano"),
                        messages=messages,
                        # No max_completion_tokens: let the model respond fully
                    )
                    raw = resp.choices[0].message.content
                    finish = resp.choices[0].finish_reason
                    if raw and raw.strip():
                        answer = raw
                    else:
                        n_msg = len(messages)
                        sys_len = len(messages[0]["content"])
                        answer = (
                            f"[No content returned. finish_reason={finish!r}, "
                            f"messages={n_msg}, system_chars={sys_len}]"
                        )
                except Exception as e:
                    answer = f"Sorry, something went wrong. Please try again. ({e})"

                st.markdown(answer)
                st.session_state.uf_chat_history.append(
                    {"role": "assistant", "content": answer}
                )

    if st.session_state.get("uf_chat_history"):
        st.button(
            "Clear chat", key="uf_clear_chat", type="secondary",
            on_click=lambda: st.session_state.update(uf_chat_history=[]),
        )


# ── main app layout ───────────────────────────────────────────────────────────

def main():
    # Sidebar (always visible)
    _sidebar_project_search()
    _sidebar_document_context()

    # Header
    st.markdown(
        "<h2 style='margin-bottom:2px'>Karnataka RERA Property Advisor</h2>"
        "<p style='color:#6b7280;margin-top:0;font-size:.9rem'>"
        "IIMB PGPBA Group 9 | CCS RAG Compliance Assistant</p>",
        unsafe_allow_html=True,
    )

    # Pipeline animation
    components.html(DEMO_HTML, height=62)

    st.markdown(
        "<div style='background:#f0fdf4;border-left:4px solid #22c55e;"
        "border-radius:6px;padding:10px 14px;margin-bottom:16px;font-size:.85rem;color:#166534'>"
        "Upload brochures or paste a URL in the sidebar to run a full due diligence report. "
        "Or just ask any question below about Karnataka RERA, prices, or builders."
        "</div>",
        unsafe_allow_html=True,
    )

    # Structured report card (shown when documents are loaded)
    report = st.session_state.get("uf_report_data")
    if report and report.get("extracted"):
        source_names = report.get("source_names", [])
        label = ", ".join(source_names) if source_names else "uploaded documents"
        with st.expander(f"Property report: {label}", expanded=True):
            _render_report(
                report["extracted"],
                report.get("rera_rows", []),
                report.get("price", {}),
                report.get("bvr", {}),
            )
        st.divider()

    # Chat (always visible)
    _render_chat()


main()
