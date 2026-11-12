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


def _log_rera_query(question: str, answer_snippet: str, session_id: str) -> None:
    """Log a RERA user query to the queries table in Supabase."""
    url = _secret("SUPABASE_URL") or os.environ.get("SUPABASE_URL", "")
    key = _secret("SUPABASE_KEY") or os.environ.get("SUPABASE_KEY", "")
    if not url or not key:
        return
    try:
        import requests as _req
        _req.post(
            f"{url}/rest/v1/queries",
            headers={
                "apikey": key,
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "Prefer": "return=minimal",
            },
            json={
                "bot": "RERA",
                "user_role": "user",
                "question": question[:1000],
                "answer_snippet": answer_snippet[:500],
                "session_id": session_id,
                "confidence": None,
                "flagged_low_conf": False,
            },
            timeout=5,
        )
    except Exception:
        pass  # Fire-and-forget; never block the chat


def _fetch_rera_user_history(limit: int = 15) -> list:
    """Fetch recent RERA user queries from the queries table (legacy, kept for internal use)."""
    url = _secret("SUPABASE_URL") or os.environ.get("SUPABASE_URL", "")
    key = _secret("SUPABASE_KEY") or os.environ.get("SUPABASE_KEY", "")
    if not url or not key:
        return []
    try:
        import requests as _req
        resp = _req.get(
            f"{url}/rest/v1/queries",
            headers={"apikey": key, "Authorization": f"Bearer {key}"},
            params={
                "bot": "eq.RERA",
                "user_role": "eq.user",
                "select": "question,created_at,session_id",
                "order": "created_at.desc",
                "limit": str(limit),
            },
            timeout=5,
        )
        if resp.ok:
            rows = resp.json()
            seen, out = set(), []
            for r in rows:
                q = (r.get("question") or "").strip()
                if q and q not in seen:
                    seen.add(q)
                    out.append({
                        "question": q,
                        "created_at": r.get("created_at", ""),
                        "session_id": r.get("session_id", ""),
                    })
            return out
        return []
    except Exception:
        return []


def _fetch_rera_sessions(limit: int = 5) -> list:
    """Return up to `limit` distinct recent sessions with a preview and message count.

    Groups raw query rows by session_id in Python since PostgREST has no GROUP BY.
    Each session entry: {session_id, preview, created_at, count}
    """
    url = _secret("SUPABASE_URL") or os.environ.get("SUPABASE_URL", "")
    key = _secret("SUPABASE_KEY") or os.environ.get("SUPABASE_KEY", "")
    if not url or not key:
        return []
    try:
        import requests as _req
        # Fetch more rows than needed so we can collect enough distinct sessions
        resp = _req.get(
            f"{url}/rest/v1/queries",
            headers={"apikey": key, "Authorization": f"Bearer {key}"},
            params={
                "bot": "eq.RERA",
                "user_role": "eq.user",
                "select": "question,created_at,session_id",
                "order": "created_at.desc",
                "limit": "100",
            },
            timeout=5,
        )
        if not resp.ok:
            return []
        rows = resp.json()
        # Group by session_id; keep the earliest question as the session preview
        from collections import OrderedDict
        sessions: dict = OrderedDict()
        for r in rows:
            sid = (r.get("session_id") or "").strip()
            if not sid:
                continue
            q = (r.get("question") or "").strip()
            ts = r.get("created_at", "")
            if sid not in sessions:
                sessions[sid] = {"session_id": sid, "preview": q, "created_at": ts, "count": 1}
            else:
                sessions[sid]["count"] += 1
                # Keep the earliest timestamp as the session start time
                if ts < sessions[sid]["created_at"]:
                    sessions[sid]["created_at"] = ts
                    sessions[sid]["preview"] = q
        # sessions are already newest-first (ordered by created_at desc on first seen)
        return list(sessions.values())[:limit]
    except Exception:
        return []


def _fetch_session_messages(session_id: str) -> list:
    """Fetch all Q&A pairs for a session, oldest first, to restore chat history."""
    url = _secret("SUPABASE_URL") or os.environ.get("SUPABASE_URL", "")
    key = _secret("SUPABASE_KEY") or os.environ.get("SUPABASE_KEY", "")
    if not url or not key:
        return []
    try:
        import requests as _req
        resp = _req.get(
            f"{url}/rest/v1/queries",
            headers={"apikey": key, "Authorization": f"Bearer {key}"},
            params={
                "session_id": f"eq.{session_id}",
                "bot": "eq.RERA",
                "user_role": "eq.user",
                "select": "question,answer_snippet,created_at",
                "order": "created_at.asc",
            },
            timeout=5,
        )
        if resp.ok:
            return resp.json()
        return []
    except Exception:
        return []


# ── Known Bangalore landmarks for nearby search ──────────────────────────────
_KNOWN_LOCATIONS: dict = {
    "iim bangalore": (13.0218, 77.5671),
    "iimb":          (13.0218, 77.5671),
    "iim b":         (13.0218, 77.5671),
    "whitefield":    (12.9698, 77.7500),
    "koramangala":   (12.9352, 77.6245),
    "indiranagar":   (12.9784, 77.6408),
    "jayanagar":     (12.9308, 77.5839),
    "electronic city": (12.8456, 77.6603),
    "hebbal":        (13.0358, 77.5970),
    "marathahalli":  (12.9591, 77.6974),
    "sarjapur":      (12.8714, 77.7853),
    "yelahanka":     (13.1004, 77.5963),
    "jp nagar":      (12.9077, 77.5852),
    "bannerghatta":  (12.8654, 77.5992),
    "rajajinagar":   (12.9906, 77.5518),
    "malleshwaram":  (13.0036, 77.5704),
    "sadashivanagar": (13.0106, 77.5811),
    "basavanagudi":  (12.9420, 77.5758),
    "btm layout":    (12.9155, 77.6109),
    "hsr layout":    (12.9116, 77.6474),
    "hennur":        (13.0441, 77.6336),
    "devanahalli":   (13.2470, 77.7122),
    "anekal":        (12.7142, 77.6960),
    "yeshwanthpur":  (13.0206, 77.5476),
    "banaswadi":     (13.0160, 77.6471),
    "bellandur":     (12.9253, 77.6763),
    "domlur":        (12.9607, 77.6388),
    "kr puram":      (13.0031, 77.6952),
    "kengeri":       (12.9112, 77.4845),
    "nagarbhavi":    (12.9681, 77.5072),
    "mysore":        (12.2958, 76.6394),
    "mangalore":     (12.9141, 74.8560),
    "hubli":         (15.3647, 75.1240),
    "dharwad":       (15.4573, 75.0152),
    "tumkur":        (13.3379, 77.1173),
}


def _geocode_location(place_name: str):
    """Return (lat, lon) for a place. Checks known landmarks first, then Nominatim OSM."""
    key = place_name.lower().strip()
    for known, coords in _KNOWN_LOCATIONS.items():
        if known in key or key in known:
            return coords
    # Nominatim fallback (free OSM geocoding, no API key needed)
    try:
        requests = _ensure("requests")
        resp = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={
                "q": f"{place_name}, Karnataka, India",
                "format": "json",
                "limit": 1,
                "countrycodes": "in",
            },
            headers={"User-Agent": "RERA-Compliance-Assistant/1.0"},
            timeout=5,
        )
        if resp.ok and resp.json():
            hit = resp.json()[0]
            return (float(hit["lat"]), float(hit["lon"]))
    except Exception:
        pass
    return None


def _search_nearby_projects(lat: float, lon: float, radius_km: float = 7,
                             status_filter: str = None, limit: int = 15) -> list:
    """Find RERA projects within radius_km of (lat, lon) using bounding box + Haversine."""
    import math
    requests = _ensure("requests")
    url_base = _secret("SUPABASE_URL") or os.environ.get("SUPABASE_URL", "")
    key = _secret("SUPABASE_KEY") or os.environ.get("SUPABASE_KEY", "")
    if not url_base or not key:
        return []

    # Bounding box pre-filter (degrees)
    d_lat = radius_km / 111.0
    d_lon = radius_km / (111.0 * math.cos(math.radians(lat)))
    lat_min, lat_max = lat - d_lat, lat + d_lat
    lon_min, lon_max = lon - d_lon, lon + d_lon

    select = (
        "project_name,promoter_name,status,district,taluk,"
        "ack_number,pin_code,completion_date,lapse_date,"
        "total_units,sold_units,latitude,longitude,detail_url"
    )
    # PostgREST allows duplicate param keys for AND conditions
    qs = (
        f"latitude=gte.{lat_min}&latitude=lte.{lat_max}"
        f"&longitude=gte.{lon_min}&longitude=lte.{lon_max}"
        f"&select={select}&limit=200"
    )
    if status_filter:
        qs += f"&status=ilike.*{status_filter}*"

    try:
        resp = requests.get(
            f"{url_base}/rest/v1/rera_projects?{qs}",
            headers={"apikey": key, "Authorization": f"Bearer {key}"},
            timeout=12,
        )
        if not resp.ok or not isinstance(resp.json(), list):
            return []
        rows = resp.json()
    except Exception:
        return []

    def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        R = 6371.0
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = (math.sin(dlat / 2) ** 2
             + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
             * math.sin(dlon / 2) ** 2)
        return R * 2 * math.asin(math.sqrt(a))

    results = []
    for r in rows:
        try:
            dist = haversine(lat, lon, float(r["latitude"]), float(r["longitude"]))
            if dist <= radius_km:
                r["_dist_km"] = round(dist, 1)
                results.append(r)
        except Exception:
            pass

    results.sort(key=lambda x: x["_dist_km"])
    return results[:limit]


def _format_nearby_rows_for_llm(rows: list, location: str) -> str:
    if not rows:
        return ""
    lines = [f"[Live K-RERA projects near '{location}' — closest first]"]
    for r in rows[:10]:
        dist = r.get("_dist_km", "?")
        sold = r.get("sold_units") or ""
        total = r.get("total_units") or ""
        units = f"{sold}/{total} units sold" if sold and total else ""
        parts = [
            f"{r.get('project_name', 'Unknown')} ({dist} km away)",
            f"Status: {r.get('status', 'unknown')}",
            f"Promoter: {r.get('promoter_name', '')}",
            f"PIN: {r.get('pin_code', '')}",
            f"Completion: {r.get('completion_date', '')}",
        ]
        if units:
            parts.append(units)
        lines.append("  " + " | ".join(p for p in parts if p.split(": ", 1)[-1]))
    lines.append(
        f"\nNote: {len(rows)} RERA-registered project(s) found within 7 km. "
        "Only projects with GPS coordinates in the K-RERA registry are shown (~15% of total)."
    )
    return "\n".join(lines)


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

    # Nearby / location-based search
    msg_lower = msg.lower()
    _nearby_signals = [
        "near me", "nearby", "close to", "near iim", "near whitefield",
        "near koramangala", "near indiranagar", "near hebbal",
        "within", "projects near", "flats near", "apartments near",
        "properties near", "km from", "around me", "in my area",
        "close by", "proximity", "walking distance",
    ]
    if any(sig in msg_lower for sig in _nearby_signals):
        # Try "near <place>" pattern
        m2 = re.search(
            r'near\s+((?:iim\s+bangalore|iimb?|[a-z][a-z\s]{2,20}?))'
            r'(?:\s+within|\s+in\s|\s+\d|\s*[,.]|\s*$)',
            msg_lower,
        )
        if m2:
            return {"query": m2.group(1).strip(), "type": "nearby"}
        # Try "X km from <place>"
        m2 = re.search(r'(?:\d+\s*km\s+from|from)\s+([a-z][a-z\s]{2,25})', msg_lower)
        if m2:
            return {"query": m2.group(1).strip(), "type": "nearby"}
        # Scan for a known landmark in the message
        for loc in _KNOWN_LOCATIONS:
            if loc in msg_lower:
                return {"query": loc, "type": "nearby"}
        # Generic nearby — default to Bangalore CBD
        return {"query": "bangalore", "type": "nearby"}

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
    if "uf_session_id" not in st.session_state:
        import uuid as _uuid_chat
        st.session_state["uf_session_id"] = str(_uuid_chat.uuid4())

    # Pick up a query injected by clicking a "Recent Queries" history button
    _injected = st.session_state.pop("uf_inject_query", None)

    for msg in st.session_state.uf_chat_history:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    user_input = st.chat_input(
        "Ask about any Karnataka property: RERA status, fair price, buy vs rent, builder reputation..."
    )

    # Injected query from history panel takes precedence when chat_input is empty
    if _injected and not user_input:
        user_input = _injected

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
                    if entity["type"] == "nearby" and entity["query"]:
                        _coords = _geocode_location(entity["query"])
                        if _coords:
                            _nearby = _search_nearby_projects(
                                _coords[0], _coords[1], radius_km=7
                            )
                            if _nearby:
                                db_context = _format_nearby_rows_for_llm(
                                    _nearby, entity["query"]
                                )
                    elif entity["type"] != "none" and entity["query"]:
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
                # Log this query to Supabase for user session history
                _log_rera_query(
                    question=user_input,
                    answer_snippet=answer[:500],
                    session_id=st.session_state.get("uf_session_id", ""),
                )

    # ── New Chat / Clear Chat buttons ─────────────────────────────────────────
    _btn_col1, _btn_col2 = st.columns([1, 1])

    def _start_new_chat():
        import uuid as _u
        st.session_state["uf_chat_history"] = []
        st.session_state["uf_session_id"] = str(_u.uuid4())

    with _btn_col1:
        st.button(
            "✦ New Chat",
            key="uf_new_chat",
            type="primary",
            use_container_width=True,
            on_click=_start_new_chat,
            help="Start a fresh conversation with a new session",
        )
    with _btn_col2:
        if st.session_state.get("uf_chat_history"):
            st.button(
                "🗑 Clear Chat",
                key="uf_clear_chat",
                type="secondary",
                use_container_width=True,
                on_click=lambda: st.session_state.update(uf_chat_history=[]),
                help="Clear messages but keep this session",
            )


# ── Compliance Officer: 4-tab view ────────────────────────────────────────────

def _render_co_tabs():
    """Render the 4-tab Compliance Officer interface."""
    _sidebar_project_search()
    _sidebar_document_context()

    st.markdown(
        "<h2 style='margin-bottom:2px'>Karnataka RERA Compliance Dashboard</h2>"
        "<p style='color:#6b7280;margin-top:0;font-size:.9rem'>"
        "IIMB PGPBA Group 9 | CCS RAG Compliance Assistant</p>",
        unsafe_allow_html=True,
    )

    _t_chat, _t_lookup, _t_invest, _t_docqa = st.tabs([
        "💬 Compliance Chat",
        "🔍 K-RERA Project Lookup",
        "📊 Investment Signals",
        "📄 Document Q&A",
    ])

    # ── Tab 1: Compliance Chat ───────────────────────────────────────────────
    with _t_chat:
        components.html(DEMO_HTML, height=62)
        st.markdown(
            "<div style='background:#f0fdf4;border-left:4px solid #22c55e;"
            "border-radius:6px;padding:10px 14px;margin-bottom:16px;font-size:.85rem;color:#166534'>"
            "Upload brochures or paste a URL in the sidebar to run a full due diligence report. "
            "Or just ask any question below about Karnataka RERA, prices, or builders."
            "</div>",
            unsafe_allow_html=True,
        )
        _report = st.session_state.get("uf_report_data")
        if _report and _report.get("extracted"):
            _src = _report.get("source_names", [])
            _lbl = ", ".join(_src) if _src else "uploaded documents"
            with st.expander(f"Property report: {_lbl}", expanded=True):
                _render_report(
                    _report["extracted"],
                    _report.get("rera_rows", []),
                    _report.get("price", {}),
                    _report.get("bvr", {}),
                )
            st.divider()
        _render_chat()

    # ── Tab 2: K-RERA Project Lookup ────────────────────────────────────────
    with _t_lookup:
        import requests as _req_lk
        _lk_url_base = _secret("SUPABASE_URL") or os.environ.get("SUPABASE_URL", "")
        _lk_key      = _secret("SUPABASE_KEY") or os.environ.get("SUPABASE_KEY", "")

        st.subheader("Search Karnataka RERA Registered Projects")
        st.caption("10,096 projects as of November 2026 — data from rera.karnataka.gov.in")

        if not _lk_url_base or not _lk_key:
            st.warning("SUPABASE_URL and SUPABASE_KEY are not set. Add them in Streamlit Secrets.")
        else:
            _lk_ca, _lk_cb = st.columns([3, 1])
            with _lk_cb:
                _lk_field = st.selectbox(
                    "Search by",
                    ["Project Name", "Builder / Promoter", "PIN Code",
                     "District / Area", "Taluk", "Near Location",
                     "ACK Number", "PRM Number"],
                    key="lk_field",
                )
            _LK_HINTS = {
                "Project Name":       "e.g. Prestige Sunrise Park, Brigade Orchards",
                "Builder / Promoter": "e.g. Prestige, Brigade, Godrej, Sobha",
                "PIN Code":           "e.g. 560066, 560034, 562110",
                "District / Area":    "e.g. BENGALURU URBAN, MYSORE, TUMAKURU",
                "Taluk":              "e.g. Bangalore North, Devanahalli, Anekal",
                "Near Location":      "e.g. IIM Bangalore, Whitefield, Koramangala, Hebbal",
                "ACK Number":         "e.g. PRM/KA/RERA/...",
                "PRM Number":         "e.g. PRM/KA/RERA/...",
            }
            with _lk_ca:
                _lk_q = st.text_input(
                    "Search projects",
                    placeholder=_LK_HINTS.get(_lk_field, "Search..."),
                    key="lk_query",
                )
            # Radius slider for nearby search
            _lk_radius = 7
            if _lk_field == "Near Location":
                _lk_radius = st.slider(
                    "Radius (km)", min_value=2, max_value=20, value=7, step=1,
                    key="lk_radius",
                )

            _LK_COL = {
                "Project Name":        "project_name",
                "Builder / Promoter":  "promoter_name",
                "PIN Code":            "pin_code",
                "District / Area":     "district",
                "Taluk":               "taluk",
                "ACK Number":          "ack_number",
                "PRM Number":          "prm_number",
            }

            if _lk_q:
                _lk_rows = []
                if _lk_field == "Near Location":
                    # Geocode + Haversine search
                    with st.spinner(f"Locating '{_lk_q}' and searching nearby projects..."):
                        _lk_coords = _geocode_location(_lk_q)
                    if _lk_coords:
                        st.caption(
                            f"Searching within {_lk_radius} km of "
                            f"({_lk_coords[0]:.4f}, {_lk_coords[1]:.4f})"
                        )
                        with st.spinner("Fetching nearby RERA projects..."):
                            _near_rows = _search_nearby_projects(
                                _lk_coords[0], _lk_coords[1],
                                radius_km=_lk_radius, limit=30
                            )
                        # Convert to same schema expected by the card renderer
                        for _nr in _near_rows:
                            _nr["_dist_label"] = f"{_nr['_dist_km']} km away"
                        _lk_rows = _near_rows
                        if not _lk_rows:
                            st.info(
                                "No RERA projects with GPS coordinates found within "
                                f"{_lk_radius} km of '{_lk_q}'. "
                                "Try a larger radius or search by PIN Code / District."
                            )
                    else:
                        st.warning(
                            f"Could not geocode '{_lk_q}'. "
                            "Try a well-known landmark: IIM Bangalore, Whitefield, Koramangala..."
                        )
                else:
                    _LK_COL = {
                        "Project Name":        "project_name",
                        "Builder / Promoter":  "promoter_name",
                        "PIN Code":            "pin_code",
                        "District / Area":     "district",
                        "Taluk":               "taluk",
                        "ACK Number":          "ack_number",
                        "PRM Number":          "prm_number",
                    }
                    _col = _LK_COL.get(_lk_field, "project_name")
                    _sel = (
                        "ack_number,prm_number,project_name,promoter_name,"
                        "status,district,taluk,project_type,total_units,sold_units,"
                        "completion_date,reg_date,lapse_date,land_area_sqft,detail_url"
                    )
                    _lk_endpoint = (
                        f"{_lk_url_base}/rest/v1/rera_projects"
                        f"?{_col}=ilike.*{_req_lk.utils.quote(_lk_q)}*"
                        f"&limit=20&select={_sel}"
                    )
                    try:
                        _resp = _req_lk.get(
                            _lk_endpoint,
                            headers={"apikey": _lk_key, "Authorization": f"Bearer {_lk_key}"},
                            timeout=15,
                        )
                        _resp.raise_for_status()
                        _lk_rows = _resp.json()
                    except Exception as _lk_err:
                        st.error(f"Search error: {_lk_err}")
                        _lk_rows = []

                if _lk_rows:
                    _nearby_mode = _lk_field == "Near Location"
                    _success_msg = (
                        f"Found {len(_lk_rows)} project(s) with GPS coordinates "
                        f"within {_lk_radius} km — sorted by distance"
                        if _nearby_mode else f"Found {len(_lk_rows)} project(s)"
                    )
                    st.success(_success_msg)
                    if _nearby_mode:
                        st.caption(
                            "Only ~15% of K-RERA projects have GPS coordinates. "
                            "For full coverage also try PIN Code or District search."
                        )
                    _SC = {"active": "🟢", "approved": "🟢", "expired": "🔴", "lapsed": "🟠", "revoked": "⛔"}
                    for _p in _lk_rows:
                        _sr = (_p.get("status") or "").lower()
                        _badge = _SC.get(_sr, "⬜")
                        _dist_tag = (
                            f"  |  📍 {_p.get('_dist_km')} km"
                            if _nearby_mode and _p.get("_dist_km") else ""
                        )
                        _ttl = (
                            f"{_badge} **{_p.get('project_name') or 'N/A'}**"
                            f"  |  {_p.get('promoter_name') or 'N/A'}"
                            f"{_dist_tag}"
                        )
                        with st.expander(_ttl):
                            _r1a, _r1b, _r1c = st.columns(3)
                            _r1a.metric("Status", (_p.get("status") or "Not enriched yet").title())
                            _r1b.metric("District", _p.get("district") or "N/A")
                            if _nearby_mode and _p.get("_dist_km"):
                                _r1c.metric("Distance", f"{_p['_dist_km']} km")
                            else:
                                _r1c.metric("Project Type", _p.get("project_type") or "N/A")
                            _r2a, _r2b, _r2c = st.columns(3)
                            _tot = _p.get("total_units")
                            _sld = _p.get("sold_units")
                            _avl = (_tot - _sld) if (_tot and _sld is not None) else None
                            _r2a.metric("Total Units", _tot if _tot else "N/A")
                            _r2b.metric("Units Sold", _sld if _sld is not None else "N/A")
                            _r2c.metric("Units Available", _avl if _avl is not None else "N/A")
                            _r3a, _r3b, _r3c = st.columns(3)
                            _r3a.metric("Registration Date", _p.get("reg_date") or "N/A")
                            _r3b.metric("Expected Completion", _p.get("completion_date") or "N/A")
                            _r3c.metric("Valid Until", _p.get("lapse_date") or "N/A")
                            _r4a, _r4b = st.columns(2)
                            _r4a.markdown(f"**ACK:** `{_p.get('ack_number') or 'N/A'}`")
                            _r4b.markdown(f"**PRM:** `{_p.get('prm_number') or 'N/A'}`")
                            if _sr == "active":
                                st.success("This project is **Active** on K-RERA. The builder is currently compliant.")
                            elif _sr in ("expired", "lapsed"):
                                st.error("Registration has **expired / lapsed**. Verify status directly with K-RERA before booking.")
                            elif _sr == "revoked":
                                st.error("Registration has been **revoked** by K-RERA. Exercise extreme caution.")
                            else:
                                st.info("Detailed status not yet scraped. Click the portal link below to verify.")
                            _pid = _p.get("prm_number") or _p.get("ack_number") or ""
                            if _pid:
                                st.markdown(
                                    f"[View full details on K-RERA portal ↗]"
                                    f"(https://rera.karnataka.gov.in/projectViewDetails?id={_pid})"
                                )
                else:
                    st.warning("No matching projects found. Try a shorter keyword.")
            else:
                st.info("Type a project name or builder name to search.")

            with st.expander("About this data"):
                st.markdown(
                    "Sourced from [K-RERA](https://rera.karnataka.gov.in/viewAllProjects) — "
                    "all 10,096 RERA-registered Karnataka projects. "
                    "Rich details (status, location, units, dates) are scraped progressively "
                    "from project detail pages and stored in our database. "
                    "Always cross-check critical decisions on the official portal."
                )

    # ── Tab 3: Investment Signals ────────────────────────────────────────────
    with _t_invest:
        import pandas as _pd_inv

        _CO_LOC = [
            # Central / Premium
            {"locality": "Indiranagar",          "pin": "560038", "avg_psf": 14000, "cagr_pct": 5.2,  "risk": "Low",    "zone": "Central", "anchored": True},
            {"locality": "Koramangala",          "pin": "560034", "avg_psf": 13200, "cagr_pct": 5.0,  "risk": "Low",    "zone": "Central", "anchored": True},
            {"locality": "Jayanagar",            "pin": "560041", "avg_psf": 12500, "cagr_pct": 4.8,  "risk": "Low",    "zone": "Central", "anchored": False},
            {"locality": "JP Nagar",             "pin": "560078", "avg_psf": 10800, "cagr_pct": 5.5,  "risk": "Low",    "zone": "South",   "anchored": False},
            {"locality": "Sadashivanagar",       "pin": "560080", "avg_psf": 15500, "cagr_pct": 4.5,  "risk": "Low",    "zone": "Central", "anchored": False},
            # East corridor
            {"locality": "Whitefield",           "pin": "560066", "avg_psf": 8500,  "cagr_pct": 8.1,  "risk": "Low",    "zone": "East",    "anchored": True},
            {"locality": "Sarjapur Road",        "pin": "560035", "avg_psf": 8200,  "cagr_pct": 9.3,  "risk": "Low",    "zone": "East",    "anchored": True},
            {"locality": "Marathahalli",         "pin": "560037", "avg_psf": 7800,  "cagr_pct": 7.8,  "risk": "Low",    "zone": "East",    "anchored": True},
            {"locality": "HSR Layout",           "pin": "560102", "avg_psf": 11200, "cagr_pct": 6.8,  "risk": "Low",    "zone": "East",    "anchored": True},
            {"locality": "KR Puram",             "pin": "560036", "avg_psf": 7200,  "cagr_pct": 8.5,  "risk": "Medium", "zone": "East",    "anchored": False},
            {"locality": "Varthur",              "pin": "560087", "avg_psf": 6800,  "cagr_pct": 9.0,  "risk": "Medium", "zone": "East",    "anchored": False},
            # North corridor
            {"locality": "Hebbal",               "pin": "560024", "avg_psf": 9800,  "cagr_pct": 8.9,  "risk": "Low",    "zone": "North",   "anchored": True},
            {"locality": "Yelahanka",            "pin": "560064", "avg_psf": 6200,  "cagr_pct": 10.2, "risk": "Medium", "zone": "North",   "anchored": True},
            {"locality": "Hennur",               "pin": "560043", "avg_psf": 6800,  "cagr_pct": 9.8,  "risk": "Low",    "zone": "North",   "anchored": False},
            {"locality": "Devanahalli",          "pin": "562110", "avg_psf": 5600,  "cagr_pct": 12.5, "risk": "Medium", "zone": "North",   "anchored": False},
            {"locality": "Bagalur",              "pin": "562149", "avg_psf": 4200,  "cagr_pct": 15.0, "risk": "High",   "zone": "North",   "anchored": False},
            # South corridor
            {"locality": "Electronic City",      "pin": "560100", "avg_psf": 5800,  "cagr_pct": 7.2,  "risk": "Low",    "zone": "South",   "anchored": True},
            {"locality": "Bannerghatta Road",    "pin": "560076", "avg_psf": 7000,  "cagr_pct": 6.5,  "risk": "Medium", "zone": "South",   "anchored": True},
            {"locality": "Kanakapura Road",      "pin": "560062", "avg_psf": 6200,  "cagr_pct": 8.2,  "risk": "Medium", "zone": "South",   "anchored": False},
            {"locality": "Bommanahalli",         "pin": "560068", "avg_psf": 7500,  "cagr_pct": 7.0,  "risk": "Low",    "zone": "South",   "anchored": False},
            # West corridor
            {"locality": "Rajajinagar",          "pin": "560010", "avg_psf": 9200,  "cagr_pct": 5.8,  "risk": "Low",    "zone": "West",    "anchored": False},
            {"locality": "Rajarajeshwari Nagar", "pin": "560098", "avg_psf": 6100,  "cagr_pct": 7.5,  "risk": "Medium", "zone": "West",    "anchored": False},
            {"locality": "Mysore Road",          "pin": "560026", "avg_psf": 5500,  "cagr_pct": 7.8,  "risk": "High",   "zone": "West",    "anchored": False},
            {"locality": "Tumkur Road",          "pin": "560073", "avg_psf": 5200,  "cagr_pct": 8.0,  "risk": "High",   "zone": "West",    "anchored": False},
            {"locality": "Hoskote",              "pin": "562114", "avg_psf": 4500,  "cagr_pct": 13.0, "risk": "High",   "zone": "East",    "anchored": False},
        ]
        _RISK_IC = {"Low": "🟢", "Medium": "🟡", "High": "🔴"}

        st.subheader("Bengaluru Real Estate Investment Advisor")
        st.warning(
            "PSF figures are sourced from NoBroker, 99acres and MagicBricks (Nov 2026). "
            "For unanchored localities, zone-level estimates are applied. "
            "Always verify with a registered property agent before any decision."
        )

        _iv_s1, _iv_s2, _iv_s3 = st.tabs([
            "🔍 Fair Price Check",
            "📊 Buy vs Rent vs Invest",
            "🗺️ Locality Explorer",
        ])

        # Sub-tab 1: Fair Price Check
        with _iv_s1:
            st.markdown(
                "##### Is your builder quoting a fair price?\n"
                "Enter the locality, flat size, and what your builder is asking. "
                "We compare it against current market rates and tell you if you're overpaying."
            )
            _fpc_c1, _fpc_c2, _fpc_c3 = st.columns(3)
            with _fpc_c1:
                _fpc_loc = st.selectbox("Locality", [p["locality"] for p in _CO_LOC], key="fpc_locality")
            with _fpc_c2:
                _fpc_area = st.number_input("Flat size (sq ft)", 300, 5000, 950, step=50, key="fpc_area")
            with _fpc_c3:
                _fpc_quoted = st.number_input("Builder's asking price (Rs lakhs)", 10, 1000, 120, step=5, key="fpc_quoted")
            _fpc_d = next(p for p in _CO_LOC if p["locality"] == _fpc_loc)
            _fpc_mkt = (_fpc_d["avg_psf"] * _fpc_area) / 100_000
            _fpc_qpsf = (_fpc_quoted * 100_000) / _fpc_area
            _fpc_diff = _fpc_quoted - _fpc_mkt
            _fpc_pct  = (_fpc_diff / _fpc_mkt) * 100
            st.markdown("---")
            _fr1, _fr2, _fr3 = st.columns(3)
            _fr1.metric("Market rate (Rs/sqft)", f"Rs{_fpc_d['avg_psf']:,}", help="NoBroker/99acres Nov 2026 benchmark")
            _fr2.metric("Your quoted rate (Rs/sqft)", f"Rs{_fpc_qpsf:,.0f}")
            _fr3.metric("Price difference", f"{'+'if _fpc_diff >= 0 else ''}Rs{_fpc_diff:.1f}L",
                        delta=f"{_fpc_pct:+.1f}% vs market", delta_color="inverse")
            if _fpc_pct <= -10:
                st.success(
                    f"**Great deal.** The asking price is **{abs(_fpc_pct):.1f}% below** market. "
                    f"At Rs{_fpc_qpsf:,.0f}/sqft vs market Rs{_fpc_d['avg_psf']:,}/sqft, "
                    f"you're saving Rs{abs(_fpc_diff):.1f}L. Verify construction quality and legal title."
                )
            elif _fpc_pct <= 5:
                st.info(
                    f"**Fair price.** Within 5% of market benchmark. "
                    f"Market rate in {_fpc_loc} is ~Rs{_fpc_d['avg_psf']:,}/sqft. "
                    f"Your effective rate is Rs{_fpc_qpsf:,.0f}/sqft -- reasonable."
                )
            elif _fpc_pct <= 15:
                st.warning(
                    f"**Slightly overpriced.** You're paying **Rs{_fpc_diff:.1f}L more** than the market benchmark. "
                    f"Try negotiating down to Rs{_fpc_mkt:.1f}L (Rs{_fpc_d['avg_psf']:,}/sqft)."
                )
            else:
                st.error(
                    f"**Significantly overpriced.** You're being asked **{_fpc_pct:.1f}% above** market -- "
                    f"an extra Rs{_fpc_diff:.1f}L. Market benchmark for {_fpc_loc} is Rs{_fpc_d['avg_psf']:,}/sqft "
                    f"(total ~Rs{_fpc_mkt:.1f}L for {_fpc_area} sqft). Negotiate hard or consider comparable localities."
                )
            if not _fpc_d["anchored"]:
                st.caption(
                    f"Note: {_fpc_loc} PSF is a zone-level estimate ({_fpc_d['zone']} zone). "
                    "For anchored localities with live market data, check Whitefield, Sarjapur Road, "
                    "HSR Layout, Indiranagar, Koramangala, Hebbal, Yelahanka, Electronic City, "
                    "Bannerghatta Road or Marathahalli."
                )

        # Sub-tab 2: Buy vs Rent vs Invest
        with _iv_s2:
            st.markdown(
                "##### Should you buy, or rent and invest the difference?\n"
                "We model your net wealth at the end of your chosen horizon across four paths: "
                "buy on EMI, rent and invest in Mutual Funds, invest in Gold, or park it in an FD."
            )
            _bri_c1, _bri_c2 = st.columns(2)
            with _bri_c1:
                st.markdown("**Property details**")
                _bri_price     = st.number_input("Property price (Rs lakhs)", 30, 500, 100, step=5, key="bri_price")
                _bri_down_pct  = st.slider("Down payment (%)", 10, 40, 20, step=5, key="bri_down")
                _bri_tenure    = st.slider("Loan tenure (years)", 5, 30, 20, step=5, key="bri_tenure")
                _bri_rate      = st.slider("Home loan rate (%)", 7.0, 11.0, 8.75, step=0.25, key="bri_rate")
                _bri_locality  = st.selectbox("Locality (for appreciation rate)", [p["locality"] for p in _CO_LOC], index=5, key="bri_locality")
            with _bri_c2:
                st.markdown("**Your current rental situation**")
                _bri_rent     = st.number_input("Monthly rent today (Rs)", 5000, 150000, 35000, step=1000, key="bri_rent")
                _bri_rent_inc = st.slider("Annual rent increase (%)", 3, 10, 5, step=1, key="bri_rent_inc")
                st.markdown("**Investment return assumptions**")
                _bri_mf_ret   = st.slider("Mutual Fund / Nifty CAGR (%)", 8, 18, 12, step=1, key="bri_mf")
                _bri_gold_ret = st.slider("Gold CAGR (%)", 5, 15, 8, step=1, key="bri_gold")
                _bri_fd_ret   = st.slider("FD / RD interest rate (%)", 5, 9, 7, step=1, key="bri_fd")
                _bri_horizon  = st.slider("Analysis horizon (years)", 5, 25, 10, step=5, key="bri_horizon")
            _bri_ld       = next(p for p in _CO_LOC if p["locality"] == _bri_locality)
            _bri_pcagr    = _bri_ld["cagr_pct"] / 100
            _bri_price_rs = _bri_price * 1e5
            _bri_down_rs  = _bri_price_rs * (_bri_down_pct / 100)
            _bri_loan_rs  = _bri_price_rs - _bri_down_rs
            _bri_mr       = (_bri_rate / 100) / 12
            _bri_nm       = _bri_tenure * 12
            if _bri_mr > 0:
                _bri_emi = _bri_loan_rs * _bri_mr * (1 + _bri_mr)**_bri_nm / ((1 + _bri_mr)**_bri_nm - 1)
            else:
                _bri_emi = _bri_loan_rs / _bri_nm
            _bri_hm       = _bri_horizon * 12
            _bri_pval     = _bri_price_rs * (1 + _bri_pcagr) ** _bri_horizon
            if _bri_horizon < _bri_tenure:
                _bri_outst = max(_bri_loan_rs * (1+_bri_mr)**_bri_hm - _bri_emi * ((1+_bri_mr)**_bri_hm - 1) / _bri_mr, 0)
            else:
                _bri_outst = 0
            _bri_buy_w    = (_bri_pval - _bri_outst) / 1e5
            _bri_lump_mf  = _bri_down_rs * (1 + _bri_mf_ret/100) ** _bri_horizon
            _bri_sip_mo   = max(_bri_emi - _bri_rent, 0)
            _bri_mfr      = (_bri_mf_ret/100) / 12
            _bri_sip_corp = (_bri_sip_mo * ((1+_bri_mfr)**_bri_hm - 1) / _bri_mfr) if _bri_mfr > 0 else _bri_sip_mo * _bri_hm
            _bri_rent_tot = sum(_bri_rent * (1+_bri_rent_inc/100)**y * 12 for y in range(_bri_horizon))
            _bri_mf_w     = (_bri_lump_mf + _bri_sip_corp) / 1e5
            _bri_mf_net   = _bri_mf_w - (_bri_rent_tot / 1e5)
            _bri_gold_w   = (_bri_down_rs * (1 + _bri_gold_ret/100) ** _bri_horizon) / 1e5 - (_bri_rent_tot / 1e5)
            _bri_fd_w     = (_bri_down_rs * (1 + _bri_fd_ret/100)   ** _bri_horizon) / 1e5 - (_bri_rent_tot / 1e5)
            st.markdown("---")
            st.markdown(f"#### Results at **{_bri_horizon}-year** horizon")
            _bm1, _bm2, _bm3, _bm4 = st.columns(4)
            _bbest = max(_bri_buy_w, _bri_mf_net, _bri_gold_w, _bri_fd_w)
            _bflag = lambda v: "🏆 " if abs(v - _bbest) < 0.5 else ""
            _bm1.metric(f"{_bflag(_bri_buy_w)}🏠 Buy (EMI)",   f"Rs{_bri_buy_w:.1f}L")
            _bm2.metric(f"{_bflag(_bri_mf_net)}📈 Rent + MF", f"Rs{_bri_mf_net:.1f}L")
            _bm3.metric(f"{_bflag(_bri_gold_w)}🥇 Rent + Gold",f"Rs{_bri_gold_w:.1f}L")
            _bm4.metric(f"{_bflag(_bri_fd_w)}🏦 Rent + FD",   f"Rs{_bri_fd_w:.1f}L")
            _bwin = max([("Buy on EMI", _bri_buy_w), ("Rent + MF", _bri_mf_net), ("Rent + Gold", _bri_gold_w), ("Rent + FD", _bri_fd_w)], key=lambda x: x[1])
            st.success(
                f"**{_bri_horizon}-year winner: {_bwin[0]}** at Rs{_bwin[1]:.1f}L net wealth. "
                f"Property appreciation assumed at {_bri_ld['cagr_pct']}% CAGR for {_bri_locality}."
            )
            st.caption("Tax implications, maintenance costs and registration charges not included.")

        # Sub-tab 3: Locality Explorer
        with _iv_s3:
            st.caption(
                "PSF from NoBroker/99acres Nov 2026 (anchored) or zone-level estimate. "
                "CAGR is 9-year historical (2017-2026)."
            )
            _iv_c1, _iv_c2 = st.columns(2)
            with _iv_c1:
                _iv_budget = st.slider("Your budget (Rs lakhs)", 30, 300, 100, step=5, key="iv_budget")
            with _iv_c2:
                _iv_area = st.slider("Desired size (sq ft)", 400, 2000, 950, step=50, key="iv_area")
            _iv_tpsf = (_iv_budget * 100_000) / _iv_area
            st.info(f"Your budget implies **Rs{_iv_tpsf:,.0f} / sq ft** for a {_iv_area} sq ft flat at Rs{_iv_budget}L")
            _iv_in = sorted([p for p in _CO_LOC if p["avg_psf"] <= _iv_tpsf], key=lambda x: abs(x["avg_psf"] - _iv_tpsf))
            _iv_st = sorted([p for p in _CO_LOC if _iv_tpsf < p["avg_psf"] <= _iv_tpsf * 1.20], key=lambda x: x["avg_psf"] - _iv_tpsf)
            if not _iv_in and not _iv_st:
                st.warning("No localities match this budget and size. Try increasing budget or reducing size.")
            else:
                if _iv_in:
                    st.markdown("#### Within your budget")
                    for _p in _iv_in[:8]:
                        _c1, _c2, _c3, _c4 = st.columns([3, 2, 2, 3])
                        _c1.markdown(f"**{_p['locality']}** `{_p['pin']}`{'  (live data)' if _p['anchored'] else ''}")
                        _c2.markdown(f"Rs{_p['avg_psf']:,} / sqft")
                        _c3.markdown(f"+{_p['cagr_pct']}% CAGR")
                        _c4.markdown(f"{_RISK_IC[_p['risk']]} **{_p['risk']}** · {_p['zone']}")
                if _iv_st:
                    st.markdown("#### Slight stretch (up to 20% over budget)")
                    for _p in _iv_st[:4]:
                        _c1, _c2, _c3, _c4 = st.columns([3, 2, 2, 3])
                        _c1.markdown(f"**{_p['locality']}** `{_p['pin']}`")
                        _c2.markdown(f"Rs{_p['avg_psf']:,} / sqft")
                        _c3.markdown(f"+{_p['cagr_pct']}% CAGR")
                        _c4.markdown(f"{_RISK_IC[_p['risk']]} **{_p['risk']}** · {_p['zone']}")
            st.markdown("---")
            st.markdown("#### Full locality table")
            _iv_df = _pd_inv.DataFrame(_CO_LOC)[["locality","pin","avg_psf","cagr_pct","risk","zone","anchored"]]
            _iv_df.columns = ["Locality","PIN","Avg PSF (Rs)","CAGR %","Risk","Zone","Anchored"]
            _iv_df["Est. cost (RsL)"] = (_iv_df["Avg PSF (Rs)"] * _iv_area / 100_000).round(1)
            _iv_df["vs. Budget"] = (_iv_df["Avg PSF (Rs)"] - _iv_tpsf).round(0).astype(int)
            _iv_df = _iv_df.sort_values("vs. Budget", key=lambda s: s.abs()).reset_index(drop=True)
            st.dataframe(_iv_df, use_container_width=True, hide_index=True)

    # ── Tab 4: Document Q&A ──────────────────────────────────────────────────
    with _t_docqa:
        import io as _io_dq
        import requests as _req_dq
        import numpy as _np_dq

        st.markdown("### 📄 Sales Deed / Agreement Analyzer")
        st.markdown(
            "Upload your **sales deed, builder-buyer agreement, or any real estate document** (PDF). "
            "The tool will answer your questions by reading your document and cross-checking against "
            "Karnataka RERA regulations -- flagging anything that looks non-compliant."
        )

        _dq_oai_key = _secret("OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY", "")
        _dq_model   = _secret("LLM_MODEL") or os.environ.get("LLM_MODEL", "gpt-4o-mini")

        if not _dq_oai_key:
            st.error("OPENAI_API_KEY not set. Add it to Streamlit Secrets.")
        else:
            # Ensure session ID for history
            if "dq_session_id" not in st.session_state:
                import uuid as _uuid_dq
                st.session_state["dq_session_id"] = str(_uuid_dq.uuid4())
            _dq_sid = st.session_state["dq_session_id"]

            _dq_uploaded = st.file_uploader("Upload PDF", type=["pdf"], key="dq_pdf")

            def _dq_extract(file_bytes, chunk_words=400):
                try:
                    import pdfplumber as _plumb
                    _chunks = []
                    with _plumb.open(_io_dq.BytesIO(file_bytes)) as _pdf:
                        for _pg_n, _pg in enumerate(_pdf.pages, 1):
                            _txt = _pg.extract_text() or ""
                            _words = _txt.split()
                            for _i in range(0, len(_words), chunk_words):
                                _chunk = " ".join(_words[_i:_i + chunk_words])
                                if _chunk.strip():
                                    _chunks.append({"text": _chunk, "page": _pg_n})
                    return _chunks
                except Exception:
                    return []

            def _dq_embed(texts):
                _r = _req_dq.post(
                    "https://api.openai.com/v1/embeddings",
                    headers={"Authorization": f"Bearer {_dq_oai_key}"},
                    json={"model": "text-embedding-3-small", "input": texts},
                    timeout=30,
                )
                _r.raise_for_status()
                return [d["embedding"] for d in _r.json()["data"]]

            def _dq_top_k(q_vec, doc_vecs, k=4):
                _q = _np_dq.array(q_vec)
                _D = _np_dq.array(doc_vecs)
                _sc = _D @ _q / (_np_dq.linalg.norm(_D, axis=1) * _np_dq.linalg.norm(_q) + 1e-9)
                return _sc.argsort()[::-1][:k].tolist()

            def _dq_ask(doc_ctx, rera_ctx, question, pages):
                _sys = (
                    "You are a RERA compliance expert helping a first-time home buyer in Karnataka. "
                    "You have two sources: (1) the buyer's uploaded document, and (2) Karnataka RERA regulations. "
                    "Answer the question using both sources. "
                    "If a clause in the document contradicts or falls short of RERA requirements, "
                    "clearly flag it as: 'COMPLIANCE CONCERN: ...'. "
                    "Cite page numbers from the uploaded document where relevant. "
                    "Do not use em dashes anywhere in your response."
                )
                _usr = (
                    f"UPLOADED DOCUMENT (pages {', '.join(pages)}):\n{doc_ctx}\n\n"
                    f"KARNATAKA RERA REGULATIONS:\n{rera_ctx}\n\n"
                    f"Question: {question}"
                )
                _r = _req_dq.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {_dq_oai_key}"},
                    json={"model": _dq_model, "messages": [{"role":"system","content":_sys}, {"role":"user","content":_usr}], "temperature": 0.2},
                    timeout=45,
                )
                _r.raise_for_status()
                return _r.json()["choices"][0]["message"]["content"]

            if _dq_uploaded:
                _dq_raw = _dq_uploaded.read()
                _dq_ck  = f"dq_vecs_{_dq_uploaded.name}_{len(_dq_raw)}"
                _dq_cc  = f"dq_chunks_{_dq_uploaded.name}_{len(_dq_raw)}"

                if st.session_state.get("dq_current_file") != _dq_uploaded.name:
                    st.session_state["dq_current_file"] = _dq_uploaded.name
                    st.session_state["dq_history"] = []

                if _dq_cc not in st.session_state:
                    with st.spinner("Reading and indexing your document..."):
                        _tmp = _dq_extract(_dq_raw)
                        if not _tmp:
                            st.error("Could not extract text. This PDF may be a scanned image. Try a text-based PDF.")
                            st.stop()
                        st.session_state[_dq_cc] = _tmp
                        st.session_state[_dq_ck] = _dq_embed([c["text"] for c in _tmp])

                _dq_chunks = st.session_state[_dq_cc]
                _dq_vecs   = st.session_state[_dq_ck]

                if "dq_history" not in st.session_state:
                    st.session_state["dq_history"] = []

                st.success(f"Indexed {len(_dq_chunks)} sections from **{_dq_uploaded.name}**")
                st.caption("Questions are answered using your document AND Karnataka RERA regulations.")

                _dq_sugg = [
                    "Is the RERA registration number mentioned?",
                    "What is the promised possession date?",
                    "What penalty applies if builder delays possession?",
                    "Is the carpet area clearly defined?",
                    "Are there any clauses that may violate RERA?",
                ]

                if "_dq_pending" in st.session_state:
                    st.session_state["dq_question"] = st.session_state.pop("_dq_pending")

                for _hi, _he in enumerate(reversed(st.session_state.get("dq_history", []))):
                    with st.chat_message("user"):
                        st.markdown(_he["q"])
                    with st.chat_message("assistant"):
                        if "COMPLIANCE CONCERN" in _he["a"]:
                            st.warning("One or more compliance concerns found in your document.")
                        st.markdown("### Analysis")
                        st.markdown(_he["a"])
                        st.caption("Sources: your uploaded document + Karnataka RERA regulatory corpus.")
                        if _he.get("followups"):
                            st.markdown("**Suggested follow-ups:**")
                            _fq_cols = st.columns(len(_he["followups"]))
                            _rev_idx = len(st.session_state["dq_history"]) - 1 - _hi
                            for _fi, _fq in enumerate(_he["followups"]):
                                if _fq_cols[_fi].button(_fq, key=f"dq_fup_{_rev_idx}_{_fi}"):
                                    st.session_state["_dq_pending"] = _fq
                                    st.rerun()

                st.markdown("---")
                _qs_label = "**Quick checks to get started:**" if not st.session_state.get("dq_history") else "**Ask another question:**"
                st.markdown(_qs_label)
                _dq_sc = st.columns(len(_dq_sugg))
                for _qi, _sq in enumerate(_dq_sugg):
                    if _dq_sc[_qi].button(_sq, key=f"dq_sq_{_qi}", use_container_width=True):
                        st.session_state["dq_question"] = _sq

                _dq_q = (
                    st.chat_input("Ask a question about your document...", key="dq_text_input")
                    or st.session_state.pop("dq_question", None)
                )

                if _dq_q:
                    _last_q = st.session_state["dq_history"][0]["q"] if st.session_state["dq_history"] else ""
                    if _dq_q != _last_q:
                        with st.spinner("Searching document and RERA regulations..."):
                            _q_vec  = _dq_embed([_dq_q])[0]
                            _top    = _dq_top_k(_q_vec, _dq_vecs, k=4)
                            _ctx    = "\n\n---\n\n".join(_dq_chunks[_i]["text"] for _i in _top)
                            _pgs    = [str(_dq_chunks[_i]["page"]) for _i in _top]
                            try:
                                _rera_hits = retriever.invoke(_dq_q)  # type: ignore[name-defined]
                                _rera_ctx  = "\n\n---\n\n".join(d.page_content for d in _rera_hits[:3])
                            except Exception:
                                _rera_ctx  = "RERA corpus not available in this session."
                            _ans = _dq_ask(_ctx, _rera_ctx, _dq_q, _pgs)
                        try:
                            _dq_followups = get_contextual_followups("RERA", _dq_q, _ans)  # type: ignore[name-defined]
                        except Exception:
                            _dq_followups = []
                        st.session_state["dq_history"].insert(0, {"q": _dq_q, "a": _ans, "followups": _dq_followups})
                        st.session_state.pop("dq_question", None)
                        st.rerun()

                if st.session_state.get("dq_history"):
                    if st.button("Clear Q&A history", key="dq_clear_hist"):
                        st.session_state["dq_history"] = []
                        st.rerun()
            else:
                st.info("Upload your sales deed or builder-buyer agreement PDF to get started.")


# ── Admin: password-gated 4-tab dashboard ─────────────────────────────────────

def _render_admin_mode():
    """Render the admin dashboard with password gate."""
    # Password gate
    if "adm_auth" not in st.session_state:
        st.session_state["adm_auth"] = False

    if not st.session_state["adm_auth"]:
        _, _ac, _ = st.columns([1, 2, 1])
        with _ac:
            st.markdown(
                "<div style='text-align:center;margin:40px 0 24px'>"
                "<div style='font-size:40px'>🛡️</div>"
                "<h3 style='margin:8px 0 4px'>Admin Access</h3>"
                "<p style='color:#6b7280;font-size:.85rem'>CCS RAG Compliance Dashboard</p>"
                "</div>",
                unsafe_allow_html=True,
            )
            _adm_user = st.text_input("Username", key="adm_user_input",
                                      label_visibility="collapsed",
                                      placeholder="Enter admin username")
            _adm_pwd = st.text_input("Password", type="password", key="adm_pwd_input",
                                     label_visibility="collapsed",
                                     placeholder="Enter admin password")
            if st.button("Sign in", key="adm_login_btn", use_container_width=True):
                _correct_user = _secret("ADMIN_USERNAME")
                _correct = _secret("ADMIN_SECRET") or _secret("ADMIN_PASSWORD") or ""
                if (
                    _correct_user
                    and _correct
                    and _adm_user.strip() == _correct_user
                    and _adm_pwd == _correct
                ):
                    st.session_state["adm_auth"] = True
                    st.session_state["adm_is_admin"] = True
                    st.rerun()
                elif not _correct_user:
                    st.error("Admin username is not configured.")
                else:
                    st.error("Incorrect username or password.")
        st.stop()

    # RERA Admin-only presentation. The User / Compliance / Admin selector and
    # credential gate remain part of the existing RERA app flow.
    if "rera_admin_dark_mode" not in st.session_state:
        st.session_state["rera_admin_dark_mode"] = False
    _is_dark = st.session_state["rera_admin_dark_mode"]
    _today_label = __import__("datetime").date.today().strftime("%d %b %Y")
    _page_bg = "#0b0f17" if _is_dark else "#f5f7fa"
    _surface = "#131929" if _is_dark else "#ffffff"
    _border = "#1e2d45" if _is_dark else "#dde3ee"
    _text = "#e4ecf7" if _is_dark else "#141c2e"
    _muted = "#8fa3bd" if _is_dark else "#64748b"
    st.markdown(f"""<style>
      .stApp, [data-testid="stAppViewContainer"] {{ background:{_page_bg} !important; color:{_text} !important; }}
      [data-testid="stHeader"] {{ background:{_page_bg} !important; }}
      .stTabs [data-baseweb="tab-list"] {{ background:{'#1a2540' if _is_dark else '#e9edf5'} !important;
        border:1px solid {_border}; border-radius:10px; padding:4px; gap:4px;
        display:inline-flex !important; width:auto !important; position:relative !important;
        left:50% !important; transform:translateX(-50%) !important; }}
      .stTabs [data-baseweb="tab"] {{ color:{_muted} !important; border-radius:8px;
        font-weight:600; font-size:15px; padding:9px 24px; }}
      .stTabs [aria-selected="true"] {{ background:{'#2563eb' if _is_dark else _surface} !important;
        color:{'#ffffff' if _is_dark else _text} !important; }}
      [data-testid="metric-container"] {{ background:{_surface} !important; border:1px solid {_border} !important;
        border-radius:12px; padding:18px 20px !important; }}
      [data-testid="stMetricValue"] {{ color:{_text} !important; font-size:32px !important; font-weight:800 !important; }}
      [data-testid="stMetricLabel"] {{ color:{_muted} !important; font-size:11px !important;
        font-weight:700 !important; text-transform:uppercase; letter-spacing:.06em; }}
      .stDataFrame, [data-testid="stDataFrame"] {{ background:{_surface} !important; border:1px solid {_border} !important; border-radius:10px; }}
      .stButton > button {{ background:{_surface} !important; color:{_text} !important;
        border:1px solid {_border} !important; border-radius:8px; font-weight:600; }}
      .stTextInput input, [data-baseweb="select"] {{ background:{_surface} !important; color:{_text} !important; }}
      .stExpander {{ background:{_surface} !important; border:1px solid {_border} !important; border-radius:10px !important; }}
    </style>""", unsafe_allow_html=True)

    _adm_hdr, _adm_btn = st.columns([6, 1])
    with _adm_hdr:
        st.markdown(f"""
        <div style='background:linear-gradient(135deg,#1e3a6e 0%,#0f1f40 100%);
                    padding:24px 30px;border-radius:14px;border:1px solid #1a3460;margin-bottom:12px;
                    display:flex;align-items:center;justify-content:space-between;'>
          <div style='display:flex;align-items:center;gap:15px;'>
            <div style='width:48px;height:48px;background:rgba(59,130,246,.2);border-radius:12px;
                        display:flex;align-items:center;justify-content:center;font-size:25px;'>🏠</div>
            <div>
              <div style='display:flex;align-items:center;gap:10px;flex-wrap:wrap;'>
                <h1 style='color:#f0f6ff;margin:0;font-size:25px;font-weight:800;letter-spacing:-.03em;'>Karnataka RERA Admin Dashboard</h1>
                <span style='background:#2563eb;color:#fff;font-size:10px;font-weight:700;border-radius:6px;padding:4px 8px;letter-spacing:.08em;'>ADMIN</span>
              </div>
              <p style='color:#93b7e3;margin:5px 0 0;font-size:13px;font-weight:500;'>RERA activity, documents, and knowledge base</p>
            </div>
          </div>
          <div style='text-align:right;min-width:110px;'>
            <div style='color:#bfdbfe;font-size:14px;font-weight:700;'>{_today_label}</div>
            <div style='color:#8fa8c4;font-size:11px;margin-top:3px;'>Live monitoring</div>
          </div>
        </div>
        """, unsafe_allow_html=True)
    with _adm_btn:
        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
        _theme_label = "☀️ Light" if _is_dark else "🌙 Dark"
        if st.button(_theme_label, key="rera_admin_theme_toggle", use_container_width=True):
            st.session_state["rera_admin_dark_mode"] = not _is_dark
            st.rerun()
        if st.button("Sign out", key="adm_logout", use_container_width=True):
            st.session_state["adm_auth"] = False
            st.rerun()

    _at1, _at2, _at3, _at4 = st.tabs(["  Activity  ", "  Documents  ", "  Bots  ", "  KB Coverage  "])

    # ── Admin Tab 1: Activity ────────────────────────────────────────────────
    with _at1:
        import datetime as _dt_adm
        import requests as _req_adm
        import pandas as _pd_adm

        _adm_url = _secret("SUPABASE_URL") or os.environ.get("SUPABASE_URL", "")
        _adm_svc = _secret("SUPABASE_SERVICE_KEY") or _secret("SUPABASE_KEY") or os.environ.get("SUPABASE_KEY", "")

        _adm_cr, _adm_rf = st.columns([4, 1])
        with _adm_cr:
            _adm_range = st.radio(
                "Date range", ["Last 7 days", "Last 30 days", "All time"],
                horizontal=True, key="adm_range",
            )
        with _adm_rf:
            st.write("")
            if st.button("Refresh", key="adm_refresh", use_container_width=True):
                st.rerun()

        _today = _dt_adm.date.today()
        if _adm_range == "Last 7 days":
            _since = (_today - _dt_adm.timedelta(days=7)).isoformat()
        elif _adm_range == "Last 30 days":
            _since = (_today - _dt_adm.timedelta(days=30)).isoformat()
        else:
            _since = "2020-01-01"

        _adm_rows = []
        if _adm_url and _adm_svc:
            try:
                _ar = _req_adm.get(
                    f"{_adm_url}/rest/v1/queries",
                    headers={"apikey": _adm_svc, "Authorization": f"Bearer {_adm_svc}"},
                    params={
                        "select": "id,created_at,bot,question,confidence,flagged_low_conf,session_id",
                        "bot": "eq.RERA",
                        "created_at": f"gte.{_since}T00:00:00",
                        "order": "created_at.desc",
                        "limit": "1000",
                    },
                    timeout=15,
                )
                _adm_rows = _ar.json() if _ar.ok else []
            except Exception:
                _adm_rows = []
        else:
            st.warning("SUPABASE_URL / SUPABASE_SERVICE_KEY not configured.")

        _total = len(_adm_rows)
        _sessions = len({r.get("session_id") for r in _adm_rows if r.get("session_id")})
        _flagged = sum(1 for r in _adm_rows if r.get("flagged_low_conf"))
        _m1, _m2, _m3 = st.columns(3)
        _m1.metric("RERA Queries", _total)
        _m2.metric("RERA Sessions", _sessions)
        _m3.metric("Low-Confidence Flags", _flagged)

        if _adm_rows:
            from collections import defaultdict as _dd
            _daily = _dd(int)
            for _r in _adm_rows:
                _day = (_r.get("created_at") or "")[:10]
                if _day:
                    _daily[_day] += 1
            _days = sorted(_daily)
            if _days:
                _df_daily = _pd_adm.DataFrame(
                    {"RERA queries": [_daily[d] for d in _days]}, index=_days,
                )
                st.markdown("#### RERA queries per day")
                st.bar_chart(_df_daily, height=260, use_container_width=True)
            _lc = [r for r in _adm_rows if r.get("flagged_low_conf")]
            st.markdown(f"#### Low-confidence RERA queries ({len(_lc)})")
            if _lc:
                _df_lc = _pd_adm.DataFrame([
                    {
                        "Date": (r.get("created_at") or "")[:10],
                        "Question": (r.get("question") or "")[:120],
                        "Score": round(r.get("confidence"), 3)
                        if r.get("confidence") is not None else "—",
                    }
                    for r in _lc
                ])
                st.dataframe(_df_lc, use_container_width=True, hide_index=True)
            else:
                st.success("No low-confidence RERA queries in this period.")
        else:
            st.info("No RERA queries found for the selected date range.")

    # ── Admin Tab 2: Documents ───────────────────────────────────────────────
    with _at2:
        import requests as _req_docs
        import pandas as _pd_docs

        _docs_url = _secret("SUPABASE_URL") or os.environ.get("SUPABASE_URL", "")
        _docs_svc = _secret("SUPABASE_SERVICE_KEY") or _secret("SUPABASE_KEY") or os.environ.get("SUPABASE_KEY", "")

        st.markdown("### RERA Knowledge Base Documents")
        if _docs_url and _docs_svc:
            try:
                _dr = _req_docs.get(
                    f"{_docs_url}/rest/v1/kb_documents",
                    headers={"apikey": _docs_svc, "Authorization": f"Bearer {_docs_svc}"},
                    params={
                        "select": "bot,doc_id,title,status,expected_update_date,source_url",
                        "bot": "eq.RERA",
                        "order": "doc_id.asc",
                    },
                    timeout=15,
                )
                _docs = _dr.json() if _dr.ok else []
            except Exception:
                _docs = []

            if _docs:
                _statuses = sorted({str(d.get("status") or "unknown") for d in _docs})
                _doc_filter_col, _doc_search_col, _doc_refresh_col = st.columns([2, 4, 1])
                with _doc_filter_col:
                    _status_filter = st.selectbox(
                        "Status", ["All statuses", *_statuses], key="rera_admin_doc_status",
                    )
                with _doc_search_col:
                    _doc_search = st.text_input(
                        "Search documents", key="rera_admin_doc_search",
                        placeholder="Search document ID or title",
                    ).strip().casefold()
                with _doc_refresh_col:
                    st.write("")
                    if st.button("Refresh", key="rera_admin_docs_refresh", use_container_width=True):
                        st.rerun()

                _filtered = _docs
                if _status_filter != "All statuses":
                    _filtered = [d for d in _filtered if str(d.get("status") or "unknown") == _status_filter]
                if _doc_search:
                    _filtered = [
                        d for d in _filtered
                        if _doc_search in str(d.get("doc_id") or "").casefold()
                        or _doc_search in str(d.get("title") or "").casefold()
                    ]

                _current_docs = sum(1 for d in _docs if (d.get("status") or "").lower() == "current")
                _pending_docs = sum(1 for d in _docs if (d.get("status") or "").lower() == "pending")
                _archived_docs = sum(1 for d in _docs if (d.get("status") or "").lower() == "archived")
                _doc_m1, _doc_m2, _doc_m3 = st.columns(3)
                _doc_m1.metric("RERA Documents", len(_docs))
                _doc_m2.metric("Current", _current_docs)
                _doc_m3.metric("Pending / Archived", f"{_pending_docs} / {_archived_docs}")
                st.info(f"Showing {len(_filtered)} of {len(_docs)} RERA documents")
                _df_docs = _pd_docs.DataFrame([
                    {
                        "Document ID": d.get("doc_id", ""),
                        "Title": d.get("title", "") or "",
                        "Status": d.get("status", "") or "",
                        "Review date": d.get("expected_update_date", "") or "",
                    }
                    for d in _filtered
                ])
                if _filtered:
                    st.dataframe(_df_docs, use_container_width=True, hide_index=True)
                else:
                    st.info("No RERA documents match those filters.")
            else:
                st.info("No RERA documents are registered in the knowledge base yet.")
        else:
            st.warning("SUPABASE_URL / SUPABASE_SERVICE_KEY not configured.")

    # ── Admin Tab 3: Bots ────────────────────────────────────────────────────
    with _at3:
        st.markdown("### Deployed RERA assistant")
        st.markdown("""
        <div style='background:linear-gradient(145deg,#0f1e38 0%,#0a1528 100%);
                    padding:30px;border-radius:14px;border:1px solid #1e3560;
                    position:relative;overflow:hidden;max-width:900px;'>
          <div style='position:absolute;top:-24px;right:-18px;width:125px;height:125px;
                      background:rgba(59,130,246,.08);border-radius:50%;'></div>
          <div style='font-size:36px;margin-bottom:12px;'>🏠</div>
          <div style='color:#93c5fd;font-size:10px;font-weight:700;text-transform:uppercase;
                      letter-spacing:.1em;margin-bottom:6px;'>Karnataka · Real estate regulation</div>
          <h2 style='color:#f0f6ff;margin:0 0 8px;font-size:22px;font-weight:800;'>Karnataka RERA Property Advisor</h2>
          <p style='color:#a9bed8;font-size:13px;margin:0 0 22px;line-height:1.6;max-width:650px;'>
            RERA Act 2016, Karnataka RERA rules, project registration, agent licensing,
            allottee protections, and property due diligence.
          </p>
          <a href='https://ccsragcomplianceassistant-rera.streamlit.app/' target='_blank' rel='noopener noreferrer'
             style='background:#2563eb;color:#fff;padding:11px 22px;border-radius:8px;
                    text-decoration:none;font-size:13px;font-weight:700;display:inline-block;'>
            Open RERA assistant →
          </a>
        </div>""", unsafe_allow_html=True)

    # ── Admin Tab 4: KB Coverage ─────────────────────────────────────────────
    with _at4:
        import pandas as _pd_kb

        _rera_domains = [
            {"domain": "Central RERA Act and Rules",          "have": 4, "total": 5,
             "have_labels": ["RERA Act 2016","Central RERA Rules 2016","Model Builder-Buyer Agreement","MoHUA circulars"],
             "missing": ["RERA (Amendment) Bill 2023"]},
            {"domain": "Karnataka RERA Rules and Procedures", "have": 0, "total": 4,
             "have_labels": [],
             "missing": ["Karnataka RERA Rules 2017","K-RERA fee schedule","K-RERA registration forms","K-RERA circulars"]},
            {"domain": "Project Registration and Compliance", "have": 3, "total": 5,
             "have_labels": ["Project registration guidelines","Quarterly progress report format","Completion certificate SOP"],
             "missing": ["Escrow account management rules","Designated bank circular"]},
            {"domain": "Agent Licensing",                     "have": 2, "total": 3,
             "have_labels": ["Real estate agent registration rules","RERA agent code of conduct"],
             "missing": ["State-specific agent fee schedules"]},
            {"domain": "Dispute Resolution and Appeals",      "have": 1, "total": 4,
             "have_labels": ["RERA adjudication process overview"],
             "missing": ["RERA Appellate Tribunal rules","K-RERA precedent orders","COVID extension circulars"]},
            {"domain": "Allottee Rights and Disclosures",     "have": 3, "total": 4,
             "have_labels": ["Disclosure norms","Allottee rights chapter","Refund and interest rules"],
             "missing": ["Homebuyer grievance portal SOP"]},
            {"domain": "K-RERA Orders and Circulars",         "have": 0, "total": 3,
             "have_labels": [],
             "missing": ["K-RERA Authority orders","Complaint precedents","COVID extension orders"]},
        ]

        _rr_have  = sum(d["have"] for d in _rera_domains)
        _rr_total = sum(d["total"] for d in _rera_domains)
        _rr_pct   = round(_rr_have / _rr_total * 100) if _rr_total else 0

        _kb_m1, _kb_m2, _kb_m3 = st.columns(3)
        _kb_m1.metric("RERA Domains", len(_rera_domains))
        _kb_m2.metric("Indexed Documents", f"{_rr_have} / {_rr_total}")
        _kb_m3.metric("Corpus Coverage", f"{_rr_pct}%")

        _bar_bg = "#0a1322" if _is_dark else "#dce5f2"
        st.markdown(f"""
        <div style='background:linear-gradient(145deg,#0f1e38 0%,#0a1528 100%);
                    border:1px solid #1e3560;border-radius:14px;padding:24px 28px;margin:22px 0;'>
          <div style='color:#93c5fd;font-size:10px;font-weight:700;text-transform:uppercase;
                      letter-spacing:.1em;margin-bottom:5px;'>Karnataka RERA knowledge base</div>
          <div style='display:flex;align-items:baseline;gap:10px;margin-bottom:14px;'>
            <span style='color:#60a5fa;font-size:40px;font-weight:800;line-height:1;'>{_rr_pct}%</span>
            <span style='color:#a9bed8;font-size:14px;'>coverage</span>
          </div>
          <div style='height:7px;background:{_bar_bg};border-radius:8px;overflow:hidden;margin-bottom:10px;'>
            <div style='height:100%;width:{_rr_pct}%;background:linear-gradient(90deg,#2563eb,#60a5fa);border-radius:8px;'></div>
          </div>
          <div style='color:#a9bed8;font-size:12px;'>{_rr_have} of {_rr_total} planned documents indexed across {len(_rera_domains)} RERA domains</div>
        </div>
        """, unsafe_allow_html=True)

        st.markdown("### RERA coverage by domain")
        from html import escape as _html_escape

        for _dom in _rera_domains:
            _cov_pct = round(_dom["have"] / _dom["total"] * 100) if _dom["total"] else 0
            _status_color = "#16a34a" if _cov_pct >= 80 else ("#d97706" if _cov_pct >= 40 else "#dc2626")
            _have_pills = "".join(
                f"<span style='display:inline-block;background:#dbeafe;color:#1d4ed8;border-radius:6px;padding:5px 8px;margin:3px;font-size:11px;'>✓ {_html_escape(_label)}</span>"
                for _label in _dom["have_labels"]
            )
            _missing_pills = "".join(
                f"<span style='display:inline-block;background:#fee2e2;color:#b91c1c;border-radius:6px;padding:5px 8px;margin:3px;font-size:11px;'>+ {_html_escape(_label)}</span>"
                for _label in _dom["missing"]
            )
            st.markdown(f"""
            <div style='background:{_surface};border:1px solid {_border};border-radius:12px;
                        padding:18px 20px;margin:0 0 12px;'>
              <div style='display:flex;align-items:flex-start;justify-content:space-between;gap:12px;'>
                <div style='font-size:15px;font-weight:700;color:{_text};'>{_html_escape(_dom['domain'])}</div>
                <span style='background:{_status_color}1a;color:{_status_color};border-radius:16px;
                             padding:5px 10px;font-size:11px;font-weight:700;white-space:nowrap;'>{_cov_pct}%</span>
              </div>
              <div style='color:{_muted};font-size:12px;margin:7px 0 10px;'>{_dom['have']} of {_dom['total']} planned documents indexed</div>
              <div style='height:5px;background:{_bar_bg};border-radius:8px;overflow:hidden;margin-bottom:12px;'>
                <div style='height:100%;width:{_cov_pct}%;background:{_status_color};border-radius:8px;'></div>
              </div>
              <div>{_have_pills}{_missing_pills}</div>
            </div>
            """, unsafe_allow_html=True)

        st.caption("Coverage is based on the RERA documents currently mapped to the planned knowledge base domains.")


# ── main app layout ───────────────────────────────────────────────────────────

def main():
    # ── Role toggle — lives in the sidebar, compact ────────────────────────
    with st.sidebar:
        st.markdown(
            "<div style='margin-bottom:2px;font-size:11px;color:#9ca3af;"
            "text-transform:uppercase;letter-spacing:.06em;font-weight:600'>"
            "View</div>",
            unsafe_allow_html=True,
        )
        _role = st.radio(
            "View",
            ["👤 User", "🎖 Compliance", "🔑 Admin"],
            horizontal=True,
            key="app_role",
            label_visibility="collapsed",
        )
        st.markdown("<hr style='margin:6px 0 10px;opacity:.15'>", unsafe_allow_html=True)

    # ── User mode ──────────────────────────────────────────────────────────
    if "User" in _role:
        _sidebar_project_search()
        _sidebar_document_context()

        # ── Past sessions panel — bottom of sidebar ────────────────────────
        with st.sidebar:
            st.markdown("<hr style='margin:4px 0 8px;opacity:.15'>", unsafe_allow_html=True)
            st.markdown(
                "<div style='margin-bottom:6px;font-size:11px;color:#9ca3af;"
                "text-transform:uppercase;letter-spacing:.06em;font-weight:600'>"
                "Past Sessions</div>",
                unsafe_allow_html=True,
            )
            _sessions = _fetch_rera_sessions(limit=5)
            # Exclude the current active session from the list
            _active_sid = st.session_state.get("uf_session_id", "")
            _sessions = [s for s in _sessions if s["session_id"] != _active_sid]

            if _sessions:
                for _s in _sessions:
                    _sid = _s["session_id"]
                    _preview = _s["preview"]
                    _short = _preview[:45] + "…" if len(_preview) > 45 else _preview
                    _cnt = _s.get("count", 1)
                    _raw_dt = _s.get("created_at", "")
                    try:
                        from datetime import datetime as _dt
                        _date_str = _dt.fromisoformat(
                            _raw_dt.replace("Z", "+00:00")
                        ).strftime("%d %b")
                    except Exception:
                        _date_str = ""
                    _msg_label = f"{_cnt} msg{'s' if _cnt != 1 else ''}"
                    _btn_label = f"{_short}"
                    _btn_help = (
                        f"{_preview}\n\n{_msg_label}  ·  {_date_str}"
                        if _date_str else f"{_preview}\n\n{_msg_label}"
                    )
                    # Sub-label row: date + message count
                    st.markdown(
                        f"<div style='font-size:.68rem;color:#6b7280;margin-bottom:1px'>"
                        f"{_date_str} &nbsp;·&nbsp; {_msg_label}</div>",
                        unsafe_allow_html=True,
                    )
                    if st.button(
                        _btn_label,
                        key=f"sess_btn_{_sid[:8]}",
                        use_container_width=True,
                        help=_btn_help,
                    ):
                        # Restore session: load all Q&A pairs as chat history
                        _msgs = _fetch_session_messages(_sid)
                        _restored = []
                        for _m in _msgs:
                            _q_text = (_m.get("question") or "").strip()
                            _a_text = (_m.get("answer_snippet") or "").strip()
                            if _q_text:
                                _restored.append({"role": "user", "content": _q_text})
                            if _a_text:
                                _restored.append({"role": "assistant", "content": _a_text})
                        st.session_state["uf_chat_history"] = _restored
                        st.session_state["uf_session_id"] = _sid
                        st.rerun()
            else:
                st.markdown(
                    "<p style='font-size:.78rem;color:#9ca3af;margin:2px 0 8px'>"
                    "No past sessions yet.</p>",
                    unsafe_allow_html=True,
                )

        st.markdown(
            "<h2 style='margin-bottom:2px'>Karnataka RERA Property Advisor</h2>"
            "<p style='color:#6b7280;margin-top:0;font-size:.9rem'>"
            "IIMB PGPBA Group 9 | CCS RAG Compliance Assistant</p>",
            unsafe_allow_html=True,
        )
        components.html(DEMO_HTML, height=62)
        st.markdown(
            "<div style='background:#f0fdf4;border-left:4px solid #22c55e;"
            "border-radius:6px;padding:10px 14px;margin-bottom:16px;font-size:.85rem;color:#166534'>"
            "Upload brochures or paste a URL in the sidebar to run a full due diligence report. "
            "Or just ask any question below about Karnataka RERA, prices, or builders."
            "</div>",
            unsafe_allow_html=True,
        )
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
        _render_chat()

    # ── Compliance Officer mode ────────────────────────────────────────────
    elif "Compliance" in _role:
        _render_co_tabs()

    # ── Admin mode ────────────────────────────────────────────────────────
    elif "Admin" in _role:
        _render_admin_mode()


main()

