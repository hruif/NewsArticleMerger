# News Article Merger

A small Flask web app that takes a topic, pulls several news articles about it
from different outlets, and uses Google Gemini to merge them into a single
article that cross-references the sources and presents multiple perspectives.

The homepage also surfaces **trending topics** — stories currently being covered
by the most distinct news sources — that you can click to read a merged write-up.

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

- Python 3.9+ (developed on 3.12)
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

| Env var             | Default                  | Purpose                                                       |
| ------------------- | ------------------------ | ------------------------------------------------------------ |
| `NEWSDATA_KEY`      | _(falls back to file)_   | newsdata.io key                                              |
| `GEMINI_API_KEY`    | _(falls back to file)_   | Gemini key                                                   |
| `GEMINI_MODEL`      | `gemini-2.5-flash-lite`  | Gemini model (`gemini-3.1-flash-lite` is richer but slower)  |
| `TOPICS_CATEGORIES` | `business,technology,science,health,world` | Categories swept for trending topics (1 credit each) |
| `TOPICS_CACHE_TTL`  | `3600`                   | Seconds before trending topics are re-swept                  |
| `CACHE_DIR`         | `<repo>/cache`           | Where the JSON cache is written (point at a disk in prod)    |
| `FLASK_DEBUG`       | _(off)_                  | Set to `1` to enable the dev reloader/debugger locally       |

## Caching

Results are cached on disk under `cache/` (git-ignored):

- **Merged articles** are cached per query — repeat searches load instantly.
- **Trending topics** persist across restarts and refresh automatically every
  `TOPICS_CACHE_TTL` seconds (default 60 min).

Both have a **Refresh** link in the UI to force a fresh result. To clear the
cache entirely, delete the `cache/` directory.

> **Note:** the cache lives on the instance's filesystem, which is ephemeral on
> most hosts (it resets on redeploy/restart and isn't shared across instances).
> That's fine here — entries simply regenerate. Point `CACHE_DIR` at a mounted
> disk if you want it to persist.

## Deploy (Render)

This repo includes a [`render.yaml`](render.yaml) blueprint.

1. Push the repo to GitHub.
2. In [Render](https://render.com), create a new **Blueprint** from the repo.
3. Set the two secrets in the dashboard: `NEWSDATA_KEY` and `GEMINI_API_KEY`.
4. Deploy. Render builds with `pip install -r requirements.txt` and serves with
   gunicorn (gthread workers, so streaming/SSE works).

Notes:
- The **free** instance sleeps after ~15 min idle and cold-starts (~30–60s) on
  the next visit.
- Mind the **newsdata.io budget (200 credits/day)**: a search costs 1 credit and a
  trending sweep costs 1 credit per category in `TOPICS_CATEGORIES`. Raise
  `TOPICS_CACHE_TTL` or trim `TOPICS_CATEGORIES` to conserve credits.

## Notes

- The trending-topic sweep is restricted to US headlines (`country=us`).
- newsdata.io's free tier only searches roughly the **last 48 hours**, so very old
  or evergreen topics may return few articles.

## Future ideas

- **Search controls** — let the user tune the query: date range, recency vs.
  relevancy sort, number of articles, region/language. Deliberately left out for
  now to keep the UI simple; newsdata.io supports most of these, so it can be
  added later if needed.
- Cache eviction (TTL / size cap) for the per-query article cache.
