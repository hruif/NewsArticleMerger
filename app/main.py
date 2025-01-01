import os
from merger import Merger
from scraper import Scraper

mrgr = Merger()
scrpr = Scraper()

def get_article(query: str):
    # get text from articles
    articles_text = scrpr.get_articles(query, 3)

    # put text from articles into text file
    file_names = []
    if not os.path.exists('./articles'):
        os.makedirs('articles')
    for i in range(len(articles_text)):
        file_name = './articles/article' + str(i + 1) + '.txt'
        file = open(file_name, 'w')
        file.write(articles_text[i])
        file.close()
        file_names.append(file_name)

    # merge articles
    file_list_file = open('./files.txt', 'w')
    for file_name in file_names:
        file_list_file.write(file_name + '\n')
    file_list_file.close()
    mrgr.process_files('./files.txt')

    # return output
    return mrgr.summary