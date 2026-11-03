"""
Daily report script for the CCS RAG Compliance Assistant.
Runs at 08:00 IST every morning via GitHub Actions.

What it does:
  1. Pulls yesterday's query stats from Supabase (queries table)
  2. Pulls documents expiring within their alert window (kb_documents table)
  3. Pulls feedback counts (feedback table)
  4. Pulls bookmark saves (bookmarks table)
  5. Pulls unique user sessions (sessions table)
  6. Pulls top low-confidence questions text
  7. Pulls 7-day rolling query trend
  8. Sends a formatted HTML email via SMTP

All config comes from environment variables (GitHub Secrets).
"""
import os, smtplib, datetime, sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import requests

# ── Config ─────────────────────────────────────────────────────────────────────
SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SVC_KEY      = os.environ["SUPABASE_SERVICE_KEY"]
TO_EMAIL     = os.environ["REPORT_TO_EMAIL"]
FROM_EMAIL   = os.environ["REPORT_FROM_EMAIL"]
SMTP_HOST    = os.environ["SMTP_HOST"]
SMTP_PORT    = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER    = os.environ["SMTP_USER"]
SMTP_PASS    = os.environ["SMTP_PASS"]

HDRS = {
    "apikey": SVC_KEY,
    "Authorization": f"Bearer {SVC_KEY}",
    "Content-Type": "application/json",
}

TODAY     = datetime.date.today()
YESTERDAY = TODAY - datetime.timedelta(days=1)
DATE_STR  = TODAY.strftime("%d %b %Y")

# IST offset for display labels
IST_OFFSET = datetime.timezone(datetime.timedelta(hours=5, minutes=30))


def _get(path, timeout=15):
    r = requests.get(f"{SUPABASE_URL}/rest/v1/{path}", headers=HDRS, timeout=timeout)
    if r.status_code != 200:
        print(f"WARNING: {path[:60]} returned {r.status_code}: {r.text[:200]}")
        return []
    return r.json()


def date_range_qs(col, d_from, d_to):
    """PostgREST query string for a date range on `col`."""
    return f"&{col}=gte.{d_from.isoformat()}T00:00:00&{col}=lt.{d_to.isoformat()}T00:00:00"


# ── 1. Query stats for yesterday ────────────────────────────────────────────────
def fetch_query_stats():
    rows = _get(
        f"queries?select=bot,flagged_low_conf,question"
        + date_range_qs("created_at", YESTERDAY, TODAY)
    )
    return rows


# ── 2. Documents expiring soon ─────────────────────────────────────────────────
def fetch_expiring_docs():
    all_docs = _get(
        "kb_documents"
        "?select=bot,doc_id,title,expected_update_date,alert_days_before,source_url"
        "&status=eq.current"
        "&expected_update_date=not.is.null"
        "&order=expected_update_date.asc"
    )
    expiring = []
    for doc in all_docs:
        try:
            upd = datetime.date.fromisoformat(doc["expected_update_date"])
            alert_days = doc.get("alert_days_before") or 30
            alert_start = upd - datetime.timedelta(days=alert_days)
            if alert_start <= TODAY:
                doc["_days_until"] = (upd - TODAY).days
                expiring.append(doc)
        except Exception:
            continue
    return expiring


# ── 3. Feedback counts for yesterday ───────────────────────────────────────────
def fetch_feedback_stats():
    rows = _get(
        f"feedback?select=rating,bot"
        + date_range_qs("created_at", YESTERDAY, TODAY)
    )
    thumbs_up   = sum(1 for r in rows if r.get("rating") in ("up", "positive", "1", 1))
    thumbs_down = sum(1 for r in rows if r.get("rating") in ("down", "negative", "-1", -1))
    return {"up": thumbs_up, "down": thumbs_down, "total": len(rows)}


# ── 4. Bookmarks saved yesterday ───────────────────────────────────────────────
def fetch_bookmark_stats():
    rows = _get(
        f"bookmarks?select=domain"
        + date_range_qs("created_at", YESTERDAY, TODAY)
    )
    hc = sum(1 for r in rows if r.get("domain") == "Healthcare")
    re = sum(1 for r in rows if r.get("domain") == "RERA")
    return {"total": len(rows), "healthcare": hc, "rera": re}


# ── 5. Unique users yesterday (from sessions) ──────────────────────────────────
def fetch_session_stats():
    rows = _get(
        f"sessions?select=user_id,domain"
        + date_range_qs("updated_at", YESTERDAY, TODAY)
    )
    unique_users = len({r["user_id"] for r in rows if r.get("user_id")})
    hc = sum(1 for r in rows if r.get("domain") == "Healthcare")
    re = sum(1 for r in rows if r.get("domain") == "RERA")
    return {"unique_users": unique_users, "healthcare": hc, "rera": re}


# ── 6. Top low-confidence questions from yesterday ─────────────────────────────
def fetch_low_conf_questions(limit=5):
    rows = _get(
        f"queries?select=question,bot"
        f"&flagged_low_conf=eq.true"
        + date_range_qs("created_at", YESTERDAY, TODAY)
        + f"&limit={limit}"
    )
    return rows


# ── 7. 7-day rolling query trend ───────────────────────────────────────────────
def fetch_weekly_trend():
    """Returns list of (date_str, count) for the last 7 days."""
    seven_days_ago = TODAY - datetime.timedelta(days=7)
    rows = _get(
        f"queries?select=created_at,bot"
        + date_range_qs("created_at", seven_days_ago, TODAY)
    )
    # Bucket by IST date
    buckets = {}
    for r in rows:
        raw = r.get("created_at", "")
        try:
            ts = raw.rstrip("Z")
            if "+" in ts:
                ts = ts[:ts.index("+")]
            dt_utc = datetime.datetime.fromisoformat(ts).replace(tzinfo=datetime.timezone.utc)
            dt_ist = dt_utc.astimezone(IST_OFFSET)
            day = dt_ist.strftime("%Y-%m-%d")
        except Exception:
            day = raw[:10]
        buckets[day] = buckets.get(day, 0) + 1

    # Build ordered list for last 7 days (oldest first, exclude today)
    trend = []
    for i in range(6, 0, -1):
        d = TODAY - datetime.timedelta(days=i)
        key = d.strftime("%Y-%m-%d")
        label = d.strftime("%a %-d")   # e.g. "Mon 3"
        trend.append((label, buckets.get(key, 0)))
    return trend


# ── 8. Build HTML email ─────────────────────────────────────────────────────────
def build_email(stats, expiring, feedback, bookmarks, sessions, low_conf_qs, trend):
    total    = len(stats)
    hc_total = sum(1 for r in stats if r["bot"] == "Healthcare")
    re_total = sum(1 for r in stats if r["bot"] == "RERA")
    flagged  = sum(1 for r in stats if r["flagged_low_conf"])

    # Expiry sections
    overdue  = [d for d in expiring if d["_days_until"] < 0]
    due_soon = [d for d in expiring if 0 <= d["_days_until"] <= 7]
    upcoming = [d for d in expiring if 7 < d["_days_until"] <= 30]

    def doc_row(d):
        days  = d["_days_until"]
        label = "OVERDUE" if days < 0 else f"Due in {days}d"
        color = "#c0392b" if days < 0 else "#e67e22" if days <= 7 else "#7f8c8d"
        url   = d.get("source_url") or "#"
        link  = (f'<a href="{url}" style="color:#2c3e50">{d["title"][:70]}</a>'
                 if url != "#" else d["title"][:70])
        return (
            f"<tr>"
            f"<td style='padding:6px 8px;border-bottom:1px solid #eee'>{d['bot']}</td>"
            f"<td style='padding:6px 8px;border-bottom:1px solid #eee'>{d['doc_id']}</td>"
            f"<td style='padding:6px 8px;border-bottom:1px solid #eee'>{link}</td>"
            f"<td style='padding:6px 8px;border-bottom:1px solid #eee;"
            f"color:{color};font-weight:600'>{label}</td>"
            f"</tr>"
        )

    expiry_html = ""
    if overdue or due_soon or upcoming:
        rows_html = "".join(doc_row(d) for d in (overdue + due_soon + upcoming))
        expiry_html = f"""
        <h2 style="color:#c0392b;margin-top:32px">
          {'🚨' if overdue else '⚠️'} Document Expiry Alerts ({len(overdue+due_soon+upcoming)} docs)
        </h2>
        <table style="width:100%;border-collapse:collapse;font-size:13px">
          <thead>
            <tr style="background:#f4f4f4;text-align:left">
              <th style="padding:8px">Bot</th>
              <th style="padding:8px">Doc ID</th>
              <th style="padding:8px">Title</th>
              <th style="padding:8px">Status</th>
            </tr>
          </thead>
          <tbody>{rows_html}</tbody>
        </table>"""
    else:
        expiry_html = '<p style="color:#27ae60">✅ No documents require attention today.</p>'

    # Feedback section
    fb_total = feedback["total"]
    fb_up    = feedback["up"]
    fb_down  = feedback["down"]
    if fb_total > 0:
        pct_pos = round(fb_up / fb_total * 100)
        fb_bar_color = "#27ae60" if pct_pos >= 70 else "#e67e22" if pct_pos >= 40 else "#c0392b"
    else:
        pct_pos = 0
        fb_bar_color = "#95a5a6"

    # Low-confidence questions list
    if low_conf_qs:
        lc_items = "".join(
            f"<li style='margin-bottom:4px;color:#555'>"
            f"<span style='font-size:10px;background:#fdebd0;color:#e67e22;"
            f"padding:1px 5px;border-radius:3px;margin-right:6px'>{q.get('bot','')}</span>"
            f"{q.get('question','')[:120]}</li>"
            for q in low_conf_qs
        )
        lc_html = f"""
        <h2 style="color:#e67e22;margin-top:28px">🔍 Low-Confidence Questions ({flagged} yesterday)</h2>
        <p style="font-size:12px;color:#7f8c8d;margin-top:-8px">
          Questions where the system was unsure — check if the knowledge base needs updating.
        </p>
        <ul style="font-size:13px;line-height:1.7;padding-left:18px">{lc_items}</ul>"""
    else:
        lc_html = (
            '<p style="color:#27ae60;margin-top:20px">✅ No low-confidence answers yesterday.</p>'
            if total > 0 else ""
        )

    # 7-day sparkline as HTML bar chart
    max_count = max((c for _, c in trend), default=1) or 1
    bar_cells = ""
    for label, count in trend:
        bar_h = max(4, round(count / max_count * 48))
        is_yesterday = (label == YESTERDAY.strftime("%a %-d"))
        bar_color = "#1a3a5c" if is_yesterday else "#a8c0d6"
        bar_cells += (
            f"<td style='text-align:center;vertical-align:bottom;padding:0 3px;width:40px'>"
            f"<div style='height:{bar_h}px;background:{bar_color};border-radius:3px 3px 0 0;"
            f"{'outline:2px solid #1a3a5c;' if is_yesterday else ''}'></div>"
            f"<div style='font-size:9px;color:#95a5a6;margin-top:3px'>{count}</div>"
            f"<div style='font-size:9px;color:#7f8c8d'>{label}</div>"
            f"</td>"
        )

    trend_html = f"""
    <h2 style="color:#1a3a5c;margin-top:28px">📈 7-Day Query Trend</h2>
    <table style="border-collapse:collapse;margin:8px 0">
      <tr style="vertical-align:bottom">{bar_cells}</tr>
    </table>
    <p style="font-size:11px;color:#95a5a6;margin-top:2px">
      Darker bar = yesterday. All times IST.
    </p>"""

    html = f"""
    <!DOCTYPE html>
    <html>
    <body style="font-family:Arial,sans-serif;max-width:700px;margin:0 auto;padding:20px;color:#2c3e50">
      <div style="background:#1a3a5c;color:#fff;padding:16px 20px;border-radius:8px 8px 0 0">
        <h1 style="margin:0;font-size:20px">CCS RAG Compliance Assistant</h1>
        <p style="margin:4px 0 0;opacity:.8;font-size:13px">Daily Report — {DATE_STR}</p>
      </div>

      <div style="background:#f8f9fa;padding:16px 20px;border:1px solid #ddd;border-top:0">

        <!-- Query stats -->
        <h2 style="color:#1a3a5c;margin-top:8px">📊 Yesterday's Query Stats ({YESTERDAY.strftime('%d %b')})</h2>
        <table style="width:100%;border-collapse:collapse">
          <tr>
            <td style="padding:10px;background:#fff;border:1px solid #ddd;border-radius:6px;text-align:center;width:22%">
              <div style="font-size:28px;font-weight:700;color:#1a3a5c">{total}</div>
              <div style="font-size:11px;color:#7f8c8d;margin-top:2px">Total Queries</div>
            </td>
            <td style="width:3%"></td>
            <td style="padding:10px;background:#fff;border:1px solid #ddd;border-radius:6px;text-align:center;width:22%">
              <div style="font-size:28px;font-weight:700;color:#1a7f4b">{hc_total}</div>
              <div style="font-size:11px;color:#7f8c8d;margin-top:2px">Healthcare</div>
            </td>
            <td style="width:3%"></td>
            <td style="padding:10px;background:#fff;border:1px solid #ddd;border-radius:6px;text-align:center;width:22%">
              <div style="font-size:28px;font-weight:700;color:#8e44ad">{re_total}</div>
              <div style="font-size:11px;color:#7f8c8d;margin-top:2px">RERA</div>
            </td>
            <td style="width:3%"></td>
            <td style="padding:10px;background:#fff;border:1px solid #ddd;border-radius:6px;text-align:center;width:22%">
              <div style="font-size:28px;font-weight:700;color:#e67e22">{flagged}</div>
              <div style="font-size:11px;color:#7f8c8d;margin-top:2px">Low Confidence</div>
            </td>
          </tr>
        </table>

        <!-- Engagement stats -->
        <h2 style="color:#1a3a5c;margin-top:28px">🤝 User Engagement</h2>
        <table style="width:100%;border-collapse:collapse">
          <tr>
            <!-- Unique users -->
            <td style="padding:10px;background:#fff;border:1px solid #ddd;border-radius:6px;text-align:center;width:22%">
              <div style="font-size:28px;font-weight:700;color:#2980b9">{sessions['unique_users']}</div>
              <div style="font-size:11px;color:#7f8c8d;margin-top:2px">Unique Users</div>
            </td>
            <td style="width:3%"></td>
            <!-- Benchmarks saved -->
            <td style="padding:10px;background:#fff;border:1px solid #ddd;border-radius:6px;text-align:center;width:22%">
              <div style="font-size:28px;font-weight:700;color:#16a085">{bookmarks['total']}</div>
              <div style="font-size:11px;color:#7f8c8d;margin-top:2px">Answers Saved</div>
            </td>
            <td style="width:3%"></td>
            <!-- Thumbs up -->
            <td style="padding:10px;background:#fff;border:1px solid #ddd;border-radius:6px;text-align:center;width:22%">
              <div style="font-size:24px;font-weight:700;color:#27ae60">👍 {fb_up}</div>
              <div style="font-size:11px;color:#7f8c8d;margin-top:2px">Helpful</div>
            </td>
            <td style="width:3%"></td>
            <!-- Thumbs down -->
            <td style="padding:10px;background:#fff;border:1px solid #ddd;border-radius:6px;text-align:center;width:22%">
              <div style="font-size:24px;font-weight:700;color:#c0392b">👎 {fb_down}</div>
              <div style="font-size:11px;color:#7f8c8d;margin-top:2px">Needs Work</div>
            </td>
          </tr>
        </table>

        <!-- Satisfaction bar -->
        {f'''
        <div style="margin-top:12px;background:#fff;border:1px solid #ddd;border-radius:6px;padding:10px 14px">
          <div style="display:flex;justify-content:space-between;font-size:12px;color:#7f8c8d;margin-bottom:5px">
            <span>Answer Satisfaction</span>
            <span style="color:{fb_bar_color};font-weight:600">{pct_pos}% positive</span>
          </div>
          <div style="background:#eee;border-radius:4px;height:8px">
            <div style="background:{fb_bar_color};width:{pct_pos}%;height:8px;border-radius:4px"></div>
          </div>
          <div style="font-size:10px;color:#95a5a6;margin-top:4px">
            Based on {fb_total} feedback responses
          </div>
        </div>''' if fb_total > 0 else
        '<p style="color:#95a5a6;font-size:12px;margin-top:8px">No feedback received yesterday.</p>'}

        <!-- Bookmarks by domain -->
        {f'''
        <p style="font-size:12px;color:#7f8c8d;margin-top:8px">
          Saved answers: <strong>{bookmarks["healthcare"]}</strong> Healthcare &nbsp;·&nbsp;
          <strong>{bookmarks["rera"]}</strong> RERA
        </p>''' if bookmarks["total"] > 0 else ""}

        <!-- 7-day trend -->
        {trend_html}

        <!-- Low-confidence questions -->
        {lc_html}

        <!-- Document expiry -->
        {expiry_html}

        <p style="margin-top:32px;font-size:11px;color:#95a5a6;border-top:1px solid #eee;padding-top:12px">
          Sent by GitHub Actions · CCS RAG Compliance Assistant ·
          <a href="https://github.com/safimohd/CCS_RAG_Compliance_Assistant" style="color:#95a5a6">GitHub Repo</a>
        </p>
      </div>
    </body>
    </html>
    """
    return html


# ── 9. Send email via SMTP ──────────────────────────────────────────────────────
def send_email(html_body, subject):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = FROM_EMAIL
    msg["To"]      = TO_EMAIL
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(SMTP_USER, SMTP_PASS)
        smtp.sendmail(FROM_EMAIL, TO_EMAIL, msg.as_string())
    print(f"Email sent to {TO_EMAIL}")


# ── Main ────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"Running daily report for {YESTERDAY} (sending on {TODAY})...")

    stats        = fetch_query_stats()
    expiring     = fetch_expiring_docs()
    feedback     = fetch_feedback_stats()
    bookmarks    = fetch_bookmark_stats()
    sessions     = fetch_session_stats()
    low_conf_qs  = fetch_low_conf_questions()
    trend        = fetch_weekly_trend()

    print(
        f"  Queries: {len(stats)} | Expiring docs: {len(expiring)} | "
        f"Feedback: {feedback['up']}up/{feedback['down']}down | "
        f"Bookmarks: {bookmarks['total']} | Users: {sessions['unique_users']}"
    )

    flagged = sum(1 for r in stats if r["flagged_low_conf"])
    subject = (
        f"[CCS RAG] Daily Report {DATE_STR} — "
        f"{len(stats)} queries · {feedback['up']}👍 {feedback['down']}👎 · "
        f"{bookmarks['total']} saved"
    )
    if any(d["_days_until"] < 0 for d in expiring):
        subject = "🚨 " + subject
    elif expiring:
        subject = "⚠️ " + subject

    html = build_email(stats, expiring, feedback, bookmarks, sessions, low_conf_qs, trend)
    send_email(html, subject)
    print("Done.")
