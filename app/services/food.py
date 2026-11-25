import aiohttp
import asyncio
import datetime
import logging
import time

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

# How long to wait before scraping again for a week the page did not show last
# time. On Sunday "tomorrow" is next week's Monday while the page still shows
# this week; without the pause every such request would re-scrape.
RECHECK_SECONDS = 3600
# A failed scrape (site down) is retried sooner: it says nothing about whether
# the menu exists, and an hour of "No data" for a blip is too long. It still
# stops every request from queueing on the lock and scraping a dead site.
FAILURE_RECHECK_SECONDS = 60
# target (year, week) -> time.monotonic() before which no new scrape is made.
_retry_at: dict[tuple[int, int], float] = {}
# True when the last scrape worked but its page showed no readable date, so
# _cached_week is None although a page was seen.
_page_week_unknown = False

# Guards fetch_weekly_menu so a burst of requests at the start of a new week
# triggers one scrape, not one per concurrent caller (production logs showed
# three full scrapes inside 30 seconds from exactly this).
_fetch_lock = asyncio.Lock()


def _page_week(table) -> tuple[int, int]:
    """The ISO (year, week) the scraped page is actually showing.

    The header cells carry each weekday's date (`span.nowDate`, Monday first).
    The page does not always switch to the new week when the week turns -
    on Sunday it still shows the current one - so the week it reports, not the
    week it was asked for, is what the cache must be marked with. None, with a
    warning, when no date can be read: guessing today's week would pass an
    old page off as the new week for as long as the cache lives.

    Format confirmed against the live page on 2026-11-25, see docs/KLAS.md.
    """
    span = table.find("span", {"class": "nowDate"})
    try:
        return datetime.date.fromisoformat(span.get_text(strip=True)).isocalendar()[:2]
    except (AttributeError, ValueError):
        logging.warning("Could not read the week from the school menu page")
        return None


async def fetch_weekly_menu() -> bool:
    """Scrape and cache the menu the page shows. Returns whether it parsed.

    On success `_cached_week` is set to the week the page shows.

    Every one of the 15 (day, meal) slots is written on a successful fetch,
    even ones with no <pre> tag (holiday, unpublished week) - stored as None.
    Without that, a genuinely empty slot was indistinguishable from "never
    fetched", and get_menu_for_day re-scraped the page on every request for
    it, forever.
    """
    global _cached_week, _page_week_unknown
    url = "https://www.kw.ac.kr/ko/life/facility11.jsp"
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
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
            _cached_week = _page_week(table)
            _page_week_unknown = _cached_week is None
            return True


async def get_menu_for_day(
    day_index: int, user_lang: Language, target_date: datetime.date | None = None
):
    """The menu text for weekday `day_index`.

    `target_date` is the calendar date that weekday falls on; the cache is
    checked against that date's ISO week. Without it "today" is assumed, which
    is only wrong when the day asked for lies in another week - Sunday's
    "tomorrow" is next week's Monday.
    """
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
    # Keyed by the week of the day being asked for, not of today: on Sunday
    # "tomorrow" is next week's Monday, and index 0 of this week's cache is
    # the Monday that has already passed.
    current_week = (target_date or timezone.now()).isocalendar()[:2]
    if _cached_week != current_week:
        async with _fetch_lock:
            # Re-check inside the lock: a concurrent caller may have already
            # refreshed the cache while this one was waiting for it.
            waiting = time.monotonic() < _retry_at.get(current_week, 0.0)
            if _cached_week != current_week and not waiting:
                logging.info(
                    f"Fetching weekly menu (year={current_week[0]}, "
                    f"week={current_week[1]})"
                )
                try:
                    fetched = await fetch_weekly_menu()
                except Exception:
                    # Network errors and a changed page layout both land
                    # here; the request must still be answered.
                    logging.exception("Error while fetching the weekly school menu")
                    fetched = False
                if fetched:
                    # fetch_weekly_menu marked the cache with the week the page
                    # shows, which can still be the old one.
                    if _cached_week != current_week:
                        _retry_at[current_week] = time.monotonic() + RECHECK_SECONDS
                else:
                    logging.warning("Failed to fetch the weekly school menu")
                    _retry_at[current_week] = (
                        time.monotonic() + FAILURE_RECHECK_SECONDS
                    )

    # Never show another week's menu under this day's name. A failed fetch
    # with nothing cached keeps the old "No data" behaviour.
    if _cached_week != current_week and (
        _cached_week is not None or _page_week_unknown
    ):
        return Strings.get("menu_not_published", user_lang)

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
    return await get_menu_for_day(today_index, user_lang, timezone.now())


async def get_tomorrow_school_food_menu(user_lang: Language):
    tomorrow = timezone.now() + datetime.timedelta(days=1)
    tomorrow_index = tomorrow.weekday()
    if tomorrow_index > 4:
        return Strings.get("school_closed_on_weekend", user_lang)
    return await get_menu_for_day(tomorrow_index, user_lang, tomorrow)


async def get_school_food_info(user_lang: Language):
    return Strings.get("school_food_info", user_lang)


if __name__ == "__main__":
    from pprint import pprint

    pprint(asyncio.run(get_today_school_food_menu(Language.KO)))
    pprint(asyncio.run(get_tomorrow_school_food_menu(Language.KO)))
