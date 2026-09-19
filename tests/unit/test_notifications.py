import asyncio
import datetime
import logging

from app.database.database import (
    get_notification_state,
    get_sent_notifications,
    update_notification_state,
)
from app.services import notifications


class FakeUser:
    def __init__(self, user_id):
        self.user_id = user_id
        self.username = f"student{user_id}"
        self.encrypted_password = b"encrypted"


class FakeApi:
    """Stands in for KwangwoonUniversityApi's async context manager."""

    def __init__(self, login_result=None, todo_list=None):
        self._login_result = login_result
        self._todo_list = todo_list

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return False

    async def login(self, username, password):
        return self._login_result

    async def get_todo_list(self):
        return self._todo_list


class FakeBot:
    """Collects what would have been sent to Telegram."""

    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    async def send_message(self, chat_id, text):
        if self.fail:
            raise RuntimeError("Telegram said no")
        self.sent.append((chat_id, text))


def make_todo(title="Report 1", hours=48, subject="Algorithms", type_="homeworks"):
    return [
        {
            "name": subject,
            "todo": {type_: [{"title": title, "left_time": datetime.timedelta(hours=hours)}]},
        }
    ]


def _patch_user_deps(monkeypatch, api, bot=None):
    bot = bot or FakeBot()
    monkeypatch.setattr(notifications, "KwangwoonUniversityApi", lambda: api)
    monkeypatch.setattr(notifications, "decrypt_password", lambda p: "password")
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

    failed, sent = await notifications.run_notification_cycle(users)
    assert (failed, sent) == (0, 0)
    assert peak > 1, "the cycle is still sequential"
    assert peak <= notifications.MAX_CONCURRENT_USERS, "KLAS must not be fanned out to"


async def test_cycle_counts_failed_users(monkeypatch):
    async def fail_the_second(user):
        return user.user_id != "2", 0

    monkeypatch.setattr(notifications, "_process_user", fail_the_second)
    users = [FakeUser("1"), FakeUser("2"), FakeUser("3")]

    failed, sent = await notifications.run_notification_cycle(users)
    assert (failed, sent) == (1, 0)


async def test_cycle_sums_notifications_sent_across_users(monkeypatch):
    """Regression: a week of production logs never showed whether the bot's
    core feature - sending a notification - fired even once."""

    async def send_some(user):
        return True, int(user.user_id)

    monkeypatch.setattr(notifications, "_process_user", send_some)
    users = [FakeUser("1"), FakeUser("2"), FakeUser("3")]

    failed, sent = await notifications.run_notification_cycle(users)
    assert (failed, sent) == (0, 1 + 2 + 3)


async def test_one_crashing_user_does_not_abort_the_cycle(monkeypatch):
    checked = []

    async def crash_the_second(user):
        if user.user_id == "2":
            raise RuntimeError("boom")
        checked.append(user.user_id)
        return True, 0

    monkeypatch.setattr(notifications, "_process_user", crash_the_second)
    users = [FakeUser("1"), FakeUser("2"), FakeUser("3")]

    failed, sent = await notifications.run_notification_cycle(users)
    assert (failed, sent) == (1, 0)
    assert sorted(checked) == ["1", "3"], "the other users still get checked"


async def test_cycle_with_no_users_reports_no_failures(monkeypatch):
    assert await notifications.run_notification_cycle([]) == (0, 0)


async def test_process_user_reports_failure_when_login_fails(monkeypatch):
    """A failed login is the user's cycle failing, not 'no assignments'."""
    _patch_user_deps(monkeypatch, FakeApi(login_result=None))

    assert await notifications._process_user(FakeUser("1")) == (False, 0)


async def test_process_user_reports_failure_when_klas_cannot_be_read(monkeypatch):
    _patch_user_deps(
        monkeypatch, FakeApi(login_result={"JSESSIONID": "x"}, todo_list=None)
    )

    assert await notifications._process_user(FakeUser("1")) == (False, 0)


async def test_process_user_succeeds_with_no_subjects(monkeypatch):
    """An empty list is a real answer: the student has no subjects this term."""
    _patch_user_deps(
        monkeypatch, FakeApi(login_result={"JSESSIONID": "x"}, todo_list=[])
    )

    assert await notifications._process_user(FakeUser("1")) == (True, 0)


async def test_first_cycle_records_existing_work_without_announcing_it(monkeypatch):
    """Switching the feature on must not announce a whole semester as new."""
    bot = _patch_user_deps(
        monkeypatch,
        FakeApi(login_result={"JSESSIONID": "x"}, todo_list=make_todo(hours=100)),
    )

    assert await notifications._process_user(FakeUser("1")) == (True, 0)

    assert bot.sent == []
    state = await get_notification_state("1")
    assert state.seeded is True
    assert await get_sent_notifications("1") == {
        "Algorithms_homeworks_Report 1": {"new"}
    }


async def test_new_assignment_is_announced_once(monkeypatch):
    api = FakeApi(login_result={"JSESSIONID": "x"}, todo_list=make_todo(hours=100))
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
        FakeApi(login_result={"JSESSIONID": "x"}, todo_list=make_todo(hours=2.5)),
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
        FakeApi(login_result={"JSESSIONID": "x"}, todo_list=make_todo(hours=0.5)),
    )

    await notifications._process_user(FakeUser("1"))

    assert any("1h" in text for _, text in bot.sent)
    assert "t1" in (await get_sent_notifications("1"))["Algorithms_homeworks_Report 1"]


async def test_a_notification_telegram_refused_is_retried(monkeypatch):
    """Recording a send that never happened would lose the notification."""
    await update_notification_state("1", seeded=True)
    failing = FakeBot(fail=True)
    api = FakeApi(login_result={"JSESSIONID": "x"}, todo_list=make_todo(hours=2.5))
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
        FakeApi(login_result={"JSESSIONID": "x"}, todo_list=make_todo(hours=-3)),
    )

    await notifications._process_user(FakeUser("1"))

    assert bot.sent == []


async def test_finished_assignments_are_forgotten(monkeypatch):
    api = FakeApi(login_result={"JSESSIONID": "x"}, todo_list=make_todo(hours=100))
    _patch_user_deps(monkeypatch, api)
    await notifications._process_user(FakeUser("1"))
    assert await get_sent_notifications("1") != {}

    # Submitted work drops off the KLAS todo list
    api._todo_list = [{"name": "Algorithms", "todo": {"homeworks": []}}]
    await notifications._process_user(FakeUser("1"))

    assert await get_sent_notifications("1") == {}


async def test_repeated_login_failures_warn_the_user_once(monkeypatch):
    """KW forces a password change every few months; the bot used to just go
    quiet and the student never learned why."""
    bot = _patch_user_deps(monkeypatch, FakeApi(login_result=None))

    for _ in range(notifications.LOGIN_FAILURES_BEFORE_WARNING + 2):
        await notifications._process_user(FakeUser("1"))

    warnings = [text for _, text in bot.sent]
    assert len(warnings) == 1, "the warning repeats every cycle"
    assert "/register" in warnings[0]
    state = await get_notification_state("1")
    assert state.credentials_warned is True


async def test_a_single_login_failure_says_nothing(monkeypatch):
    """One failed login is usually KLAS dropping the connection."""
    bot = _patch_user_deps(monkeypatch, FakeApi(login_result=None))

    await notifications._process_user(FakeUser("1"))

    assert bot.sent == []
    state = await get_notification_state("1")
    assert state.login_failures == 1


async def test_a_working_login_clears_the_failure_count(monkeypatch):
    _patch_user_deps(monkeypatch, FakeApi(login_result=None))
    await notifications._process_user(FakeUser("1"))

    _patch_user_deps(
        monkeypatch, FakeApi(login_result={"JSESSIONID": "x"}, todo_list=[])
    )
    await notifications._process_user(FakeUser("1"))

    state = await get_notification_state("1")
    assert state.login_failures == 0
    assert state.credentials_warned is False


async def test_send_notification_returns_false_and_logs_on_failure(monkeypatch, caplog):
    async def boom(**kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(notifications.bot, "send_message", boom)

    result = await notifications.send_notification("body", "42", 1)

    assert result is False
    assert "Error sending notification to 42" in caplog.text


async def test_send_notification_logs_the_send_without_the_assignment_text(
    monkeypatch, caplog
):
    """The log line names the user and the threshold, never what was due."""
    caplog.set_level(logging.INFO)
    bot = FakeBot()
    monkeypatch.setattr(notifications, "bot", bot)

    assert await notifications.send_notification("Secret essay title", "42", 3) is True

    assert "Notification sent to 42 (threshold=3h)" in caplog.text
    assert "Secret essay title" not in caplog.text
