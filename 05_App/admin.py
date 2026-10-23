"""
CCS RAG Compliance Assistant -- Admin Dashboard
A lightweight Streamlit app for monitoring query activity and document status.
Deployed as a separate app on Streamlit Community Cloud pointing to this file.

Secrets required (same Supabase project as main app):
    SUPABASE_URL
    SUPABASE_SERVICE_KEY
    ADMIN_PASSWORD   -- password for regular access (Prof, reviewers)
    ADMIN_USERNAME   -- username for admin-level access
    ADMIN_SECRET     -- password for admin-level access
"""
import datetime
import requests
import streamlit as st

# Page config
st.set_page_config(
    page_title="CCS RAG Admin",
    page_icon="shield:",
    layout="wide",
)

# Dark mode toggle (off by default)
if "dark_mode" not in st.session_state:
    st.session_state.dark_mode = False

# Dark mode CSS (injected only when toggled on)
if st.session_state.dark_mode:
    st.markdown("""
    <style>
      .stApp { background-color: #0d1117 !important; color: #e6edf3 !important; }
      section[data-testid="stSidebar"] { background-color: #161b22 !important; }
      .stTabs [data-baseweb="tab-list"] { background: #161b22; border-radius: 10px; padding: 4px; gap: 4px; }
      .stTabs [data-baseweb="tab"] { background: transparent; color: #8b949e; border-radius: 8px; font-weight: 500; padding: 8px 18px; }
      .stTabs [aria-selected="true"] { background: #21262d; color: #e6edf3; }
      [data-testid="metric-container"] { background: #161b22 !important; border: 1px solid #30363d; border-radius: 10px; padding: 16px; }
      [data-testid="stMetricValue"] { color: #e6edf3 !important; }
      .stButton > button { background: #21262d; border: 1px solid #30363d; color: #e6edf3; border-radius: 8px; font-weight: 500; }
      .stButton > button:hover { background: #30363d; border-color: #58a6ff; color: #58a6ff; }
      .stTextInput > div > div > input, .stSelectbox > div > div { background: #161b22 !important; border: 1px solid #30363d; color: #e6edf3; border-radius: 8px; }
      .stDataFrame { border: 1px solid #30363d; border-radius: 8px; }
      hr { border-color: #21262d; }
      .stAlert { border-radius: 8px; }
      .stRadio > div { gap: 12px; }
    </style>
    """, unsafe_allow_html=True)

# Always hide Streamlit chrome
st.markdown("<style>#MainMenu, footer, header { visibility: hidden; }</style>", unsafe_allow_html=True)


# Auth guard
ADMIN_PASSWORD = st.secrets.get("ADMIN_PASSWORD", "")
ADMIN_USERNAME = st.secrets.get("ADMIN_USERNAME", "")
ADMIN_SECRET   = st.secrets.get("ADMIN_SECRET", "")

if "authenticated" not in st.session_state:
    st.session_state.authenticated = False
if "is_admin" not in st.session_state:
    st.session_state.is_admin = False

if not st.session_state.authenticated:
    # Centered login card - everything inside one column
    _, center_col, _ = st.columns([1, 1.2, 1])
    with center_col:
        st.markdown("<div style='height:60px'></div>", unsafe_allow_html=True)
        st.markdown("""
        <div style='text-align:center;margin-bottom:24px;'>
          <div style='font-size:44px;margin-bottom:10px;'>🛡️</div>
          <h2 style='margin:0;font-size:22px;font-weight:700;'>CCS RAG Admin</h2>
          <p style='color:#888;margin:6px 0 0;font-size:13px;'>Compliance Intelligence Dashboard</p>
        </div>
        """, unsafe_allow_html=True)

        login_type = st.radio(
            "Login as",
            ["Reviewer / Guest", "Admin"],
            horizontal=True,
            key="login_type_radio",
        )

        if login_type == "Admin":
            uname = st.text_input("Username", key="login_uname")
            pwd   = st.text_input("Password", type="password", key="login_pwd_admin")
            if st.button("Login", key="btn_admin_login", use_container_width=True):
                if uname == ADMIN_USERNAME and pwd == ADMIN_SECRET:
                    st.session_state.authenticated = True
                    st.session_state.is_admin = True
                    st.rerun()
                else:
                    st.error("Invalid username or password.")
        else:
            pwd = st.text_input("Password", type="password", key="login_pwd_guest")
            if st.button("Login", key="btn_guest_login", use_container_width=True):
                if pwd == ADMIN_PASSWORD:
                    st.session_state.authenticated = True
                    st.session_state.is_admin = False
                    st.rerun()
                else:
                    st.error("Incorrect password.")

    st.stop()


# Supabase config
SUPABASE_URL = st.secrets["SUPABASE_URL"].rstrip("/")
SVC_KEY      = st.secrets["SUPABASE_SERVICE_KEY"]

HDRS = {
    "apikey":        SVC_KEY,
    "Authorization": f"Bearer {SVC_KEY}",
    "Content-Type":  "application/json",
}

TODAY = datetime.date.today()


# Data fetchers
@st.cache_data(ttl=120)
def fetch_queries(since_date: str):
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/queries"
        f"?select=id,created_at,bot,question,confidence,flagged_low_conf"
        f"&created_at=gte.{since_date}T00:00:00"
        f"&order=created_at.desc",
        headers=HDRS,
        timeout=15,
    )
    if r.status_code != 200:
        st.error(f"Failed to fetch queries ({r.status_code}): {r.text[:200]}")
        return []
    return r.json()


@st.cache_data(ttl=120)
def fetch_documents():
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/kb_documents"
        f"?select=bot,doc_id,title,status,expected_update_date,alert_days_before,source_url"
        f"&order=bot.asc,doc_id.asc",
        headers=HDRS,
        timeout=15,
    )
    if r.status_code != 200:
        st.error(f"Failed to fetch documents ({r.status_code}): {r.text[:200]}")
        return []
    return r.json()


# Header
role_badge = (
    "<span style='background:#1f6feb;color:#e6edf3;font-size:11px;font-weight:600;"
    "border-radius:12px;padding:2px 10px;margin-left:10px;'>ADMIN</span>"
    if st.session_state.is_admin else
    "<span style='background:#30363d;color:#8b949e;font-size:11px;"
    "border-radius:12px;padding:2px 10px;margin-left:10px;'>VIEWER</span>"
)

hdr_col, dm_col = st.columns([5, 1])
with hdr_col:
    st.markdown(f"""
    <div style='background:linear-gradient(135deg,#1a3a5c 0%,#0d1f33 100%);
                padding:20px 28px;border-radius:12px;margin-bottom:8px;
                border:1px solid #1f3a5c;display:flex;align-items:center;justify-content:space-between;'>
      <div>
        <h1 style='color:#e6edf3;margin:0;font-size:22px;font-weight:700;'>
          🛡️ CCS RAG Compliance Assistant {role_badge}
        </h1>
        <p style='color:#8b949e;margin:4px 0 0;font-size:13px;'>Admin Dashboard</p>
      </div>
      <div style='text-align:right;'>
        <div style='color:#58a6ff;font-size:12px;font-weight:500;'>{TODAY.strftime("%d %b %Y")}</div>
      </div>
    </div>
    """, unsafe_allow_html=True)
with dm_col:
    st.markdown("<div style='height:14px'></div>", unsafe_allow_html=True)
    dm_label = "☀️ Light" if st.session_state.dark_mode else "🌙 Dark"
    if st.button(dm_label, key="dark_mode_toggle", use_container_width=True):
        st.session_state.dark_mode = not st.session_state.dark_mode
        st.rerun()
st.markdown("<div style='margin-bottom:16px'></div>", unsafe_allow_html=True)

tab1, tab2, tab3, tab4 = st.tabs(["📊 Query Stats", "📁 Document Registry", "🤖 Bots", "📚 KB Coverage"])


# Tab 1: Query Stats
with tab1:
    col_range, col_refresh = st.columns([3, 1])
    with col_range:
        range_opt = st.radio(
            "Date range",
            ["Last 7 days", "Last 30 days", "All time"],
            horizontal=True,
        )
    with col_refresh:
        st.write("")
        if st.button("Refresh"):
            st.cache_data.clear()
            st.rerun()

    if range_opt == "Last 7 days":
        since = (TODAY - datetime.timedelta(days=7)).isoformat()
    elif range_opt == "Last 30 days":
        since = (TODAY - datetime.timedelta(days=30)).isoformat()
    else:
        since = "2020-01-01"

    rows = fetch_queries(since)

    total    = len(rows)
    hc       = sum(1 for r in rows if r["bot"] == "Healthcare")
    rera     = sum(1 for r in rows if r["bot"] == "RERA")
    flagged  = sum(1 for r in rows if r.get("flagged_low_conf"))
    flag_pct = f"{flagged/total*100:.1f}%" if total else "0%"

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Queries", total)
    c2.metric("Healthcare", hc)
    c3.metric("RERA", rera)
    c4.metric("Low Confidence", f"{flagged} ({flag_pct})")

    st.divider()

    if rows:
        from collections import defaultdict
        daily_hc   = defaultdict(int)
        daily_rera = defaultdict(int)

        for r in rows:
            day = r["created_at"][:10]
            if r["bot"] == "Healthcare":
                daily_hc[day] += 1
            else:
                daily_rera[day] += 1

        all_days = sorted(set(list(daily_hc.keys()) + list(daily_rera.keys())))
        chart_data = {
            "Date":       all_days,
            "Healthcare": [daily_hc.get(d, 0) for d in all_days],
            "RERA":       [daily_rera.get(d, 0) for d in all_days],
        }

        st.subheader("Queries per Day")
        import pandas as pd
        df_chart = pd.DataFrame(chart_data).set_index("Date")
        st.bar_chart(df_chart, color=["#2196f3", "#4caf50"])

        st.divider()

        low_conf_rows = [r for r in rows if r.get("flagged_low_conf")]
        st.subheader(f"Low Confidence Queries ({len(low_conf_rows)})")
        if low_conf_rows:
            df_lc = pd.DataFrame([
                {
                    "Date":  r["created_at"][:10],
                    "Bot":   r["bot"],
                    "Query": (r.get("question") or "")[:120] + ("..." if len(r.get("question") or "") > 120 else ""),
                    "Score": round(r.get("confidence") or 0, 3),
                }
                for r in low_conf_rows
            ])
            st.dataframe(df_lc, use_container_width=True, hide_index=True)
        else:
            st.success("No low-confidence queries in this period.")
    else:
        st.info("No queries found for the selected date range.")


# Tab 2: Document Registry
with tab2:
    col_bot, col_status, col_ref2 = st.columns([2, 2, 1])
    with col_ref2:
        st.write("")
        if st.button("Refresh ", key="refresh_docs"):
            st.cache_data.clear()
            st.rerun()

    docs = fetch_documents()

    with col_bot:
        bot_filter = st.selectbox("Bot", ["All", "Healthcare", "RERA"])
    with col_status:
        status_filter = st.selectbox("Status", ["All", "current", "archived", "pending"])

    if bot_filter != "All":
        docs = [d for d in docs if d["bot"] == bot_filter]
    if status_filter != "All":
        docs = [d for d in docs if d["status"] == status_filter]

    st.caption(f"{len(docs)} documents shown")

    if docs:
        import pandas as pd

        def days_label(d):
            exp = d.get("expected_update_date")
            if not exp:
                return ""
            delta = (datetime.date.fromisoformat(exp) - TODAY).days
            if delta < 0:
                return f"OVERDUE ({abs(delta)}d)"
            if delta <= 7:
                return f"Due in {delta}d"
            return f"{delta}d"

        df_docs = pd.DataFrame([
            {
                "Bot":         d["bot"],
                "Doc ID":      d["doc_id"],
                "Title":       d["title"][:80],
                "Status":      d["status"],
                "Review Date": d.get("expected_update_date") or "",
                "Days Left":   days_label(d),
            }
            for d in docs
        ])
        st.dataframe(df_docs, use_container_width=True, hide_index=True)
    else:
        st.info("No documents match the selected filters.")


# Tab 3: Bots
with tab3:
    st.markdown("### 🤖 Deployed Compliance Bots")
    st.caption("Click a bot to open it in a new tab.")

    col_hc, col_rera = st.columns(2)

    with col_hc:
        st.markdown("""
        <div style='background:linear-gradient(135deg,#1a3a5c 0%,#0d2137 100%);
                    padding:28px 24px;border-radius:12px;text-align:center;
                    border:1px solid #1f4068;'>
          <div style='font-size:40px;margin-bottom:10px;'>🏥</div>
          <h3 style='color:#e6edf3;margin:0 0 6px;font-size:18px;font-weight:700;'>Healthcare Bot</h3>
          <p style='color:#8b949e;font-size:13px;margin:0 0 20px;'>
            CDSCO / Drug and Cosmetics Act compliance assistant
          </p>
          <a href='https://ccsragcomplianceassistant-6hug2utprjrpwtyijwjanr.streamlit.app/' target='_blank'
             style='background:#2196f3;color:#fff;padding:10px 24px;border-radius:8px;
                    text-decoration:none;font-size:14px;font-weight:600;'>
            Open Bot
          </a>
        </div>
        """, unsafe_allow_html=True)

    with col_rera:
        st.markdown("""
        <div style='background:linear-gradient(135deg,#2d4a1e 0%,#1a2e10 100%);
                    padding:28px 24px;border-radius:12px;text-align:center;
                    border:1px solid #3a6324;'>
          <div style='font-size:40px;margin-bottom:10px;'>🏠</div>
          <h3 style='color:#e6edf3;margin:0 0 6px;font-size:18px;font-weight:700;'>RERA Bot</h3>
          <p style='color:#8b949e;font-size:13px;margin:0 0 20px;'>
            Karnataka RERA real estate compliance assistant
          </p>
          <a href='https://ccsragcomplianceassistant-cisyv46xzq7kte95ywkmhg.streamlit.app/' target='_blank'
             style='background:#4caf50;color:#fff;padding:10px 24px;border-radius:8px;
                    text-decoration:none;font-size:14px;font-weight:600;'>
            Open Bot
          </a>
        </div>
        """, unsafe_allow_html=True)

    st.divider()
    st.info("If the URLs above do not match your actual Streamlit deployment links, update them in 05_App/admin.py under the Bots tab.")


# Tab 4: KB Coverage
with tab4:
    import pandas as pd

    # ── Domain data ──────────────────────────────────────────────────────────
    hc_domains = [
        {"domain": "Primary Legislation and Regulatory Framework",    "have": 3,  "total": 5,
         "have_labels": ["D&C Act + Rules (Consolidated)", "D&C Act standalone", "India Code legislative text"],
         "missing": ["Drugs and Magic Remedies Act 1954", "State Pharmacy Acts (Maharashtra, Karnataka)"]},
        {"domain": "New Drug Approval and Clinical Trials",           "have": 4,  "total": 6,
         "have_labels": ["NDCT Rules 2019", "CDSCO NDA guidance", "CT Protocol template", "Import licence rules"],
         "missing": ["Cell and Gene Therapy guidance", "Paediatric clinical trial addendum"]},
        {"domain": "Medical Devices and Diagnostics",                 "have": 5,  "total": 7,
         "have_labels": ["MDR 2017", "In-vitro diagnostics rules", "CDSCO IVD guidance", "QMS guidance", "Import/export rules"],
         "missing": ["Class C/D device specific guidance", "Post-market surveillance SOP"]},
        {"domain": "Pharmacovigilance and Drug Safety",               "have": 4,  "total": 5,
         "have_labels": ["PvPI guidelines", "ADR reporting SOP", "Good Pharmacovigilance Practice", "Schedule Y"],
         "missing": ["PvPI periodic safety update guidance"]},
        {"domain": "Insurance, PMJAY and ABDM",                      "have": 8,  "total": 10,
         "have_labels": ["PMJAY scheme doc", "ABDM HPR", "ABDM HFR", "PHR policy", "IRDAI health regulations",
                         "CGHS rates", "Ayushman Bharat guidelines", "NHA operational manual"],
         "missing": ["State-level Ayushman implementation orders", "IRDAI TPA licensing circulars"]},
        {"domain": "Cosmetics Regulation",                            "have": 1,  "total": 4,
         "have_labels": ["Cosmetics FAQ document"],
         "missing": ["Cosmetics Rules 2020 full text", "Cosmetics labelling schedule", "Import licence guidance"]},
        {"domain": "E-Pharmacy",                                      "have": 0,  "total": 1,
         "have_labels": [],
         "missing": ["Draft E-Pharmacy Rules 2018 (intentionally excluded, unnotified draft)"]},
        {"domain": "AYUSH / Traditional Medicine",                    "have": 0,  "total": 3,
         "have_labels": [],
         "missing": ["Ayurvedic drug rules", "Siddha/Unani manufacturing rules", "AYUSH Ministry circulars"]},
        {"domain": "State-Level Licensing",                           "have": 0,  "total": 3,
         "have_labels": [],
         "missing": ["Maharashtra FDA circulars", "Gujarat FDA fee schedules", "State-specific licensing SOPs"]},
        {"domain": "Blood Banks / Large-Volume Parenterals",          "have": 1,  "total": 3,
         "have_labels": ["CDSCO blood bank schedule"],
         "missing": ["CLA-licensed category guidance", "LVP manufacturing compliance guide"]},
    ]

    rera_domains = [
        {"domain": "Central RERA Act and Rules",               "have": 4,  "total": 5,
         "have_labels": ["Real Estate (Regulation and Development) Act 2016", "Central RERA Rules 2016",
                         "Model Builder-Buyer Agreement", "MoHUA circulars"],
         "missing": ["RERA (Amendment) Bill 2023"]},
        {"domain": "Karnataka RERA Rules and Procedures",      "have": 0,  "total": 4,
         "have_labels": [],
         "missing": ["Karnataka RERA Rules 2017", "K-RERA fee schedule", "K-RERA registration forms", "K-RERA circulars"]},
        {"domain": "Project Registration and Compliance",      "have": 3,  "total": 5,
         "have_labels": ["Project registration guidelines", "Quarterly progress report format", "Completion certificate SOP"],
         "missing": ["Escrow account management rules", "Designated bank circular"]},
        {"domain": "Agent Licensing",                          "have": 2,  "total": 3,
         "have_labels": ["Real estate agent registration rules", "RERA agent code of conduct"],
         "missing": ["State-specific agent fee schedules"]},
        {"domain": "Dispute Resolution and Appeals",           "have": 1,  "total": 4,
         "have_labels": ["RERA adjudication process overview"],
         "missing": ["RERA Appellate Tribunal rules", "K-RERA precedent orders", "COVID extension circulars"]},
        {"domain": "Allottee Rights and Disclosures",          "have": 3,  "total": 4,
         "have_labels": ["Disclosure norms", "Allottee rights chapter", "Refund and interest rules"],
         "missing": ["Homebuyer grievance portal SOP"]},
        {"domain": "K-RERA Orders and Circulars",              "have": 0,  "total": 3,
         "have_labels": [],
         "missing": ["K-RERA Authority orders", "Complaint precedents", "COVID extension orders"]},
    ]

    # Overall coverage % per domain group
    hc_have  = sum(d["have"] for d in hc_domains)
    hc_total = sum(d["total"] for d in hc_domains)
    hc_overall_pct = round(hc_have / hc_total * 100) if hc_total else 0

    rr_have  = sum(d["have"] for d in rera_domains)
    rr_total = sum(d["total"] for d in rera_domains)
    rr_overall_pct = round(rr_have / rr_total * 100) if rr_total else 0

    # Live Supabase counts
    col_ref_kb, _ = st.columns([1, 3])
    with col_ref_kb:
        if st.button("Refresh", key="refresh_kb"):
            st.cache_data.clear()
            st.rerun()

    all_docs = fetch_documents()
    total_registered = len(all_docs) if all_docs else 0
    hc_live   = sum(1 for d in (all_docs or []) if (d.get("domain") or d.get("bot") or "").lower() == "healthcare")
    rera_live = sum(1 for d in (all_docs or []) if (d.get("domain") or d.get("bot") or "").lower() == "rera")
    HC_DEAD   = 7

    # Summary tiles
    st.markdown("### 📚 Knowledge Base Coverage")
    st.caption("Live counts from Supabase. Domain breakdown is based on the curated knowledge base audit.")

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total Registered", total_registered)
    m2.metric("Healthcare Docs",  total_registered - rera_live if rera_live else hc_live)
    m3.metric("RERA Docs",        rera_live)
    m4.metric("Dead URLs (HC)",   HC_DEAD, delta="permanently unreachable", delta_color="inverse")

    # Overall coverage bars side by side
    st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)
    ov1, ov2 = st.columns(2)

    with ov1:
        st.markdown(f"""
        <div style='background:linear-gradient(135deg,#1a3a5c 0%,#0d2137 100%);
                    border:1px solid #1f4068;border-radius:12px;padding:20px 22px;'>
          <div style='color:#8b949e;font-size:11px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;margin-bottom:4px;'>Healthcare (CDSCO)</div>
          <div style='display:flex;align-items:baseline;gap:8px;margin-bottom:12px;'>
            <span style='color:#58a6ff;font-size:32px;font-weight:700;'>{hc_overall_pct}%</span>
            <span style='color:#8b949e;font-size:13px;'>overall coverage</span>
          </div>
          <div style='background:#0d1117;border-radius:6px;height:8px;overflow:hidden;'>
            <div style='background:linear-gradient(90deg,#2196f3,#58a6ff);width:{hc_overall_pct}%;height:8px;border-radius:6px;'></div>
          </div>
          <div style='color:#8b949e;font-size:12px;margin-top:10px;'>
            {hc_have} of {hc_total} regulatory docs across {len(hc_domains)} domains
          </div>
        </div>
        """, unsafe_allow_html=True)

    with ov2:
        st.markdown(f"""
        <div style='background:linear-gradient(135deg,#2d4a1e 0%,#1a2e10 100%);
                    border:1px solid #3a6324;border-radius:12px;padding:20px 22px;'>
          <div style='color:#8b949e;font-size:11px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;margin-bottom:4px;'>Real Estate (K-RERA)</div>
          <div style='display:flex;align-items:baseline;gap:8px;margin-bottom:12px;'>
            <span style='color:#4caf50;font-size:32px;font-weight:700;'>{rr_overall_pct}%</span>
            <span style='color:#8b949e;font-size:13px;'>overall coverage</span>
          </div>
          <div style='background:#0d1117;border-radius:6px;height:8px;overflow:hidden;'>
            <div style='background:linear-gradient(90deg,#4caf50,#81c784);width:{rr_overall_pct}%;height:8px;border-radius:6px;'></div>
          </div>
          <div style='color:#8b949e;font-size:12px;margin-top:10px;'>
            {rr_have} of {rr_total} regulatory docs across {len(rera_domains)} domains
          </div>
        </div>
        """, unsafe_allow_html=True)

    st.markdown("<div style='height:24px'></div>", unsafe_allow_html=True)
    st.divider()

    # Domain toggle
    st.markdown("#### Domain-level Breakdown")
    domain_view = st.radio(
        "View domain",
        ["🏥 Healthcare (CDSCO)", "🏠 Real Estate (K-RERA)"],
        horizontal=True,
        key="kb_domain_toggle",
        label_visibility="collapsed",
    )

    active_domains  = hc_domains  if "Healthcare" in domain_view else rera_domains
    accent_have_bg  = "#1a3a5c"   if "Healthcare" in domain_view else "#2d4a1e"
    accent_have_col = "#58a6ff"   if "Healthcare" in domain_view else "#81c784"
    bar_accent      = "#2196f3"   if "Healthcare" in domain_view else "#4caf50"
    card_border     = "#1f4068"   if "Healthcare" in domain_view else "#3a6324"

    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

    for item in active_domains:
        pct       = round(item["have"] / item["total"] * 100) if item["total"] else 0
        bar_color = bar_accent  if pct >= 60 else ("#f59e0b" if pct >= 30 else "#ef4444")
        badge_bg  = accent_have_bg if pct >= 60 else ("#2e1f00" if pct >= 30 else "#2a0d0d")
        badge_col = accent_have_col if pct >= 60 else ("#fbbf24" if pct >= 30 else "#f87171")

        have_chips = "".join(
            f"<span style='background:{accent_have_bg};color:{accent_have_col};"
            f"border-radius:4px;padding:3px 9px;font-size:11px;margin:2px;display:inline-block;'>{h}</span>"
            for h in item["have_labels"]
        )
        miss_chips = "".join(
            f"<span style='background:#1f0d0d;color:#f87171;border:1px solid #3a1515;"
            f"border-radius:4px;padding:3px 9px;font-size:11px;margin:2px;display:inline-block;'>✗ {m}</span>"
            for m in item["missing"]
        )

        st.markdown(f"""
        <div style='background:#161b22;border:1px solid {card_border};border-radius:10px;
                    padding:16px 20px;margin-bottom:10px;'>
          <div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;'>
            <span style='color:#e6edf3;font-weight:600;font-size:14px;'>{item["domain"]}</span>
            <span style='background:{badge_bg};color:{badge_col};border-radius:20px;
                         padding:3px 14px;font-size:12px;font-weight:600;white-space:nowrap;'>
              {pct}% covered
            </span>
          </div>
          <div style='color:#8b949e;font-size:12px;margin-bottom:8px;'>
            {item["have"]} of ~{item["total"]} docs indexed
          </div>
          <div style='background:#0d1117;border-radius:4px;height:5px;margin-bottom:12px;overflow:hidden;'>
            <div style='background:{bar_color};width:{pct}%;height:5px;border-radius:4px;'></div>
          </div>
          <div style='line-height:2;'>{have_chips}{miss_chips}</div>
        </div>""", unsafe_allow_html=True)

    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)
    st.markdown(f"""
    <div style='background:#1c1500;border:1px solid #9a6700;border-radius:10px;
                padding:14px 18px;color:#d29922;font-size:13px;'>
      ⚠️ <strong>{HC_DEAD} Healthcare URLs are permanently unreachable.</strong>
      MoHFW returned 404/403 errors and mciindia.org DNS lookup fails (MCI was dissolved in 2020).
      These are excluded from all counts above.
    </div>""", unsafe_allow_html=True)
