# Deploying to Streamlit Community Cloud

Written 27 Sep 2026. Run these in order. The code changes are already made —
what remains is git, the index upload, and the Streamlit setup.

---

## Why the index is not in the repository

`chroma_db/` is ~290 MB, and `chroma.sqlite3` alone is 247 MB. **GitHub hard-
rejects any single file over 100 MB.** Git LFS would work but its free tier is
1 GB of bandwidth a month, which a few redeploys would exhaust.

So the index ships as a **GitHub Release asset** — 2 GB per file, free, no LFS
quota — and `app.py` downloads it on first boot into the container's local disk.
Rebuilding on the server is not an option: that is a six-hour job.

---

## Step 1 — Verify nothing secret is staged

```powershell
cd "C:\Users\rahma\OneDrive - Indian Institute of Management\Desktop\Term 5\CCS\RAG_Project"
git init
git add -A
git status --short
```

**Read the list before committing.** These must NOT appear:

- `.env`
- `05_App/chroma_db/...`
- `05_App/.answer_cache.json`
- anything under `02_Corpus/` ending `.pdf`
- anything under `08_Archive/`

If `.env` appears, stop. `git rm --cached .env`, confirm `.gitignore` has it,
re-check. A key pushed to a public repo is compromised and must be revoked at
the provider, not just deleted from the repo.

## Step 2 — First commit

```powershell
git commit -m "Indian Regulatory Compliance Intelligence Assistant

Authority-tiered RAG over 345 primary-source documents across Healthcare
and Real Estate/RERA. LangChain + ChromaDB + BGE-small, Streamlit UI,
benchmark of 100 questions and an evaluation runner."
```

## Step 3 — Create the GitHub repository and push

With the GitHub CLI:

```powershell
gh auth login
gh repo create rag-compliance-assistant --public --source=. --remote=origin --push
```

Without it: create an empty repo on github.com, then

```powershell
git remote add origin https://github.com/<you>/rag-compliance-assistant.git
git branch -M main
git push -u origin main
```

## Step 4 — Package and publish the index

```powershell
cd 05_App
tar -czf chroma_db.tar.gz chroma_db
dir chroma_db.tar.gz
```

Expect roughly 120–160 MB. `tar` ships with Windows 10 and later.

```powershell
cd ..
gh release create index-v1 "05_App\chroma_db.tar.gz" ^
  --title "Corpus index v1" ^
  --notes "Chroma index: 345 documents, 25,955 passages, BAAI/bge-small-en-v1.5, built 27 Sep 2026."
```

Or upload it by hand at **Releases → Draft a new release → attach binary**.

Then copy the asset's download URL. It looks like:

```
https://github.com/<you>/rag-compliance-assistant/releases/download/index-v1/chroma_db.tar.gz
```

**Do not commit `chroma_db.tar.gz`** — delete it from `05_App/` afterwards, or
add it to `.gitignore`.

## Step 5 — Deploy

1. Go to **share.streamlit.io** and sign in with GitHub
2. **New app** → pick the repo, branch `main`
3. **Main file path:** `05_App/app.py`
4. Open **Advanced settings → Secrets** and paste:

```toml
OPENAI_API_KEY = "sk-..."
INDEX_URL = "https://github.com/<you>/rag-compliance-assistant/releases/download/index-v1/chroma_db.tar.gz"
LLM_REASONING_EFFORT = "low"
```

No quotes problem here — TOML wants the quotes, unlike `.env`.

5. Deploy.

**First boot takes several minutes:** installing torch and sentence-transformers,
downloading the 150 MB index, then pulling the BGE model from Hugging Face. The
status panel reports each stage. Subsequent loads are fast.

---

## What was changed in the code for this

| Change | Why |
|---|---|
| `pysqlite3` shim at the top of `app.py` | Chroma needs SQLite ≥ 3.35; the Cloud image ships older. Must run before anything imports chromadb. Degrades quietly on Windows, where system sqlite3 is already new enough. |
| `pysqlite3-binary; sys_platform == "linux"` in requirements | The package has no Windows wheel. The marker keeps local installs working. |
| `_secrets_into_env()` | Cloud has no `.env`. Reads `st.secrets` as a fallback; `.env` still wins locally, so nothing changes on your machine. |
| `ensure_index()` | Downloads and extracts the Release asset when `chroma_db/` is absent. No-ops locally. |

---

## Limits and what will bite

[Streamlit Community Cloud](https://docs.streamlit.io/deploy/streamlit-community-cloud/manage-your-app)
gives 0.078–2 CPU cores, 690 MB–2.7 GB RAM and up to 50 GB storage per app.

- **Memory is the real constraint.** torch, sentence-transformers, the BGE model
  and Chroma with 25,955 vectors should sit near 700 MB–1 GB. It fits, but not
  with much room. If you see "this app has gone over its resource limits", the
  first lever is rebuilding the index without tier E, which drops 117 documents
  that retrieval excludes by default anyway.
- **Apps sleep after inactivity.** Waking one is a fresh container, so the index
  downloads again. Wake it a few minutes before any demo.
- **The answer cache does not persist** across container restarts in the cloud.
  It still works within a session.
- **A public repo means public code.** That is fine and expected here — but it is
  the reason Step 1 matters.

---

## Do not deploy the evaluation

`run_evaluation.py` stays local. It writes into the benchmark workbooks, needs
both API keys, and takes 30–45 minutes. It is not a web app and should never be
triggered from one.
