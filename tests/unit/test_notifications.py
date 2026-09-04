import asyncio

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


def _patch_user_deps(monkeypatch, api):
    monkeypatch.setattr(notifications, "KwangwoonUniversityApi", lambda: api)
    monkeypatch.setattr(notifications, "decrypt_password", lambda p: "password")

    async def fake_language(user_id):
        return None

    monkeypatch.setattr(notifications, "get_user_language", fake_language)


async def test_cycle_checks_every_user(monkeypatch):
    checked = []

    async def record(user, tracker):
        checked.append(user.user_id)
        return True

    monkeypatch.setattr(notifications, "_process_user", record)
    users = [FakeUser("1"), FakeUser("2"), FakeUser("3")]

    assert await notifications.run_notification_cycle(users, {}) == 0
    assert sorted(checked) == ["1", "2", "3"]


async def test_cycle_runs_users_concurrently_but_bounded(monkeypatch):
    """Users used to be checked strictly one after another, so a cycle cost the
    sum of everyone's KLAS round trips."""
    in_flight = 0
    peak = 0

    async def slow_check(user, tracker):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return True

    monkeypatch.setattr(notifications, "_process_user", slow_check)
    users = [FakeUser(str(i)) for i in range(12)]

    assert await notifications.run_notification_cycle(users, {}) == 0
    assert peak > 1, "the cycle is still sequential"
    assert peak <= notifications.MAX_CONCURRENT_USERS, "KLAS must not be fanned out to"


async def test_cycle_counts_failed_users(monkeypatch):
    async def fail_the_second(user, tracker):
        return user.user_id != "2"

    monkeypatch.setattr(notifications, "_process_user", fail_the_second)
    users = [FakeUser("1"), FakeUser("2"), FakeUser("3")]

    assert await notifications.run_notification_cycle(users, {}) == 1


async def test_one_crashing_user_does_not_abort_the_cycle(monkeypatch):
    checked = []

    async def crash_the_second(user, tracker):
        if user.user_id == "2":
            raise RuntimeError("boom")
        checked.append(user.user_id)
        return True

    monkeypatch.setattr(notifications, "_process_user", crash_the_second)
    users = [FakeUser("1"), FakeUser("2"), FakeUser("3")]

    assert await notifications.run_notification_cycle(users, {}) == 1
    assert sorted(checked) == ["1", "3"], "the other users still get checked"


async def test_cycle_with_no_users_reports_no_failures(monkeypatch):
    assert await notifications.run_notification_cycle([], {}) == 0


async def test_process_user_reports_failure_when_login_fails(monkeypatch):
    """A failed login is the user's cycle failing, not 'no assignments'."""
    _patch_user_deps(monkeypatch, FakeApi(login_result=None))

    assert await notifications._process_user(FakeUser("1"), {}) is False


async def test_process_user_reports_failure_when_klas_cannot_be_read(monkeypatch):
    _patch_user_deps(
        monkeypatch, FakeApi(login_result={"JSESSIONID": "x"}, todo_list=None)
    )

    assert await notifications._process_user(FakeUser("1"), {}) is False


async def test_process_user_succeeds_with_no_subjects(monkeypatch):
    """An empty list is a real answer: the student has no subjects this term."""
    _patch_user_deps(
        monkeypatch, FakeApi(login_result={"JSESSIONID": "x"}, todo_list=[])
    )

    assert await notifications._process_user(FakeUser("1"), {}) is True
