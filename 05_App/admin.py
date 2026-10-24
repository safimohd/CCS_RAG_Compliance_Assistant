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
    page_icon="🛡️",
    layout="wide",
)

# Dark mode toggle (off by default)
if "dark_mode" not in st.session_state:
    st.session_state.dark_mode = False

# ─── Base font + global reset ──────────────────────────────────────────────
st.markdown("""
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
  html, body, [class*="st-"], .stApp, .stMarkdown, .stMetric, button, input, select, textarea {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif !important;
  }
  #MainMenu, footer, header { visibility: hidden; }
</style>
""", unsafe_allow_html=True)

# ─── Theme CSS ────────────────────────────────────────────────────────────
if st.session_state.dark_mode:
    st.markdown("""<style>
      :root {
        --bg:      #0b0f17;
        --surface: #131929;
        --border:  #1e2d45;
        --text:    #e4ecf7;
        --muted:   #6b7f9a;
        --accent:  #3b82f6;
        --green:   #22c55e;
        --amber:   #f59e0b;
        --red:     #ef4444;
      }
      .stApp, [data-testid="stAppViewContainer"] { background: var(--bg) !important; color: var(--text) !important; }
      [data-testid="stHeader"] { background: var(--bg) !important; }
      /* Tabs */
      .stTabs [data-baseweb="tab-list"] {
        background: #1a2540 !important; border-radius: 10px;
        padding: 4px; gap: 4px; border: 1px solid #2a3f65;
        display: inline-flex !important; width: auto !important;
        position: relative !important; left: 50% !important;
        transform: translateX(-50%) !important;
      }
      .stTabs [data-baseweb="tab"] {
        background: transparent !important; color: #a8bdd8 !important;
        border-radius: 8px; font-weight: 600; font-size: 15px; padding: 9px 24px;
        letter-spacing: 0.01em;
      }
      .stTabs [aria-selected="true"] {
        background: #2563eb !important; color: #ffffff !important;
      }
      .stTabs [data-baseweb="tab-panel"] { background: var(--bg) !important; padding-top: 20px; }
      /* Metrics */
      [data-testid="metric-container"] {
        background: var(--surface) !important; border: 1px solid var(--border) !important;
        border-radius: 12px; padding: 20px 24px !important;
      }
      [data-testid="stMetricValue"] { color: var(--text) !important; font-size: 36px !important; font-weight: 800 !important; }
      [data-testid="stMetricLabel"] { color: var(--muted) !important; font-size: 12px !important; font-weight: 600 !important; text-transform: uppercase; letter-spacing: 0.06em; }
      /* Buttons */
      .stButton > button {
        background: var(--surface) !important; border: 1px solid var(--border) !important;
        color: var(--text) !important; border-radius: 8px; font-weight: 600; font-size: 13px;
      }
      .stButton > button:hover { border-color: var(--accent) !important; color: #93c5fd !important; }
      /* Inputs */
      .stTextInput > div > div > input {
        background: var(--surface) !important; border: 1px solid var(--border) !important;
        color: var(--text) !important; border-radius: 8px;
      }
      [data-baseweb="select"] { background: var(--surface) !important; border-color: var(--border) !important; }
      [data-baseweb="select"] span { color: var(--text) !important; }
      /* Dataframe */
      .stDataFrame, [data-testid="stDataFrame"] {
        background: var(--surface) !important; border: 1px solid var(--border) !important; border-radius: 10px;
      }
      /* Radio */
      .stRadio label, [data-testid="stWidgetLabel"] { color: var(--muted) !important; font-size: 13px !important; }
      hr { border-color: var(--border) !important; }
      .stAlert { border-radius: 8px !important; }
    </style>""", unsafe_allow_html=True)
else:
    st.markdown("""<style>
      :root {
        --bg:      #f5f7fa;
        --surface: #ffffff;
        --border:  #dde3ee;
        --text:    #141c2e;
        --muted:   #6b7a99;
        --accent:  #2563eb;
        --green:   #16a34a;
        --amber:   #d97706;
        --red:     #dc2626;
      }
      .stApp, [data-testid="stAppViewContainer"] { background: var(--bg) !important; color: var(--text) !important; }
      [data-testid="stHeader"] { background: var(--bg) !important; }
      .stTabs [data-baseweb="tab-list"] {
        background: #e9edf5 !important; border-radius: 10px;
        padding: 4px; gap: 4px; border: 1px solid #d1d9eb;
        display: inline-flex !important; width: auto !important;
        position: relative !important; left: 50% !important;
        transform: translateX(-50%) !important;
      }
      .stTabs [data-baseweb="tab"] {
        background: transparent !important; color: var(--muted) !important;
        border-radius: 8px; font-weight: 600; font-size: 15px; padding: 9px 24px;
        letter-spacing: 0.01em;
      }
      .stTabs [aria-selected="true"] {
        background: var(--surface) !important; color: var(--text) !important;
        box-shadow: 0 1px 4px rgba(0,0,0,.08);
      }
      .stTabs [data-baseweb="tab-panel"] { background: var(--bg) !important; padding-top: 20px; }
      [data-testid="metric-container"] {
        background: var(--surface) !important; border: 1px solid var(--border) !important;
        border-radius: 12px; padding: 20px 24px !important;
      }
      [data-testid="stMetricValue"] { color: var(--text) !important; font-size: 36px !important; font-weight: 800 !important; }
      [data-testid="stMetricLabel"] { color: var(--muted) !important; font-size: 12px !important; font-weight: 600 !important; text-transform: uppercase; letter-spacing: 0.06em; }
      .stButton > button {
        background: var(--surface) !important; border: 1px solid var(--border) !important;
        color: var(--text) !important; border-radius: 8px; font-weight: 600; font-size: 13px;
      }
      .stButton > button:hover { border-color: var(--accent) !important; color: var(--accent) !important; }
      .stTextInput > div > div > input {
        background: var(--surface) !important; border: 1px solid var(--border) !important;
        color: var(--text) !important; border-radius: 8px;
      }
      .stDataFrame, [data-testid="stDataFrame"] {
        background: var(--surface) !important; border: 1px solid var(--border) !important; border-radius: 10px;
      }
      .stRadio label, [data-testid="stWidgetLabel"] { color: var(--muted) !important; font-size: 13px !important; }
      hr { border-color: var(--border) !important; }
    </style>""", unsafe_allow_html=True)


# ─── Auth guard ────────────────────────────────────────────────────────────
ADMIN_PASSWORD = st.secrets.get("ADMIN_PASSWORD", "")
ADMIN_USERNAME = st.secrets.get("ADMIN_USERNAME", "")
ADMIN_SECRET   = st.secrets.get("ADMIN_SECRET", "")

if "authenticated" not in st.session_state:
    st.session_state.authenticated = False
if "is_admin" not in st.session_state:
    st.session_state.is_admin = False

if not st.session_state.authenticated:
    _, center_col, _ = st.columns([1, 1.2, 1])
    with center_col:
        st.markdown("<div style='height:72px'></div>", unsafe_allow_html=True)
        st.markdown("""
        <div style='text-align:center;margin-bottom:32px;'>
          <div style='width:64px;height:64px;background:linear-gradient(135deg,#1e3a6e,#2563eb);
                      border-radius:16px;margin:0 auto 16px;display:flex;align-items:center;
                      justify-content:center;font-size:30px;'>🛡️</div>
          <h2 style='margin:0;font-size:26px;font-weight:800;letter-spacing:-0.02em;'>CCS RAG Admin</h2>
          <p style='color:#6b7a99;margin:8px 0 0;font-size:14px;font-weight:500;'>Compliance Intelligence Dashboard</p>
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
            if st.button("Sign in", key="btn_admin_login", use_container_width=True):
                if uname == ADMIN_USERNAME and pwd == ADMIN_SECRET:
                    st.session_state.authenticated = True
                    st.session_state.is_admin = True
                    st.rerun()
                else:
                    st.error("Invalid username or password.")
        else:
            pwd = st.text_input("Password", type="password", key="login_pwd_guest")
            if st.button("Sign in", key="btn_guest_login", use_container_width=True):
                if pwd == ADMIN_PASSWORD:
                    st.session_state.authenticated = True
                    st.session_state.is_admin = False
                    st.rerun()
                else:
                    st.error("Incorrect password.")
    st.stop()


# ─── Supabase config ────────────────────────────────────────────────────────
SUPABASE_URL = st.secrets["SUPABASE_URL"].rstrip("/")
SVC_KEY      = st.secrets["SUPABASE_SERVICE_KEY"]
HDRS = {
    "apikey":        SVC_KEY,
    "Authorization": f"Bearer {SVC_KEY}",
    "Content-Type":  "application/json",
}
TODAY = datetime.date.today()


# ─── Data fetchers ──────────────────────────────────────────────────────────
@st.cache_data(ttl=120)
def fetch_queries(since_date: str):
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/queries"
        f"?select=id,created_at,bot,question,confidence,flagged_low_conf"
        f"&created_at=gte.{since_date}T00:00:00&order=created_at.desc",
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


# ─── Header ─────────────────────────────────────────────────────────────────
is_dark = st.session_state.dark_mode
hdr_bg   = "linear-gradient(135deg, #0f1e38 0%, #0a1322 100%)" if is_dark else "linear-gradient(135deg, #1e3a6e 0%, #0f1f40 100%)"
hdr_bdr  = "#1e2d45" if is_dark else "#1a3460"

role_label = "ADMIN" if st.session_state.is_admin else "VIEWER"
role_color = "#3b82f6" if st.session_state.is_admin else "#64748b"

hdr_col, btn_col = st.columns([6, 1])
with hdr_col:
    st.markdown(f"""
    <div style='background:{hdr_bg};padding:24px 32px;border-radius:14px;margin-bottom:12px;
                border:1px solid {hdr_bdr};display:flex;align-items:center;justify-content:space-between;'>
      <div style='display:flex;align-items:center;gap:16px;'>
        <div style='width:48px;height:48px;background:rgba(59,130,246,0.2);border-radius:12px;
                    display:flex;align-items:center;justify-content:center;font-size:24px;flex-shrink:0;'>🛡️</div>
        <div>
          <div style='display:flex;align-items:center;gap:10px;'>
            <h1 style='color:#f0f6ff;margin:0;font-size:28px;font-weight:800;letter-spacing:-0.03em;line-height:1;'>
              CCS RAG Compliance Assistant
            </h1>
            <span style='background:{role_color};color:#fff;font-size:10px;font-weight:700;
                          border-radius:6px;padding:3px 8px;letter-spacing:0.08em;flex-shrink:0;'>
              {role_label}
            </span>
          </div>
          <p style='color:#7aa3d0;margin:5px 0 0;font-size:13px;font-weight:500;letter-spacing:0.01em;'>
            Admin Dashboard
          </p>
        </div>
      </div>
      <div style='text-align:right;'>
        <div style='color:#93c5fd;font-size:14px;font-weight:600;'>{TODAY.strftime("%d %b %Y")}</div>
        <div style='color:#4a6f9a;font-size:11px;margin-top:2px;font-weight:500;'>Live monitoring</div>
      </div>
    </div>
    """, unsafe_allow_html=True)

with btn_col:
    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)
    dm_label = "☀️ Light" if is_dark else "🌙 Dark"
    if st.button(dm_label, key="dark_mode_toggle", use_container_width=True):
        st.session_state.dark_mode = not st.session_state.dark_mode
        st.rerun()
    st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)
    if st.button("Sign out", key="logout_btn", use_container_width=True):
        st.session_state.authenticated = False
        st.session_state.is_admin = False
        st.rerun()

st.markdown("<div style='height:4px'></div>", unsafe_allow_html=True)

# ─── Tabs ───────────────────────────────────────────────────────────────────
tab1, tab2, tab3, tab4 = st.tabs([
    "  Activity  ",
    "  Documents  ",
    "  Bots  ",
    "  KB Coverage  ",
])
# Force center tabs via JS (CSS alone can't override Streamlit's tab container)
st.markdown("""
<script>
(function centerTabs() {
  const apply = () => {
    const tl = document.querySelector('[data-baseweb="tab-list"]');
    if (!tl) { setTimeout(apply, 200); return; }
    const parent = tl.parentElement;
    if (parent) {
      parent.style.display = 'flex';
      parent.style.justifyContent = 'center';
    }
    tl.style.width = 'fit-content';
  };
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', apply);
  } else { apply(); }
})();
</script>
""", unsafe_allow_html=True)


# ─── Tab 1: Activity ────────────────────────────────────────────────────────
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
        if st.button("Refresh", key="refresh_qs"):
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
    flagged = sum(1 for r in rows if r.get("flagged_low_conf"))
    flag_pct = f"{flagged/total*100:.1f}%" if total else "0.0%"

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Queries", total)
    c2.metric("Healthcare", hc)
    c3.metric("RERA", rera)
    c4.metric("Low Confidence", f"{flagged} ({flag_pct})")

    st.markdown("<div style='height:24px'></div>", unsafe_allow_html=True)

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

        all_days  = sorted(set(list(daily_hc.keys()) + list(daily_rera.keys())))
        df_chart  = pd.DataFrame({
            "Date":       all_days,
            "Healthcare": [daily_hc.get(d, 0) for d in all_days],
            "RERA":       [daily_rera.get(d, 0) for d in all_days],
        }).set_index("Date")

        # Section label -- small, not a giant subheader
        surface_c = "#131929" if is_dark else "#ffffff"
        border_c  = "#1e2d45" if is_dark else "#dde3ee"
        label_c   = "#6b7f9a" if is_dark else "#6b7a99"
        text_c    = "#e4ecf7" if is_dark else "#141c2e"

        st.markdown(f"""
        <p style='color:{label_c};font-size:11px;font-weight:700;text-transform:uppercase;
                  letter-spacing:0.08em;margin:0 0 10px;'>Queries per day</p>
        """, unsafe_allow_html=True)
        import plotly.graph_objects as go
        bg_color = "#0b0f17" if is_dark else "#ffffff"
        grid_color = "#1e2d45" if is_dark else "#e5e9f2"
        txt_color = "#a8bdd8" if is_dark else "#6b7a99"
        fig = go.Figure()
        fig.add_trace(go.Bar(x=all_days, y=[daily_hc.get(d, 0) for d in all_days], name="Healthcare", marker_color="#3b82f6"))
        fig.add_trace(go.Bar(x=all_days, y=[daily_rera.get(d, 0) for d in all_days], name="RERA", marker_color="#22c55e"))
        fig.update_layout(
            paper_bgcolor=bg_color, plot_bgcolor=bg_color,
            barmode="group", showlegend=True, height=280, margin=dict(l=0, r=0, t=10, b=0),
            legend=dict(font=dict(color=txt_color), bgcolor="rgba(0,0,0,0)"),
            xaxis=dict(gridcolor=grid_color, color=txt_color, tickfont=dict(color=txt_color)),
            yaxis=dict(gridcolor=grid_color, color=txt_color, tickfont=dict(color=txt_color)),
        )
        st.plotly_chart(fig, use_container_width=True)

        st.markdown("<div style='height:24px'></div>", unsafe_allow_html=True)

        low_conf_rows = [r for r in rows if r.get("flagged_low_conf")]
        st.markdown(f"""
        <p style='color:{label_c};font-size:11px;font-weight:700;text-transform:uppercase;
                  letter-spacing:0.08em;margin:0 0 10px;'>Low confidence queries ({len(low_conf_rows)})</p>
        """, unsafe_allow_html=True)
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


# ─── Tab 2: Documents ───────────────────────────────────────────────────────
with tab2:
    col_bot, col_status, col_ref2 = st.columns([2, 2, 1])
    with col_ref2:
        st.write("")
        if st.button("Refresh", key="refresh_docs"):
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

    label_c = "#6b7f9a" if is_dark else "#6b7a99"
    st.markdown(f"""
    <p style='color:{label_c};font-size:11px;font-weight:700;text-transform:uppercase;
              letter-spacing:0.08em;margin:4px 0 12px;'>{len(docs)} documents shown</p>
    """, unsafe_allow_html=True)

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


# ─── Tab 3: Bots ────────────────────────────────────────────────────────────
with tab3:
    label_c  = "#6b7f9a" if is_dark else "#6b7a99"
    text_c   = "#e4ecf7" if is_dark else "#141c2e"
    sub_c    = "#7aa3d0" if is_dark else "#4a6f9a"

    st.markdown(f"""
    <p style='color:{label_c};font-size:11px;font-weight:700;text-transform:uppercase;
              letter-spacing:0.08em;margin:0 0 20px;'>Deployed compliance bots</p>
    """, unsafe_allow_html=True)

    col_hc, col_rera = st.columns(2)

    with col_hc:
        st.markdown(f"""
        <div style='background:linear-gradient(145deg,#0f1e38 0%,#0a1528 100%);
                    padding:32px 28px;border-radius:14px;border:1px solid #1e3560;
                    position:relative;overflow:hidden;'>
          <div style='position:absolute;top:-20px;right:-20px;width:100px;height:100px;
                      background:rgba(59,130,246,0.08);border-radius:50%;'></div>
          <div style='font-size:36px;margin-bottom:14px;'>🏥</div>
          <div style='color:#93c5fd;font-size:10px;font-weight:700;text-transform:uppercase;
                      letter-spacing:0.1em;margin-bottom:6px;'>Healthcare</div>
          <h3 style='color:#f0f6ff;margin:0 0 8px;font-size:20px;font-weight:800;letter-spacing:-0.02em;'>
            CDSCO Compliance Bot
          </h3>
          <p style='color:#4a6f9a;font-size:13px;margin:0 0 24px;line-height:1.5;'>
            Drug and Cosmetics Act, NDCT Rules, Medical Device Regulations
          </p>
          <a href='https://ccsragcomplianceassistant-6hug2utprjrpwtyijwjanr.streamlit.app/' target='_blank'
             style='background:#2563eb;color:#fff;padding:11px 24px;border-radius:8px;
                    text-decoration:none;font-size:13px;font-weight:700;letter-spacing:0.01em;
                    display:inline-block;'>
            Open bot →
          </a>
        </div>
        """, unsafe_allow_html=True)

    with col_rera:
        st.markdown(f"""
        <div style='background:linear-gradient(145deg,#0d2218 0%,#081510 100%);
                    padding:32px 28px;border-radius:14px;border:1px solid #1a4030;
                    position:relative;overflow:hidden;'>
          <div style='position:absolute;top:-20px;right:-20px;width:100px;height:100px;
                      background:rgba(34,197,94,0.08);border-radius:50%;'></div>
          <div style='font-size:36px;margin-bottom:14px;'>🏠</div>
          <div style='color:#86efac;font-size:10px;font-weight:700;text-transform:uppercase;
                      letter-spacing:0.1em;margin-bottom:6px;'>Real Estate</div>
          <h3 style='color:#f0f6ff;margin:0 0 8px;font-size:20px;font-weight:800;letter-spacing:-0.02em;'>
            Karnataka RERA Bot
          </h3>
          <p style='color:#2d5a40;font-size:13px;margin:0 0 24px;line-height:1.5;'>
            RERA Act 2016, K-RERA Rules, Project Registration, Agent Licensing
          </p>
          <a href='https://ccsragcomplianceassistant-cisyv46xzq7kte95ywkmhg.streamlit.app/' target='_blank'
             style='background:#16a34a;color:#fff;padding:11px 24px;border-radius:8px;
                    text-decoration:none;font-size:13px;font-weight:700;letter-spacing:0.01em;
                    display:inline-block;'>
            Open bot →
          </a>
        </div>
        """, unsafe_allow_html=True)

    st.markdown("<div style='height:20px'></div>", unsafe_allow_html=True)
    st.info("Update the bot URLs in 05_App/admin.py → Tab 3 once deployment links are finalized.")


# ─── Tab 4: KB Coverage ─────────────────────────────────────────────────────
with tab4:
    import pandas as pd

    is_dark  = st.session_state.dark_mode
    label_c  = "#6b7f9a" if is_dark else "#6b7a99"

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

    hc_have  = sum(d["have"] for d in hc_domains)
    hc_total = sum(d["total"] for d in hc_domains)
    hc_pct   = round(hc_have / hc_total * 100) if hc_total else 0

    rr_have  = sum(d["have"] for d in rera_domains)
    rr_total = sum(d["total"] for d in rera_domains)
    rr_pct   = round(rr_have / rr_total * 100) if rr_total else 0

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
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total Registered", total_registered)
    m2.metric("Healthcare Docs",  total_registered - rera_live if rera_live else hc_live)
    m3.metric("RERA Docs",        rera_live)
    m4.metric("Dead URLs (HC)",   HC_DEAD, delta="unreachable", delta_color="inverse")

    st.markdown("<div style='height:20px'></div>", unsafe_allow_html=True)

    # Overall coverage tiles
    ov1, ov2 = st.columns(2)

    with ov1:
        bar_dark = "#0a1322" if is_dark else "#e0e8f5"
        st.markdown(f"""
        <div style='background:linear-gradient(145deg,#0f1e38 0%,#0a1528 100%);
                    border:1px solid #1e3560;border-radius:14px;padding:24px 28px;'>
          <div style='color:#93c5fd;font-size:10px;font-weight:700;text-transform:uppercase;
                      letter-spacing:0.1em;margin-bottom:6px;'>Healthcare — CDSCO</div>
          <div style='display:flex;align-items:baseline;gap:8px;margin-bottom:16px;'>
            <span style='color:#3b82f6;font-size:44px;font-weight:800;letter-spacing:-0.03em;line-height:1;'>
              {hc_pct}%
            </span>
            <span style='color:#4a6f9a;font-size:13px;font-weight:500;'>coverage</span>
          </div>
          <div style='background:#0a1322;border-radius:6px;height:6px;overflow:hidden;'>
            <div style='background:linear-gradient(90deg,#2563eb,#60a5fa);width:{hc_pct}%;height:6px;border-radius:6px;'></div>
          </div>
          <div style='color:#4a6f9a;font-size:12px;margin-top:10px;font-weight:500;'>
            {hc_have} of {hc_total} regulatory docs across {len(hc_domains)} domains
          </div>
        </div>
        """, unsafe_allow_html=True)

    with ov2:
        st.markdown(f"""
        <div style='background:linear-gradient(145deg,#0d2218 0%,#081510 100%);
                    border:1px solid #1a4030;border-radius:14px;padding:24px 28px;'>
          <div style='color:#86efac;font-size:10px;font-weight:700;text-transform:uppercase;
                      letter-spacing:0.1em;margin-bottom:6px;'>Real Estate — K-RERA</div>
          <div style='display:flex;align-items:baseline;gap:8px;margin-bottom:16px;'>
            <span style='color:#22c55e;font-size:44px;font-weight:800;letter-spacing:-0.03em;line-height:1;'>
              {rr_pct}%
            </span>
            <span style='color:#2d5a40;font-size:13px;font-weight:500;'>coverage</span>
          </div>
          <div style='background:#081510;border-radius:6px;height:6px;overflow:hidden;'>
            <div style='background:linear-gradient(90deg,#16a34a,#4ade80);width:{rr_pct}%;height:6px;border-radius:6px;'></div>
          </div>
          <div style='color:#2d5a40;font-size:12px;margin-top:10px;font-weight:500;'>
            {rr_have} of {rr_total} regulatory docs across {len(rera_domains)} domains
          </div>
        </div>
        """, unsafe_allow_html=True)

    st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)

    domain_view = st.radio(
        "Domain breakdown",
        ["Healthcare (CDSCO)", "Real Estate (K-RERA)"],
        horizontal=True,
        key="kb_domain_toggle",
    )

    active_domains = hc_domains if "Healthcare" in domain_view else rera_domains
    is_hc = "Healthcare" in domain_view

    card_bg     = "#131929" if is_dark else "#ffffff"
    card_bdr    = "#1e3560" if (is_dark and is_hc) else ("#1a4030" if is_dark else "#dde3ee")
    chip_have_bg  = "rgba(37,99,235,0.15)" if is_hc else "rgba(22,163,74,0.15)"
    chip_have_col = "#93c5fd" if is_hc else "#86efac"
    bar_good    = "#2563eb" if is_hc else "#16a34a"

    st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)

    for item in active_domains:
        pct = round(item["have"] / item["total"] * 100) if item["total"] else 0
        bar_c  = bar_good if pct >= 60 else ("#d97706" if pct >= 30 else "#dc2626")
        badge_bg  = chip_have_bg if pct >= 60 else ("rgba(217,119,6,0.15)" if pct >= 30 else "rgba(220,38,38,0.15)")
        badge_col = chip_have_col if pct >= 60 else ("#fcd34d" if pct >= 30 else "#fca5a5")

        have_chips = "".join(
            f"<span style='background:{chip_have_bg};color:{chip_have_col};"
            f"border-radius:5px;padding:3px 10px;font-size:11px;font-weight:500;"
            f"margin:2px;display:inline-block;'>{h}</span>"
            for h in item["have_labels"]
        )
        miss_chips = "".join(
            f"<span style='background:rgba(220,38,38,0.1);color:#fca5a5;"
            f"border-radius:5px;padding:3px 10px;font-size:11px;font-weight:500;"
            f"margin:2px;display:inline-block;'>— {m}</span>"
            for m in item["missing"]
        )

        text_main = "#e4ecf7" if is_dark else "#141c2e"
        sub_text  = "#6b7f9a" if is_dark else "#6b7a99"
        inner_bg  = "#0a1322" if is_dark else "#f0f4fa"

        st.markdown(f"""
        <div style='background:{card_bg};border:1px solid {card_bdr};border-radius:12px;
                    padding:18px 22px;margin-bottom:10px;'>
          <div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;'>
            <span style='color:{text_main};font-weight:700;font-size:14px;letter-spacing:-0.01em;'>{item["domain"]}</span>
            <span style='background:{badge_bg};color:{badge_col};border-radius:20px;
                         padding:4px 14px;font-size:11px;font-weight:700;white-space:nowrap;letter-spacing:0.02em;'>
              {pct}%
            </span>
          </div>
          <div style='color:{sub_text};font-size:12px;font-weight:500;margin-bottom:10px;'>
            {item["have"]} of ~{item["total"]} docs indexed
          </div>
          <div style='background:{inner_bg};border-radius:4px;height:4px;margin-bottom:14px;overflow:hidden;'>
            <div style='background:{bar_c};width:{pct}%;height:4px;border-radius:4px;'></div>
          </div>
          <div style='line-height:2;'>{have_chips}{miss_chips}</div>
        </div>""", unsafe_allow_html=True)

    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)
    st.markdown(f"""
    <div style='background:rgba(217,119,6,0.1);border:1px solid rgba(217,119,6,0.3);
                border-radius:10px;padding:14px 18px;color:#d97706;font-size:13px;font-weight:500;'>
      <strong>Note:</strong> {HC_DEAD} Healthcare URLs are permanently unreachable.
      MoHFW returned 404/403 errors and mciindia.org DNS lookup fails (MCI was dissolved in 2020).
      These are excluded from all counts above.
    </div>""", unsafe_allow_html=True)
