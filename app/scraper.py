"""
KNOWN ISSUES:
 - scrape() will throw errors on some URLs/domains; those articles are skipped.
 - newsapi.get_everything() will sometimes give invalid URLs (<removed>).
"""

import os
import time
from collections import Counter, OrderedDict
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import requests
from newsapi import NewsApiClient
from newspaper import Article

import cache

# Repo root = parent of the directory this file lives in (app/).
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

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

# Trending-topics tuning. Sweeping categories builds a pool large enough that the
# same story shows up from many outlets; results are cached to avoid re-spending
# API quota (one sweep == several requests) on every homepage load.
_HEADLINE_CATEGORIES = (
    "general", "business", "technology", "science", "health", "sports", "entertainment",
)
_MIN_TOPIC_SOURCES = 2  # a "topic" must be covered by at least this many outlets
_TOPICS_CACHE_TTL = 900  # seconds (15 min) before an automatic re-sweep
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
    "years", "time", "week", "these", "those", "some", "many", "much", "very",
}


def _load_key(env_var: str, file_name: str) -> str:
    """Read an API key from an env var, falling back to a key file at the repo root."""
    key = os.environ.get(env_var)
    if key:
        return key.strip()
    key_path = os.path.join(ROOT_DIR, file_name)
    with open(key_path, "r") as f:
        return f.readline().strip()


newsapi = NewsApiClient(api_key=_load_key("NEWSAPI_KEY", "newsapi_key.txt"))


class Scraper:
    def get_articles(self, query: str, num_articles: int):
        """
        Get text from a diverse set of articles for a query.

        Over-fetches a candidate pool, orders it so different news sources come
        first (for a wide range of perspectives), scrapes candidates in parallel,
        and returns the text of the first `num_articles` that scrape successfully.

        :param string query: Query to search for.
        :param int num_articles: Number of articles to include.
        :return: List of strings containing article texts.
        """
        # Pull a generous pool so 403s / dead URLs don't starve the result set.
        pool_size = min(max(num_articles * 6, 30), 100)
        response = newsapi.get_everything(
            q=query, sort_by="relevancy", language="en", page_size=pool_size, page=1
        )
        if response.get("status") != "ok":
            return []

        candidates = self._order_by_diversity(response.get("articles", []))
        # Only attempt a bounded number of scrapes (enough to cover failures).
        candidates = candidates[: num_articles * 2]
        urls = [a["url"] for a in candidates]

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            texts = list(executor.map(self.scrape, urls))

        # Keep successes in diversity order, up to the requested count.
        return [t for t in texts if t][:num_articles]

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
                response = newsapi.get_top_headlines(
                    language="en", country="us", category=category, page_size=100
                )
            except Exception as e:
                print(f"top_headlines ({category}) failed: {e}")
                continue
            if response.get("status") != "ok":
                continue
            for article in response.get("articles", []):
                url = article.get("url")
                if url and url not in seen:
                    seen.add(url)
                    pool.append(article)
        return pool

    def _cluster_topics(self, articles):
        """Cluster headlines by shared subject; return those spanning >= 2 sources."""
        parsed = []
        for article in articles:
            title = article.get("title")
            if not title:
                continue
            # NewsAPI titles often end with " - <Source>"; trim for a clean topic.
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
            if not url or url == "https://removed.com":
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
