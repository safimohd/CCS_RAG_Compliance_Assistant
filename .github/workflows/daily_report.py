"""
Daily report script for the CCS RAG Compliance Assistant.
Runs at 08:00 IST every morning via GitHub Actions.

What it does:
  1. Pulls yesterday's query stats from Supabase (queries table)
  2. Pulls documents expiring within their alert window (kb_documents table)
  3. Sends a formatted HTML email via SMTP

All config comes from environment variables (GitHub Secrets).
"""
import os, json, smtplib, datetime, sys, textwrap
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


# ── 1. Query stats for yesterday ────────────────────────────────────────────────
def fetch_query_stats():
    params = {
        "select": "bot,flagged_low_conf",
        "created_at": f"gte.{YESTERDAY.isoformat()}T00:00:00",
        # Supabase: created_at < today
        "and": f"(created_at.lt.{TODAY.isoformat()}T00:00:00)",
    }
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/queries",
        headers=HDRS,
        params={
            "select": "bot,flagged_low_conf",
            "created_at": f"gte.{YESTERDAY.isoformat()}T00:00:00",
            "created_at_lt": f"lt.{TODAY.isoformat()}T00:00:00",
        },
        timeout=15,
    )
    # Use PostgREST range filter properly
    r2 = requests.get(
        f"{SUPABASE_URL}/rest/v1/queries"
        f"?select=bot,flagged_low_conf"
        f"&created_at=gte.{YESTERDAY.isoformat()}T00:00:00"
        f"&created_at=lt.{TODAY.isoformat()}T00:00:00",
        headers={**HDRS, "Prefer": "count=exact"},
        timeout=15,
    )
    if r2.status_code != 200:
        print(f"WARNING: query stats fetch failed ({r2.status_code}): {r2.text[:200]}")
        return []
    return r2.json()


# ── 2. Documents expiring soon ─────────────────────────────────────────────────
def fetch_expiring_docs():
    """
    Returns docs where:
        expected_update_date <= TODAY + alert_days_before
    and status = 'current'
    """
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/kb_documents"
        f"?select=bot,doc_id,title,expected_update_date,alert_days_before,source_url"
        f"&status=eq.current"
        f"&expected_update_date=not.is.null"
        f"&order=expected_update_date.asc",
        headers=HDRS,
        timeout=15,
    )
    if r.status_code != 200:
        print(f"WARNING: kb_documents fetch failed ({r.status_code}): {r.text[:200]}")
        return []
    all_docs = r.json()
    # Filter client-side: alert when update_date <= today + alert_days_before
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


# ── 3. Build HTML email ─────────────────────────────────────────────────────────
def build_email(stats, expiring):
    total     = len(stats)
    hc_total  = sum(1 for r in stats if r["bot"] == "Healthcare")
    re_total  = sum(1 for r in stats if r["bot"] == "RERA")
    flagged   = sum(1 for r in stats if r["flagged_low_conf"])

    # Expiry sections
    overdue   = [d for d in expiring if d["_days_until"] < 0]
    due_soon  = [d for d in expiring if 0 <= d["_days_until"] <= 7]
    upcoming  = [d for d in expiring if 7 < d["_days_until"] <= 30]

    def doc_row(d):
        days = d["_days_until"]
        label = ("OVERDUE" if days < 0
                 else f"Due in {days}d" if days <= 7
                 else f"Due in {days}d")
        color = "#c0392b" if days < 0 else "#e67e22" if days <= 7 else "#7f8c8d"
        url   = d.get("source_url") or "#"
        link  = f'<a href="{url}" style="color:#2c3e50">{d["title"][:70]}</a>' if url != "#" else d["title"][:70]
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

    html = f"""
    <!DOCTYPE html>
    <html>
    <body style="font-family:Arial,sans-serif;max-width:700px;margin:0 auto;padding:20px;color:#2c3e50">
      <div style="background:#1a3a5c;color:#fff;padding:16px 20px;border-radius:8px 8px 0 0">
        <h1 style="margin:0;font-size:20px">CCS RAG Compliance Assistant</h1>
        <p style="margin:4px 0 0;opacity:.8;font-size:13px">Daily Report — {DATE_STR}</p>
      </div>

      <div style="background:#f8f9fa;padding:16px 20px;border:1px solid #ddd;border-top:0">

        <h2 style="color:#1a3a5c;margin-top:8px">📊 Yesterday's Query Stats ({YESTERDAY.strftime('%d %b')})</h2>
        <table style="width:100%;border-collapse:collapse">
          <tr>
            <td style="padding:10px;background:#fff;border:1px solid #ddd;border-radius:6px;text-align:center;width:25%">
              <div style="font-size:28px;font-weight:700;color:#1a3a5c">{total}</div>
              <div style="font-size:11px;color:#7f8c8d;margin-top:2px">Total Queries</div>
            </td>
            <td style="width:4%"></td>
            <td style="padding:10px;background:#fff;border:1px solid #ddd;border-radius:6px;text-align:center;width:25%">
              <div style="font-size:28px;font-weight:700;color:#1a7f4b">{hc_total}</div>
              <div style="font-size:11px;color:#7f8c8d;margin-top:2px">Healthcare</div>
            </td>
            <td style="width:4%"></td>
            <td style="padding:10px;background:#fff;border:1px solid #ddd;border-radius:6px;text-align:center;width:25%">
              <div style="font-size:28px;font-weight:700;color:#8e44ad">{re_total}</div>
              <div style="font-size:11px;color:#7f8c8d;margin-top:2px">RERA</div>
            </td>
            <td style="width:4%"></td>
            <td style="padding:10px;background:#fff;border:1px solid #ddd;border-radius:6px;text-align:center;width:25%">
              <div style="font-size:28px;font-weight:700;color:#e67e22">{flagged}</div>
              <div style="font-size:11px;color:#7f8c8d;margin-top:2px">Low Confidence</div>
            </td>
          </tr>
        </table>

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


# ── 4. Send email via SMTP ──────────────────────────────────────────────────────
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
    stats    = fetch_query_stats()
    expiring = fetch_expiring_docs()
    print(f"  Queries yesterday: {len(stats)} | Expiring docs: {len(expiring)}")

    html    = build_email(stats, expiring)
    flagged = sum(1 for r in stats if r["flagged_low_conf"])
    subject = f"[CCS RAG] Daily Report {DATE_STR} — {len(stats)} queries, {flagged} flagged"
    if any(d["_days_until"] < 0 for d in expiring):
        subject = "🚨 " + subject
    elif expiring:
        subject = "⚠️ " + subject

    send_email(html, subject)
    print("Done.")
