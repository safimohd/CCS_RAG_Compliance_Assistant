# Going live: two apps, both warm

Written 11 Oct 2026. Follow in order. `DEPLOY.md` covers deploying one app and
publishing the index; this covers the two things it does not: **two separate
apps**, and **staying fast when nobody warned us a reviewer is coming**.

Prof. Majumdar's feedback of 9 Oct, items 1 and 3:

> Two separate apps, healthcare and RERA, to demo to different users.
> Keep the server warm before sharing.

The second cannot be followed as advice, because he forwards the link without
telling us first. It has to be automatic.

---

## Why a woken app is slow

A Streamlit Community Cloud app sleeps after **12 hours** of inactivity
(reduced from 7 days). Waking it gives you a fresh container, and a fresh
container has to do all of this before it can answer one question:

1. install torch and sentence-transformers,
2. download the ~150 MB index from the GitHub Release,
3. extract it,
4. pull the BGE embedding model from Hugging Face.

That is minutes. A reviewer who clicks a link and waits three minutes has
already formed an opinion. Everything below exists to make sure the *robot*
pays that cost, never a person.

---

## Step 1 — Two entry files, because one script is one app

Community Cloud identifies an app by **repo + branch + main file path**.
Deploying the same script twice just redeploys the first app. So the repo now
has two tiny entry files, already written:

| File | Sets | Deploy as |
|---|---|---|
| `05_App/app_healthcare.py` | `APP_DOMAIN=Healthcare` | app 1 |
| `05_App/app_rera.py` | `APP_DOMAIN=RERA` | app 2 |

Each sets the domain and then runs the shared `app.py`. Nothing is duplicated,
so a fix lands in both apps at once.

The domain is set **in the entry file, not in Secrets**, on purpose: `_secret()`
in `app.py` reads environment variables before Streamlit Secrets, so the entry
file wins. A stale `APP_DOMAIN` left in a Secrets panel cannot flip an app to
the wrong domain. Verified.

`streamlit run app.py` locally still shows the domain selector, unchanged.

## Step 2 — Push

```powershell
cd "C:\Users\rahma\OneDrive - Indian Institute of Management\Desktop\Term 5\CCS\RAG_Project"
git add -A
git status --short
```

Before committing, confirm these do **not** appear in the list:

- `.env`
- `05_App/chroma_db/...`
- `05_App/chroma_db.tar.gz`
- anything under `02_Corpus/` ending `.pdf`

`.gitignore` already covers all four, but check the list rather than trusting
it: a `.gitignore` write once reported success without reaching disk, a 156 MB
tar.gz got staged, and the push was rejected. If anything looks wrong:

```powershell
git check-ignore -v 05_App/chroma_db.tar.gz 05_App/.env
```

Then commit and push:

```powershell
git commit -m "Scope filter in the query; two deployment entry points; keep-warm workflow"
git push
```

## Step 3 — Deploy both apps

At **share.streamlit.io**, twice:

| | App 1 | App 2 |
|---|---|---|
| Main file path | `05_App/app_healthcare.py` | `05_App/app_rera.py` |
| Suggested URL | `…-healthcare` | `…-rera` |

Secrets for **both** apps, identical except nothing — `APP_DOMAIN` is no longer
needed here:

```toml
OPENAI_API_KEY = "sk-proj-..."
INDEX_URL = "https://github.com/safimohd/CCS_RAG_Compliance_Assistant/releases/download/index-v1/chroma_db.tar.gz"
LLM_REASONING_EFFORT = "low"
DAILY_QUESTION_LIMIT = "100"
CONTACT_EMAIL = "rahmansafi2000@gmail.com"
```

TOML wants the quotes, unlike `.env`.

First boot of each app takes several minutes. Watch it finish before moving on.

## Step 4 — Make both public

**This is not optional, and not only about convenience.** A private app cannot
be kept warm: an anonymous request reaches the Google sign-in page, not the
app, so no monitor can touch it and the 12-hour timer keeps running.

For each app: **Settings → Sharing →** set to public / anyone with the link.

The spend risk that privacy was covering is handled inside the app instead:
`DAILY_QUESTION_LIMIT` caps generations per day, cached answers are free and
deliberately uncounted, and retrieval costs nothing. The worst case is a number
you chose rather than an open tap.

## Step 5 — Turn on the keep-warm workflow

`.github/workflows/keep-warm.yml` is in the repo. It pings both apps every 10
minutes, which keeps them far from the 12-hour threshold and means that if a
container is recycled, the workflow absorbs the cold start.

**Edit the two URLs in it** after Step 3:

```yaml
HEALTHCARE_URL: https://your-real-healthcare-url.streamlit.app
RERA_URL: https://your-real-rera-url.streamlit.app
```

Until you do, it skips with a warning rather than failing. Free on public
repositories. It also logs a warning whenever a ping takes over 30 seconds,
which is how you find out a recycle happened.

To warm the apps by hand before a demo: **Actions → keep apps warm → Run
workflow**.

### If an app still sleeps

Streamlit staff have **not** confirmed that HTTP traffic resets the sleep
timer; the only keep-alive they endorse is a scheduled commit to the repo. The
ping is the cheaper thing to try first and almost certainly counts as activity.
If you observe an app asleep despite the workflow running green, add a second
job that pushes an empty commit every 8 hours:

```yaml
  nudge:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }
      - run: |
          git config user.name  "keep-warm"
          git config user.email "noreply@github.com"
          git commit --allow-empty -m "keep app awake [skip ci]"
          git push
```

It works, but it puts an empty commit in the history every 8 hours, which is
untidy in a graded repository. Only reach for it if the ping proves not enough.

### Optional: alerting

The workflow tells you an app is down only if you look at the Actions tab. If
you want a notification, add a free uptime monitor (UptimeRobot and similar
offer 5-minute checks) on both URLs. Not required for speed — the workflow
already handles that — only for being told.

---

## Step 6 — Before any demo, 30 seconds

```
Open both URLs. Ask one question in each.
```

If both answer in a few seconds, they are warm. If one takes minutes, it was
recycled and is now warm for the next 12 hours — which is exactly why you
checked.

---

## What "fast" actually means here

| Situation | Time to first answer |
|---|---|
| Warm container, question already cached | near-instant |
| Warm container, new question | a few seconds of retrieval plus generation |
| Recycled container (what the workflow is there to prevent) | minutes |

Retrieval is local and free. Generation is the only API spend, and
`choose_effort()` already keeps it at the low end — lowering reasoning effort
cut latency from about 60s to 30s *and* improved the FY2023-24 answer, because
less reasoning meant less gap-filling.

---

## Do not deploy the evaluation

`run_evaluation.py` stays local. It writes into the benchmark workbooks, needs
API keys, and is not a web app. Nothing in these two deployments touches it.
