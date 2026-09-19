import time
import logging

from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram import Dispatcher, F, types

from app.strings import Strings
from app.database.database import get_user
from app.keyboards import (
    create_login_klas_keyboard,
    create_show_filter_keyboard,
    quick_access_labels,
)
from app.utils.encryption import decrypt_password
from app.services.kw import KwangwoonUniversityApi
from app.utils.language_utils import get_user_language_with_fallback

# Assignment types the "Only assignments" filter covers - everything that
# isn't a lecture to watch. Kept separate from "lectures" because that's the
# distinction students actually care about: watch vs. produce and submit.
ASSIGNMENT_TYPES = {"homeworks", "quizzes", "team_projects"}
TYPE_EMOJIS = {
    "lectures": "📚",
    "homeworks": "📝",
    "quizzes": "🧠",
    "team_projects": "👥",
}

# get_todo_list() logs in and issues a gather() of 4 requests per subject, so
# a filter tap re-running it would feel like a hang. Cache the flattened,
# already-sorted list per user for a few minutes instead.
CACHE_TTL_SECONDS = 300
_items_cache: dict[str, tuple[float, list[dict]]] = {}


def _flatten_and_sort(todo_list: list[dict]) -> list[dict]:
    """One list across every subject and type, soonest deadline first."""
    items = []
    for subject in todo_list:
        subject_name = subject.get("name", "Unknown Subject")
        for assignment_type, assignments in subject["todo"].items():
            for assignment in assignments:
                left_time = assignment["left_time"]
                if left_time.total_seconds() < 0:
                    continue
                items.append(
                    {
                        "type": assignment_type,
                        "subject": subject_name,
                        "title": assignment.get("title", "Untitled"),
                        "left_time": left_time,
                    }
                )
    items.sort(key=lambda item: item["left_time"])
    return items


async def _load_items(user_id: str) -> tuple[str, list[dict] | None]:
    """(status, items). status is "no_user", "login_failed" or "ok"."""
    cached = _items_cache.get(user_id)
    if cached and time.monotonic() - cached[0] < CACHE_TTL_SECONDS:
        return "ok", cached[1]

    user = await get_user(user_id)
    if not user:
        return "no_user", None

    async with KwangwoonUniversityApi() as kw:
        if not await kw.login(user.username, decrypt_password(user.encrypted_password)):
            return "login_failed", None
        todo_list = await kw.get_todo_list()

    if todo_list is None:
        return "login_failed", None

    items = _flatten_and_sort(todo_list)
    _items_cache[user_id] = (time.monotonic(), items)
    return "ok", items


def _filter_items(items: list[dict], category: str) -> list[dict]:
    if category == "lectures":
        return [item for item in items if item["type"] == "lectures"]
    if category == "assignments":
        return [item for item in items if item["type"] in ASSIGNMENT_TYPES]
    return items


def _format_time_left(left_time, user_lang) -> str:
    total_seconds = int(left_time.total_seconds())
    days, remainder = divmod(max(total_seconds, 0), 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes = remainder // 60

    parts = []
    if days:
        parts.append(Strings.get("time_days", user_lang, count=days))
    if hours:
        parts.append(Strings.get("time_hours", user_lang, count=hours))
    if minutes or not parts:
        parts.append(Strings.get("time_minutes", user_lang, count=minutes))
    return " ".join(parts)


def _render_chunks(items: list[dict], user_lang, limit: int = 4096) -> list[str]:
    """Message chunks that never cut an item in half."""
    if not items:
        return [Strings.get("no_assignments", user_lang)]

    header = Strings.get("show_header", user_lang)
    blocks = [
        Strings.get(
            "show_item",
            user_lang,
            emoji=TYPE_EMOJIS.get(item["type"], "📌"),
            time_str=_format_time_left(item["left_time"], user_lang),
            type_label=Strings.get(f"type_{item['type']}", user_lang),
            title=item["title"],
            subject=item["subject"],
        )
        for item in items
    ]

    chunks = []
    current = header
    for block in blocks:
        if len(current) + len(block) > limit and current != header:
            chunks.append(current)
            current = header
        current += block
    chunks.append(current)
    return chunks


def _filter_keyboard(items: list[dict], category: str, user_lang):
    """None when there's nothing to filter into - one category with no items
    in it makes every filter button either a no-op or a dead end."""
    has_lectures = any(item["type"] == "lectures" for item in items)
    has_assignments = any(item["type"] in ASSIGNMENT_TYPES for item in items)
    if not (has_lectures and has_assignments):
        return None
    return create_show_filter_keyboard(user_lang, category)


async def show_all_assignments(message: types.Message):
    try:
        user_lang = await get_user_language_with_fallback(message)
        logging.info(f"User {message.from_user.id} used /show command")
        user_id = str(message.from_user.id)

        status, items = await _load_items(user_id)
        if status == "no_user":
            await message.answer(
                Strings.get("need_to_register", user_lang),
                reply_markup=create_login_klas_keyboard(user_lang),
            )
            return
        if status == "login_failed":
            await message.answer(Strings.get("unexpected_error", user_lang))
            return

        filtered = _filter_items(items, "all")
        chunks = _render_chunks(filtered, user_lang)
        keyboard = _filter_keyboard(items, "all", user_lang)
        for i, chunk in enumerate(chunks):
            is_last = i == len(chunks) - 1
            await message.answer(chunk, reply_markup=keyboard if is_last else None)

    except Exception as e:
        logging.error(f"Error in show_all_assignments: {e}")
        await message.answer(Strings.get("unexpected_error", user_lang))


async def process_show_filter_callback(callback_query: types.CallbackQuery):
    try:
        user_lang = await get_user_language_with_fallback(callback_query)
        user_id = str(callback_query.from_user.id)
        category = callback_query.data.removeprefix("show_")

        status, items = await _load_items(user_id)
        if status != "ok":
            # The cache expired between opening /show and tapping a filter,
            # and the retry itself failed - ask the user to just run it again
            # rather than block the tap on a second full KLAS login here.
            await callback_query.answer(Strings.get("unexpected_error", user_lang))
            return

        filtered = _filter_items(items, category)
        chunks = _render_chunks(filtered, user_lang)
        keyboard = _filter_keyboard(items, category, user_lang)

        try:
            await callback_query.message.edit_text(
                chunks[0], reply_markup=keyboard if len(chunks) == 1 else None
            )
        except TelegramBadRequest:
            # e.g. "message is not modified" - a harmless double-tap
            pass
        for i, extra in enumerate(chunks[1:]):
            is_last = i == len(chunks) - 2
            await callback_query.message.answer(
                extra, reply_markup=keyboard if is_last else None
            )

        await callback_query.answer()
    except Exception as e:
        logging.error(f"Error in process_show_filter_callback: {e}")
        await callback_query.answer(Strings.get("unexpected_error", user_lang))


def register_handlers(dp: Dispatcher):
    dp.message.register(show_all_assignments, Command("show"))
    dp.message.register(
        show_all_assignments, F.text.in_(quick_access_labels("button_todos"))
    )
    dp.callback_query.register(
        process_show_filter_callback,
        F.data.in_({"show_all", "show_lectures", "show_assignments"}),
    )
