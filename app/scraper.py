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
import re
import time
from collections import Counter, OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
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
NEWSDATA_ARCHIVE_URL = "https://newsdata.io/api/1/archive"
NEWSDATA_KEY = config.load_key("NEWSDATA_KEY", "newsdata_key.txt")


def _env_int(name, default, minimum=1):
    try:
        return max(minimum, int(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


# Regular searches use the archive endpoint so important stories remain
# findable after the latest/breaking-news feed has moved on.
_SEARCH_LOOKBACK_DAYS = _env_int("NEWS_SEARCH_LOOKBACK_DAYS", 7)
_SEARCH_PAGE_LIMIT = _env_int("NEWS_SEARCH_PAGES", 2)
_SEARCH_PAGE_SIZE = _env_int("NEWS_SEARCH_SIZE", 10)
_SEARCH_CANDIDATE_MULTIPLIER = _env_int("NEWS_CANDIDATE_MULTIPLIER", 4)
_SEARCH_PRIORITY_DOMAIN = os.environ.get("NEWS_PRIORITY_DOMAIN", "medium").strip()
_LATEST_TIMEFRAME_HOURS = min(48, _env_int("NEWS_LATEST_TIMEFRAME_HOURS", 48))
_BEST_RECENCY_WEIGHT = 1.0
_LATEST_RECENCY_WEIGHT = 6.0

MODE_BEST = "best"
MODE_LATEST = "latest"
_SEARCH_MODES = {MODE_BEST, MODE_LATEST}

# Trending-topics tuning. Sweeping categories builds a pool large enough that the
# same story shows up from many outlets. Lead with hard-news categories so the
# raw pool skews substantive; the model then curates it (see _curate). Each
# category costs one API credit, so the list and cache TTL are env-tunable.
_HEADLINE_CATEGORIES = tuple(
    c.strip() for c in os.environ.get(
        "TOPICS_CATEGORIES", "top,world,politics,business,science"
    ).split(",") if c.strip()
)
_MIN_TOPIC_SOURCES = 2  # a "topic" must be covered by at least this many outlets
_TOPICS_CACHE_TTL = int(os.environ.get("TOPICS_CACHE_TTL", "3600"))  # 60 min default
_TOPICS_CACHE_FILE = "topics.json"
_EMPTY_TOPICS = {"top": [], "notable": []}
_topics_cache = {"at": 0.0, "topics": dict(_EMPTY_TOPICS)}

# Conservative, high-precision clickbait/listicle markers. The model is the real
# filter; this just trims the most obvious slop before curation (and protects the
# heuristic fallback). Kept tight to avoid dropping real headlines.
_SLOP_RE = re.compile(
    r"\bif\s+you\s+remember\b"
    r"|\byou\s+won'?t\s+believe\b"
    r"|\b\d+\s+(things|ways|reasons|signs|tips|gadgets|products|gifts|deals)\b"
    r"|\bthese\s+\d+\b"
    r"|\baccording\s+to\s+users\b"
    r"|\b(we|i)\s+tried\b"
    r"|\b(top|best)\s+\d+\b",
    re.IGNORECASE,
)


def _looks_like_slop(title: str) -> bool:
    return bool(_SLOP_RE.search(title or ""))


def normalize_search_mode(mode: str) -> str:
    mode = (mode or MODE_BEST).strip().lower()
    return mode if mode in _SEARCH_MODES else MODE_BEST

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


def _newsdata_request(url, **params):
    """Call a newsdata.io endpoint and return its decoded response object."""
    params["apikey"] = NEWSDATA_KEY
    params.setdefault("language", "en")
    response = requests.get(url, params=params, timeout=10)
    response.raise_for_status()
    data = response.json()
    if data.get("status") != "success":
        message = data.get("results") or data.get("message") or data
        print(f"newsdata request failed: {message}")
        return {}
    return data


def _newsdata_latest(**params):
    """Call newsdata.io /latest and return its list of article results."""
    return _newsdata_request(NEWSDATA_URL, **params).get("results", [])


def _newsdata_latest_page(**params):
    """Call newsdata.io /latest and return the decoded response object."""
    return _newsdata_request(NEWSDATA_URL, **params)


def _newsdata_archive(**params):
    """Call newsdata.io /archive and return the decoded response object."""
    return _newsdata_request(NEWSDATA_ARCHIVE_URL, **params)


def _to_internal(article):
    """Normalize a newsdata.io article to the shape the rest of this module uses."""
    return {
        "title": article.get("title"),
        "url": article.get("link"),
        "description": article.get("description") or article.get("content") or "",
        "pub_date": article.get("pubDate"),
        "source": {
            "name": article.get("source_name") or article.get("source_id") or "Unknown"
        },
    }


class Scraper:
    def get_articles(self, query: str, num_articles: int, mode: str = MODE_BEST):
        """
        Get a diverse set of articles (with metadata) for a query.

        Best-coverage mode searches the archive over roughly a week, ranking
        relevance over recency. Latest mode searches the live latest endpoint and
        gives recency a stronger, capped weight. Both modes then diversity-order
        by source domain, scrape candidates in parallel, and return the first
        `num_articles` that scrape successfully.

        :param string query: Query to search for.
        :param int num_articles: Number of articles to include.
        :param string mode: "best" for coverage, "latest" for fresher updates.
        :return: List of dicts: {"title", "url", "name" (source), "text"}.
        """
        mode = normalize_search_mode(mode)
        try:
            if mode == MODE_LATEST:
                results = self._search_latest(query, num_articles)
            else:
                results = self._search_archive(query, num_articles)
                if not results:
                    print(
                        f"archive search returned no results for {query!r}; "
                        "falling back to latest"
                    )
                    results = _newsdata_latest(q=query)
        except Exception as e:
            if mode == MODE_LATEST:
                print(f"newsdata latest search failed for {query!r}: {e}")
                return []
            print(
                f"newsdata archive search failed for {query!r}: {e}; "
                "falling back to latest"
            )
            try:
                results = _newsdata_latest(q=query)
            except Exception as latest_error:
                print(f"newsdata latest fallback failed for {query!r}: {latest_error}")
                return []

        candidates = [_to_internal(a) for a in results]
        candidates = self._rank_by_relevance(candidates, query, mode)
        candidates = self._order_by_diversity(candidates)

        # Scrape a broader pool than we need so a few 403s/parser failures do not
        # force the final article back to whatever happened to scrape first.
        scrape_limit = max(num_articles * 2, num_articles * _SEARCH_CANDIDATE_MULTIPLIER)
        candidates = candidates[:scrape_limit]

        if not candidates:
            return []

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

    def _search_latest(self, query: str, num_articles: int):
        """Search the live feed for fresh updates, paging enough to rank locally."""
        params = {
            "q": query,
            "timeframe": _LATEST_TIMEFRAME_HOURS,
            "size": _SEARCH_PAGE_SIZE,
        }
        if _SEARCH_PRIORITY_DOMAIN:
            params["prioritydomain"] = _SEARCH_PRIORITY_DOMAIN

        target = max(num_articles * _SEARCH_CANDIDATE_MULTIPLIER, num_articles)
        results, seen = [], set()
        page = None
        for _ in range(_SEARCH_PAGE_LIMIT):
            page_params = dict(params)
            if page:
                page_params["page"] = page
            data = _newsdata_latest_page(**page_params)
            for article in data.get("results", []):
                url = article.get("link")
                if not url or url in seen:
                    continue
                seen.add(url)
                results.append(article)
            if len(results) >= target:
                break
            page = data.get("nextPage")
            if not page:
                break
        return results

    def _search_archive(self, query: str, num_articles: int):
        """Search roughly the past week, paging enough to rank a wider pool."""
        to_date = datetime.now(timezone.utc).date()
        from_date = to_date - timedelta(days=_SEARCH_LOOKBACK_DAYS)
        params = {
            "q": query,
            "from_date": from_date.isoformat(),
            "to_date": to_date.isoformat(),
            "size": _SEARCH_PAGE_SIZE,
        }
        if _SEARCH_PRIORITY_DOMAIN:
            params["prioritydomain"] = _SEARCH_PRIORITY_DOMAIN

        target = max(num_articles * _SEARCH_CANDIDATE_MULTIPLIER, num_articles)
        results, seen = [], set()
        page = None
        for _ in range(_SEARCH_PAGE_LIMIT):
            page_params = dict(params)
            if page:
                page_params["page"] = page
            data = _newsdata_archive(**page_params)
            for article in data.get("results", []):
                url = article.get("link")
                if not url or url in seen:
                    continue
                seen.add(url)
                results.append(article)
            if len(results) >= target:
                break
            page = data.get("nextPage")
            if not page:
                break
        return results

    def _rank_by_relevance(self, articles, query, mode):
        """Prefer direct query matches; scale recency by search mode."""
        query_terms = self._keywords(query)
        phrase = " ".join(query.lower().split())
        if mode == MODE_LATEST:
            recency_weight = _LATEST_RECENCY_WEIGHT
            recency_window = max(_LATEST_TIMEFRAME_HOURS / 24, 1 / 24)
        else:
            recency_weight = _BEST_RECENCY_WEIGHT
            recency_window = _SEARCH_LOOKBACK_DAYS

        def score(article):
            title = article.get("title") or ""
            desc = article.get("description") or ""
            title_l = title.lower()
            desc_l = desc.lower()
            title_terms = self._keywords(title)
            desc_terms = self._keywords(desc)

            value = 0.0
            if phrase and phrase in title_l:
                value += 10
            if phrase and phrase in desc_l:
                value += 5
            value += 4 * len(query_terms & title_terms)
            value += 1.5 * len(query_terms & desc_terms)

            age = self._age_days(article.get("pub_date"))
            if age is not None:
                value += recency_weight * max(0, recency_window - age) / recency_window
            return value

        return sorted(articles, key=score, reverse=True)

    @staticmethod
    def _age_days(pub_date):
        if not pub_date:
            return None
        try:
            parsed = datetime.fromisoformat(pub_date.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - parsed).total_seconds() / 86400
        except ValueError:
            return None

    def get_trending_topics(self, refresh: bool = False):
        """
        Find and curate the most significant trending stories right now.

        Sweeps top headlines across categories, clusters them by shared subject
        keywords (so a story shows up from many outlets), drops obvious slop, then
        lets the model select the genuinely important stories and split them into
        "top" and "notable" tiers. Cached in memory and on disk; pass refresh=True
        to force a fresh sweep.

        :param bool refresh: Bypass cache and re-sweep.
        :return: dict {"top": [...], "notable": [...]} of topic dicts, each with
                 'title' (display label), 'query' (search string), and
                 'source_count' (how many outlets cover it).
        """
        now = time.time()
        if not refresh:
            if self._cache_fresh(now):
                return _topics_cache["topics"]
            persisted = cache.load(_TOPICS_CACHE_FILE)
            if (
                persisted
                and isinstance(persisted.get("topics"), dict)
                and now - persisted.get("at", 0) < _TOPICS_CACHE_TTL
            ):
                _topics_cache.update(at=persisted["at"], topics=persisted["topics"])
                return persisted["topics"]

        candidates = self._cluster_topics(self._fetch_headline_pool())
        candidates = [c for c in candidates if not _looks_like_slop(c["title"])][:15]
        tiered = self._curate(candidates)

        if tiered["top"] or tiered["notable"]:
            _topics_cache.update(at=now, topics=tiered)
            cache.save(_TOPICS_CACHE_FILE, {"at": now, "topics": tiered})
            return tiered

        # Sweep produced nothing (e.g. API hiccup) — serve whatever we still have.
        return _topics_cache["topics"] or (cache.load(_TOPICS_CACHE_FILE) or {}).get(
            "topics"
        ) or dict(_EMPTY_TOPICS)

    @staticmethod
    def _cache_fresh(now):
        topics = _topics_cache["topics"]
        has_content = topics.get("top") or topics.get("notable")
        return has_content and now - _topics_cache["at"] < _TOPICS_CACHE_TTL

    def _curate(self, candidates):
        """Model-curate candidates into top/notable tiers; heuristic fallback."""
        import merger  # local import keeps scraper importable without the model

        result = merger.curate_topics(candidates)
        if result is None:
            # Model unavailable: fall back to source-count order (already sorted).
            return {"top": candidates[:6], "notable": candidates[6:12]}
        important, notable = result
        return {"top": important[:6], "notable": notable[:6]}

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
