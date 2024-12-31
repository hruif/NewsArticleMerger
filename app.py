from flask import Flask, request, render_template
import main

app = Flask(__name__)

# Route for the search page
@app.route("/")
def search_page():
    return render_template("search.html")

# Route for the results page
@app.route("/results")
def results_page():
    query = request.args.get("query")
    return f"""
    <h1>Results for: {query}</h1>
    <p>{main.get_article(query)}</p>
    <button onclick="window.location.href='/'">Back to Search</button>
    """

if __name__ == "__main__":
    app.run(debug=True)