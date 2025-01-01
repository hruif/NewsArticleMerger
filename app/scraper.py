"""
KNOWN ISSUES:
 - scrape() will throw errors on some URLs/domains
 - newsapi.get_everything() will give invalid URLs (<removed>)
 - cannot process some URLs
"""

import requests
from newsapi import NewsApiClient
from newspaper import Article

newsapi_key_file = open("newsapi_key.txt", "r")
newsapi = NewsApiClient(api_key=newsapi_key_file.readline().strip())
newsapi_key_file.close()

class Scraper:
    def get_articles(self, query: str, num_articles: int):
        """
        Get text from articles given a query.
        :param string query: Query to search for.
        :param int num_articles: Number of articles to include.
        :return: List of strings containing article texts.
        """
        all_articles = {}
        articles_text = []

        # each page only has 100 results, so loop through for desired number of articles.
        page_num = 1
        while (num_articles > 100):
            all_articles = newsapi.get_everything(q=query, sort_by='relevancy', page=page_num)

            # check that length of page exceeds 100
            if (len(all_articles) < 100):
                break
            page_num += 1
            cnt = 0
            for article in all_articles['articles']:
                articles_text.append(self.scrape(article['url']))
                cnt += 1
            num_articles -= cnt
        
        # add articles on last page
        all_articles = newsapi.get_everything(q=query, sort_by='relevancy', page=page_num)
        for i in range(min(num_articles, len(all_articles['articles']))):
            articles_text.append(self.scrape(all_articles['articles'][i]['url']))
        return articles_text

    def scrape(self, url: str):
        """
        Get article contents from a url.
        :param string url: URL to scrape.
        :return: string containing the article contents.
        """
        article = Article(url)

        # article.download()
        # doesn't work for certain sites/url

        response = requests.get(url)
        article.download(input_html=response.text)
        article.parse()
        return article.text