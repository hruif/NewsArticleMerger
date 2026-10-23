---
title: News Article Merger
emoji: 📰
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
---

# News Article Merger

A small Flask web app that takes a topic, pulls several news articles about it
from different outlets, and uses Google Gemini to merge them into a single
article that cross-references the sources and presents multiple perspectives.

The homepage also surfaces **trending topics** — stories currently being covered
by the most distinct news sources — that you can click to read a merged write-up.

Searches support two modes:

- **Best coverage** (default) looks back roughly a week and prioritizes relevance
  and source quality, with recency used as a tie-breaker.
- **Latest** searches the fresher live feed and gives recent articles a stronger,
  capped boost.

The merged article **streams in live** as Gemini writes it, with:

- **Inline citations** linking claims to a numbered **Sources** list
- **Hover-perspectives** — contested points are highlighted; hover to see how the
  sources differ
- a **TL;DR** whose items jump to the relevant section
- **export**: copy as Markdown, print / save as PDF, or copy a shareable link
- a **Recent articles** page backed by the on-disk cache

## How it works

```
query ─▶ newsdata.io (find article URLs) ─▶ newspaper3k (scrape text, in parallel)
      ─▶ Gemini (stream a merged HTML article) ─▶ browser (Server-Sent Events)
```

- `app/app.py` — Flask routes (search, results, `/recent`, SSE article stream)
- `app/main.py` — pipeline glue + per-query article cache
- `app/scraper.py` — newsdata.io search, parallel scraping, trending-topic detection
- `app/merger.py` — Gemini streaming merge (article + citation/perspective markers)
- `app/cache.py` — JSON file cache (survives restarts)
- `app/config.py` — shared paths + API-key loading

## Requirements

- Python 3.12.15 (see `.python-version`)
- A [newsdata.io](https://newsdata.io/) API key (free tier: 200 credits/day,
  commercial use allowed) and a [Google Gemini](https://aistudio.google.com/apikey) API key

## Setup

```bash
# from the repo root
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### API keys

Provide the two keys in **either** way (environment variables take precedence):

**Option A — environment variables:**

```bash
export NEWSDATA_KEY="your-newsdata-key"
export GEMINI_API_KEY="your-gemini-key"
```

**Option B — key files** at the repo root (each containing just the key on the
first line). These are git-ignored:

```bash
echo "your-newsdata-key" > newsdata_key.txt
echo "your-gemini-key"   > gemini_api_key.txt
```

## Run (local)

```bash
cd app
python app.py            # add FLASK_DEBUG=1 for auto-reload during development
```

Then open <http://127.0.0.1:5000/>.

Search any topic, or click a trending topic. The article streams in as it is
generated (typically a few seconds), then the TL;DR, citations, sources, and
hover-perspectives become interactive.

For a production-style run, use the same gunicorn command as the deploy:

```bash
gunicorn --chdir app --worker-class gthread --workers 1 --threads 8 --timeout 120 app:app
```

## Configuration

| Env var                         | Default                  | Purpose                                                       |
| ------------------------------- | ------------------------ | ------------------------------------------------------------ |
| `NEWSDATA_KEY`                  | _(falls back to file)_   | newsdata.io key                                              |
| `GEMINI_API_KEY`                | _(falls back to file)_   | Gemini key                                                   |
| `GEMINI_MODEL`                  | `gemini-2.5-flash-lite`  | Gemini model (`gemini-3.1-flash-lite` is richer but slower)  |
| `NEWS_SEARCH_LOOKBACK_DAYS`     | `7`                      | Regular searches use the archive endpoint over roughly this many days |
| `NEWS_SEARCH_PAGES`             | `2`                      | Archive pages to scan before ranking candidates locally      |
| `NEWS_SEARCH_SIZE`              | `10`                     | Requested articles per archive page (free plans may cap this) |
| `NEWS_PRIORITY_DOMAIN`          | `medium`                 | Prefer higher-authority domains (`top`, `medium`, `low`, or empty) |
| `NEWS_CANDIDATE_MULTIPLIER`     | `4`                      | Scrape this many candidates per final source needed          |
| `NEWS_LATEST_TIMEFRAME_HOURS`   | `48`                     | Latest-mode search window, capped at 48 hours                |
| `TOPICS_CATEGORIES`             | `top,world,politics,business,science` | Categories swept for trending topics (1 credit each) |
| `TOPICS_CACHE_TTL`              | `3600`                   | Seconds before trending topics are re-swept                  |
| `CACHE_DIR`                     | `<repo>/cache`           | Where the JSON cache is written (point at a disk in prod)    |
| `FLASK_DEBUG`                   | _(off)_                  | Set to `1` to enable the dev reloader/debugger locally       |

## Caching

Results are cached on disk under `cache/` (git-ignored):

- **Merged articles** are cached per query — repeat searches load instantly.
- **Trending topics** persist across restarts and refresh automatically every
  `TOPICS_CACHE_TTL` seconds (default 60 min).

Both have a **Refresh** link in the UI to force a fresh result. Article cache
entries are keyed by both query and search mode, and versioned so older search
strategies regenerate automatically after code changes. To clear the cache
entirely, delete the `cache/` directory.

> **Note:** the cache lives on the instance's filesystem, which is ephemeral on
> most hosts (it resets on redeploy/restart and isn't shared across instances).
> That's fine here — entries simply regenerate. Point `CACHE_DIR` at a mounted
> disk if you want it to persist.

## Deploy

The app is a standard gunicorn web service, so it runs on most hosts. Two recipes:

### Hugging Face Spaces (free, no credit card)

This repo includes a [`Dockerfile`](Dockerfile) and the Space config (the YAML at
the top of this README — `sdk: docker`, `app_port: 7860`).

1. Create a free account at [huggingface.co](https://huggingface.co).
2. **New Space** → SDK **Docker** → **Blank** → name it (e.g. `news-article-merger`).
3. In the Space's **Settings → Variables and secrets**, add secrets `NEWSDATA_KEY`
   and `GEMINI_API_KEY`.
4. Push this code to the Space's git repo (auth with your HF username + a write
   token from <https://huggingface.co/settings/tokens>):
   ```bash
   git remote add space https://huggingface.co/spaces/<your-username>/news-article-merger
   git push space main
   ```
   The Space builds the Dockerfile and serves at `https://<user>-news-article-merger.hf.space`.

### Render

A [`render.yaml`](render.yaml) blueprint is also included. Note: the *Blueprint*
flow currently asks for a card — create a plain **Web Service** instead (Build:
`pip install -r requirements.txt`; Start: the gunicorn command from the local-run
section but with `--bind 0.0.0.0:$PORT`), and set the two secrets. The free
instance sleeps after ~15 min idle and cold-starts on the next visit.

> **Budget:** mind the **newsdata.io free limit (200 credits/day)** — a search is 1
> credit and a trending sweep is 1 credit per category in `TOPICS_CATEGORIES`.
> Raise `TOPICS_CACHE_TTL` or trim `TOPICS_CATEGORIES` to conserve credits.

## Notes

- Regular searches prefer newsdata.io's `/archive` endpoint over roughly the past
  week, then locally rank a wider candidate pool by query relevance before
  scraping. The default **Best coverage** mode uses this path. If archive search
  is unavailable or empty, the app falls back to `/latest`.
- **Latest** mode uses newsdata.io's `/latest` endpoint and gives recency more
  weight while still requiring query relevance.
- The trending-topic sweep is restricted to US headlines (`country=us`) and still
  uses `/latest`.
- newsdata.io's archive access is plan-dependent; accounts limited to `/latest`
  will still only search roughly the **last 48 hours**.

## Future ideas

- **Search controls** — let the user tune the query: date range, recency vs.
  relevancy sort, number of articles, region/language. Deliberately left out for
  now to keep the UI simple; newsdata.io supports most of these, so it can be
  added later if needed.
- Cache eviction (TTL / size cap) for the per-query article cache.

## Reproducible Python dependencies

Use Python 3.12 and install the checked-in security-reviewed lock file:

```sh
python -m venv .venv
source .venv/bin/activate
python -m pip install --require-hashes -r requirements.txt
```

To refresh all pinned dependencies with uv 0.13.5 or later:

```sh
uv pip compile requirements.in --universal --python-version 3.12 --generate-hashes --upgrade -o requirements.txt
```

Review the diff, run the project checks, and audit the resulting environment before committing an update.
