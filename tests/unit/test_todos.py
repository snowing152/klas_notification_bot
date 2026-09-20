import datetime
import unittest.mock as mock

import pytest
from unittest.mock import AsyncMock
from aiogram import types

from app.database.database import save_user
from app.handlers import todos
from app.handlers.todos import process_show_filter_callback, show_all_assignments
from app.strings import Language, Strings
from app.utils import timezone


class FakeApi:
    """Stands in for KwangwoonUniversityApi's async context manager."""

    def __init__(self, login_result=True, todo_list=None, calls=None):
        self._login_result = login_result
        self._todo_list = todo_list if todo_list is not None else []
        self.calls = calls if calls is not None else {"login": 0, "todo": 0}

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return False

    async def login(self, username, password):
        self.calls["login"] += 1
        return self._login_result

    async def get_todo_list(self):
        self.calls["todo"] += 1
        return self._todo_list


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
    callback.answer = AsyncMock()
    return callback


async def _register(user_id):
    await save_user(user_id, f"student{user_id}", "enc", Language.EN)


@pytest.fixture(autouse=True)
def _clear_cache():
    todos._items_cache.clear()
    yield
    todos._items_cache.clear()


@pytest.fixture(autouse=True)
def _fake_decrypt(monkeypatch):
    monkeypatch.setattr(todos, "decrypt_password", lambda p: "password")


@pytest.mark.asyncio
async def test_unregistered_user_gets_a_login_button_not_just_text():
    message = make_message(user_id="no_such_user")

    await show_all_assignments(message)

    markup = message.answer.call_args.kwargs["reply_markup"]
    assert markup.inline_keyboard[0][0].callback_data == "account_klas"


@pytest.mark.asyncio
async def test_login_failure_shows_the_generic_error():
    await _register("701")
    api = FakeApi(login_result=False)

    with mock.patch.object(todos, "KwangwoonUniversityApi", lambda: api):
        message = make_message(user_id="701")
        await show_all_assignments(message)

    assert message.answer.call_args[0][0] == Strings.get("unexpected_error", Language.EN)


@pytest.mark.asyncio
async def test_no_assignments_shows_the_celebration_text_with_no_filters():
    await _register("702")
    api = FakeApi(todo_list=[])

    with mock.patch.object(todos, "KwangwoonUniversityApi", lambda: api):
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

    with mock.patch.object(todos, "KwangwoonUniversityApi", lambda: api):
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

    with mock.patch.object(todos, "KwangwoonUniversityApi", lambda: api):
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

    with mock.patch.object(todos, "KwangwoonUniversityApi", lambda: api):
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

    with mock.patch.object(todos, "KwangwoonUniversityApi", lambda: api):
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

    with mock.patch.object(todos, "KwangwoonUniversityApi", lambda: api):
        await show_all_assignments(make_message(user_id="707"))
        await show_all_assignments(make_message(user_id="707"))

    assert api.calls["login"] == 1
    assert api.calls["todo"] == 1


@pytest.mark.asyncio
async def test_deleting_a_users_data_drops_their_cached_assignments():
    """The cache is consulted before the database, so a user who deleted
    everything would otherwise keep being shown their assignments from
    memory until the TTL ran out."""
    from app.handlers.auth import delete_all_user_data

    await _register("709")
    todo_list = [make_subject("Algorithms", [("homeworks", "Secret report", 5)])]
    api = FakeApi(todo_list=todo_list)

    with mock.patch.object(todos, "KwangwoonUniversityApi", lambda: api):
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

    with mock.patch.object(todos, "KwangwoonUniversityApi", lambda: api):
        await show_all_assignments(make_message(user_id="708"))
        callback = make_callback("show_lectures", user_id="708")
        await process_show_filter_callback(callback)

    assert api.calls["login"] == 1  # the filter tap reused the cache
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

    with mock.patch.object(todos, "KwangwoonUniversityApi", lambda: api):
        await show_all_assignments(make_message(user_id="710"))
        callback = make_callback("show_assignments", user_id="710")
        await process_show_filter_callback(callback)

    text = callback.message.edit_text.call_args[0][0]
    assert "💬 " in text
    assert "논문을 읽고 토론" in text
    assert "Week 3" not in text


def test_a_whole_number_of_days_still_prints_its_zero_hours():
    """"1d 0h 58m" must not collapse into "1d 58m" - read literally, that is a
    deadline an hour earlier than KLAS actually gives."""
    left = datetime.timedelta(days=1, minutes=58)

    assert todos._format_time_left(left, Language.EN) == "1d 0h 58m"


def test_shorter_spans_drop_the_units_above_them():
    assert (
        todos._format_time_left(
            datetime.timedelta(hours=3, minutes=20), Language.EN
        )
        == "3h 20m"
    )
    assert todos._format_time_left(datetime.timedelta(minutes=45), Language.EN) == "45m"
    assert todos._format_time_left(datetime.timedelta(seconds=-30), Language.EN) == "0m"


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
            [("homeworks", "P02 Primitives and Keyboard", 25)],
            now=datetime.datetime(2026, 9, 20, 22, 59),
        )
    ]
    api = FakeApi(todo_list=todo_list)

    with mock.patch.object(todos, "KwangwoonUniversityApi", lambda: api):
        first = make_message(user_id="712")
        await show_all_assignments(first)

        clock.moment += datetime.timedelta(minutes=5)
        second = make_message(user_id="712")
        await show_all_assignments(second)

    assert api.calls["todo"] == 1, "the second /show should come from the cache"
    assert "1d 0h 58m" in first.answer.call_args[0][0]
    assert "1d 0h 53m" in second.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_a_deadline_that_passes_while_cached_drops_out_of_the_list():
    await _register("713")
    todo_list = [
        make_subject(
            "Algorithms", [("homeworks", "Due in a minute", 1 / 60), ("lectures", "Week 3", 10)]
        )
    ]
    api = FakeApi(todo_list=todo_list)

    with mock.patch.object(todos, "KwangwoonUniversityApi", lambda: api):
        await show_all_assignments(make_message(user_id="713"))
        later = make_message(user_id="713")
        with mock.patch.object(
            todos, "timezone", FakeClock(timezone.now() + datetime.timedelta(minutes=2))
        ):
            await show_all_assignments(later)

    assert api.calls["todo"] == 1
    assert "Due in a minute" not in later.answer.call_args[0][0]
    assert "Week 3" in later.answer.call_args[0][0]
