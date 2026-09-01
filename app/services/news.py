import logging
import aiohttp
import asyncio
import time

from bs4 import BeautifulSoup

news_cache = {"foreigners": [], "all": []}
# Tracked per feed: fetching one type must not mark the other one fresh
last_fetch_time = {"foreigners": 0.0, "all": 0.0}
CACHE_TTL_SECONDS = 3600


async def fetch_news(news_type: str):
    if news_type == "foreigners":
        # news for foreigners
        url = "https://www.kw.ac.kr/ko/life/notice.jsp?srCategoryId=10"
    else:
        # all news
        url = "https://www.kw.ac.kr/ko/life/notice.jsp?srCategoryId="

    items = []
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            beautiful_soup = BeautifulSoup(await response.text(), "html.parser")
            table_scroll_box = beautiful_soup.find("div", {"class": "board-list-box"})
            if table_scroll_box:
                for item in table_scroll_box.find_all("li"):
                    link = "https://www.kw.ac.kr" + item.find("a").get("href")
                    title = (
                        item.find("a")
                        .text.replace("\n", "")
                        .replace("Attachment", "")
                        .replace("\r", " ")
                        .replace("  ", "")
                        .strip()
                    )
                    date = (
                        item.find("p", {"class": "info"})
                        .text.split("|")[2]
                        .replace("수정일", "")
                        .strip()
                    )
                    items.append({"title": title, "link": link, "date": date})

    # Replace instead of append: appending made the list grow on every refresh
    # and kept serving the first batch ever fetched
    news_cache[news_type] = items
    last_fetch_time[news_type] = time.time()


async def get_news(news_type: str):
    is_stale = time.time() - last_fetch_time[news_type] > CACHE_TTL_SECONDS
    if not news_cache[news_type] or is_stale:
        logging.info(f"Fetching {news_type} news from the website")
        await fetch_news(news_type)
    return news_cache[news_type]


if __name__ == "__main__":
    from pprint import pprint

    pprint(asyncio.run(get_news("all")))
