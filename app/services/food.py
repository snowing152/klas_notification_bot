import aiohttp
import asyncio
import logging

from bs4 import BeautifulSoup

from app.strings import Strings, Language
from app.utils import timezone


local_cache = {}

# The ISO (year, week) local_cache currently holds data for; None means it has
# never been populated. Refetching is gated on this changing, rather than on
# clearing the cache back to empty at the start of every Monday call - the old
# approach meant *every* request on Monday saw an empty cache and re-scraped
# the page until one happened to land after the week's menu was in local_cache.
_cached_week = None

# Guards fetch_weekly_menu so a burst of requests at the start of a new week
# triggers one scrape, not one per concurrent caller (production logs showed
# three full scrapes inside 30 seconds from exactly this).
_fetch_lock = asyncio.Lock()


async def fetch_weekly_menu() -> bool:
    """Scrape and cache this week's menu. Returns whether the page parsed.

    Every one of the 15 (day, meal) slots is written on a successful fetch,
    even ones with no <pre> tag (holiday, unpublished week) - stored as None.
    Without that, a genuinely empty slot was indistinguishable from "never
    fetched", and get_menu_for_day re-scraped the page on every request for
    it, forever.
    """
    url = "https://www.kw.ac.kr/ko/life/facility11.jsp"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            beautiful_soup = BeautifulSoup(await response.text(), "html.parser")
            table_scroll_box = beautiful_soup.find("div", {"class": "table-scroll-box"})

            if not table_scroll_box:
                return False

            table = table_scroll_box.find("table", {"class": "tbl-list"})
            if not table:
                return False

            rows = table.find("tbody", {"class": "dietData"}).find_all("tr")

            new_data = {}
            for meal_index, row in enumerate(rows):
                cells = row.find_all("td", {"class": "vt al"})

                # Process the 5 weekdays - always, even when a cell is
                # missing or empty, so every slot gets an explicit entry.
                for day_index in range(5):
                    cell = cells[day_index] if day_index < len(cells) else None
                    pre_tag = cell.find("pre") if cell else None
                    menu_text = None
                    if pre_tag:
                        menu_text = pre_tag.get_text(strip=False)
                        # Preserve the original format from the pre tags
                        menu_text = "\r\n".join(
                            [
                                line.strip()
                                for line in menu_text.strip().split("\n")
                                if line.strip()
                            ]
                        )

                    # 0-4: Monday-Friday breakfast
                    # 5-9: Monday-Friday lunch
                    # 10-14: Monday-Friday dinner
                    cache_index = (meal_index * 5) + day_index
                    new_data[cache_index] = menu_text

            local_cache.clear()
            local_cache.update(new_data)
            return True


async def get_menu_for_day(day_index: int, user_lang: Language):
    if user_lang == Language.RU:
        days_of_week = [
            "Понедельник",
            "Вторник",
            "Среда",
            "Четверг",
            "Пятница",
        ]
    elif user_lang == Language.KO:
        days_of_week = [
            "월요일",
            "화요일",
            "수요일",
            "목요일",
            "금요일",
        ]
    else:
        days_of_week = [
            "Monday",
            "Tuesday",
            "Wednesday",
            "Thursday",
            "Friday",
        ]
    header = Strings.get(
        "school_food_menu_header", user_lang, day=days_of_week[day_index]
    )

    # Refetch once per calendar week, not once per request that happens to
    # miss the cache - a day with no published menu would otherwise miss
    # forever and re-scrape on every single call for it.
    current_week = timezone.now().isocalendar()[:2]
    global _cached_week
    if _cached_week != current_week:
        async with _fetch_lock:
            # Re-check inside the lock: a concurrent caller may have already
            # refreshed the cache while this one was waiting for it.
            if _cached_week != current_week:
                logging.info(
                    f"Fetching weekly menu (year={current_week[0]}, "
                    f"week={current_week[1]})"
                )
                if await fetch_weekly_menu():
                    _cached_week = current_week
                else:
                    logging.warning("Failed to fetch the weekly school menu")

    # Calculate the indexes for breakfast, lunch, and dinner for this day
    breakfast_index = day_index
    lunch_index = day_index + 5
    dinner_index = day_index + 10

    # Get meal labels based on language
    if user_lang == Language.RU:
        breakfast_label = "🌞 Завтрак"
        lunch_label = "🍲 Обед"
        dinner_label = "🍔 Обед(Фудкорт)"
    elif user_lang == Language.KO:
        breakfast_label = "🌞 아침"
        lunch_label = "🍲 점심"
        dinner_label = "🍔 전심 (푸드코트)"
    else:  # Default to English
        breakfast_label = "🌞 Breakfast"
        lunch_label = "🍲 Lunch"
        dinner_label = "🍔 Lunch (Food Court)"

    # Get the meals from cache. A key missing entirely means no successful
    # fetch has landed yet; a key present but None means the fetch succeeded
    # and this slot genuinely has nothing published - both show as "No data".
    breakfast = local_cache.get(breakfast_index) or "No data"
    lunch = local_cache.get(lunch_index) or "No data"
    dinner = local_cache.get(dinner_index) or "No data"

    # Combine into one formatted string
    combined_menu = f"{breakfast_label}:\n{breakfast}\n\n{lunch_label}:\n{lunch}\n\n{dinner_label}:\n{dinner}"

    return header + combined_menu


async def get_today_school_food_menu(user_lang: Language):
    today_index = timezone.now().weekday() % 7
    if today_index > 4:
        return Strings.get("school_closed_on_weekend", user_lang)
    return await get_menu_for_day(today_index, user_lang)


async def get_tomorrow_school_food_menu(user_lang: Language):
    tomorrow_index = (timezone.now().weekday() + 1) % 7
    if tomorrow_index > 4:
        return Strings.get("school_closed_on_weekend", user_lang)
    return await get_menu_for_day(tomorrow_index, user_lang)


async def get_school_food_info(user_lang: Language):
    return Strings.get("school_food_info", user_lang)


if __name__ == "__main__":
    from pprint import pprint

    pprint(asyncio.run(get_today_school_food_menu(Language.KO)))
    pprint(asyncio.run(get_tomorrow_school_food_menu(Language.KO)))
