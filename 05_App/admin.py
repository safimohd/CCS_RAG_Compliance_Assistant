"""
CCS RAG Compliance Assistant -- Admin Dashboard
"""
import datetime
import requests
import streamlit as st

st.set_page_config(page_title="CCS RAG Admin", page_icon="🛡️", layout="wide")

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

SUPABASE_URL = st.secrets["SUPABASE_URL"].rstrip("/")
SVC_KEY      = st.secrets["SUPABASE_SERVICE_KEY"]
HDRS = {
    "apikey":        SVC_KEY,
    "Authorization": f"Bearer {SVC_KEY}",
    "Content-Type":  "application/json",
}
TODAY = datetime.date.today()

@st.cache_data(ttl=120)
def fetch_queries(since_date: str):
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/queries"
        f"?select=id,created_at,bot,query,similarity_score,flagged_low_conf"
        f"&created_at=gte.{since_date}T00:00:00"
        f"&order=created_at.desc",
        headers=HDRS, timeout=15,
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
        headers=HDRS, timeout=15,
    )
    if r.status_code != 200:
        st.error(f"Failed to fetch documents ({r.status_code}): {r.text[:200]}")
        return []
    return r.json()

st.markdown(
    "<div style='background:#1a3a5c;padding:16px 24px;border-radius:8px;margin-bottom:24px'>"
    "<h1 style='color:#fff;margin:0;font-size:22px'>🛡️ CCS RAG Compliance Assistant</h1>"
    "<p style='color:#aac4e0;margin:4px 0 0;font-size:13px'>Admin Dashboard</p></div>",
    unsafe_allow_html=True,
)

tab1, tab2 = st.tabs(["📊 Query Stats", "📁 Document Registry"])

with tab1:
    col_range, col_refresh = st.columns([3, 1])
    with col_range:
        range_opt = st.radio("Date range", ["Last 7 days", "Last 30 days", "All time"], horizontal=True)
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
    total   = len(rows)
    hc      = sum(1 for r in rows if r["bot"] == "Healthcare")
    rera    = sum(1 for r in rows if r["bot"] == "RERA")
    flagged = sum(1 for r in rows if r["flagged_low_conf"])
    flag_pct = f"{flagged/total*100:.1f}%" if total else "0%"

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Queries", total)
    c2.metric("Healthcare", hc)
    c3.metric("RERA", rera)
    c4.metric("Low Confidence", f"{flagged} ({flag_pct})")
    st.divider()

    if rows:
        from collections import defaultdict
        import pandas as pd
        daily_hc   = defaultdict(int)
        daily_rera = defaultdict(int)
        for r in rows:
            day = r["created_at"][:10]
            if r["bot"] == "Healthcare":
                daily_hc[day] += 1
            else:
                daily_rera[day] += 1
        all_days = sorted(set(list(daily_hc.keys()) + list(daily_rera.keys())))
        df_chart = pd.DataFrame({
            "Healthcare": [daily_hc.get(d, 0) for d in all_days],
            "RERA":       [daily_rera.get(d, 0) for d in all_days],
        }, index=all_days)
        st.subheader("Queries per Day")
        st.bar_chart(df_chart, color=["#1a7f4b", "#8e44ad"])
        st.divider()
        low_conf_rows = [r for r in rows if r["flagged_low_conf"]]
        st.subheader(f"Low Confidence Queries ({len(low_conf_rows)})")
        if low_conf_rows:
            import pandas as pd
            df_lc = pd.DataFrame([{
                "Date":  r["created_at"][:10],
                "Bot":   r["bot"],
                "Query": r["query"][:120] + ("..." if len(r["query"]) > 120 else ""),
                "Score": round(r["similarity_score"] or 0, 3),
            } for r in low_conf_rows])
            st.dataframe(df_lc, use_container_width=True, hide_index=True)
        else:
            st.success("No low-confidence queries in this period.")
    else:
        st.info("No queries found for the selected date range.")

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
        df_docs = pd.DataFrame([{
            "Bot":         d["bot"],
            "Doc ID":      d["doc_id"],
            "Title":       d["title"][:80],
            "Status":      d["status"],
            "Review Date": d.get("expected_update_date") or "",
            "Days Left":   days_label(d),
        } for d in docs])
        st.dataframe(df_docs, use_container_width=True, hide_index=True)
    else:
        st.info("No documents match the selected filters.")
