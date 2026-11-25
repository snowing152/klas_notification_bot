from unittest.mock import AsyncMock

import pytest
from aiogram import types
from aiogram.exceptions import TelegramBadRequest

from app.handlers import callbacks
from app.handlers.callbacks import process_callback_query


def make_callback(data, user_id="900"):
    from_user = AsyncMock()
    from_user.id = user_id
    from_user.language_code = "en"

    callback = AsyncMock(spec=types.CallbackQuery)
    callback.data = data
    callback.from_user = from_user
    callback.message = AsyncMock(spec=types.Message)
    callback.message.edit_text = AsyncMock()
    callback.message.reply = AsyncMock()
    callback.message.answer = AsyncMock()
    callback.message.delete = AsyncMock()
    callback.answer = AsyncMock()
    return callback


@pytest.fixture(autouse=True)
def _stub_services(monkeypatch):
    async def text(*args, **kwargs):
        return "menu"

    async def news(kind):
        return [{"date": "d", "title": "t", "link": "https://example.com"}]

    monkeypatch.setattr(callbacks, "get_school_food_info", text)
    monkeypatch.setattr(callbacks, "get_today_school_food_menu", text)
    monkeypatch.setattr(callbacks, "get_tomorrow_school_food_menu", text)
    monkeypatch.setattr(callbacks, "get_news", news)


@pytest.mark.parametrize(
    "data",
    [
        "filter_food_info",
        "filter_today",
        "filter_tomorrow",
        "filter_news",
        "language_en",
        "language_ko",
        "language_ru",
        "filter_foreigners_news",
        "filter_all_news",
    ],
)
async def test_every_branch_answers_the_callback(data):
    callback = make_callback(data)

    await process_callback_query(callback)

    callback.answer.assert_awaited_once_with()


async def test_a_failing_news_fetch_still_answers(monkeypatch):
    async def broken(kind):
        raise RuntimeError("down")

    monkeypatch.setattr(callbacks, "get_news", broken)
    callback = make_callback("filter_all_news")

    await process_callback_query(callback)

    callback.message.reply.assert_awaited_once()
    callback.answer.assert_awaited_once_with()


async def test_a_refused_edit_still_answers():
    callback = make_callback("filter_today")
    callback.message.edit_text.side_effect = TelegramBadRequest(
        method=None, message="Bad Request: message can't be edited"
    )

    await process_callback_query(callback)

    callback.message.reply.assert_awaited_once()
    callback.answer.assert_awaited_once_with()


async def test_a_too_old_answer_does_not_become_an_error_message():
    callback = make_callback("filter_today")
    callback.answer.side_effect = TelegramBadRequest(
        method=None, message="Bad Request: query is too old"
    )

    await process_callback_query(callback)

    callback.message.edit_text.assert_awaited_once()
    callback.message.reply.assert_not_called()
    callback.answer.assert_awaited_once_with()


async def test_the_callback_is_answered_before_slow_work(monkeypatch):
    order = []
    callback = make_callback("filter_today")
    callback.answer.side_effect = lambda: order.append("answer")

    async def slow_menu(lang):
        order.append("scrape")
        return "menu"

    monkeypatch.setattr(callbacks, "get_today_school_food_menu", slow_menu)

    await process_callback_query(callback)

    assert order == ["answer", "scrape"]


@pytest.mark.parametrize("data", ["filter_foreigners_news", "filter_all_news"])
async def test_a_failing_delete_in_news_does_not_raise(data):
    callback = make_callback(data)
    callback.message.delete.side_effect = TelegramBadRequest(
        method=None, message="Bad Request: message can't be deleted"
    )
    callback.message.reply.side_effect = TelegramBadRequest(
        method=None, message="Bad Request: reply refused"
    )

    await process_callback_query(callback)

    callback.answer.assert_awaited_once_with()


async def test_a_failing_error_reply_does_not_raise():
    callback = make_callback("filter_today")
    callback.message.edit_text.side_effect = RuntimeError("boom")
    callback.message.reply.side_effect = RuntimeError("reply failed")

    await process_callback_query(callback)

    callback.answer.assert_awaited_once_with()


async def test_an_unexpected_error_still_answers():
    callback = make_callback("filter_today")
    callback.message.edit_text.side_effect = RuntimeError("boom")

    await process_callback_query(callback)

    callback.answer.assert_awaited_once_with()
