# Handoff — News Article Merger

> A handoff note for the next agent/Claude (or human) picking up this project.
> Written 2026-06-16. Project is in a healthy, deployed, fully-working state.

## What this project is

A Flask web app that, given a search topic, scrapes several recent news articles
about it from different sources and uses Google Gemini to **merge them into one
streamed, well-structured article** with inline citations, a TL;DR, and a
"differing perspectives" panel. The homepage shows AI-curated **trending topics**.

Live: **https://hruif-news-article-merger.hf.space** (Hugging Face Spaces, Docker).

## Current state (trust but verify)

- `main` is the working branch. Both remotes are in sync with local `main`:
  - GitHub `origin` (`https://github.com/hruif/NewsArticleMerger.git`)
  - HF `space` (`https://hruif@huggingface.co/spaces/hruif/news-article-merger`)
- All tests pass (smoke + security + one live end-to-end). See "Testing" below.
- Deployment is current; HF auto-rebuilds on push to `space`.
- Run `git status` / `git log --oneline -5` before assuming the above still holds.

## Architecture / file map

All app code lives in `app/` and modules import each other by **bare name**
(`import main`, `import config`, ...), so commands must run **from inside `app/`**.

- `app/config.py` — `ROOT_DIR` + `load_key(env_var, file_name)`: env var first,
  then `<repo-root>/<file>`. Single source of truth for key loading.
- `app/cache.py` — file-based JSON cache. `load(name, default)` / atomic `save(name, data)`
  (tempfile + `os.replace`). Dir = `$CACHE_DIR` or `<repo-root>/cache`.
- `app/scraper.py` — `Scraper`:
  - `get_articles(query, n)` → `list[{title,url,name,text}]`, diversity-ordered,
    parallel scrape (ThreadPoolExecutor), browser User-Agent to beat 403s.
  - `get_trending_topics(refresh=False)` → `{"top":[...], "notable":[...]}`:
    category sweep → keyword clustering (seed-anchored) → rank by distinct source
    count → slop regex prefilter → Gemini curation (`merger.curate_topics`).
  - News source: **newsdata.io** `/latest` endpoint.
- `app/merger.py` — `Merger`, Gemini via the **`google-genai`** SDK (`from google import genai`):
  - `merge_stream(sources)` generator → yields `("delta", html_so_far)` then
    `("done", {article_html, tldr, perspectives})`. Splits model output on
    `===METADATA===` delimiter; tolerant JSON parse of the trailer.
  - `curate_topics(candidates)` → `(important, notable)` or `None` on failure.
  - Model: `gemini-2.5-flash-lite` (fast; override via `GEMINI_MODEL`).
- `app/main.py` — pipeline glue + per-query article cache (`articles.json`):
  `get_topics`, `get_sources`, `get_cached` (ignores legacy entries without
  `article_html`), `save_article`, `list_recent`.
- `app/app.py` — Flask routes + SSE + **nh3 sanitization**:
  - `/` search page + trending, `/results` loading screen, `/recent` cached list,
    `GET /api/article/stream` (SSE: `delta`/`done`/`error` events).
  - `_clean()` runs ALL model HTML through nh3 (allow-list incl. `mark`/`sup`/
    `data-*`/heading `id`s). Only successful results are cached.
- `app/templates/` — `base.html` (masthead + fonts), `search.html`, `results.html`
  (all the streaming/citation/perspective/export JS), `recent.html`.
- `app/static/style.css` — editorial/publication theme (CSS vars, serif fonts).
- `Dockerfile` — python:3.12-slim, non-root uid 1000, gunicorn gthread on port 7860.
- `render.yaml` — alt deploy (unused; Render wanted a credit card).

## How to run locally

```
cd app
../venv/bin/python -c "import app as a; a.app.run(port=5000)"   # or:
PORT=5000 ../venv/bin/python app.py
```
Then open http://localhost:5000. Requires the two API keys (see Secrets).

## Secrets / keys (IMPORTANT — never commit)

Keys are loaded env-var-first, else from `<repo-root>/*.txt` files, which are
**git-ignored** (`*.txt` in `.gitignore`) and **excluded from the Docker image**
(`.dockerignore`). Never commit them or bake them into the image.

- `NEWSDATA_KEY` / `newsdata_key.txt` — newsdata.io (free tier 200 credits/day).
- `GEMINI_API_KEY` / `gemini_api_key.txt` — Google Gemini.
- In production these are set as **HF Space secrets** (Settings → Variables and
  secrets). Missing either crashes the container on boot (keys read at import).
- `newsapi_key.txt` is dead (newsapi.org was dropped — its free plan forbids
  production use). Safe to ignore.

## Deploy / push auth

- **GitHub:** `git push origin main` (uses the `gh` CLI credential helper; works).
- **HF Space:** `git push space main`. The HF write token is stored in the macOS
  keychain, so this authenticates automatically — no inline token. HF auto-rebuilds.
  - If it ever 401s ("not authorized"), the keychain entry is stale. Erase it:
    `printf 'protocol=https\nhost=huggingface.co\n\n' | git credential-osxkeychain erase`
    then run `git push space main` in a **real terminal** (not a non-TTY shell) and
    paste a fresh HF **Write** token at the password prompt to re-store it.
  - A write token was once pasted in chat and is **compromised/revoked** — never
    reuse it; always mint a fresh one.

## Testing

No committed test files; tests are ad-hoc. The standard battery:
1. Import smoke (all modules import from `app/`).
2. Security: `_clean()` strips `<script>`/`onerror`/`<img>` but keeps
   `mark`/`sup`/`data-*`; `/results` escapes the query; empty-query stream returns
   a `done` event with no API call.
3. Live end-to-end: `GET /api/article/stream?query=...&refresh=1` → multiple
   `delta` events, then `done` with non-empty tldr/sources; ~15-18s. Uses real API
   credits, so use sparingly.

## Known quirks / gotchas

- Modules import by bare name → **always `cd app` first**.
- Some sources 403 on scrape (e.g. financialexpress); they're skipped gracefully —
  not a failure as long as enough sources succeed.
- The lite model emits perspective `<mark>` highlights only best-effort; the
  always-on "Differing perspectives" panel is the reliable surface. Do NOT
  reintroduce server-side quote-matching (it was tried and removed — the model
  quotes source text, not its own rewritten prose, so matches were ~0%).
- newsdata.io free tier = 200 credits/day; trending sweep + searches consume them.

## Optional / not-yet-done (only if asked)

- Deferred feature idea: search controls (date range, recency-vs-relevance sort,
  article count, region/language) — documented in `README.md` future ideas.
- LinkedIn write-up for the project (user was drafting one).

## Pointers

- Persistent project memory: `~/.claude/projects/-Users-ruifan-Documents-GitHub-NewsArticleMerger/memory/`
  (`MEMORY.md` index + `deployment.md`). Loaded automatically each session.
- Prior full transcript (if deep history needed):
  `~/.claude/projects/-Users-ruifan-Documents-GitHub-NewsArticleMerger/*.jsonl`.
