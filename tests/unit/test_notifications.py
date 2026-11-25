import asyncio
import datetime
import logging
import re
from contextlib import asynccontextmanager

import pytest

from app.database.database import (
    RecordResult,
    save_user,
    get_klas_session,
    get_notification_state,
    get_sent_notifications,
    update_klas_session,
    update_notification_state,
    update_user_settings,
)
from app.keyboards import create_klas_relogin_keyboard
from app.services import notifications
from app.services.klas_session import KlasAccess, SessionStatus
from app.strings import Language, Strings
from app.utils.assignment_render import INDENT


class FakeUser:
    def __init__(self, user_id):
        self.user_id = user_id
        self.username = f"student{user_id}"
        self.encrypted_password = b"encrypted"


class FakeApi:
    """Stands in for the KwangwoonUniversityApi that open_session yields."""

    def __init__(self, todo_list=None):
        self._todo_list = todo_list

    async def get_todo_list(self):
        return self._todo_list


class FakeBot:
    """Collects what would have been sent to Telegram."""

    def __init__(self, fail=False, fail_on_call=None):
        self.sent = []
        self.markups = []
        self.parse_modes = []
        self.fail = fail
        # 1-based number of the send_message call Telegram refuses, once.
        self.fail_on_call = fail_on_call
        self.calls = 0

    async def send_message(self, chat_id, text, reply_markup=None, parse_mode=None):
        self.calls += 1
        if self.fail or self.calls == self.fail_on_call:
            raise RuntimeError("Telegram said no")
        self.sent.append((chat_id, text))
        self.markups.append(reply_markup)
        self.parse_modes.append(parse_mode)


@pytest.fixture(autouse=True)
def fresh_cycle_memory():
    """What the notification loop keeps between cycles is module state."""
    notifications._holding.clear()
    notifications._unrecorded.clear()
    notifications._waiting_for_login.clear()
    notifications._session_since.clear()


@pytest.fixture(autouse=True)
async def registered_user():
    """The notification tables only take rows for a registered user."""
    await save_user("1", "student1", "enc", Language.EN)


def at_hour(hour):
    """What FakeClock(hour) reads - the base a fixture's deadlines are set from."""
    return datetime.datetime(2026, 9, 20, hour, 0)


def make_todo(
    title="Report 1", hours=48, subject="Algorithms", type_="homeworks", hour=12
):
    """`hours` from the clock the test runs at, as an absolute deadline."""
    return [
        {
            "name": subject,
            "todo": {
                type_: [
                    {
                        "title": title,
                        "expire_at": at_hour(hour) + datetime.timedelta(hours=hours),
                    }
                ]
            },
        }
    ]


NBSP = INDENT


def reminder_item(title, subject="Algorithms", type_="homeworks", hours=2.5, kind="t3"):
    """An item as _collect_messages builds it, due `hours` after the test clock."""
    return {
        "type": type_,
        "subject": subject,
        "title": title,
        "expire_at": at_hour(12) + datetime.timedelta(hours=hours),
        "entry": (f"{subject}_{type_}_{title}", kind),
    }


class FakeClock:
    """Freezes Korean local time, so a test's result does not depend on when
    the suite happens to run - quiet hours are on by default."""

    def __init__(self, hour):
        self.hour = hour

    def now(self):
        return at_hour(self.hour)


def _patch_user_deps(
    monkeypatch, api, bot=None, hour=12, status=SessionStatus.ACTIVE
):
    """`api` is what an ACTIVE session yields; other statuses yield none."""
    bot = bot or FakeBot()
    monkeypatch.setattr(notifications, "timezone", FakeClock(hour))

    @asynccontextmanager
    async def fake_open_session(user):
        yield KlasAccess(status, api if status is SessionStatus.ACTIVE else None)

    monkeypatch.setattr(notifications, "open_session", fake_open_session)
    monkeypatch.setattr(notifications, "bot", bot)
    monkeypatch.setattr(notifications, "SEND_DELAY_SECONDS", 0)

    async def fake_language(user_id):
        return None

    monkeypatch.setattr(notifications, "get_user_language", fake_language)
    return bot


async def test_cycle_checks_every_user(monkeypatch):
    checked = []

    async def record(user):
        checked.append(user.user_id)
        return True, 0

    monkeypatch.setattr(notifications, "_process_user", record)
    users = [FakeUser("1"), FakeUser("2"), FakeUser("3")]

    assert await notifications.run_notification_cycle(users) == (0, 0)
    assert sorted(checked) == ["1", "2", "3"]


async def test_cycle_runs_users_concurrently_but_bounded(monkeypatch):
    """Users used to be checked strictly one after another, so a cycle cost the
    sum of everyone's KLAS round trips."""
    in_flight = 0
    peak = 0

    async def slow_check(user):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return True, 0

    monkeypatch.setattr(notifications, "_process_user", slow_check)
    users = [FakeUser(str(i)) for i in range(12)]

    assert await notifications.run_notification_cycle(users) == (0, 0)
    assert peak > 1, "the cycle is still sequential"
    assert peak <= notifications.MAX_CONCURRENT_USERS, "KLAS must not be fanned out to"


async def test_cycle_counts_failed_users(monkeypatch):
    async def fail_the_second(user):
        return user.user_id != "2", 0

    monkeypatch.setattr(notifications, "_process_user", fail_the_second)
    users = [FakeUser("1"), FakeUser("2"), FakeUser("3")]

    assert await notifications.run_notification_cycle(users) == (1, 0)


async def test_cycle_sums_notifications_sent_across_users(monkeypatch):
    """Regression: a week of production logs never showed whether the bot's
    core feature - sending a notification - fired even once."""

    async def send_some(user):
        return True, int(user.user_id)

    monkeypatch.setattr(notifications, "_process_user", send_some)
    users = [FakeUser("1"), FakeUser("2"), FakeUser("3")]

    assert await notifications.run_notification_cycle(users) == (0, 1 + 2 + 3)


def test_summary_separates_users_waiting_for_login():
    """Regression: "all 31 users checked" was logged while 21 of them were
    waiting for a login and nothing was read for them."""
    users = [FakeUser(str(i)) for i in range(5)]
    notifications._waiting_for_login.update({"1", "2"})

    level, line = notifications.cycle_summary(users, failed=1, sent=3)

    assert level == logging.WARNING
    assert line == (
        "Notification cycle finished: 2 read, 2 waiting for login, "
        "1 failed, 3 notifications sent"
    )


def test_summary_is_info_when_nothing_failed():
    level, _ = notifications.cycle_summary([FakeUser("1")], failed=0, sent=0)

    assert level == logging.INFO


def test_summary_ignores_waiting_users_who_left():
    notifications._waiting_for_login.add("gone")

    _, line = notifications.cycle_summary([FakeUser("1")], failed=0, sent=0)

    assert "1 read, 0 waiting for login" in line


def test_summary_reports_the_oldest_session_of_current_users():
    now = at_hour(12)
    notifications._session_since.update(
        {
            "1": now - datetime.timedelta(hours=5),
            "2": now - datetime.timedelta(hours=32, minutes=24),
            "gone": now - datetime.timedelta(hours=100),
        }
    )

    _, line = notifications.cycle_summary(
        [FakeUser("1"), FakeUser("2")], failed=0, sent=0, now=now
    )

    assert line.endswith("; oldest KLAS session 32.4h")


def test_summary_leaves_out_session_age_when_none_is_known():
    _, line = notifications.cycle_summary([FakeUser("1")], failed=0, sent=0)

    assert "oldest KLAS session" not in line


async def test_waiting_for_login_is_logged_once_per_wait(monkeypatch, caplog):
    """Regression: 456 of 501 log lines in one day were this message, repeated
    every 30 minutes for the same users."""
    caplog.set_level(logging.INFO)
    _patch_user_deps(monkeypatch, None, status=SessionStatus.NEEDS_LOGIN)

    async def no_prompt(*args):
        return None

    monkeypatch.setattr(notifications, "_ask_for_login", no_prompt)

    for _ in range(3):
        assert await notifications._process_user(FakeUser("1")) == (True, 0)

    lines = [r.getMessage() for r in caplog.records if "has to act" in r.getMessage()]
    assert lines == ["User 1 has to act before KLAS can be read"]
    assert notifications._waiting_for_login == {"1"}


async def test_a_read_ends_the_wait_and_records_the_session_start(monkeypatch):
    started = at_hour(3)
    notifications._waiting_for_login.add("1")
    _patch_user_deps(monkeypatch, FakeApi(todo_list=[]))

    @asynccontextmanager
    async def active_session(user):
        yield KlasAccess(SessionStatus.ACTIVE, FakeApi(todo_list=[]), started)

    monkeypatch.setattr(notifications, "open_session", active_session)

    assert await notifications._process_user(FakeUser("1")) == (True, 0)
    assert notifications._waiting_for_login == set()
    assert notifications._session_since == {"1": started}


def _hold_lines(caplog) -> list:
    return [
        record.getMessage()
        for record in caplog.records
        if "held" in record.getMessage().lower()
    ]


async def test_a_hold_is_logged_once_however_long_the_night_is(monkeypatch, caplog):
    """A hold is checked again on every cycle until morning. Counted per cycle,
    one reminder read as fourteen, and no line named the user or the reminder."""
    caplog.set_level(logging.INFO)
    await _threshold_only_user()
    api = FakeApi(todo_list=make_todo(hours=20, hour=3))

    for hour in (3, 4, 5):
        _patch_user_deps(monkeypatch, api, hour=hour)
        await notifications._process_user(FakeUser("1"))

    assert _hold_lines(caplog) == [
        "Notification held for 1 (threshold=24h, quiet hours)"
    ]

    bot = _patch_user_deps(monkeypatch, api, hour=9)
    await notifications._process_user(FakeUser("1"))

    assert len(bot.sent) == 1
    assert "Notification sent to 1 (threshold=24h)" in caplog.text
    assert len(_hold_lines(caplog)) == 1, "a delivered reminder was reported dropped"


async def test_a_held_reminder_that_is_never_sent_says_so(monkeypatch, caplog):
    """Only the tightest threshold a deadline fits in is kept, so with a 07:00
    deadline the 6h and 3h reminders are held and then simply gone. The logs
    used to call both "held", as if they were waiting for the morning."""
    caplog.set_level(logging.INFO)
    await _threshold_only_user()
    api = FakeApi(todo_list=make_todo(hours=6, hour=1))

    for hour in (1, 4, 5):
        bot = _patch_user_deps(monkeypatch, api, hour=hour)
        await notifications._process_user(FakeUser("1"))

    assert _hold_lines(caplog) == [
        "Notification held for 1 (threshold=6h, quiet hours)",
        "Notification held for 1 (threshold=3h, quiet hours)",
        "Held notification for 1 (threshold=6h) dropped unsent: no longer due",
        "Held notification for 1 (threshold=3h) dropped unsent: no longer due",
    ]
    assert len(bot.sent) == 1, "the 2h reminder is exempt from quiet hours"
    assert _thresholds_recorded(await get_sent_notifications("1")) == {"t2"}


async def test_a_reminder_telegram_refused_is_not_reported_dropped(monkeypatch, caplog):
    """Held overnight, then the morning send fails: that is an error with its
    own line and a retry next cycle, not a reminder that stopped being due."""
    caplog.set_level(logging.INFO)
    await _threshold_only_user()
    api = FakeApi(todo_list=make_todo(hours=20, hour=3))
    _patch_user_deps(monkeypatch, api, hour=3)
    await notifications._process_user(FakeUser("1"))

    _patch_user_deps(monkeypatch, api, bot=FakeBot(fail=True), hour=9)
    await notifications._process_user(FakeUser("1"))

    assert "Error sending notification to 1" in caplog.text
    assert not any("dropped" in line for line in _hold_lines(caplog))


async def test_a_sent_reminder_the_database_refused_is_not_sent_again(monkeypatch, caplog):
    """The message is in the chat before the row is written. When the write
    failed nothing remembered the send, and the same reminder went out again
    every 30 minutes for as long as the database stayed locked."""
    await _threshold_only_user()
    api = FakeApi(todo_list=make_todo(hours=20))
    bot = _patch_user_deps(monkeypatch, api)
    real_record = notifications.record_sent_notifications

    async def database_is_locked(user_id, entries):
        return RecordResult.FAILED

    monkeypatch.setattr(notifications, "record_sent_notifications", database_is_locked)
    assert await notifications._process_user(FakeUser("1")) == (True, 1)
    assert await notifications._process_user(FakeUser("1")) == (True, 0)
    assert len(bot.sent) == 1, "the reminder was repeated"
    assert "Could not record 1 sent notifications for 1" in caplog.text

    monkeypatch.setattr(notifications, "record_sent_notifications", real_record)
    assert await notifications._process_user(FakeUser("1")) == (True, 0)
    assert _thresholds_recorded(await get_sent_notifications("1")) == {"t24"}, (
        "the row was never written once the database came back"
    )
    assert notifications._unrecorded == {}


async def test_what_was_sent_is_counted_even_when_the_check_then_breaks(monkeypatch):
    """The catch-all used to answer "nothing sent" for a cycle that had sent."""
    await _threshold_only_user()
    bot = _patch_user_deps(
        monkeypatch,
        FakeApi(todo_list=make_todo(hours=20)),
    )

    async def broken_prune(user_id, current_keys):
        raise RuntimeError("boom")

    monkeypatch.setattr(notifications, "prune_sent_notifications", broken_prune)

    assert await notifications._process_user(FakeUser("1")) == (False, 1)
    assert len(bot.sent) == 1


async def test_one_crashing_user_does_not_abort_the_cycle(monkeypatch):
    checked = []

    async def crash_the_second(user):
        if user.user_id == "2":
            raise RuntimeError("boom")
        checked.append(user.user_id)
        return True, 0

    monkeypatch.setattr(notifications, "_process_user", crash_the_second)
    users = [FakeUser("1"), FakeUser("2"), FakeUser("3")]

    assert await notifications.run_notification_cycle(users) == (1, 0)
    assert sorted(checked) == ["1", "3"], "the other users still get checked"


async def test_cycle_with_no_users_reports_no_failures(monkeypatch):
    assert await notifications.run_notification_cycle([]) == (0, 0)


async def test_process_user_reports_failure_when_klas_is_unavailable(monkeypatch):
    """KLAS being unreachable is a failed check, and nobody is messaged."""
    bot = _patch_user_deps(monkeypatch, None, status=SessionStatus.UNAVAILABLE)

    assert await notifications._process_user(FakeUser("1")) == (False, 0)
    assert bot.sent == []


async def test_process_user_reports_failure_when_klas_cannot_be_read(monkeypatch):
    _patch_user_deps(
        monkeypatch, FakeApi(todo_list=None)
    )

    assert await notifications._process_user(FakeUser("1")) == (False, 0)


async def test_process_user_succeeds_with_no_subjects(monkeypatch):
    """An empty list is a real answer: the student has no subjects this term."""
    _patch_user_deps(
        monkeypatch, FakeApi(todo_list=[])
    )

    assert await notifications._process_user(FakeUser("1")) == (True, 0)


async def test_first_cycle_records_existing_work_without_announcing_it(monkeypatch):
    """Switching the feature on must not announce a whole semester as new."""
    bot = _patch_user_deps(
        monkeypatch,
        FakeApi(todo_list=make_todo(hours=100)),
    )

    assert await notifications._process_user(FakeUser("1")) == (True, 0)

    assert bot.sent == []
    state = await get_notification_state("1")
    assert state.seeded is True
    assert await get_sent_notifications("1") == {
        "Algorithms_homeworks_Report 1": {"new"}
    }


async def test_new_assignment_is_announced_once(monkeypatch):
    api = FakeApi(todo_list=make_todo(hours=100))
    bot = _patch_user_deps(monkeypatch, api)
    await notifications._process_user(FakeUser("1"))  # seeding cycle

    api._todo_list = make_todo(hours=100) + make_todo(title="Essay", hours=72)
    await notifications._process_user(FakeUser("1"))

    assert len(bot.sent) == 1
    assert "Essay" in bot.sent[0][1]
    assert "Report 1" not in bot.sent[0][1], "already known work is not 'new'"

    await notifications._process_user(FakeUser("1"))
    assert len(bot.sent) == 1, "the announcement repeated on the next cycle"


async def test_deadline_threshold_is_sent_once(monkeypatch):
    await update_notification_state("1", seeded=True)
    bot = _patch_user_deps(
        monkeypatch,
        FakeApi(todo_list=make_todo(hours=2.5)),
    )

    await notifications._process_user(FakeUser("1"))

    threshold_messages = [text for _, text in bot.sent if "3h" in text]
    assert len(threshold_messages) == 1, "the 3h threshold fires for 2.5h left"
    assert await get_sent_notifications("1") == {
        "Algorithms_homeworks_Report 1": {"new", "t3"}
    }

    await notifications._process_user(FakeUser("1"))
    assert len([text for _, text in bot.sent if "3h" in text]) == 1


async def test_the_last_hour_is_not_skipped(monkeypatch):
    """Under an hour left used to match no threshold at all, so the most
    urgent warning was the one that never arrived."""
    await update_notification_state("1", seeded=True)
    bot = _patch_user_deps(
        monkeypatch,
        FakeApi(todo_list=make_todo(hours=0.5)),
    )

    await notifications._process_user(FakeUser("1"))

    assert any("1h" in text for _, text in bot.sent)
    assert "t1" in (await get_sent_notifications("1"))["Algorithms_homeworks_Report 1"]


async def test_a_notification_telegram_refused_is_retried(monkeypatch):
    """Recording a send that never happened would lose the notification."""
    await update_notification_state("1", seeded=True)
    failing = FakeBot(fail=True)
    api = FakeApi(todo_list=make_todo(hours=2.5))
    _patch_user_deps(monkeypatch, api, bot=failing)

    await notifications._process_user(FakeUser("1"))
    assert await get_sent_notifications("1") == {}

    working = _patch_user_deps(monkeypatch, api)
    await notifications._process_user(FakeUser("1"))
    assert any("3h" in text for _, text in working.sent)


async def test_expired_assignment_is_left_alone(monkeypatch):
    await update_notification_state("1", seeded=True)
    bot = _patch_user_deps(
        monkeypatch,
        FakeApi(todo_list=make_todo(hours=-3)),
    )

    await notifications._process_user(FakeUser("1"))

    assert bot.sent == []


async def test_finished_assignments_are_forgotten(monkeypatch):
    api = FakeApi(todo_list=make_todo(hours=100))
    _patch_user_deps(monkeypatch, api)
    await notifications._process_user(FakeUser("1"))
    assert await get_sent_notifications("1") != {}

    # Submitted work drops off the KLAS todo list
    api._todo_list = [{"name": "Algorithms", "todo": {"homeworks": []}}]
    await notifications._process_user(FakeUser("1"))

    assert await get_sent_notifications("1") == {}


def _refused(todo_list):
    """The same read with KLAS having refused a category of the first subject."""
    return [{**todo_list[0], "incomplete": True}] + todo_list[1:]


async def test_a_partial_read_does_not_forget_what_was_sent(monkeypatch):
    """KLAS refusing one request used to look like the work being handed in:
    its rows were pruned, and the next full read announced it as new and sent
    its reminders again."""
    api = FakeApi(todo_list=make_todo(hours=100))
    bot = _patch_user_deps(monkeypatch, api)
    await notifications._process_user(FakeUser("1"))  # seeding cycle
    history = await get_sent_notifications("1")

    api._todo_list = _refused([{"name": "Algorithms", "todo": {"homeworks": []}}])
    assert await notifications._process_user(FakeUser("1")) == (True, 0)
    assert await get_sent_notifications("1") == history

    api._todo_list = make_todo(hours=100)
    await notifications._process_user(FakeUser("1"))
    assert bot.sent == [], "known work came back as new after a partial read"


async def test_a_partial_read_still_sends_what_it_did_read(monkeypatch):
    await update_notification_state("1", seeded=True)
    bot = _patch_user_deps(
        monkeypatch,
        FakeApi(
            todo_list=_refused(make_todo(hours=2.5)),
        ),
    )

    assert await notifications._process_user(FakeUser("1")) == (True, 2)
    assert len(bot.sent) == 2, "the new assignment and its 3h reminder"


async def test_the_first_cycle_does_not_seed_from_a_partial_read(monkeypatch):
    await _registered()
    """Seeding records what exists so that it is never announced. Seeded from
    a read with a category missing, that category arrives as new one cycle
    later - the dump of a semester seeding exists to prevent."""
    api = FakeApi(
        todo_list=_refused(make_todo(hours=100))
    )
    bot = _patch_user_deps(monkeypatch, api)

    assert await notifications._process_user(FakeUser("1")) == (False, 0)
    assert not getattr(await get_notification_state("1"), "seeded", False)

    api._todo_list = make_todo(hours=100)
    assert await notifications._process_user(FakeUser("1")) == (True, 0)
    assert bot.sent == []
    assert (await get_notification_state("1")).seeded is True


async def _registered(user_id="1"):
    """Session rows, the prompt flag among them, exist only for registered users."""
    await save_user(user_id, f"student{user_id}", b"encrypted", Language.EN)


def _needs_login(monkeypatch, hour=12, bot=None):
    return _patch_user_deps(
        monkeypatch, None, bot=bot, hour=hour, status=SessionStatus.NEEDS_LOGIN
    )


async def _prompt_sent(user_id="1") -> bool:
    row = await get_klas_session(user_id)
    return bool(row and row.login_prompt_sent)


async def test_a_user_who_must_log_in_is_asked_once_with_the_button(monkeypatch):
    await _registered()
    bot = _needs_login(monkeypatch)

    assert await notifications._process_user(FakeUser("1")) == (True, 0)

    assert [text for _, text in bot.sent] == [
        Strings.get("klas_login_required", Language.EN)
    ]
    assert bot.markups == [create_klas_relogin_keyboard(Language.EN)]
    assert await _prompt_sent()


async def test_the_login_prompt_is_not_repeated_every_cycle(monkeypatch):
    await _registered()
    bot = _needs_login(monkeypatch)
    await notifications._process_user(FakeUser("1"))
    await notifications._process_user(FakeUser("1"))

    assert len(bot.sent) == 1


async def test_no_prompt_when_one_was_already_sent(monkeypatch):
    await _registered()
    await update_klas_session("1", needs_login=True, login_prompt_sent=True)
    bot = _needs_login(monkeypatch)

    assert await notifications._process_user(FakeUser("1")) == (True, 0)
    assert bot.sent == []


async def test_the_login_prompt_is_held_during_quiet_hours(monkeypatch):
    await _registered()
    bot = _needs_login(monkeypatch, hour=3)

    assert await notifications._process_user(FakeUser("1")) == (True, 0)
    assert bot.sent == []
    assert not await _prompt_sent(), "held prompt was recorded as sent"

    daytime = _needs_login(monkeypatch, hour=9)
    await notifications._process_user(FakeUser("1"))
    assert len(daytime.sent) == 1, "the held prompt never went out in the morning"
    assert await _prompt_sent()


async def test_the_login_prompt_is_not_recorded_when_telegram_refused(monkeypatch):
    await _registered()
    _needs_login(monkeypatch, bot=FakeBot(fail=True))

    assert await notifications._process_user(FakeUser("1")) == (True, 0)
    assert not await _prompt_sent()

    working = _needs_login(monkeypatch)
    await notifications._process_user(FakeUser("1"))
    assert len(working.sent) == 1, "the refused prompt was not retried"


async def test_send_notification_returns_zero_and_logs_on_failure(monkeypatch, caplog):
    async def boom(**kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(notifications.bot, "send_message", boom)

    result = await notifications.send_notification(
        [reminder_item("Essay")], "42", 1, now=at_hour(12)
    )

    assert result == 0
    assert "Error sending notification to 42" in caplog.text


async def test_send_notification_logs_the_send_without_the_assignment_text(
    monkeypatch, caplog
):
    """The log line names the user and the threshold, never what was due."""
    caplog.set_level(logging.INFO)
    bot = FakeBot()
    monkeypatch.setattr(notifications, "bot", bot)

    sent = await notifications.send_notification(
        [reminder_item("Secret essay title")], "42", 3, now=at_hour(12)
    )

    assert sent == 1
    assert "Secret essay title" in bot.sent[0][1]
    assert "Notification sent to 42 (threshold=3h)" in caplog.text
    assert "Secret essay title" not in caplog.text


async def _threshold_only_user(user_id="1"):
    """Seeded and with new-assignment alerts off, so only thresholds speak."""
    await update_notification_state(user_id, seeded=True)
    await update_user_settings(user_id, new_assignment_alerts=False)


def _thresholds_recorded(sent: dict) -> set:
    kinds = set().union(*sent.values()) if sent else set()
    return {kind for kind in kinds if kind.startswith("t")}


async def test_quiet_hours_start_after_the_midnight_reminder(monkeypatch):
    """KLAS deadlines are usually 23:59, so the 24h warning falls due just
    after midnight. Held until 08:00 it used to arrive with sixteen hours left."""
    await _threshold_only_user()
    bot = _patch_user_deps(
        monkeypatch,
        FakeApi(todo_list=make_todo(hours=23.9, hour=0)),
        hour=0,
    )

    await notifications._process_user(FakeUser("1"))

    assert len(bot.sent) == 1, "the 24h reminder waited for the morning"
    assert _thresholds_recorded(await get_sent_notifications("1")) == {"t24"}


def test_quiet_hours_cover_the_night_and_nothing_else():
    class Defaults:
        quiet_hours = True
        quiet_start = 1
        quiet_end = 8

    quiet = {
        hour
        for hour in range(24)
        if notifications.in_quiet_hours(Defaults(), now=at_hour(hour))
    }

    assert quiet == set(range(1, 8))


async def test_quiet_hours_hold_the_day_ahead_reminder(monkeypatch):
    """A reminder that falls due in the small hours waits for the morning."""
    await _threshold_only_user()
    api = FakeApi(todo_list=make_todo(hours=20, hour=2))
    bot = _patch_user_deps(monkeypatch, api, hour=2)

    await notifications._process_user(FakeUser("1"))
    assert bot.sent == [], "the 24h reminder woke the student at 02:00"
    assert _thresholds_recorded(await get_sent_notifications("1")) == set(), (
        "held, not swallowed"
    )

    daytime = _patch_user_deps(monkeypatch, api, hour=9)
    await notifications._process_user(FakeUser("1"))
    assert len(daytime.sent) == 1, "the held reminder never arrived in the morning"
    assert _thresholds_recorded(await get_sent_notifications("1")) == {"t24"}


async def test_quiet_hours_let_the_last_hours_through(monkeypatch):
    """A deadline two hours away is worth waking someone for."""
    await _threshold_only_user()
    bot = _patch_user_deps(
        monkeypatch,
        FakeApi(todo_list=make_todo(hours=1.5, hour=2)),
        hour=2,
    )

    await notifications._process_user(FakeUser("1"))

    assert len(bot.sent) == 1


async def test_quiet_hours_can_be_switched_off(monkeypatch):
    await _threshold_only_user()
    await update_user_settings("1", quiet_hours=False)
    bot = _patch_user_deps(
        monkeypatch,
        FakeApi(todo_list=make_todo(hours=20, hour=2)),
        hour=2,
    )

    await notifications._process_user(FakeUser("1"))

    assert len(bot.sent) == 1


async def test_urgent_only_drops_the_wider_thresholds(monkeypatch):
    await _threshold_only_user()
    await update_user_settings("1", urgent_thresholds_only=True)
    api = FakeApi(todo_list=make_todo(hours=20))
    bot = _patch_user_deps(monkeypatch, api)

    await notifications._process_user(FakeUser("1"))
    assert bot.sent == [], "20h left is not urgent"

    api._todo_list = make_todo(hours=5)
    await notifications._process_user(FakeUser("1"))
    assert len(bot.sent) == 1, "the 6h threshold still fires"


async def test_deadline_alerts_can_be_switched_off(monkeypatch):
    await _threshold_only_user()
    await update_user_settings("1", deadline_alerts=False)
    bot = _patch_user_deps(
        monkeypatch,
        FakeApi(todo_list=make_todo(hours=0.5)),
    )

    await notifications._process_user(FakeUser("1"))

    assert bot.sent == []


async def test_new_assignment_alerts_off_still_records_the_assignment(monkeypatch):
    """Otherwise switching the setting back on announces the whole backlog."""
    await update_notification_state("1", seeded=True)
    await update_user_settings("1", new_assignment_alerts=False)
    api = FakeApi(todo_list=make_todo(hours=100))
    bot = _patch_user_deps(monkeypatch, api)

    await notifications._process_user(FakeUser("1"))
    assert bot.sent == []
    assert await get_sent_notifications("1") == {
        "Algorithms_homeworks_Report 1": {"new"}
    }

    await update_user_settings("1", new_assignment_alerts=True)
    await notifications._process_user(FakeUser("1"))
    assert bot.sent == [], "old work was announced as new after switching on"


async def test_a_discussion_is_announced_like_any_other_assignment(monkeypatch):
    """TYPE_EMOJIS is what decides which categories get notified at all, so a
    type missing from it would be collected by /show and never reminded about."""
    api = FakeApi(todo_list=make_todo(hours=100))
    bot = _patch_user_deps(monkeypatch, api)
    await notifications._process_user(FakeUser("1"))  # seeding cycle

    api._todo_list = make_todo(
        title="논문을 읽고 토론", hours=30, subject="English", type_="discussions"
    )
    await notifications._process_user(FakeUser("1"))

    assert len(bot.sent) == 1
    text = bot.sent[0][1]
    assert f"\nEnglish\n{NBSP}💬 논문을 읽고 토론" in text


async def test_a_rejected_password_asks_for_register_once_without_a_button(monkeypatch):
    await _registered()
    """The button would only resend the password KLAS refused."""
    bot = _patch_user_deps(monkeypatch, None, status=SessionStatus.PASSWORD_REJECTED)

    assert await notifications._process_user(FakeUser("1")) == (True, 0)
    await notifications._process_user(FakeUser("1"))

    assert [text for _, text in bot.sent] == [
        Strings.get("credentials_expired", Language.EN)
    ]
    assert bot.markups == [None]
    assert await _prompt_sent()


# --- message format, HTML and chunked delivery --------------------------------


def utf16_len(text):
    return len(text.encode("utf-16-le")) // 2


def ids_in(text):
    """The "T<subject>-<n> " markers many_todo puts at the start of each title."""
    return set(re.findall(r"T\d\d-\d\d ", text))


def many_todo(subjects, per_subject, hours_for):
    """Long distinct titles, so a batch is certain to need several messages."""
    return [
        {
            "name": f"Subject {s:02d}",
            "todo": {
                "homeworks": [
                    {
                        "title": f"T{s:02d}-{n:02d} " + "x" * 100,
                        "expire_at": at_hour(12)
                        + datetime.timedelta(hours=hours_for(s, n)),
                    }
                    for n in range(per_subject)
                ]
            },
        }
        for s in range(subjects)
    ]


async def _recorded_ids(kind):
    sent = await get_sent_notifications("1")
    return {key.split("_homeworks_")[1][:7] for key, kinds in sent.items() if kind in kinds}


@pytest.mark.parametrize(
    "lang, header, in_time",
    [
        (Language.EN, "<b>⏰ Less than 3h left</b>", "in 3h 20m"),
        (Language.KO, "<b>⏰ 마감까지 3시간 미만</b>", "3시간 20분 후"),
        (Language.RU, "<b>⏰ Осталось менее 3 ч</b>", "через 3ч 20м"),
    ],
)
async def test_reminder_is_localized_compact_and_without_a_footer(
    monkeypatch, lang, header, in_time
):
    bot = FakeBot()
    monkeypatch.setattr(notifications, "bot", bot)
    items = [
        reminder_item("Essay", "Algorithms", hours=3 + 1 / 3),
        reminder_item("Quiz 2", "Algorithms", "quizzes", hours=1),
        reminder_item("Lab", "Networks", hours=2),
    ]

    assert await notifications.send_notification(items, "42", 3, lang, at_hour(12)) == 1

    text = bot.sent[0][1]
    lines = text.split("\n")
    assert lines[0] == header
    # Subject is plain, ordered by earliest deadline, items indented under it.
    assert lines[1:3] == ["Algorithms", f"{NBSP}🧠 Quiz 2 · " + lines[2].split(" · ")[1]]
    assert "Networks" in lines and "<b>Algorithms</b>" not in text
    assert in_time in text
    assert "🚨" not in text, "the footer is gone"
    assert lines[-1].startswith(NBSP), "the message ends with an item, not a footer"
    if lang is Language.RU:
        assert " h" not in text and "3h" not in text


async def test_new_assignments_use_day_grouping_and_their_own_header(monkeypatch):
    await update_notification_state("1", seeded=True)
    api = FakeApi(todo_list=make_todo(hours=100) + make_todo("Quiz", hours=30, subject="Networks"))
    bot = _patch_user_deps(monkeypatch, api)

    await notifications._process_user(FakeUser("1"))

    text = bot.sent[0][1]
    assert text.startswith("<b>🆕 New in KLAS</b>\n\n<b>Tomorrow</b>\nNetworks\n")
    assert "<b>Thu, 24 Sep · in 4 days</b>\nAlgorithms\n" in text
    assert "Tasks" not in text, "the /show header leaked into the announcement"
    assert "Time remaining" not in text


async def test_new_assignments_far_out_are_shown_open_not_folded(monkeypatch):
    # Most work is posted more than a week ahead; folding it into /show's
    # "Later" block would hide the very thing the alert announces.
    await update_notification_state("1", seeded=True)
    api = FakeApi(todo_list=make_todo(title="Project", hours=24 * 12))
    bot = _patch_user_deps(monkeypatch, api)

    await notifications._process_user(FakeUser("1"))

    text = bot.sent[0][1]
    assert "<blockquote" not in text
    assert "Later" not in text
    assert "\nAlgorithms\n" in text and "Project" in text


async def test_titles_are_html_escaped_in_both_kinds_of_message(monkeypatch):
    await update_notification_state("1", seeded=True)
    todo = make_todo(title="a < b & <i>c</i>", hours=2.5, subject="R&D <x>")
    bot = _patch_user_deps(monkeypatch, FakeApi(todo_list=todo))

    await notifications._process_user(FakeUser("1"))

    assert len(bot.sent) == 2, "the announcement and the 3h reminder"
    for _, text in bot.sent:
        assert "a &lt; b &amp; &lt;i&gt;c&lt;/i&gt;" in text
        assert "\nR&amp;D &lt;x&gt;\n" in text
        assert "<i>" not in text and "<x>" not in text


async def test_notifications_are_sent_as_html_but_login_prompts_stay_plain(monkeypatch):
    await update_notification_state("1", seeded=True)
    bot = _patch_user_deps(monkeypatch, FakeApi(todo_list=make_todo(hours=2.5)))
    await notifications._process_user(FakeUser("1"))
    assert bot.parse_modes == ["HTML", "HTML"]

    await _registered()
    prompt = _needs_login(monkeypatch)
    await notifications._process_user(FakeUser("1"))
    assert prompt.parse_modes == [None], "the login prompt contains a literal '<'"


async def test_a_reminder_is_split_and_a_refused_chunk_keeps_only_the_rest_unrecorded(
    monkeypatch,
):
    await _threshold_only_user()
    api = FakeApi(
        todo_list=many_todo(12, 8, lambda s, n: 2.01 + (s * 8 + n) / 200)
    )
    everything = {f"T{s:02d}-{n:02d} " for s in range(12) for n in range(8)}
    flaky = FakeBot(fail_on_call=2)
    _patch_user_deps(monkeypatch, api, bot=flaky)

    assert await notifications._process_user(FakeUser("1")) == (True, 1)

    assert flaky.calls == 2, "sending went on after Telegram refused a chunk"
    first = ids_in(flaky.sent[0][1])
    assert first and first < everything
    assert await _recorded_ids("t3") == first, "recorded something that was not delivered"

    working = _patch_user_deps(monkeypatch, api)
    _, sent_count = await notifications._process_user(FakeUser("1"))

    assert sent_count == len(working.sent) >= 2
    retried = set().union(*(ids_in(text) for _, text in working.sent))
    assert retried == everything - first, "a delivered item was sent again"
    assert await _recorded_ids("t3") == everything
    for _, text in flaky.sent + working.sent:
        assert utf16_len(text) <= 4096
        assert text.startswith("<b>⏰ Less than 3h left</b>\n")

    assert await notifications._process_user(FakeUser("1")) == (True, 0)
    assert len(working.sent) == sent_count


async def test_new_assignments_are_split_by_day_and_recorded_per_chunk(monkeypatch):
    await update_notification_state("1", seeded=True)
    # Two subjects a day for six days; the first message holds the first days.
    api = FakeApi(todo_list=many_todo(12, 8, lambda s, n: 30 + 24 * (s // 2) + n / 10))
    everything = {f"T{s:02d}-{n:02d} " for s in range(12) for n in range(8)}
    flaky = FakeBot(fail_on_call=2)
    _patch_user_deps(monkeypatch, api, bot=flaky)

    assert await notifications._process_user(FakeUser("1")) == (True, 1)

    assert flaky.calls == 2
    first = ids_in(flaky.sent[0][1])
    assert first and first < everything
    assert await _recorded_ids("new") == first
    assert flaky.sent[0][1].startswith("<b>🆕 New in KLAS</b>\n\n")

    working = _patch_user_deps(monkeypatch, api)
    await notifications._process_user(FakeUser("1"))

    assert len(flaky.sent) + len(working.sent) >= 3
    retried = set().union(*(ids_in(text) for _, text in working.sent))
    assert retried == everything - first
    assert await _recorded_ids("new") == everything
    for _, text in flaky.sent + working.sent:
        assert utf16_len(text) <= 4096
    # A day is never cut in half: each header sits in exactly one message.
    texts = [text for _, text in flaky.sent + working.sent]
    for day in ("Tomorrow", "Tue, 22 Sep", "Wed, 23 Sep"):
        assert sum(f"<b>{day}" in text for text in texts) == 1


async def test_a_duplicate_pair_does_not_block_later_recording():
    """Two same-titled assignments owe the same pair. The rejected batch used
    to stay in _unrecorded and fail every later write with it."""
    await notifications._record_sent(
        "1", [("A_homeworks_Report", "new"), ("A_homeworks_Report", "new")]
    )
    await notifications._record_sent("1", [("B_quizzes_Q", "new")])

    assert notifications._unrecorded == {}
    assert await get_sent_notifications("1") == {
        "A_homeworks_Report": {"new"},
        "B_quizzes_Q": {"new"},
    }


async def test_a_poisoned_unrecorded_batch_recovers():
    from app.database.database import record_sent_notifications

    await record_sent_notifications("1", [("A_homeworks_Report", "new")])
    notifications._unrecorded["1"] = [
        ("A_homeworks_Report", "new"),
        ("A_homeworks_Report", "new"),
    ]

    await notifications._record_sent("1", [("B_quizzes_Q", "t3")])

    assert notifications._unrecorded == {}
    assert await get_sent_notifications("1") == {
        "A_homeworks_Report": {"new"},
        "B_quizzes_Q": {"t3"},
    }


async def test_a_failed_state_read_does_not_reseed_the_user(monkeypatch):
    """None from the database used to read as "never seeded", which recorded
    the real new assignments as announced without sending them."""
    await update_notification_state("1", seeded=True)
    api = FakeApi(todo_list=make_todo(hours=100))
    bot = _patch_user_deps(monkeypatch, api)

    async def unreadable(user_id):
        return None

    monkeypatch.setattr(notifications, "get_notification_state", unreadable)

    assert await notifications._process_user(FakeUser("1")) == (False, 0)
    assert bot.sent == []
    assert await get_sent_notifications("1") == {}


async def test_failed_settings_read_does_not_fall_back_to_defaults(monkeypatch):
    """A user who switched alerts off would get them again."""
    await update_notification_state("1", seeded=True)
    await update_user_settings("1", deadline_alerts=False, new_assignment_alerts=False)
    bot = _patch_user_deps(monkeypatch, FakeApi(todo_list=make_todo(hours=20)))

    async def unreadable(user_id):
        return None

    monkeypatch.setattr(notifications, "get_user_settings", unreadable)

    assert await notifications._process_user(FakeUser("1")) == (False, 0)
    assert bot.sent == []


async def test_forget_user_clears_the_in_memory_state():
    notifications._unrecorded["1"] = [("A_homeworks_Report", "new")]
    notifications._holding["1"] = {"threshold=3h"}
    notifications._unrecorded["2"] = [("B_quizzes_Q", "new")]

    notifications.forget_user("1")

    assert "1" not in notifications._unrecorded
    assert "1" not in notifications._holding
    assert "2" in notifications._unrecorded


async def test_a_user_who_switched_accounts_while_queued_is_skipped(monkeypatch):
    """The cycle holds the user object loaded before the switch; logging in
    with it would read the old account and seed the new one from it."""
    from app.database.database import save_user as save

    api = FakeApi(todo_list=make_todo(hours=100))
    bot = _patch_user_deps(monkeypatch, api)
    await save("1", "student-other", "enc", Language.EN)  # re-registered

    assert await notifications._process_user(FakeUser("1")) == (True, 0)

    assert bot.sent == []
    assert not (await get_notification_state("1")).seeded


async def test_a_user_who_switched_accounts_during_the_fetch_is_not_seeded(monkeypatch):
    from app.database.database import save_user as save

    class SwitchingApi(FakeApi):
        async def get_todo_list(self):
            await save("1", "student-other", "enc", Language.EN)
            return self._todo_list

    bot = _patch_user_deps(monkeypatch, SwitchingApi(todo_list=make_todo(hours=100)))

    assert await notifications._process_user(FakeUser("1")) == (True, 0)

    assert not (await get_notification_state("1")).seeded
    assert await get_sent_notifications("1") == {}
    assert bot.sent == []


async def test_a_user_deleted_before_their_turn_is_not_a_failure(monkeypatch, caplog):
    from app.database.database import delete_user

    _patch_user_deps(monkeypatch, FakeApi(todo_list=make_todo(hours=100)))
    await delete_user("1")

    with caplog.at_level(logging.INFO):
        assert await notifications._process_user(FakeUser("1")) == (True, 0)

    assert "Could not read notification settings" not in caplog.text


async def test_a_user_deleted_between_the_check_and_the_reads_is_not_a_failure(
    monkeypatch, caplog
):
    from app.database.database import delete_user

    _patch_user_deps(monkeypatch, FakeApi(todo_list=make_todo(hours=100)))
    real_current = notifications._is_current

    async def current_then_deleted(user):
        result = await real_current(user)
        await delete_user(user.user_id)
        return result

    monkeypatch.setattr(notifications, "_is_current", current_then_deleted)

    with caplog.at_level(logging.INFO):
        assert await notifications._process_user(FakeUser("1")) == (True, 0)

    assert "Could not read notification settings" not in caplog.text


async def test_a_deleted_user_is_not_requeued_when_the_write_fails(monkeypatch):
    async def database_is_locked(user_id, entries):
        return RecordResult.FAILED

    async def user_gone(user_id, entries):
        return RecordResult.USER_GONE

    monkeypatch.setattr(notifications, "record_sent_notifications", database_is_locked)
    await notifications._record_sent("1", [("A_homeworks_Report", "new")])
    assert "1" in notifications._unrecorded

    notifications.forget_user("1")
    monkeypatch.setattr(notifications, "record_sent_notifications", user_gone)
    await notifications._record_sent("1", [("A_homeworks_Report", "new")])

    assert notifications._unrecorded == {}
