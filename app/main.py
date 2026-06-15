"""Pipeline glue: query -> scrape diverse sources -> stream a merged article.

Holds the single Scraper/Merger instances and the per-query article cache. The
streaming orchestration itself lives in app.py (it is tied to SSE + sanitization).
"""

import cache
from merger import Merger
from scraper import Scraper

NUM_ARTICLES = 5
ARTICLES_CACHE = "articles.json"

mrgr = Merger()
scrpr = Scraper()


def get_topics(refresh: bool = False):
    """Curated trending topics for the homepage: {"top": [...], "notable": [...]}."""
    return scrpr.get_trending_topics(refresh=refresh)


def get_sources(query: str):
    """Scrape a diverse set of source articles for a query."""
    return scrpr.get_articles(query, NUM_ARTICLES)


def _normalize(query: str) -> str:
    return " ".join(query.lower().split())


def get_cached(query: str):
    """Return the cached structured article for a query, or None.

    Legacy entries (pre-citation format, which only had an 'html' field) are
    treated as a miss so they get regenerated in the new shape.
    """
    entry = cache.load(ARTICLES_CACHE, {}).get(_normalize(query))
    if entry and "article_html" in entry:
        return entry
    return None


def save_article(query: str, result: dict) -> None:
    """Persist a structured article result keyed by normalized query."""
    store = cache.load(ARTICLES_CACHE, {})
    store[_normalize(query)] = result
    cache.save(ARTICLES_CACHE, store)


def list_recent(limit: int = 30):
    """Recently generated articles, newest first: [{query, created_at}]."""
    store = cache.load(ARTICLES_CACHE, {})
    items = [
        {"query": e.get("query", k), "created_at": e.get("created_at", 0)}
        for k, e in store.items()
        if "article_html" in e
    ]
    items.sort(key=lambda x: x["created_at"], reverse=True)
    return items[:limit]
