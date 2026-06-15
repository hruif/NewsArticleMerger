import json
import time

import nh3
from flask import Flask, Response, render_template, request

import main

# Request flow:
#   /                    -> search page + trending topics (main.get_topics)
#   /results             -> loading screen that opens the SSE stream below
#   /api/article/stream  -> streams the article (scrape+merge), returns safe HTML
#   /recent              -> list of previously generated (cached) articles
app = Flask(__name__)

# Tags/attributes allowed in the merged article HTML. Anything else (scripts,
# event handlers, javascript: urls, etc.) is stripped by nh3 before rendering.
# `mark`/`sup` + class/data-* carry the perspective + citation markers; heading
# ids are the TL;DR scroll targets.
ALLOWED_TAGS = {
    "h1", "h2", "h3", "h4", "h5", "h6",
    "p", "br", "hr", "div", "span", "blockquote",
    "ul", "ol", "li", "strong", "em", "b", "i", "u",
    "a", "table", "thead", "tbody", "tr", "th", "td",
    "mark", "sup",
}
ALLOWED_ATTRIBUTES = {
    "a": {"href", "title"},
    "sup": {"class", "data-src"},
    "mark": {"class", "data-persp"},
    **{h: {"id"} for h in ("h1", "h2", "h3", "h4", "h5", "h6")},
}


def _clean(html: str) -> str:
    return nh3.clean(html or "", tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRIBUTES)


def _sse(event: str, data: dict) -> str:
    """Format one Server-Sent Event."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _message_done(html: str) -> str:
    """A terminal 'done' event carrying just a message (empty interactivity)."""
    return _sse("done", {
        "article_html": html, "tldr": [], "sources": [], "perspectives": [],
        "cached": False, "created_at": None,
    })


# Homepage: hot topics + search bar
@app.route("/")
def search_page():
    refresh = request.args.get("refresh") == "1"
    try:
        topics = main.get_topics(refresh=refresh)
    except Exception as e:
        print(f"Failed to load topics: {e}")
        topics = []
    return render_template("search.html", topics=topics)


# Results page: renders a loading screen immediately, then opens the SSE stream.
@app.route("/results")
def results_page():
    query = request.args.get("query", "")
    return render_template("results.html", query=query)


# Recently generated (cached) articles.
@app.route("/recent")
def recent_page():
    return render_template("recent.html", items=main.list_recent())


# Streams the article as it is generated. Each SSE 'delta' carries the sanitized
# article-so-far; the final 'done' carries the full structured result.
@app.route("/api/article/stream")
def api_article_stream():
    query = request.args.get("query", "")
    refresh = request.args.get("refresh") == "1"

    def generate():
        if not query:
            yield _message_done("<p>Please enter a search term.</p>")
            return

        if not refresh:
            cached = main.get_cached(query)
            if cached:
                yield _sse("done", {**cached, "cached": True})
                return

        try:
            sources = main.get_sources(query)
            if not sources:
                yield _message_done(
                    "<p>No articles could be retrieved for that query. Try another search.</p>"
                )
                return

            # We own the source links (from NewsAPI), so render them from trusted
            # metadata rather than anything the model emits.
            sources_meta = [
                {"id": i + 1, "title": s["title"], "url": s["url"], "name": s["name"]}
                for i, s in enumerate(sources)
            ]

            final = None
            for kind, payload in main.mrgr.merge_stream(sources):
                if kind == "delta":
                    yield _sse("delta", {"html": _clean(payload)})
                else:
                    final = payload

            result = {
                "query": query,
                "created_at": time.time(),
                "article_html": _clean(final["article_html"]),
                "tldr": final["tldr"],
                "sources": sources_meta,
                "perspectives": final["perspectives"],
            }
            main.save_article(query, result)  # only successful results are cached
            yield _sse("done", {**result, "cached": False})
        except Exception as e:
            print(f"Failed to build article for {query!r}: {e}")
            yield _sse("error", {
                "message": "Sorry, something went wrong building this article. Please try again."
            })

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


if __name__ == "__main__":
    app.run(debug=True, threaded=True)
