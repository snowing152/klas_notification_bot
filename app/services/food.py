import aiohttp
import asyncio
import logging

from bs4 import BeautifulSoup

from app.strings import Strings, Language
from app.utils import timezone


local_cache = {}


async def fetch_weekly_menu():
    url = "https://www.kw.ac.kr/ko/life/facility11.jsp"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            beautiful_soup = BeautifulSoup(await response.text(), "html.parser")
            table_scroll_box = beautiful_soup.find("div", {"class": "table-scroll-box"})

            if table_scroll_box:
                table = table_scroll_box.find("table", {"class": "tbl-list"})
                if not table:
                    return False

                rows = table.find("tbody", {"class": "dietData"}).find_all("tr")

                meal_index = 0
                for row in rows:
                    cells = row.find_all("td", {"class": "vt al"})

                    # Process the 5 weekdays
                    for day_index, cell in enumerate(cells):
                        pre_tag = cell.find("pre")
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

                            # Store each meal in the local cache
                            # 0-4: Monday-Friday breakfast
                            # 5-9: Monday-Friday lunch
                            # 10-14: Monday-Friday dinner
                            cache_index = (meal_index * 5) + day_index
                            local_cache[cache_index] = menu_text

                    meal_index += 1

                return True
            else:
                return False


def clear_cache_if_monday():
    # Clear the cache if today is Monday
    if timezone.now().weekday() == 0:
        local_cache.clear()


async def get_menu_for_day(day_index: int, user_lang: Language):
    clear_cache_if_monday()
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

    # Check if the cache is empty or doesn't have the current week's data
    if not local_cache or day_index not in local_cache:
        logging.info(f"Fetching weekly menu for {days_of_week[day_index]}")
        await fetch_weekly_menu()

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

    # Get the meals from cache
    breakfast = local_cache.get(breakfast_index, "No data")
    lunch = local_cache.get(lunch_index, "No data")
    dinner = local_cache.get(dinner_index, "No data")

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
