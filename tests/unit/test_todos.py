import datetime
import unittest.mock as mock
from contextlib import asynccontextmanager

import pytest
from unittest.mock import AsyncMock
from aiogram import types

from app.database.database import save_user
from app.handlers import todos
from app.keyboards import KLAS_RELOGIN_CALLBACK
from app.handlers.todos import process_show_filter_callback, show_all_assignments
from app.services.klas_session import KlasAccess, SessionStatus
from app.strings import Language, Strings
from app.utils import timezone


class FakeApi:
    """Stands in for the KwangwoonUniversityApi that open_session yields."""

    def __init__(self, todo_list=None, calls=None):
        self._todo_list = todo_list if todo_list is not None else []
        self.calls = calls if calls is not None else {"open": 0, "todo": 0}

    async def get_todo_list(self):
        self.calls["todo"] += 1
        return self._todo_list


def patch_session(api, status=SessionStatus.ACTIVE):
    """Replace open_session in the handler with one that yields `api`.

    The handler never logs in any more, so what a test controls is the status
    open_session reports; `api` is only handed out when it is ACTIVE.
    """

    @asynccontextmanager
    async def fake_open_session(user):
        if api is not None:
            api.calls["open"] += 1
        yield KlasAccess(status, api if status is SessionStatus.ACTIVE else None)

    return mock.patch.object(todos, "open_session", fake_open_session)


def make_subject(subject, items, now=None):
    """items: list of (type, title, hours_left).

    KLAS data carries the deadline itself, so the hours are turned into one
    here - against the same clock the handler will read unless a test freezes
    its own.
    """
    moment = now or timezone.now()
    todo = {}
    for type_, title, hours in items:
        todo.setdefault(type_, []).append(
            {"title": title, "expire_at": moment + datetime.timedelta(hours=hours)}
        )
    return {"name": subject, "todo": todo}


class FakeClock:
    """A `now()` the test moves by hand."""

    def __init__(self, moment):
        self.moment = moment

    def now(self):
        return self.moment


def make_message(user_id="700", language_code="en"):
    from_user = AsyncMock()
    from_user.id = user_id
    from_user.language_code = language_code

    message = AsyncMock(spec=types.Message)
    message.from_user = from_user
    message.answer = AsyncMock()
    return message


def make_callback(data, user_id="700", language_code="en"):
    from_user = AsyncMock()
    from_user.id = user_id
    from_user.language_code = language_code

    callback = AsyncMock(spec=types.CallbackQuery)
    callback.data = data
    callback.from_user = from_user
    callback.message = AsyncMock(spec=types.Message)
    callback.message.edit_text = AsyncMock()
    callback.message.edit_reply_markup = AsyncMock()
    callback.message.answer = AsyncMock()
    callback.answer = AsyncMock()
    return callback


async def _register(user_id):
    await save_user(user_id, f"student{user_id}", "enc", Language.EN)


@pytest.fixture(autouse=True)
def _clear_cache():
    todos._items_cache.clear()
    yield
    todos._items_cache.clear()


@pytest.mark.asyncio
async def test_unregistered_user_gets_a_login_button_not_just_text():
    message = make_message(user_id="no_such_user")

    await show_all_assignments(message)

    markup = message.answer.call_args.kwargs["reply_markup"]
    assert markup.inline_keyboard[0][0].callback_data == "account_klas"


@pytest.mark.asyncio
async def test_missing_session_asks_for_a_login_with_a_button():
    await _register("701")

    with patch_session(None, SessionStatus.NEEDS_LOGIN):
        message = make_message(user_id="701")
        await show_all_assignments(message)

    assert message.answer.call_args[0][0] == Strings.get(
        "klas_login_required", Language.EN
    )
    markup = message.answer.call_args.kwargs["reply_markup"]
    assert markup.inline_keyboard[0][0].callback_data == KLAS_RELOGIN_CALLBACK


@pytest.mark.asyncio
async def test_unreachable_klas_says_to_try_later():
    await _register("711")

    with patch_session(None, SessionStatus.UNAVAILABLE):
        message = make_message(user_id="711")
        await show_all_assignments(message)

    assert message.answer.call_args[0][0] == Strings.get("klas_unavailable", Language.EN)
    assert message.answer.call_args.kwargs.get("reply_markup") is None


@pytest.mark.asyncio
async def test_a_todo_list_klas_did_not_return_counts_as_unavailable():
    await _register("714")
    api = FakeApi()
    api._todo_list = None

    with patch_session(api):
        message = make_message(user_id="714")
        await show_all_assignments(message)

    assert message.answer.call_args[0][0] == Strings.get("klas_unavailable", Language.EN)
    assert "714" not in todos._items_cache


@pytest.mark.asyncio
async def test_filter_tap_with_an_ended_session_sends_the_login_button():
    await _register("715")

    with patch_session(None, SessionStatus.NEEDS_LOGIN):
        callback = make_callback("show_lectures", user_id="715")
        await process_show_filter_callback(callback)

    answer = callback.message.answer.call_args
    assert answer[0][0] == Strings.get("klas_login_required", Language.EN)
    markup = answer.kwargs["reply_markup"]
    assert markup.inline_keyboard[0][0].callback_data == KLAS_RELOGIN_CALLBACK
    callback.answer.assert_awaited_once_with()
    callback.message.edit_text.assert_not_called()


@pytest.mark.asyncio
async def test_filter_tap_with_unreachable_klas_answers_with_a_short_notice():
    await _register("716")

    with patch_session(None, SessionStatus.UNAVAILABLE):
        callback = make_callback("show_lectures", user_id="716")
        await process_show_filter_callback(callback)

    callback.answer.assert_awaited_once_with(
        Strings.get("klas_unavailable", Language.EN)
    )
    callback.message.edit_text.assert_not_called()


@pytest.mark.asyncio
async def test_no_assignments_shows_the_celebration_text_with_no_filters():
    await _register("702")
    api = FakeApi(todo_list=[])

    with patch_session(api):
        message = make_message(user_id="702")
        await show_all_assignments(message)

    assert message.answer.call_args[0][0] == Strings.get("no_assignments", Language.EN)
    assert message.answer.call_args.kwargs.get("reply_markup") is None


@pytest.mark.asyncio
async def test_items_are_sorted_soonest_deadline_first_across_subjects():
    await _register("703")
    todo_list = [
        make_subject("Algorithms", [("homeworks", "Late one", 48)]),
        make_subject("Networks", [("quizzes", "Urgent one", 2)]),
    ]
    api = FakeApi(todo_list=todo_list)

    with patch_session(api):
        message = make_message(user_id="703")
        await show_all_assignments(message)

    text = message.answer.call_args[0][0]
    assert text.index("Urgent one") < text.index("Late one")


@pytest.mark.asyncio
async def test_expired_items_are_dropped():
    await _register("704")
    todo_list = [
        make_subject(
            "Algorithms",
            [("homeworks", "Already expired", -1), ("homeworks", "Still open", 5)],
        )
    ]
    api = FakeApi(todo_list=todo_list)

    with patch_session(api):
        message = make_message(user_id="704")
        await show_all_assignments(message)

    text = message.answer.call_args[0][0]
    assert "Still open" in text
    assert "Already expired" not in text


@pytest.mark.asyncio
async def test_filter_buttons_only_appear_when_both_categories_exist():
    await _register("705")
    # Only lectures - filtering into "assignments" would be empty, so no
    # filter buttons should be offered at all.
    todo_list = [make_subject("Algorithms", [("lectures", "Week 3", 10)])]
    api = FakeApi(todo_list=todo_list)

    with patch_session(api):
        message = make_message(user_id="705")
        await show_all_assignments(message)

    assert message.answer.call_args.kwargs.get("reply_markup") is None


@pytest.mark.asyncio
async def test_filter_buttons_appear_and_exclude_the_current_view():
    await _register("706")
    todo_list = [
        make_subject(
            "Algorithms", [("lectures", "Week 3", 10), ("homeworks", "Report", 5)]
        )
    ]
    api = FakeApi(todo_list=todo_list)

    with patch_session(api):
        message = make_message(user_id="706")
        await show_all_assignments(message)

    markup = message.answer.call_args.kwargs["reply_markup"]
    callbacks = [b.callback_data for row in markup.inline_keyboard for b in row]
    # "all" is the current view, so it isn't offered again
    assert callbacks == ["show_lectures", "show_assignments"]


@pytest.mark.asyncio
async def test_a_second_show_within_the_cache_window_does_not_hit_klas_again():
    await _register("707")
    todo_list = [make_subject("Algorithms", [("lectures", "Week 3", 10)])]
    api = FakeApi(todo_list=todo_list)

    with patch_session(api):
        await show_all_assignments(make_message(user_id="707"))
        await show_all_assignments(make_message(user_id="707"))

    assert api.calls["open"] == 1
    assert api.calls["todo"] == 1


@pytest.mark.asyncio
async def test_a_list_klas_answered_incompletely_is_shown_but_not_cached():
    """Cached, the gap would be repeated to the user for five minutes, when
    asking again a moment later usually fills it."""
    await _register("708")
    subject = make_subject("Algorithms", [("lectures", "Week 3", 10)])
    api = FakeApi(todo_list=[{**subject, "incomplete": True}])
    message = make_message(user_id="708")

    with patch_session(api):
        await show_all_assignments(message)
        await show_all_assignments(make_message(user_id="708"))

    assert "Week 3" in message.answer.call_args.args[0]
    assert api.calls["todo"] == 2


@pytest.mark.asyncio
async def test_deleting_a_users_data_drops_their_cached_assignments():
    """The cache is consulted before the database, so a user who deleted
    everything would otherwise keep being shown their assignments from
    memory until the TTL ran out."""
    from app.handlers.auth import delete_all_user_data

    await _register("709")
    todo_list = [make_subject("Algorithms", [("homeworks", "Secret report", 5)])]
    api = FakeApi(todo_list=todo_list)

    with patch_session(api):
        await show_all_assignments(make_message(user_id="709"))
        assert "709" in todos._items_cache

        await delete_all_user_data("709")
        assert "709" not in todos._items_cache

        message = make_message(user_id="709")
        await show_all_assignments(message)

    # Back to "you need to register", not the cached list
    text = message.answer.call_args[0][0]
    assert "Secret report" not in text
    assert text == Strings.get("need_to_register", Language.EN)


@pytest.mark.asyncio
async def test_filter_callback_serves_from_cache_and_narrows_the_view():
    await _register("708")
    todo_list = [
        make_subject(
            "Algorithms", [("lectures", "Week 3", 10), ("homeworks", "Report", 5)]
        )
    ]
    api = FakeApi(todo_list=todo_list)

    with patch_session(api):
        await show_all_assignments(make_message(user_id="708"))
        callback = make_callback("show_lectures", user_id="708")
        await process_show_filter_callback(callback)

    assert api.calls["open"] == 1  # the filter tap reused the cache
    text = callback.message.edit_text.call_args[0][0]
    assert "Week 3" in text
    assert "Report" not in text
    markup = callback.message.edit_text.call_args.kwargs["reply_markup"]
    callbacks = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert callbacks == ["show_all", "show_assignments"]


@pytest.mark.asyncio
async def test_discussions_show_up_and_filter_as_assignments():
    """A 토론 is something to write, not something to watch, so the "only
    assignments" filter has to keep it."""
    await _register("710")
    todo_list = [
        make_subject(
            "English",
            [("lectures", "Week 3", 10), ("discussions", "논문을 읽고 토론", 30)],
        )
    ]
    api = FakeApi(todo_list=todo_list)

    with patch_session(api):
        await show_all_assignments(make_message(user_id="710"))
        callback = make_callback("show_assignments", user_id="710")
        await process_show_filter_callback(callback)

    text = callback.message.edit_text.call_args[0][0]
    assert "💬 " in text
    assert "논문을 읽고 토론" in text
    assert "Week 3" not in text


@pytest.mark.asyncio
async def test_a_cached_list_keeps_counting_down(monkeypatch):
    """The cache holds deadlines, not a countdown frozen at fetch time: a
    second /show inside the TTL must not repeat the minutes already gone."""
    await _register("712")
    clock = FakeClock(datetime.datetime(2026, 9, 20, 23, 1))
    monkeypatch.setattr(todos, "timezone", clock)
    todo_list = [
        make_subject(
            "컴퓨터그래픽스",
            [("homeworks", "P02 Primitives and Keyboard", 2)],
            now=datetime.datetime(2026, 9, 20, 22, 59),
        )
    ]
    api = FakeApi(todo_list=todo_list)

    with patch_session(api):
        first = make_message(user_id="712")
        await show_all_assignments(first)

        clock.moment += datetime.timedelta(minutes=5)
        second = make_message(user_id="712")
        await show_all_assignments(second)

    assert api.calls["todo"] == 1, "the second /show should come from the cache"
    assert "in 1h 58m" in first.answer.call_args[0][0]
    assert "in 1h 53m" in second.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_a_deadline_that_passes_while_cached_drops_out_of_the_list():
    await _register("713")
    todo_list = [
        make_subject(
            "Algorithms", [("homeworks", "Due in a minute", 1 / 60), ("lectures", "Week 3", 10)]
        )
    ]
    api = FakeApi(todo_list=todo_list)

    with patch_session(api):
        await show_all_assignments(make_message(user_id="713"))
        later = make_message(user_id="713")
        with mock.patch.object(
            todos, "timezone", FakeClock(timezone.now() + datetime.timedelta(minutes=2))
        ):
            await show_all_assignments(later)

    assert api.calls["todo"] == 1
    assert "Due in a minute" not in later.answer.call_args[0][0]
    assert "Week 3" in later.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_a_rejected_password_says_to_register_again():
    """No login button: tapping it would only resend the password KLAS refused."""
    await _register("717")

    with patch_session(None, SessionStatus.PASSWORD_REJECTED):
        message = make_message(user_id="717")
        await show_all_assignments(message)

    assert message.answer.call_args[0][0] == Strings.get(
        "credentials_expired", Language.EN
    )
    assert message.answer.call_args.kwargs.get("reply_markup") is None


@pytest.mark.asyncio
async def test_filter_tap_with_a_rejected_password_says_to_register_again():
    await _register("718")

    with patch_session(None, SessionStatus.PASSWORD_REJECTED):
        callback = make_callback("show_lectures", user_id="718")
        await process_show_filter_callback(callback)

    callback.message.answer.assert_awaited_once_with(
        Strings.get("credentials_expired", Language.EN)
    )
    callback.message.edit_text.assert_not_called()


@pytest.mark.asyncio
async def test_lists_are_sent_as_html_and_filters_share_one_row():
    await _register("719")
    todo_list = [
        make_subject(
            "Algorithms", [("lectures", "Week 3", 10), ("homeworks", "a < b", 5)]
        )
    ]
    api = FakeApi(todo_list=todo_list)

    with patch_session(api):
        message = make_message(user_id="719")
        await show_all_assignments(message)
        callback = make_callback("show_lectures", user_id="719")
        await process_show_filter_callback(callback)

    assert message.answer.call_args.kwargs["parse_mode"] == "HTML"
    assert "a &lt; b" in message.answer.call_args[0][0]
    assert callback.message.edit_text.call_args.kwargs["parse_mode"] == "HTML"
    rows = message.answer.call_args.kwargs["reply_markup"].inline_keyboard
    assert len(rows) == 1 and len(rows[0]) == 2


async def test_a_repeated_filter_tap_is_silent():
    from aiogram.exceptions import TelegramBadRequest

    await _register("720")
    api = FakeApi(todo_list=[make_subject("Algorithms", [("lectures", "Week 3", 10)])])
    with patch_session(api):
        await show_all_assignments(make_message(user_id="720"))
        callback = make_callback("show_all", user_id="720")
        callback.message.edit_text.side_effect = TelegramBadRequest(
            method=None, message="Bad Request: message is not modified"
        )
        await process_show_filter_callback(callback)

    callback.answer.assert_awaited_once_with()


async def test_a_filter_tap_that_telegram_refuses_is_reported_not_swallowed(caplog):
    from aiogram.exceptions import TelegramBadRequest

    await _register("721")
    api = FakeApi(todo_list=[make_subject("Algorithms", [("lectures", "Week 3", 10)])])
    with patch_session(api):
        await show_all_assignments(make_message(user_id="721"))
        callback = make_callback("show_all", user_id="721")
        callback.message.edit_text.side_effect = TelegramBadRequest(
            method=None, message="Bad Request: can't parse entities"
        )
        await process_show_filter_callback(callback)

    callback.answer.assert_awaited_once_with(
        Strings.get("unexpected_error", Language.EN)
    )
    assert "can't parse entities" in caplog.text


@pytest.mark.asyncio
async def test_a_deleted_user_is_not_served_from_a_cache_written_after_deletion():
    """A fetch that was in flight when the data was deleted writes its entry
    after forget_cached_items ran; the cache must not answer for them."""
    from app.database.database import delete_user

    await _register("710")
    todos._items_cache["710"] = (
        todos.time.monotonic(),
        [
            {
                "type": "homeworks",
                "subject": "A",
                "title": "Secret",
                "expire_at": timezone.now() + datetime.timedelta(days=1),
            }
        ],
    )

    await delete_user("710")

    assert await todos._load_items("710") == ("no_user", None)
    assert "710" not in todos._items_cache


@pytest.mark.asyncio
async def test_nothing_is_cached_when_the_user_was_deleted_during_the_fetch():
    """KLAS takes seconds; an entry written after the deletion would be a
    deleted user's assignment list sitting in memory."""
    from app.database.database import delete_user

    await _register("711")

    class DeletingApi(FakeApi):
        async def get_todo_list(self):
            await delete_user("711")
            return self._todo_list

    api = DeletingApi(todo_list=[make_subject("Algorithms", [("homeworks", "Secret", 5)])])

    with patch_session(api):
        await todos._load_items("711")

    assert "711" not in todos._items_cache


async def test_a_multi_message_filter_result_is_sent_fresh_in_order(monkeypatch):
    """The keyboard sits on the last message of the old list. Editing it into
    chunk 1 and sending chunks 2.. after it scrambled the chat."""
    monkeypatch.setattr(
        todos, "render_assignments", lambda *a, **k: ["part 1", "part 2", "part 3"]
    )
    await _register("730")
    # Lectures and assignments both present, so the filter keyboard exists.
    api = FakeApi(
        todo_list=[
            make_subject("Algorithms", [("lectures", "Week 3", 10), ("homeworks", "R", 5)])
        ]
    )
    with patch_session(api):
        callback = make_callback("show_all", user_id="730")
        await process_show_filter_callback(callback)

    callback.message.edit_text.assert_not_called()
    callback.message.edit_reply_markup.assert_awaited_once_with(reply_markup=None)
    sent = callback.message.answer.await_args_list
    assert [call.args[0] for call in sent] == ["part 1", "part 2", "part 3"]
    assert [call.kwargs["reply_markup"] is not None for call in sent] == [
        False,
        False,
        True,
    ]
    callback.answer.assert_awaited_once_with()


async def test_a_single_message_filter_result_edits_the_tapped_message(monkeypatch):
    await _register("731")
    api = FakeApi(
        todo_list=[
            make_subject("Algorithms", [("lectures", "Week 3", 10), ("homeworks", "R", 5)])
        ]
    )
    with patch_session(api):
        callback = make_callback("show_lectures", user_id="731")
        await process_show_filter_callback(callback)

    callback.message.edit_text.assert_awaited_once()
    callback.message.edit_reply_markup.assert_not_called()
    callback.message.answer.assert_not_called()


async def test_a_refused_keyboard_removal_is_reported_not_swallowed(monkeypatch, caplog):
    from aiogram.exceptions import TelegramBadRequest

    monkeypatch.setattr(todos, "render_assignments", lambda *a, **k: ["a", "b"])
    await _register("732")
    api = FakeApi(todo_list=[make_subject("Algorithms", [("lectures", "Week 3", 10)])])
    with patch_session(api):
        callback = make_callback("show_all", user_id="732")
        callback.message.edit_reply_markup.side_effect = TelegramBadRequest(
            method=None, message="Bad Request: message can't be edited"
        )
        await process_show_filter_callback(callback)

    callback.message.answer.assert_not_called()
    callback.answer.assert_awaited_once_with(
        Strings.get("unexpected_error", Language.EN)
    )


async def test_an_item_with_a_null_title_is_listed_as_untitled():
    await _register("733")
    todo_list = [make_subject("Algorithms", [("lectures", None, 10)])]
    todo_list.append({"name": None, "todo": {"homeworks": [
        {"title": None, "expire_at": timezone.now() + datetime.timedelta(hours=3)}
    ]}})
    api = FakeApi(todo_list=todo_list)
    message = make_message(user_id="733")

    with patch_session(api):
        await show_all_assignments(message)

    text = message.answer.call_args.args[0]
    assert "Untitled" in text
    assert "Unknown Subject" in text
