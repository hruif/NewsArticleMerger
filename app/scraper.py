"""
News discovery via newsdata.io (https://newsdata.io). We use it only to find
article URLs (+ title/source); the full text is scraped ourselves with newspaper.

KNOWN ISSUES:
 - scrape() will throw errors on some URLs/domains; those articles are skipped.
 - newsdata.io's free tier only covers roughly the last 48 hours.

API CREDITS (free tier = 200/day): a search is 1 credit; a trending sweep is one
credit per category (see _HEADLINE_CATEGORIES), cached for TOPICS_CACHE_TTL.
"""

import os
import time
from collections import Counter, OrderedDict
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import requests
from newspaper import Article

import cache
import config

# Many sites 403 a request with no/identifiable bot User-Agent, so present a
# normal browser UA. This recovers a lot of the articles that used to fail.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}
SCRAPE_TIMEOUT = 8  # seconds per article
MAX_WORKERS = 10

NEWSDATA_URL = "https://newsdata.io/api/1/latest"
NEWSDATA_KEY = config.load_key("NEWSDATA_KEY", "newsdata_key.txt")

# Trending-topics tuning. Sweeping categories builds a pool large enough that the
# same story shows up from many outlets. Each category costs one API credit, so
# both the category list and the cache TTL are env-tunable to fit the daily budget.
_HEADLINE_CATEGORIES = tuple(
    c.strip() for c in os.environ.get(
        "TOPICS_CATEGORIES", "business,technology,science,health,world"
    ).split(",") if c.strip()
)
_MIN_TOPIC_SOURCES = 2  # a "topic" must be covered by at least this many outlets
_TOPICS_CACHE_TTL = int(os.environ.get("TOPICS_CACHE_TTL", "3600"))  # 60 min default
_TOPICS_CACHE_FILE = "topics.json"
_topics_cache = {"at": 0.0, "topics": []}

# Filler words to ignore when finding the subject shared across headlines.
_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "of", "to", "in", "on", "for", "with",
    "at", "by", "from", "as", "is", "are", "was", "were", "be", "been", "it",
    "its", "this", "that", "these", "those", "his", "her", "their", "our", "your",
    "he", "she", "they", "we", "you", "i", "after", "amid", "over", "into", "out",
    "up", "down", "off", "new", "says", "say", "said", "could", "will", "would",
    "can", "may", "might", "report", "reports", "reportedly", "amp", "how", "why",
    "what", "who", "when", "where", "more", "than", "about", "first", "live",
    "update", "updates", "video", "watch", "us", "u.s.",
    "need", "needs", "want", "which", "real", "really", "plus", "also", "here",
    "there", "now", "then", "get", "got", "make", "made", "take", "like", "just",
    "back", "still", "best", "top", "big", "one", "two", "day", "days", "year",
    "years", "time", "week", "some", "many", "much", "very",
}


def _newsdata_latest(**params):
    """Call newsdata.io /latest and return its list of article results."""
    params["apikey"] = NEWSDATA_KEY
    params.setdefault("language", "en")
    response = requests.get(NEWSDATA_URL, params=params, timeout=10)
    response.raise_for_status()
    data = response.json()
    if data.get("status") != "success":
        return []
    return data.get("results", [])


def _to_internal(article):
    """Normalize a newsdata.io article to the shape the rest of this module uses."""
    return {
        "title": article.get("title"),
        "url": article.get("link"),
        "source": {"name": article.get("source_name") or article.get("source_id") or "Unknown"},
    }


class Scraper:
    def get_articles(self, query: str, num_articles: int):
        """
        Get a diverse set of articles (with metadata) for a query.

        Searches newsdata.io (one credit), orders results so different news sources
        come first (for a wide range of perspectives), scrapes candidates in
        parallel, and returns the first `num_articles` that scrape successfully.

        :param string query: Query to search for.
        :param int num_articles: Number of articles to include.
        :return: List of dicts: {"title", "url", "name" (source), "text"}.
        """
        try:
            results = _newsdata_latest(q=query)
        except Exception as e:
            print(f"newsdata search failed for {query!r}: {e}")
            return []

        candidates = self._order_by_diversity([_to_internal(a) for a in results])
        # Only attempt a bounded number of scrapes (enough to cover failures).
        candidates = candidates[: num_articles * 2]

        # Scrape in parallel; keep each candidate paired with its scraped text.
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            texts = list(executor.map(lambda a: self.scrape(a["url"]), candidates))

        articles = []
        for candidate, text in zip(candidates, texts):
            if not text:
                continue
            articles.append(
                {
                    "title": candidate.get("title") or "Untitled",
                    "url": candidate.get("url"),
                    "name": (candidate.get("source") or {}).get("name") or "Unknown",
                    "text": text,
                }
            )
            if len(articles) >= num_articles:
                break
        return articles

    def get_trending_topics(self, count: int = 5, refresh: bool = False):
        """
        Find the topics being covered by the most distinct sources right now.

        Sweeps top headlines across categories, clusters them by shared subject
        keywords, and returns the stories covered by the most different outlets
        (i.e. genuinely trending, not one-off articles). Results are cached in
        memory and on disk; pass refresh=True to force a fresh sweep.

        :param int count: Number of topics to return.
        :param bool refresh: Bypass cache and re-sweep.
        :return: List of dicts with 'title' (display label), 'query' (search
                 string), and 'source_count' (how many outlets cover it).
        """
        now = time.time()
        if not refresh:
            if _topics_cache["topics"] and now - _topics_cache["at"] < _TOPICS_CACHE_TTL:
                return _topics_cache["topics"][:count]
            # Fall back to the persisted copy (survives restarts).
            persisted = cache.load(_TOPICS_CACHE_FILE)
            if persisted and now - persisted.get("at", 0) < _TOPICS_CACHE_TTL:
                _topics_cache.update(at=persisted["at"], topics=persisted["topics"])
                return persisted["topics"][:count]

        pool = self._fetch_headline_pool()
        topics = self._cluster_topics(pool)
        if topics:
            _topics_cache.update(at=now, topics=topics)
            cache.save(_TOPICS_CACHE_FILE, {"at": now, "topics": topics})
            return topics[:count]

        # Sweep produced nothing (e.g. API hiccup) — serve whatever we still have.
        fallback = _topics_cache["topics"] or (cache.load(_TOPICS_CACHE_FILE) or {}).get(
            "topics", []
        )
        return fallback[:count]

    def _fetch_headline_pool(self):
        """Gather a de-duplicated pool of top headlines across categories."""
        pool, seen = [], set()
        for category in _HEADLINE_CATEGORIES:
            try:
                results = _newsdata_latest(category=category, country="us")
            except Exception as e:
                print(f"newsdata category ({category}) failed: {e}")
                continue
            for article in results:
                internal = _to_internal(article)
                url = internal["url"]
                if url and url not in seen:
                    seen.add(url)
                    pool.append(internal)
        return pool

    def _cluster_topics(self, articles):
        """Cluster headlines by shared subject; return those spanning >= 2 sources."""
        parsed = []
        for article in articles:
            title = article.get("title")
            if not title:
                continue
            # Some titles end with " - <Source>"; trim for a clean topic.
            clean = title.rsplit(" - ", 1)[0].strip()
            keywords = self._keywords(clean)
            if len(keywords) < 2:
                continue
            source = (article.get("source") or {}).get("name") or "Unknown"
            parsed.append({"title": clean, "source": source, "keywords": keywords})

        # Greedy clustering against each cluster's fixed seed keywords. Matching the
        # seed (rather than a growing union) keeps clusters from drifting together.
        clusters = []
        for item in parsed:
            best, best_overlap = None, 1  # need overlap >= 2 to join a cluster
            for cluster in clusters:
                overlap = len(item["keywords"] & cluster["seed"])
                if overlap > best_overlap:
                    best, best_overlap = cluster, overlap
            if best is not None:
                best["items"].append(item)
                best["sources"].add(item["source"])
            else:
                clusters.append(
                    {
                        "seed": set(item["keywords"]),
                        "items": [item],
                        "sources": {item["source"]},
                    }
                )

        # Keep only multi-source stories; rank by breadth of coverage.
        clusters = [c for c in clusters if len(c["sources"]) >= _MIN_TOPIC_SOURCES]
        clusters.sort(key=lambda c: (len(c["sources"]), len(c["items"])), reverse=True)

        topics = []
        for cluster in clusters:
            # Keywords shared across the most headlines drive the search query.
            freq = Counter()
            for item in cluster["items"]:
                freq.update(item["keywords"])
            top_keywords = [kw for kw, _ in freq.most_common(3)]
            # Display label: the headline that best represents the shared subject.
            representative = max(
                cluster["items"],
                key=lambda it: (
                    len(set(top_keywords) & it["keywords"]),
                    -len(it["title"]),
                ),
            )
            topics.append(
                {
                    "title": representative["title"],
                    "query": " ".join(top_keywords) or representative["title"],
                    "source_count": len(cluster["sources"]),
                }
            )
        return topics

    def _keywords(self, text: str):
        """Significant lowercase keywords from a headline (drops filler words)."""
        words = "".join(c if c.isalnum() else " " for c in text.lower()).split()
        return {w for w in words if len(w) > 2 and w not in _STOPWORDS}

    def _order_by_diversity(self, articles):
        """Interleave articles by domain so the first ones come from distinct sources."""
        by_domain = OrderedDict()
        for article in articles:
            url = article.get("url")
            if not url:
                continue
            domain = urlparse(url).netloc.lower().removeprefix("www.")
            by_domain.setdefault(domain, []).append(article)

        ordered = []
        while by_domain:
            for domain in list(by_domain.keys()):
                ordered.append(by_domain[domain].pop(0))
                if not by_domain[domain]:
                    del by_domain[domain]
        return ordered

    def scrape(self, url: str):
        """
        Get article contents from a url.
        :param string url: URL to scrape.
        :return: string containing the article contents, or "" if scraping fails.
        """
        try:
            article = Article(url)
            # article.download() doesn't work for certain sites/urls, so fetch
            # the HTML ourselves (with a browser UA) and hand it to newspaper.
            response = requests.get(url, headers=HEADERS, timeout=SCRAPE_TIMEOUT)
            response.raise_for_status()
            article.download(input_html=response.text)
            article.parse()
            return article.text
        except Exception as e:
            print(f"Failed to scrape {url}: {e}")
            return ""
