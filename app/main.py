import time

import cache
from merger import Merger
from scraper import Scraper

NUM_ARTICLES = 5
ARTICLES_CACHE = "articles.json"

mrgr = Merger()
scrpr = Scraper()


def get_topics(count: int = 5, refresh: bool = False):
    """Topics covered by the most distinct sources, for the homepage."""
    return scrpr.get_trending_topics(count, refresh=refresh)


def _result(html, cached, created_at):
    return {"html": html, "cached": cached, "created_at": created_at}


def get_article(query: str, refresh: bool = False):
    """
    Build (or load from cache) a merged article for `query`.
    :return: dict with 'html', 'cached' (bool), and 'created_at' (epoch or None).
    """
    if not query:
        return _result("<p>Please enter a search term.</p>", False, None)

    key = " ".join(query.lower().split())
    store = cache.load(ARTICLES_CACHE, {})

    if not refresh and key in store:
        entry = store[key]
        return _result(entry["html"], True, entry["created_at"])

    # get text from a diverse set of articles, then merge into one HTML article
    articles_text = scrpr.get_articles(query, NUM_ARTICLES)
    if not articles_text:
        return _result(
            "<p>No articles could be retrieved for that query. Try another search.</p>",
            False,
            None,
        )

    html = mrgr.merge_texts(articles_text)
    created_at = time.time()
    store[key] = {"query": query, "html": html, "created_at": created_at}
    cache.save(ARTICLES_CACHE, store)
    return _result(html, False, created_at)
