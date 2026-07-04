"""Pipeline glue: query -> scrape diverse sources -> stream a merged article.

Holds the single Scraper/Merger instances and the per-query article cache. The
streaming orchestration itself lives in app.py (it is tied to SSE + sanitization).
"""

import cache
from merger import Merger
from scraper import MODE_BEST, MODE_LATEST, Scraper, normalize_search_mode

NUM_ARTICLES = 5
ARTICLES_CACHE = "articles.json"
ARTICLE_CACHE_VERSION = "search-modes-v1"
SEARCH_MODES = [
    {"value": MODE_BEST, "label": "Best coverage"},
    {"value": MODE_LATEST, "label": "Latest"},
]
_MODE_LABELS = {m["value"]: m["label"] for m in SEARCH_MODES}

mrgr = Merger()
scrpr = Scraper()


def get_topics(refresh: bool = False):
    """Curated trending topics for the homepage: {"top": [...], "notable": [...]}."""
    return scrpr.get_trending_topics(refresh=refresh)


def normalize_mode(mode: str) -> str:
    return normalize_search_mode(mode)


def mode_label(mode: str) -> str:
    return _MODE_LABELS[normalize_mode(mode)]


def get_sources(query: str, mode: str = MODE_BEST):
    """Scrape a diverse set of source articles for a query."""
    return scrpr.get_articles(query, NUM_ARTICLES, normalize_mode(mode))


def _normalize(query: str) -> str:
    return " ".join(query.lower().split())


def _cache_key(query: str, mode: str) -> str:
    return f"{normalize_mode(mode)}:{_normalize(query)}"


def get_cached(query: str, mode: str = MODE_BEST):
    """Return the cached structured article for a query, or None.

    Legacy entries (pre-citation format, which only had an 'html' field) are
    treated as a miss so they get regenerated in the new shape.
    """
    entry = cache.load(ARTICLES_CACHE, {}).get(_cache_key(query, mode))
    if (
        entry
        and "article_html" in entry
        and entry.get("cache_version") == ARTICLE_CACHE_VERSION
        and entry.get("mode") == normalize_mode(mode)
    ):
        return entry
    return None


def save_article(query: str, result: dict, mode: str = MODE_BEST) -> None:
    """Persist a structured article result keyed by normalized query."""
    mode = normalize_mode(mode)
    store = cache.load(ARTICLES_CACHE, {})
    store[_cache_key(query, mode)] = {
        **result,
        "mode": mode,
        "mode_label": mode_label(mode),
        "cache_version": ARTICLE_CACHE_VERSION,
    }
    cache.save(ARTICLES_CACHE, store)


def list_recent(limit: int = 30):
    """Recently generated articles, newest first: [{query, created_at}]."""
    store = cache.load(ARTICLES_CACHE, {})
    items = [
        {
            "query": e.get("query", k.split(":", 1)[-1]),
            "mode": normalize_mode(e.get("mode")),
            "mode_label": mode_label(e.get("mode")),
            "created_at": e.get("created_at", 0),
        }
        for k, e in store.items()
        if "article_html" in e
    ]
    items.sort(key=lambda x: x["created_at"], reverse=True)
    return items[:limit]
