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

    HC_TOTAL   = 255   # downloadable Healthcare docs
    HC_DEAD    = 7     # permanently dead URLs
    RERA_TOTAL = 230   # RERA docs
    BASELINE   = 114   # docs registered before the bulk-ingest run

    hc_docs   = [d for d in (all_docs or []) if (d.get("domain") or "").lower() == "healthcare"]
    rera_docs = [d for d in (all_docs or []) if (d.get("domain") or "").lower() == "rera"]

    hc_pct   = round(len(hc_docs)   / HC_TOTAL   * 100, 1) if HC_TOTAL   else 0
    rera_pct = round(len(rera_docs) / RERA_TOTAL * 100, 1) if RERA_TOTAL else 0

    st.markdown("### Knowledge Base Coverage")
    st.caption("Documents registered in Supabase and available to the RAG pipeline.")

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total Registered", total_registered, delta=f"+{total_registered - BASELINE} vs baseline")
    m2.metric("Healthcare Coverage", f"{hc_pct}%", delta=f"{len(hc_docs)} / {HC_TOTAL} docs")
    m3.metric("RERA Coverage",       f"{rera_pct}%", delta=f"{len(rera_docs)} / {RERA_TOTAL} docs")
    m4.metric("Dead URLs (HC)",      HC_DEAD, delta="permanently unreachable", delta_color="inverse")

    st.divider()

    # Progress bars
    st.markdown("#### Coverage by Domain")
    col_hc, col_rera = st.columns(2)

    with col_hc:
        st.markdown(f"""
        <div style='background:#1a3a5c;border-radius:10px;padding:18px;'>
          <div style='color:#90caf9;font-size:13px;font-weight:600;letter-spacing:.05em;'>HEALTHCARE</div>
          <div style='color:#fff;font-size:28px;font-weight:700;margin:6px 0;'>{hc_pct}%</div>
          <div style='background:#0d2137;border-radius:6px;height:10px;'>
            <div style='background:#2196f3;width:{hc_pct}%;height:10px;border-radius:6px;'></div>
          </div>
          <div style='color:#90caf9;font-size:12px;margin-top:8px;'>{len(hc_docs)} of {HC_TOTAL} docs &nbsp;|&nbsp; {HC_DEAD} dead URLs excluded</div>
        </div>""", unsafe_allow_html=True)

    with col_rera:
        st.markdown(f"""
        <div style='background:#2d4a1e;border-radius:10px;padding:18px;'>
          <div style='color:#a5d6a7;font-size:13px;font-weight:600;letter-spacing:.05em;'>K-RERA</div>
          <div style='color:#fff;font-size:28px;font-weight:700;margin:6px 0;'>{rera_pct}%</div>
          <div style='background:#1a2e10;border-radius:6px;height:10px;'>
            <div style='background:#4caf50;width:{rera_pct}%;height:10px;border-radius:6px;'></div>
          </div>
          <div style='color:#a5d6a7;font-size:12px;margin-top:8px;'>{len(rera_docs)} of {RERA_TOTAL} docs indexed</div>
        </div>""", unsafe_allow_html=True)

    st.divider()

    # Before / After
    st.markdown("#### Before vs After Bulk Ingest")
    ba1, ba2 = st.columns(2)
    with ba1:
        st.markdown(f"""
        <div style='background:#1a1a2e;border:1px solid #444;border-radius:8px;padding:16px;text-align:center;'>
          <div style='color:#aaa;font-size:12px;'>BEFORE (baseline)</div>
          <div style='color:#ff7043;font-size:36px;font-weight:700;'>{BASELINE}</div>
          <div style='color:#aaa;font-size:12px;'>documents registered</div>
        </div>""", unsafe_allow_html=True)
    with ba2:
        st.markdown(f"""
        <div style='background:#1a2e1a;border:1px solid #4caf50;border-radius:8px;padding:16px;text-align:center;'>
          <div style='color:#a5d6a7;font-size:12px;'>AFTER (bulk ingest)</div>
          <div style='color:#4caf50;font-size:36px;font-weight:700;'>{total_registered}</div>
          <div style='color:#a5d6a7;font-size:12px;'>documents registered</div>
        </div>""", unsafe_allow_html=True)

    st.divider()

    # Dead URL warning
    st.markdown(f"""
    <div style='background:#1a1205;border:1px solid #9a6700;border-radius:8px;padding:14px;color:#d29922;'>
      ⚠️ <strong>{HC_DEAD} Healthcare URLs are permanently unreachable</strong> (MoHFW 404/403 and mciindia.org DNS failure — MCI dissolved 2020).
      These are excluded from the coverage count.
    </div>""", unsafe_allow_html=True)

    st.divider()

    # Source breakdown table
    st.markdown("#### Source Breakdown")
    sources = [
        {"Domain": "Healthcare", "Source": "MoHFW / NHA / ABDM / CGHS", "Docs": len(hc_docs), "Coverage": f"{hc_pct}%"},
        {"Domain": "RERA",       "Source": "K-RERA / MahaRERA / Central RERA", "Docs": len(rera_docs), "Coverage": f"{rera_pct}%"},
    ]
    st.dataframe(pd.DataFrame(sources), use_container_width=True, hide_index=True)
