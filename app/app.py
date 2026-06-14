import nh3
from flask import Flask, jsonify, render_template, request

import main

app = Flask(__name__)

# Tags/attributes allowed in the merged article HTML. Anything else (scripts,
# event handlers, javascript: urls, etc.) is stripped by nh3 before rendering.
ALLOWED_TAGS = {
    "h1", "h2", "h3", "h4", "h5", "h6",
    "p", "br", "hr", "div", "span", "blockquote",
    "ul", "ol", "li", "strong", "em", "b", "i", "u",
    "a", "table", "thead", "tbody", "tr", "th", "td",
}
ALLOWED_ATTRIBUTES = {"a": {"href", "title"}}


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


# Results page: renders a loading screen immediately, then fetches the article.
@app.route("/results")
def results_page():
    query = request.args.get("query", "")
    return render_template("results.html", query=query)


# JSON endpoint that does the slow work (scrape + merge) and returns safe HTML.
@app.route("/api/article")
def api_article():
    query = request.args.get("query", "")
    refresh = request.args.get("refresh") == "1"
    result = main.get_article(query, refresh=refresh)
    safe_html = nh3.clean(
        result["html"],
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
    )
    return jsonify(
        html=safe_html,
        cached=result["cached"],
        created_at=result["created_at"],
    )


if __name__ == "__main__":
    app.run(debug=True)
