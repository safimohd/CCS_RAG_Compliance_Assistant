# Running the shared demo

Written 9 Oct 2026, after Prof. Majumdar's feedback and the news that the link
is forwarded to domain reviewers (Prof. Arnab for healthcare, someone to be
named for real estate) **without us being told in advance**.

That last fact drives most of what follows. "Wake the app fifteen minutes before
sharing" is not advice we can act on, so the app has to stay warm by itself.

---

## 1. Why the app had to become public

The standard way to keep a Community Cloud app warm is an uptime service
requesting the URL every few minutes. A private app cannot be pinged: an
anonymous request only ever reaches the Google sign-in page, so the container
still goes dormant. **Private and always-warm are mutually exclusive here.**

We made it public and moved the protection inside the app instead:

- `DAILY_QUESTION_LIMIT` caps generations per day. Beyond it, the app still
  searches and shows sources but stops calling the API, with a civil message.
- Cached answers are **not** counted — they cost nothing.
- The counter keeps only today's date, so it resets itself.

A password gate was considered and rejected. The link gets forwarded without
warning, so it would be forwarded without the password too, and a reviewer would
hit a wall. A wall is worse than a slow page.

---

## 2. Two deployments, one file

`APP_DOMAIN` in each app's secrets locks that deployment to one domain and hides
the domain selector. Both run from the same repo, branch and entry point.

| | Healthcare app | RERA app |
|---|---|---|
| Main file | `05_App/app.py` | `05_App/app.py` |
| `APP_DOMAIN` | `Healthcare` | `RERA` |

Everything else in the secrets is identical.

### Secrets for each app

```toml
OPENAI_API_KEY = "sk-proj-..."
INDEX_URL = "https://github.com/safimohd/CCS_RAG_Compliance_Assistant/releases/download/index-v1/chroma_db.tar.gz"
APP_DOMAIN = "Healthcare"          # or "RERA" in the second app
LLM_REASONING_EFFORT = "low"
DAILY_QUESTION_LIMIT = "100"
CONTACT_EMAIL = "rahmansafi2000@gmail.com"
```

### Creating the second app
1. share.streamlit.io → **Create app** → *Deploy a public app from GitHub*
2. Same repository, branch `main`, main file `05_App/app.py`
3. Choose a distinct URL — something like `rera-compliance-assistant`
4. Advanced settings → Python 3.12 → paste the secrets above with
   `APP_DOMAIN = "RERA"`
5. Deploy

### Switching the existing app to public
App settings → **Sharing** → "Anyone with the link can view this app". Then add
`APP_DOMAIN` to its secrets so it stops showing both domains.

---

## 3. Keeping both warm

Use any free uptime monitor — cron-job.org and UptimeRobot both work.

- One check per app URL, every **5 minutes**
- Method GET, no authentication
- These are page loads only. They never ask a question, so they cost nothing
  against the API key and do not touch the daily cap.

First load after a genuine cold start still takes several minutes, because the
149MB document library has to be downloaded again. The monitor's job is to make
sure that cold start essentially never happens while anyone might be looking.

---

## 4. What changed in the interface, and why

Prof. Majumdar's three points plus his earlier note about minimising technical
detail were all the same problem: the app was instrumented for the people
building it, not for someone checking a regulation.

**The dead buttons were a real bug, not a misunderstanding.** The tool buttons
set a question and re-ran the app, and the answer rendered in the *Assistant*
tab. Streamlit keeps the active tab across a re-run, so a reader on the
"Healthcare tools" tab saw nothing happen. The tabs are gone. Example questions
now sit directly above the chat and ask in place, which removes the bug and the
need to explain it.

**Removed from view:** similarity scores, passage counts, tier letters, the
reasoning-effort indicator, the "binding evidence" percentage, retrieved-passage
sliders and the internal corpus folder names that were appearing under every
source.

**Kept, in plainer words:** whether a source binds the reader. "Core regulatory
(binding)" became **Binding regulation**; "Supporting guidance (explanatory, not
binding)" became **Guidance — not binding**. That distinction is the point of
the whole system and the thing no general-purpose tool offers.

**Replaced:** the evidence percentage became a sentence — *"Based on 2 binding
regulations and 1 guidance document."* It counts documents rather than
passages, deliberately: six extracts from one circular is one source, and
showing six would mislead exactly where someone is most likely to rely on it.

**Scope filters stayed**, reworded. They are genuinely user-facing and they fix
two measured retrieval failures — circulars that differ only by financial year,
and facility standards published as near-identical templates.

---

## 5. Worth telling Prof. Majumdar

- The app is now **public**, because keeping it warm requires it. Spend on his
  key is capped at `DAILY_QUESTION_LIMIT` answers a day.
- Two separate links, one per domain, as he asked.
- The buttons he clicked were genuinely broken, not unclear. Fixed.
- If he would rather it were not public, the alternative is a private app and a
  three-to-five minute wait on every forwarded link.
