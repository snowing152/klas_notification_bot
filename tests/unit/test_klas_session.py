import datetime
import json

import pytest
from cryptography.fernet import Fernet

from app.database.database import get_klas_session, save_user, update_klas_session
from app.services import klas_session
from app.services.klas_session import SessionStatus, open_session
from app.services.kw import LoginResult, LoginStatus
from app.strings import Language
from app.utils import encryption


@pytest.fixture(autouse=True)
def throwaway_key(monkeypatch):
    """Encrypt with a key made for the test, never the real key file."""
    cipher = Fernet(Fernet.generate_key())
    monkeypatch.setattr(encryption, "_cipher", lambda: cipher)


@pytest.fixture
def fixed_now(monkeypatch):
    now = datetime.datetime(2026, 11, 24, 12, 0)
    monkeypatch.setattr(klas_session.timezone, "now", lambda: now)
    return now


class FakeUser:
    def __init__(self, user_id="42", username="2024000000"):
        self.user_id = user_id
        self.username = username
        self.encrypted_password = encryption.encrypt_password("pw")


TWO_FACTOR = LoginResult(LoginStatus.TWO_FACTOR_REQUIRED)


class FakeApi:
    """Stands in for KwangwoonUniversityApi.

    `alive` is what keep_alive says, `login` what a silent login answers.
    The default login asks for the push, which is what the bot meets from a
    server KLAS does not trust.
    """

    instances = []

    def __init__(self, alive=True, cookies_after=None, login=TWO_FACTOR):
        self.alive = alive
        self.cookies_after = cookies_after
        self.login = login
        self.restored = None
        self.login_id = None
        self.logins = []
        FakeApi.instances.append(self)

    async def attempt_login(self, login_id, password):
        self.logins.append((login_id, password))
        if self.login.status is LoginStatus.OK:
            self.restored = {"JSESSIONID": "silent"}
        return self.login

    def forget_cookies(self):
        self.restored = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def restore_cookies(self, cookies):
        self.restored = cookies

    async def keep_alive(self):
        return self.alive

    def klas_cookies(self):
        return self.cookies_after if self.cookies_after is not None else self.restored


@pytest.fixture
def fake_api(monkeypatch):
    FakeApi.instances = []

    def install(**kwargs):
        monkeypatch.setattr(klas_session, "KwangwoonUniversityApi", lambda: FakeApi(**kwargs))

    return install


async def _stored_cookies(user_id):
    row = await get_klas_session(user_id)
    return json.loads(encryption.decrypt_secret(row.encrypted_cookies))


def _logins():
    return [login for api in FakeApi.instances for login in api.logins]


async def test_without_a_session_and_with_the_push_required_a_login_is_needed(fake_api):
    fake_api()

    async with open_session(FakeUser()) as access:
        assert access.status is SessionStatus.NEEDS_LOGIN
        assert access.api is None

    assert _logins() == [("2024000000", "pw")]
    row = await get_klas_session("42")
    assert row.two_factor_required is True


async def test_a_silent_login_replaces_a_missing_session(fake_api, fixed_now):
    """Where KLAS lets the bot in without a push, the student never notices."""
    fake_api(login=LoginResult(LoginStatus.OK, {"JSESSIONID": "silent"}))

    async with open_session(FakeUser()) as access:
        assert access.status is SessionStatus.ACTIVE

    row = await get_klas_session("42")
    assert row.logged_in_at == fixed_now
    assert await _stored_cookies("42") == {"JSESSIONID": "silent"}


async def test_a_silent_login_replaces_a_dead_session(fake_api):
    await klas_session.save_new_session("42", {"JSESSIONID": "old"})
    fake_api(alive=False, login=LoginResult(LoginStatus.OK, {"JSESSIONID": "silent"}))

    async with open_session(FakeUser()) as access:
        assert access.status is SessionStatus.ACTIVE

    assert await _stored_cookies("42") == {"JSESSIONID": "silent"}


async def test_once_the_push_was_required_the_password_is_not_sent_again(fake_api):
    """Not every 30 minutes: one try per lost session."""
    fake_api()
    async with open_session(FakeUser()):
        pass

    fake_api()
    async with open_session(FakeUser()) as access:
        assert access.status is SessionStatus.NEEDS_LOGIN

    assert len(_logins()) == 1


async def test_a_rejected_password_is_not_sent_again(fake_api):
    """KLAS locks the account after five wrong passwords."""
    fake_api(login=LoginResult(LoginStatus.FAILED))
    async with open_session(FakeUser()) as access:
        assert access.status is SessionStatus.PASSWORD_REJECTED

    fake_api(login=LoginResult(LoginStatus.FAILED))
    async with open_session(FakeUser()) as access:
        assert access.status is SessionStatus.PASSWORD_REJECTED

    assert len(_logins()) == 1
    assert (await get_klas_session("42")).password_rejected is True


async def test_unreachable_klas_during_a_silent_login_is_retried_later(fake_api):
    fake_api(login=LoginResult(LoginStatus.UNAVAILABLE))
    async with open_session(FakeUser()) as access:
        assert access.status is SessionStatus.UNAVAILABLE

    fake_api(login=LoginResult(LoginStatus.UNAVAILABLE))
    async with open_session(FakeUser()):
        pass

    assert len(_logins()) == 2, "an outage says nothing; nothing stops the next try"


async def test_an_approved_login_allows_silent_logins_again(fake_api):
    fake_api()
    async with open_session(FakeUser()):
        pass

    await klas_session.save_new_session("42", {"JSESSIONID": "approved"})

    assert (await get_klas_session("42")).two_factor_required is False


async def test_a_live_session_is_reused_and_written_back(fake_api, fixed_now):
    await klas_session.save_new_session("42", {"JSESSIONID": "old"})
    fake_api(cookies_after={"JSESSIONID": "rotated", "SESSION": "s"})

    async with open_session(FakeUser()) as access:
        assert access.status is SessionStatus.ACTIVE
        assert access.api.restored == {"JSESSIONID": "old"}
        assert access.api.login_id == "2024000000"

    assert await _stored_cookies("42") == {"JSESSIONID": "rotated", "SESSION": "s"}


async def test_cookies_are_stored_encrypted(fake_api):
    await klas_session.save_new_session("42", {"JSESSIONID": "secret-value"})

    row = await get_klas_session("42")

    assert "secret-value" not in row.encrypted_cookies


async def test_cookies_are_written_back_when_the_operation_fails(fake_api):
    await klas_session.save_new_session("42", {"JSESSIONID": "old"})
    fake_api(cookies_after={"JSESSIONID": "rotated"})

    with pytest.raises(RuntimeError):
        async with open_session(FakeUser()):
            raise RuntimeError("the operation broke")

    assert await _stored_cookies("42") == {"JSESSIONID": "rotated"}


async def test_a_dead_session_is_dropped(fake_api, monkeypatch):
    start = datetime.datetime(2026, 11, 24, 8, 0)
    monkeypatch.setattr(klas_session.timezone, "now", lambda: start)
    await klas_session.save_new_session("42", {"JSESSIONID": "old"})
    monkeypatch.setattr(
        klas_session.timezone, "now", lambda: start + datetime.timedelta(hours=5)
    )
    fake_api(alive=False)

    async with open_session(FakeUser()) as access:
        assert access.status is SessionStatus.NEEDS_LOGIN

    row = await get_klas_session("42")
    assert row.needs_login is True
    assert row.encrypted_cookies is None


async def test_unreachable_klas_keeps_the_session(fake_api):
    """A network fault says nothing about the session; dropping it would make
    the student approve a push for nothing."""
    await klas_session.save_new_session("42", {"JSESSIONID": "old"})
    fake_api(alive=None)

    async with open_session(FakeUser()) as access:
        assert access.status is SessionStatus.UNAVAILABLE

    row = await get_klas_session("42")
    assert row.needs_login is False
    assert await _stored_cookies("42") == {"JSESSIONID": "old"}


async def test_an_unreadable_session_asks_for_a_new_login(fake_api):
    """A token written with another key cannot be decrypted."""
    await update_klas_session("42", encrypted_cookies="not-a-fernet-token")
    fake_api()

    async with open_session(FakeUser()) as access:
        assert access.status is SessionStatus.NEEDS_LOGIN

    assert (await get_klas_session("42")).needs_login is True


async def test_a_new_login_clears_the_prompt_flag(fake_api, fixed_now):
    await update_klas_session("42", needs_login=True, login_prompt_sent=True)

    await klas_session.save_new_session("42", {"JSESSIONID": "new"})

    row = await get_klas_session("42")
    assert row.needs_login is False
    assert row.login_prompt_sent is False
    assert row.logged_in_at == fixed_now


async def test_deleting_a_users_data_drops_their_klas_session():
    from app.handlers.auth import delete_all_user_data

    await save_user("42", "2024000000", b"encrypted", Language.EN)
    await klas_session.save_new_session("42", {"JSESSIONID": "s"})

    await delete_all_user_data("42")

    assert await get_klas_session("42") is None


async def test_an_operation_does_not_bring_back_a_deleted_session(fake_api):
    """/unregister during an operation: the write-back used to re-insert the
    row, leaving a live session nothing would ever clear."""
    from app.handlers.auth import delete_all_user_data

    await save_user("42", "2024000000", b"encrypted", Language.EN)
    await klas_session.save_new_session("42", {"JSESSIONID": "old"})
    fake_api(cookies_after={"JSESSIONID": "rotated"})

    async with open_session(FakeUser()) as access:
        assert access.status is SessionStatus.ACTIVE
        await delete_all_user_data("42")

    assert await get_klas_session("42") is None


async def test_an_operation_does_not_overwrite_a_newer_login(fake_api, monkeypatch):
    start = datetime.datetime(2026, 11, 24, 8, 0)
    monkeypatch.setattr(klas_session.timezone, "now", lambda: start)
    await klas_session.save_new_session("42", {"JSESSIONID": "old"})
    fake_api(cookies_after={"JSESSIONID": "old-rotated"})

    async with open_session(FakeUser()):
        monkeypatch.setattr(
            klas_session.timezone, "now", lambda: start + datetime.timedelta(minutes=5)
        )
        await klas_session.save_new_session("42", {"JSESSIONID": "newer"})

    assert await _stored_cookies("42") == {"JSESSIONID": "newer"}


async def test_a_dead_old_session_does_not_end_a_newer_login(fake_api, monkeypatch):
    """The old session dies while the student approves a new one."""
    start = datetime.datetime(2026, 11, 24, 8, 0)
    monkeypatch.setattr(klas_session.timezone, "now", lambda: start)
    await klas_session.save_new_session("42", {"JSESSIONID": "old"})

    async def newer_login_then_dead():
        monkeypatch.setattr(
            klas_session.timezone, "now", lambda: start + datetime.timedelta(minutes=5)
        )
        await klas_session.save_new_session("42", {"JSESSIONID": "newer"})
        return False

    fake_api()
    FakeApi.keep_alive = lambda self: newer_login_then_dead()
    try:
        async with open_session(FakeUser()) as access:
            # Not NEEDS_LOGIN: the student just logged in, and asking them to
            # do it again would be wrong. The next operation uses the new one.
            assert access.status is SessionStatus.UNAVAILABLE
    finally:
        del FakeApi.keep_alive

    row = await get_klas_session("42")
    assert row.needs_login is False
    assert row.two_factor_required is False
    assert await _stored_cookies("42") == {"JSESSIONID": "newer"}
    assert _logins() == [], "no silent login over a session replaced meanwhile"
