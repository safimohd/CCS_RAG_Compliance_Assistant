"""
CCS RAG Compliance Assistant -- Admin Dashboard
A lightweight Streamlit app for monitoring query activity and document status.
Deployed as a separate app on Streamlit Community Cloud pointing to this file.

Secrets required (same Supabase project as main app):
    SUPABASE_URL
    SUPABASE_SERVICE_KEY
"""
import datetime
import requests
import streamlit as st

# ── Page config ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="CCS RAG Admin",
    page_icon="🛡️",
    layout="wide",
)

# ── Auth guard (simple password check) ─────────────────────────────────────────
ADMIN_PASSWORD = st.secrets.get("ADMIN_PASSWORD", "")

if ADMIN_PASSWORD:
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False

    if not st.session_state.authenticated:
        st.title("🛡️ CCS RAG Admin Dashboard")
        pwd = st.text_input("Enter admin password", type="password")
        if st.button("Login"):
            if pwd == ADMIN_PASSWORD:
                st.session_state.authenticated = True
                st.rerun()
            else:
                st.error("Incorrect password.")
        st.stop()

# ── Supabase config ────────────────────────────────────────────────────────────
SUPABASE_URL = st.secrets["SUPABASE_URL"].rstrip("/")
SVC_KEY      = st.secrets["SUPABASE_SERVICE_KEY"]

HDRS = {
    "apikey":        SVC_KEY,
    "Authorization": f"Bearer {SVC_KEY}",
    "Content-Type":  "application/json",
}

TODAY = datetime.date.today()


# ── Data fetchers ──────────────────────────────────────────────────────────────
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


# ── Header ─────────────────────────────────────────────────────────────────────
st.markdown(
    """
    <div style='background:#1a3a5c;padding:16px 24px;border-radius:8px;margin-bottom:24px'>
      <h1 style='color:#fff;margin:0;font-size:22px'>🛡️ CCS RAG Compliance Assistant</h1>
      <p style='color:#aac4e0;margin:4px 0 0;font-size:13px'>Admin Dashboard</p>
    </div>
    """,
    unsafe_allow_html=True,
)

tab1, tab2, tab3, tab4 = st.tabs(["📊 Query Stats", "📁 Document Registry", "🤖 Bots", "📚 KB Coverage"])


# ── Tab 1: Query Stats ─────────────────────────────────────────────────────────
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

    # Stat tiles
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Queries", total)
    c2.metric("Healthcare", hc)
    c3.metric("RERA", rera)
    c4.metric("Low Confidence", f"{flagged} ({flag_pct})")

    st.divider()

    if rows:
        # Queries per day chart
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
        st.bar_chart(df_chart, color=["#1a7f4b", "#8e44ad"])

        st.divider()

        # Low confidence table
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


# ── Tab 2: Document Registry ───────────────────────────────────────────────────
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


# ── Tab 3: Bots ────────────────────────────────────────────────────────────────
with tab3:
    st.markdown("### 🤖 Deployed Compliance Bots")
    st.caption("Click a bot to open it in a new tab.")

    col_hc, col_rera = st.columns(2)

    with col_hc:
        st.markdown(
            """
            <div style='background:#1a3a5c;padding:20px 24px;border-radius:10px;text-align:center'>
              <div style='font-size:36px'>🏥</div>
              <h3 style='color:#fff;margin:8px 0 4px'>Healthcare Bot</h3>
              <p style='color:#aac4e0;font-size:13px;margin:0 0 16px'>
                CDSCO / Drug & Cosmetics Act compliance assistant
              </p>
              <a href='https://ccsragcomplianceassistant-6hug2utprjrpwtyijwjanr.streamlit.app/' target='_blank'
                 style='background:#2196f3;color:#fff;padding:8px 20px;border-radius:6px;
                        text-decoration:none;font-size:14px;font-weight:600'>
                Open Bot →
              </a>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with col_rera:
        st.markdown(
            """
            <div style='background:#2d4a1e;padding:20px 24px;border-radius:10px;text-align:center'>
              <div style='font-size:36px'>🏠</div>
              <h3 style='color:#fff;margin:8px 0 4px'>RERA Bot</h3>
              <p style='color:#a8c890;font-size:13px;margin:0 0 16px'>
                Karnataka RERA real estate compliance assistant
              </p>
              <a href='https://ccsragcomplianceassistant-cisyv46xzq7kte95ywkmhg.streamlit.app/' target='_blank'
                 style='background:#4caf50;color:#fff;padding:8px 20px;border-radius:6px;
                        text-decoration:none;font-size:14px;font-weight:600'>
                Open Bot →
              </a>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.divider()
    st.info(
        "If the URLs above don't match your actual Streamlit deployment links, "
        "update them in `05_App/admin.py` under the Bots tab."
    )


# ── Tab 4: KB Coverage ─────────────────────────────────────────────────────────
with tab4:
    import pandas as pd

    col_ref_kb, _ = st.columns([1, 3])
    with col_ref_kb:
        if st.button("Refresh", key="refresh_kb"):
            st.cache_data.clear()
            st.rerun()

    all_docs = fetch_documents()
    total_registered = len(all_docs) if all_docs else 0

    HC_TOTAL   = 255
    HC_DEAD    = 7
    RERA_TOTAL = 230

    hc_docs   = [d for d in (all_docs or []) if (d.get("domain") or "").lower() == "healthcare"]
    rera_docs = [d for d in (all_docs or []) if (d.get("domain") or "").lower() == "rera"]

    hc_pct   = round(len(hc_docs)   / HC_TOTAL   * 100, 1) if HC_TOTAL   else 0
    rera_pct = round(len(rera_docs) / RERA_TOTAL * 100, 1) if RERA_TOTAL else 0

    st.markdown("### 📚 Knowledge Base Coverage")
    st.caption("Live counts from Supabase — documents registered and available to the RAG pipeline.")

    # Top-level summary tiles
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total Registered", total_registered)
    m2.metric("Healthcare",  f"{len(hc_docs)} docs",  delta=f"{hc_pct}% coverage")
    m3.metric("RERA",        f"{len(rera_docs)} docs", delta=f"{rera_pct}% coverage")
    m4.metric("Dead URLs (HC)", HC_DEAD, delta="permanently unreachable", delta_color="inverse")

    st.divider()

    # ── Healthcare domain breakdown ────────────────────────────────────────────
    st.markdown("#### 🏥 Healthcare (CDSCO / Drug Regulation)")

    hc_domains = [
        {"domain": "Primary Legislation & Regulatory Framework",   "have": 3,  "total": 5,
         "have_labels": ["D&C Act + Rules (Consolidated)", "D&C Act standalone", "India Code legislative text"],
         "missing": ["Drugs & Magic Remedies Act 1954", "State Pharmacy Acts (Maharashtra, Karnataka)"]},
        {"domain": "New Drug Approval & Clinical Trials",          "have": 4,  "total": 6,
         "have_labels": ["NDCT Rules 2019", "CDSCO NDA guidance", "CT Protocol template", "Import licence rules"],
         "missing": ["Cell & Gene Therapy guidance", "Paediatric clinical trial addendum"]},
        {"domain": "Medical Devices & Diagnostics",                "have": 5,  "total": 7,
         "have_labels": ["MDR 2017", "In-vitro diagnostics rules", "CDSCO IVD guidance", "QMS guidance", "Import/export rules"],
         "missing": ["Class C/D device specific guidance", "Post-market surveillance SOP"]},
        {"domain": "Pharmacovigilance & Drug Safety",              "have": 4,  "total": 5,
         "have_labels": ["PvPI guidelines", "ADR reporting SOP", "Good Pharmacovigilance Practice", "Schedule Y"],
         "missing": ["PVPI periodic safety update guidance"]},
        {"domain": "Insurance & PMJAY / ABDM",                    "have": 8,  "total": 10,
         "have_labels": ["PMJAY scheme doc", "ABDM HPR", "ABDM HFR", "PHR policy", "IRDAI health regulations",
                         "CGHS rates", "Ayushman Bharat guidelines", "NHA operational manual"],
         "missing": ["State-level Ayushman implementation orders", "IRDAI TPA licensing circulars"]},
        {"domain": "Cosmetics Regulation",                         "have": 1,  "total": 4,
         "have_labels": ["Cosmetics FAQ document"],
         "missing": ["Cosmetics Rules 2020 full text", "Cosmetics labelling schedule", "Import licence guidance"]},
        {"domain": "E-Pharmacy",                                   "have": 0,  "total": 1,
         "have_labels": [],
         "missing": ["Draft E-Pharmacy Rules 2018 — intentionally excluded (unnotified)"]},
        {"domain": "AYUSH / Traditional Medicine",                 "have": 0,  "total": 3,
         "have_labels": [],
         "missing": ["Ayurvedic drug rules", "Siddha/Unani manufacturing rules", "AYUSH Ministry circulars"]},
        {"domain": "State-Level Licensing",                        "have": 0,  "total": 3,
         "have_labels": [],
         "missing": ["Maharashtra FDA circulars", "Gujarat FDA fee schedules", "State-specific licensing SOPs"]},
        {"domain": "Blood Banks / Large-Volume Parenterals",       "have": 1,  "total": 3,
         "have_labels": ["CDSCO blood bank schedule"],
         "missing": ["CLA-licensed category guidance", "LVP manufacturing compliance guide"]},
    ]

    for item in hc_domains:
        pct = round(item["have"] / item["total"] * 100) if item["total"] else 0
        bar_color = "#2196f3" if pct >= 60 else ("#f59e0b" if pct >= 30 else "#ef4444")
        badge_bg  = "#1a3a5c" if pct >= 60 else ("#3a2a00" if pct >= 30 else "#3a0a0a")
        badge_col = "#90caf9" if pct >= 60 else ("#fbbf24" if pct >= 30 else "#f87171")

        have_chips = "".join(
            f"<span style='background:#1a3a5c;color:#90caf9;border-radius:4px;padding:2px 8px;font-size:11px;margin:2px;display:inline-block;'>{h}</span>"
            for h in item["have_labels"]
        )
        miss_chips = "".join(
            f"<span style='background:#2a1515;color:#f87171;border-radius:4px;padding:2px 8px;font-size:11px;margin:2px;display:inline-block;'>✗ {m}</span>"
            for m in item["missing"]
        )

        st.markdown(f"""
        <div style='border:1px solid #2a3a4a;border-radius:8px;padding:14px 18px;margin-bottom:10px;'>
          <div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;'>
            <span style='font-weight:600;font-size:14px;'>{item["domain"]}</span>
            <span style='background:{badge_bg};color:{badge_col};border-radius:12px;padding:2px 12px;font-size:12px;font-weight:600;'>{pct}% covered</span>
          </div>
          <div style='color:#888;font-size:12px;margin-bottom:8px;'>We have <strong>{item["have"]} docs</strong> &nbsp; Total available ~<strong>{item["total"]}</strong></div>
          <div style='background:#1a2230;border-radius:4px;height:6px;margin-bottom:10px;'>
            <div style='background:{bar_color};width:{pct}%;height:6px;border-radius:4px;'></div>
          </div>
          <div style='line-height:1.8;'>{have_chips}{miss_chips}</div>
        </div>""", unsafe_allow_html=True)

    st.divider()

    # ── RERA domain breakdown ──────────────────────────────────────────────────
    st.markdown("#### 🏠 Real Estate (K-RERA)")

    rera_domains = [
        {"domain": "Central RERA Act & Rules",               "have": 4,  "total": 5,
         "have_labels": ["Real Estate (Regulation & Development) Act 2016", "Central RERA Rules 2016",
                         "Model Builder-Buyer Agreement", "MoHUA circulars"],
         "missing": ["RERA (Amendment) Bill 2023"]},
        {"domain": "Karnataka RERA Rules & Procedures",      "have": 0,  "total": 4,
         "have_labels": [],
         "missing": ["Karnataka RERA Rules 2017", "K-RERA fee schedule", "K-RERA registration forms", "K-RERA circulars"]},
        {"domain": "Project Registration & Compliance",      "have": 3,  "total": 5,
         "have_labels": ["Project registration guidelines", "Quarterly progress report format", "Completion certificate SOP"],
         "missing": ["Escrow account management rules", "Designated bank circular"]},
        {"domain": "Agent Licensing",                        "have": 2,  "total": 3,
         "have_labels": ["Real estate agent registration rules", "RERA agent code of conduct"],
         "missing": ["State-specific agent fee schedules"]},
        {"domain": "Dispute Resolution & Appeals",           "have": 1,  "total": 4,
         "have_labels": ["RERA adjudication process overview"],
         "missing": ["RERA Appellate Tribunal rules", "K-RERA precedent orders", "COVID extension circulars"]},
        {"domain": "Allottee Rights & Disclosures",          "have": 3,  "total": 4,
         "have_labels": ["Disclosure norms", "Allottee rights chapter", "Refund & interest rules"],
         "missing": ["Homebuyer grievance portal SOP"]},
        {"domain": "K-RERA Orders & Circulars",              "have": 0,  "total": 3,
         "have_labels": [],
         "missing": ["K-RERA Authority orders", "Complaint precedents", "COVID extension orders"]},
    ]

    for item in rera_domains:
        pct = round(item["have"] / item["total"] * 100) if item["total"] else 0
        bar_color = "#4caf50" if pct >= 60 else ("#f59e0b" if pct >= 30 else "#ef4444")
        badge_bg  = "#2d4a1e" if pct >= 60 else ("#3a2a00" if pct >= 30 else "#3a0a0a")
        badge_col = "#a5d6a7" if pct >= 60 else ("#fbbf24" if pct >= 30 else "#f87171")

        have_chips = "".join(
            f"<span style='background:#2d4a1e;color:#a5d6a7;border-radius:4px;padding:2px 8px;font-size:11px;margin:2px;display:inline-block;'>{h}</span>"
            for h in item["have_labels"]
        )
        miss_chips = "".join(
            f"<span style='background:#2a1515;color:#f87171;border-radius:4px;padding:2px 8px;font-size:11px;margin:2px;display:inline-block;'>✗ {m}</span>"
            for m in item["missing"]
        )

        st.markdown(f"""
        <div style='border:1px solid #2a3a2a;border-radius:8px;padding:14px 18px;margin-bottom:10px;'>
          <div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;'>
            <span style='font-weight:600;font-size:14px;'>{item["domain"]}</span>
            <span style='background:{badge_bg};color:{badge_col};border-radius:12px;padding:2px 12px;font-size:12px;font-weight:600;'>{pct}% covered</span>
          </div>
          <div style='color:#888;font-size:12px;margin-bottom:8px;'>We have <strong>{item["have"]} docs</strong> &nbsp; Total available ~<strong>{item["total"]}</strong></div>
          <div style='background:#1a2a1a;border-radius:4px;height:6px;margin-bottom:10px;'>
            <div style='background:{bar_color};width:{pct}%;height:6px;border-radius:4px;'></div>
          </div>
          <div style='line-height:1.8;'>{have_chips}{miss_chips}</div>
        </div>""", unsafe_allow_html=True)

    st.divider()

    # Dead URL note
    st.markdown(f"""
    <div style='background:#1a1205;border:1px solid #9a6700;border-radius:8px;padding:14px;color:#d29922;font-size:13px;'>
      ⚠️ <strong>{HC_DEAD} Healthcare URLs are permanently unreachable</strong> — MoHFW 404/403 errors and mciindia.org DNS failure (MCI dissolved 2020). Excluded from all counts above.
    </div>""", unsafe_allow_html=True)
