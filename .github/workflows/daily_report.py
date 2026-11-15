#!/usr/bin/env python3
"""
Daily Report — Query Stats + Expiry Alerts
Sends one email per enabled domain (RERA, Healthcare).

Required GitHub secrets:
    SUPABASE_URL            Supabase project REST URL
    SUPABASE_SERVICE_KEY    Service-role key (bypasses RLS)
    SMTP_HOST               e.g. smtp.gmail.com
    SMTP_PORT               e.g. 587
    SMTP_USER               Sender Gmail / SMTP account
    SMTP_PASS               App password for SMTP_USER
    REPORT_FROM_EMAIL       From address in the email header
    REPORT_TO_EMAIL         Recipient (can be same as SMTP_USER)

Optional toggle secrets (set to "false" to silence that domain):
    RERA_REPORT_ENABLED         default: true
    HEALTHCARE_REPORT_ENABLED   default: true

For manual workflow_dispatch runs the UI checkboxes override these secrets.
"""
import os
import smtplib
import sys
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests

# ---------------------------------------------------------------------------
# Config from environment
# ---------------------------------------------------------------------------
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
SMTP_HOST    = os.environ.get("SMTP_HOST", "")
SMTP_PORT    = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER    = os.environ.get("SMTP_USER", "")
SMTP_PASS    = os.environ.get("SMTP_PASS", "")
FROM_EMAIL   = os.environ.get("REPORT_FROM_EMAIL", SMTP_USER)
TO_EMAIL     = os.environ.get("REPORT_TO_EMAIL", "")

SEND_RERA        = os.environ.get("SEND_RERA", "true").strip().lower() == "true"
SEND_HEALTHCARE  = os.environ.get("SEND_HEALTHCARE", "true").strip().lower() == "true"

HDRS = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}

IST = timezone(timedelta(hours=5, minutes=30))


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------
def _get(path: str, params: dict) -> list:
    try:
        r = requests.get(f"{SUPABASE_URL}/rest/v1/{path}",
                         headers=HDRS, params=params, timeout=15)
        return r.json() if r.ok else []
    except Exception as e:
        print(f"  [warn] GET {path} failed: {e}", file=sys.stderr)
        return []


def fetch_queries(domain: str, since: str) -> list:
    return _get("queries", {
        "select": "id,created_at,bot,question,confidence,flagged_low_conf",
        "bot": f"eq.{domain}",
        "created_at": f"gte.{since}T00:00:00",
        "order": "created_at.desc",
    })


def fetch_sessions(domain: str, since: str) -> list:
    return _get("sessions", {
        "select": "id,user_id,domain,user_type,title,message_count,updated_at",
        "domain": f"eq.{domain}",
        "updated_at": f"gte.{since}T00:00:00",
        "order": "updated_at.desc",
    })


def fetch_kb_documents(domain: str) -> list:
    return _get("kb_documents", {
        "select": "bot,doc_id,title,status,expected_update_date,alert_days_before,source_url",
        "bot": f"eq.{domain}",
        "order": "expected_update_date.asc",
    })


def fetch_exclusions(domain: str) -> list:
    return _get("doc_exclusions", {
        "select": "bot,doc_id,excluded_by,excluded_by_email,excluded_at,reason,status,expires_at",
        "bot": f"eq.{domain}",
        "order": "excluded_at.desc",
    })


# ---------------------------------------------------------------------------
# Report builder
# ---------------------------------------------------------------------------
DOMAIN_DISPLAY = {"RERA": "RERA", "Healthcare": "Healthcare"}
DOMAIN_COLOR   = {"RERA": "#1e3a5f", "Healthcare": "#0f5132"}
DOMAIN_ACCENT  = {"RERA": "#2563eb", "Healthcare": "#198754"}


def _expiry_rows(docs: list, now: datetime) -> list:
    """Return docs that fall within their alert window (or already past due)."""
    rows = []
    for doc in docs:
        exp = doc.get("expected_update_date")
        if not exp:
            continue
        try:
            exp_dt = datetime.fromisoformat(exp.rstrip("Z")).replace(
                tzinfo=timezone.utc)
            days_left = (exp_dt.date() - now.astimezone(IST).date()).days
            alert_days = int(doc.get("alert_days_before") or 30)
            if days_left <= alert_days:
                rows.append({**doc, "_days_left": days_left})
        except Exception:
            pass
    return sorted(rows, key=lambda d: d["_days_left"])


def _badge(days: int) -> str:
    if days < 0:
        return (f"<span style='background:#dc3545;color:#fff;padding:2px 7px;"
                f"border-radius:4px;font-size:.75rem;font-weight:600'>"
                f"OVERDUE {abs(days)}d</span>")
    if days == 0:
        return (f"<span style='background:#fd7e14;color:#fff;padding:2px 7px;"
                f"border-radius:4px;font-size:.75rem;font-weight:600'>"
                f"DUE TODAY</span>")
    if days <= 7:
        return (f"<span style='background:#ffc107;color:#212529;padding:2px 7px;"
                f"border-radius:4px;font-size:.75rem;font-weight:600'>"
                f"{days}d left</span>")
    return (f"<span style='background:#0d6efd;color:#fff;padding:2px 7px;"
            f"border-radius:4px;font-size:.75rem;font-weight:600'>"
            f"{days}d left</span>")


def build_html(domain: str) -> tuple[str, str]:
    """Return (subject_line, html_body) for the given domain."""
    now       = datetime.now(tz=IST)
    today_str = now.strftime("%Y-%m-%d")
    week_str  = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    display   = DOMAIN_DISPLAY.get(domain, domain)
    color     = DOMAIN_COLOR.get(domain, "#1a1a2e")
    accent    = DOMAIN_ACCENT.get(domain, "#0d6efd")

    # --- fetch data ---------------------------------------------------------
    today_q   = fetch_queries(domain, today_str)
    week_q    = fetch_queries(domain, week_str)
    today_s   = fetch_sessions(domain, today_str)
    docs      = fetch_kb_documents(domain)
    excl      = fetch_exclusions(domain)
    expiring  = _expiry_rows(docs, now)

    low_conf_today = [q for q in today_q if q.get("flagged_low_conf")]
    week_sessions  = fetch_sessions(domain, week_str)

    unique_users_today = len({s.get("user_id") for s in today_s if s.get("user_id")})
    total_msgs_today   = sum(int(s.get("message_count") or 0) for s in today_s)

    expiry_alert_count = len(expiring)
    active_excl        = [e for e in excl if e.get("status") == "excluded"]
    expired_excl       = [e for e in excl if e.get("status") == "expired"]

    # --- subject line -------------------------------------------------------
    subject = (
        f"[{display}] Daily Report {now.strftime('%b %d, %Y')} "
        f"| {len(today_q)} queries"
        + (f" · {expiry_alert_count} expiry alerts" if expiry_alert_count else "")
        + (f" · {len(low_conf_today)} low-conf" if low_conf_today else "")
    )

    # --- helper: section block ----------------------------------------------
    def section(title: str, body: str) -> str:
        return f"""
        <div style="margin:24px 0 0;background:#fff;border-radius:8px;
                    box-shadow:0 1px 3px rgba(0,0,0,.08);overflow:hidden">
          <div style="background:{color};color:#fff;padding:10px 18px;
                      font-size:.8rem;font-weight:700;letter-spacing:.06em;
                      text-transform:uppercase">{title}</div>
          <div style="padding:16px 18px">{body}</div>
        </div>"""

    def stat_row(*stats) -> str:
        """stats = list of (value, label) tuples, up to 4."""
        cells = "".join(
            f"<td style='text-align:center;padding:8px 16px;border-right:1px solid #e5e7eb'>"
            f"<div style='font-size:1.5rem;font-weight:700;color:{accent}'>{v}</div>"
            f"<div style='font-size:.7rem;color:#6b7280;margin-top:2px'>{l}</div></td>"
            for v, l in stats
        )
        return (f"<table style='width:100%;border-collapse:collapse;"
                f"border:1px solid #e5e7eb;border-radius:6px;overflow:hidden'>"
                f"<tr>{cells}</tr></table>")

    # --- build sections -----------------------------------------------------
    # 1. Query stats
    q_body = stat_row(
        (len(today_q),        "Queries today"),
        (len(week_q),         "Queries this week"),
        (len(low_conf_today), "Low-confidence today"),
        (unique_users_today,  "Unique users today"),
    )
    if today_q:
        rows_html = "".join(
            f"<tr style='border-bottom:1px solid #f3f4f6'>"
            f"<td style='padding:5px 8px;font-size:.75rem;color:#374151'>"
            f"{'⚠️ ' if q.get('flagged_low_conf') else ''}"
            f"{(q.get('question') or '')[:90]}{'…' if len(q.get('question',''))>90 else ''}</td>"
            f"<td style='padding:5px 8px;font-size:.7rem;color:#9ca3af;white-space:nowrap'>"
            f"{(q.get('created_at','')[:16]).replace('T',' ')} IST</td></tr>"
            for q in today_q[:15]
        )
        q_body += (
            f"<div style='margin-top:14px;font-size:.72rem;color:#6b7280;"
            f"font-weight:600;text-transform:uppercase;letter-spacing:.05em'>"
            f"Today's questions (latest {min(15, len(today_q))})</div>"
            f"<table style='width:100%;border-collapse:collapse;margin-top:4px'>"
            f"{rows_html}</table>"
        )
    else:
        q_body += "<p style='color:#9ca3af;font-size:.85rem;margin:12px 0 0'>No queries yet today.</p>"

    # 2. Session stats
    s_body = stat_row(
        (len(today_s),        "Sessions today"),
        (len(week_sessions),  "Sessions this week"),
        (total_msgs_today,    "Messages today"),
        (unique_users_today,  "Active users today"),
    )

    # 3. Expiry alerts
    if expiring:
        exp_rows = "".join(
            f"<tr style='border-bottom:1px solid #f3f4f6'>"
            f"<td style='padding:5px 8px;font-size:.77rem;font-weight:500'>"
            f"{doc.get('title', doc.get('doc_id',''))[:60]}</td>"
            f"<td style='padding:5px 8px;text-align:center'>{_badge(doc['_days_left'])}</td>"
            f"<td style='padding:5px 8px;font-size:.72rem;color:#6b7280'>"
            f"{(doc.get('expected_update_date') or '')[:10]}</td></tr>"
            for doc in expiring
        )
        exp_body = (
            f"<table style='width:100%;border-collapse:collapse'>"
            f"<thead><tr style='background:#f9fafb'>"
            f"<th style='text-align:left;padding:5px 8px;font-size:.7rem;color:#374151'>Document</th>"
            f"<th style='padding:5px 8px;font-size:.7rem;color:#374151'>Status</th>"
            f"<th style='padding:5px 8px;font-size:.7rem;color:#374151'>Due Date</th>"
            f"</tr></thead><tbody>{exp_rows}</tbody></table>"
        )
    else:
        exp_body = "<p style='color:#198754;font-size:.85rem;margin:4px 0'>✅ No documents approaching expiry.</p>"

    # 4. Exclusions / active overrides
    if active_excl or expired_excl:
        excl_rows = "".join(
            f"<tr style='border-bottom:1px solid #f3f4f6'>"
            f"<td style='padding:5px 8px;font-size:.75rem'>{e.get('doc_id','')[:40]}</td>"
            f"<td style='padding:5px 8px;font-size:.72rem;color:#6b7280'>"
            f"{'⏳ Expired' if e.get('status')=='expired' else '🚫 Excluded'}</td>"
            f"<td style='padding:5px 8px;font-size:.72rem;color:#6b7280'>"
            f"{e.get('reason','')[:50]}</td>"
            f"<td style='padding:5px 8px;font-size:.72rem;color:#6b7280'>"
            f"{(e.get('expires_at') or '')[:10]}</td></tr>"
            for e in (active_excl + expired_excl)[:20]
        )
        excl_body = (
            f"<table style='width:100%;border-collapse:collapse'>"
            f"<thead><tr style='background:#f9fafb'>"
            f"<th style='text-align:left;padding:5px 8px;font-size:.7rem;color:#374151'>Doc ID</th>"
            f"<th style='padding:5px 8px;font-size:.7rem;color:#374151'>Type</th>"
            f"<th style='padding:5px 8px;font-size:.7rem;color:#374151'>Reason</th>"
            f"<th style='padding:5px 8px;font-size:.7rem;color:#374151'>Expires</th>"
            f"</tr></thead><tbody>{excl_rows}</tbody></table>"
        )
    else:
        excl_body = "<p style='color:#198754;font-size:.85rem;margin:4px 0'>✅ No active exclusions or overrides.</p>"

    # --- assemble full HTML -------------------------------------------------
    html = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{subject}</title></head>
<body style="margin:0;padding:0;background:#f3f4f6;font-family:-apple-system,BlinkMacSystemFont,
             'Segoe UI',Roboto,sans-serif">
  <table width="100%" cellpadding="0" cellspacing="0">
    <tr><td align="center" style="padding:24px 12px">
      <table width="600" cellpadding="0" cellspacing="0"
             style="max-width:600px;width:100%">

        <!-- Header -->
        <tr><td style="background:{color};border-radius:10px 10px 0 0;
                       padding:22px 24px 18px">
          <div style="color:rgba(255,255,255,.65);font-size:.7rem;font-weight:600;
                      text-transform:uppercase;letter-spacing:.08em">
            ClaritAI · Daily Report</div>
          <div style="color:#fff;font-size:1.35rem;font-weight:700;margin-top:4px">
            {display} Compliance Assistant</div>
          <div style="color:rgba(255,255,255,.8);font-size:.82rem;margin-top:2px">
            {now.strftime("%A, %B %d, %Y")} &nbsp;·&nbsp; Generated {now.strftime("%I:%M %p IST")}</div>
        </td></tr>

        <!-- Body -->
        <tr><td style="background:#f3f4f6;padding:0 0 24px">

          {section("Query Activity", q_body)}
          {section("Session Activity", s_body)}
          {section(f"Document Expiry Alerts ({expiry_alert_count})", exp_body)}
          {section("Active Exclusions &amp; Overrides", excl_body)}

          <!-- Footer -->
          <div style="text-align:center;color:#9ca3af;font-size:.7rem;margin-top:20px">
            Automated report from ClaritAI · IIM Bangalore CCS Project<br>
            To stop receiving this report, disable it in the GitHub Actions workflow.
          </div>

        </td></tr>
      </table>
    </td></tr>
  </table>
</body></html>"""

    return subject, html


# ---------------------------------------------------------------------------
# Email sender
# ---------------------------------------------------------------------------
def send_email(to: str, subject: str, html: str) -> None:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = FROM_EMAIL
    msg["To"]      = to
    msg.attach(MIMEText(html, "html", "utf-8"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
        s.ehlo()
        s.starttls()
        s.login(SMTP_USER, SMTP_PASS)
        s.sendmail(FROM_EMAIL, [to], msg.as_string())
    print(f"  ✓ Sent: {subject[:70]}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR: SUPABASE_URL / SUPABASE_SERVICE_KEY not set", file=sys.stderr)
        sys.exit(1)
    if not all([SMTP_HOST, SMTP_USER, SMTP_PASS, TO_EMAIL]):
        print("ERROR: SMTP_HOST / SMTP_USER / SMTP_PASS / REPORT_TO_EMAIL not set",
              file=sys.stderr)
        sys.exit(1)

    sent = 0
    for domain, enabled in [("RERA", SEND_RERA), ("Healthcare", SEND_HEALTHCARE)]:
        if not enabled:
            print(f"  – {domain} report disabled, skipping.")
            continue
        print(f"Building {domain} report …")
        subject, html = build_html(domain)
        send_email(TO_EMAIL, subject, html)
        sent += 1

    if sent == 0:
        print("WARNING: Both reports are disabled — nothing was sent.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
