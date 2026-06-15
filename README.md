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
query ─▶ NewsAPI (find articles) ─▶ newspaper3k (scrape text, in parallel)
      ─▶ Gemini (stream a merged HTML article) ─▶ browser (Server-Sent Events)
```

- `app/app.py` — Flask routes (search, results, `/recent`, SSE article stream)
- `app/main.py` — pipeline glue + per-query article cache
- `app/scraper.py` — NewsAPI search, parallel scraping, trending-topic detection
- `app/merger.py` — Gemini streaming merge (article + citation/perspective markers)
- `app/cache.py` / `app/config.py` — JSON file cache + shared paths/key loading
- `app/cache.py` — JSON file cache (survives restarts)
- `app/config.py` — shared paths + API-key loading

## Requirements

- Python 3.9+ (developed on 3.12)
- A [NewsAPI](https://newsapi.org/) key and a [Google Gemini](https://aistudio.google.com/apikey) API key

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
export NEWSAPI_KEY="your-newsapi-key"
export GEMINI_API_KEY="your-gemini-key"
```

**Option B — key files** at the repo root (each containing just the key on the
first line). These are git-ignored:

```bash
echo "your-newsapi-key" > newsapi_key.txt
echo "your-gemini-key"  > gemini_api_key.txt
```

## Run

```bash
cd app
python app.py
```

Then open <http://127.0.0.1:5000/>.

Search any topic, or click a trending topic. The article streams in as it is
generated (typically a few seconds), then the TL;DR, citations, sources, and
hover-perspectives become interactive.

## Configuration

| Env var          | Default                 | Purpose                                            |
| ---------------- | ----------------------- | -------------------------------------------------- |
| `NEWSAPI_KEY`    | _(falls back to file)_  | NewsAPI key                                        |
| `GEMINI_API_KEY` | _(falls back to file)_  | Gemini key                                         |
| `GEMINI_MODEL`   | `gemini-2.5-flash-lite` | Gemini model (e.g. `gemini-3.1-flash-lite` is richer but slower) |

## Caching

Results are cached on disk under `cache/` (git-ignored):

- **Merged articles** are cached per query — repeat searches load instantly.
- **Trending topics** persist across restarts and refresh automatically every
  15 minutes.

Both have a **Refresh** link in the UI to force a fresh result. To clear the
cache entirely, delete the `cache/` directory.

> **Note:** the cached article format changed when citations/perspectives were
> added. Old `articles.json` entries are ignored (regenerated on next search), so
> you can simply delete `cache/articles.json` after upgrading.

## Notes

- The trending-topic sweep is hardcoded to US headlines and costs several
  NewsAPI requests; the free NewsAPI tier allows 100 requests/day, so caching
  matters.
- `debug=True` in `app.py` is convenient for local development but should be
  turned off if you ever expose this beyond localhost.

## Future ideas

- **Search controls** — let the user tune the query: date range, recency vs.
  relevancy sort, number of articles, region/language. Deliberately left out for
  now to keep the UI simple; NewsAPI already supports all of these, so it can be
  added later if needed.
- Cache eviction (TTL / size cap) for the per-query article cache.
