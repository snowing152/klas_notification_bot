import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import types
from cryptography.fernet import Fernet

from app.database.database import get_klas_session, get_user, save_user
from app.handlers import auth
from app.handlers import klas_login
from app.handlers.klas_login import LoginOutcome, run_push_login
from app.services.kw import LoginResult, LoginStatus, PushRequest, PushStatus
from app.strings import Language, Strings
from app.utils import encryption

OK = LoginResult(LoginStatus.OK, {"JSESSIONID": "s"})
TWO_FACTOR = LoginResult(LoginStatus.TWO_FACTOR_REQUIRED)
WRONG = LoginResult(LoginStatus.FAILED)
DOWN = LoginResult(LoginStatus.UNAVAILABLE)
SENT = PushRequest(PushStatus.SENT, tid="t", limit="1")


@pytest.fixture(autouse=True)
def throwaway_key(monkeypatch):
    """Encrypt with a key made for the test, never the real key file."""
    cipher = Fernet(Fernet.generate_key())
    monkeypatch.setattr(encryption, "_cipher", lambda: cipher)


@pytest.fixture(autouse=True)
def no_logins_in_progress():
    klas_login._logins_in_progress.clear()
    yield
    klas_login._logins_in_progress.clear()


class FakeApi:
    """KwangwoonUniversityApi with scripted answers for each login step."""

    def __init__(self, first=TWO_FACTOR, push=SENT, approval=OK, gate=None):
        self.first = first
        self.push = push
        self.approval = approval
        self.gate = gate
        self.pushes = 0
        self.attempts = 0
        # Set once the login reaches the push wait; lets a test look at what
        # already happened by then.
        self.waiting = asyncio.Event()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def attempt_login(self, login_id, password):
        self.attempts += 1
        return self.first

    async def request_push(self):
        self.pushes += 1
        return self.push

    async def wait_for_push_approval(self, password, push):
        self.waiting.set()
        if self.gate is not None:
            await self.gate.wait()
        return self.approval

    def klas_cookies(self):
        return {"JSESSIONID": "approved"}


@pytest.fixture
def fake_api(monkeypatch):
    def install(**kwargs):
        api = FakeApi(**kwargs)
        monkeypatch.setattr(klas_login, "KwangwoonUniversityApi", lambda: api)
        return api

    return install


def _bot():
    bot = MagicMock()
    bot.send_message = AsyncMock()
    return bot


def _sent_texts(bot):
    return [call.args[1] for call in bot.send_message.call_args_list]


async def _register(user_id="42", username="2024000000", password="pw"):
    await save_user(user_id, username, encryption.encrypt_password(password), Language.EN)


async def _login(bot, **kwargs):
    if await get_user("42") is None:
        await _register()
    return await run_push_login(bot, 1, "42", "2024000000", "pw", Language.EN, **kwargs)


async def test_approved_push_stores_the_session(fake_api):
    fake_api()
    bot = _bot()

    assert await _login(bot) is LoginOutcome.LOGGED_IN

    assert _sent_texts(bot) == [Strings.get("klas_push_sent", Language.EN)]
    row = await get_klas_session("42")
    assert row is not None and row.needs_login is False


async def test_wrong_password_sends_no_push(fake_api):
    api = fake_api(first=WRONG)
    bot = _bot()

    assert await _login(bot) is LoginOutcome.WRONG_CREDENTIALS
    assert api.pushes == 0
    assert await get_klas_session("42") is None


async def test_unreachable_klas_is_not_reported_as_a_wrong_password(fake_api):
    fake_api(first=DOWN)
    bot = _bot()

    assert await _login(bot) is LoginOutcome.FAILED
    assert _sent_texts(bot) == [Strings.get("klas_login_failed", Language.EN)]


async def test_a_login_without_second_factor_needs_no_push(fake_api):
    api = fake_api(first=OK)

    assert await _login(_bot()) is LoginOutcome.LOGGED_IN
    assert api.pushes == 0


async def test_timeout_offers_the_button_again(fake_api):
    fake_api(approval=TWO_FACTOR)
    bot = _bot()

    assert await _login(bot) is LoginOutcome.TIMED_OUT

    last = bot.send_message.call_args_list[-1]
    assert last.args[1] == Strings.get("klas_push_timeout", Language.EN)
    assert last.kwargs["reply_markup"] is not None
    assert await get_klas_session("42") is None


async def test_no_registered_phone_is_explained(fake_api):
    fake_api(push=PushRequest(PushStatus.NO_DEVICE))
    bot = _bot()

    assert await _login(bot) is LoginOutcome.NO_DEVICE
    assert _sent_texts(bot) == [Strings.get("klas_no_push_device", Language.EN)]


async def test_credentials_are_saved_before_the_push(fake_api):
    """So that a push that times out can be retried with the button."""
    order = []
    api = fake_api(approval=TWO_FACTOR)
    original = api.request_push

    async def request_push():
        order.append("push")
        return await original()

    api.request_push = request_push

    async def save():
        order.append("save")
        return True

    await _login(_bot(), on_password_accepted=save)

    assert order == ["save", "push"]


async def test_a_failed_save_stops_the_login(fake_api):
    api = fake_api()

    outcome = await _login(_bot(), on_password_accepted=AsyncMock(return_value=False))

    assert outcome is LoginOutcome.FAILED
    assert api.pushes == 0


async def test_a_second_tap_does_not_send_a_second_push(fake_api):
    gate = asyncio.Event()
    api = fake_api(gate=gate)
    bot = _bot()

    first = asyncio.create_task(_login(bot))
    await asyncio.sleep(0)
    second = await _login(bot)
    gate.set()

    assert second is LoginOutcome.ALREADY_RUNNING
    assert await first is LoginOutcome.LOGGED_IN
    assert api.pushes == 1


def _message(text, user_id=42):
    message = AsyncMock(spec=types.Message)
    message.text = text
    message.from_user = MagicMock(id=user_id)
    message.chat = MagicMock(id=user_id)
    message.bot = _bot()
    message.answer = AsyncMock()
    message.delete = AsyncMock()
    return message


def _state(username="2024000000"):
    state = AsyncMock()
    state.get_data = AsyncMock(return_value={"username": username})
    return state


async def test_register_saves_the_user_and_announces_it(fake_api):
    fake_api()
    message = _message("pw")
    state = _state()

    await auth.process_password(message, state)

    message.delete.assert_awaited()
    state.clear.assert_awaited()
    user = await get_user("42")
    assert user.username == "2024000000"
    assert encryption.decrypt_password(user.encrypted_password) == "pw"
    message.answer.assert_awaited_with(
        Strings.get("registration_successful", Language.EN)
    )


async def test_register_with_a_wrong_password_saves_nothing(fake_api):
    fake_api(first=WRONG)
    message = _message("wrong")

    await auth.process_password(message, _state())

    assert await get_user("42") is None
    message.answer.assert_awaited_with(Strings.get("invalid_credentials", Language.EN))


async def test_register_keeps_the_password_when_the_push_times_out(fake_api):
    fake_api(approval=TWO_FACTOR)

    await auth.process_password(_message("pw"), _state())

    assert await get_user("42") is not None, "the button retries with it"


def _callback(user_id=42):
    callback = AsyncMock(spec=types.CallbackQuery)
    callback.from_user = MagicMock(id=user_id)
    callback.message = MagicMock()
    callback.message.chat = MagicMock(id=user_id)
    callback.message.answer = AsyncMock()
    callback.bot = _bot()
    callback.answer = AsyncMock()
    return callback


async def test_relogin_button_uses_the_saved_password(fake_api):
    fake_api()
    await save_user("42", "2024000000", encryption.encrypt_password("pw"), Language.EN)
    callback = _callback()

    await auth.process_klas_relogin(callback)

    assert _sent_texts(callback.bot)[-1] == Strings.get("klas_login_success", Language.EN)
    assert (await get_klas_session("42")).needs_login is False


async def test_relogin_with_a_changed_password_says_so(fake_api):
    fake_api(first=WRONG)
    await save_user("42", "2024000000", encryption.encrypt_password("old"), Language.EN)
    callback = _callback()

    await auth.process_klas_relogin(callback)

    assert _sent_texts(callback.bot)[-1] == Strings.get(
        "credentials_expired", Language.EN
    )


async def test_relogin_without_registration_leads_to_register(fake_api):
    api = fake_api()
    callback = _callback()

    await auth.process_klas_relogin(callback)

    assert _sent_texts(callback.bot) == [Strings.get("need_to_register", Language.EN)]
    assert api.pushes == 0


async def test_no_session_is_stored_for_a_user_who_left_during_the_wait(fake_api):
    """/unregister while the push waited: nothing would ever clear the row."""
    gate = asyncio.Event()
    fake_api(gate=gate)
    await _register()

    task = asyncio.create_task(
        run_push_login(_bot(), 1, "42", "2024000000", "pw", Language.EN)
    )
    await asyncio.sleep(0)
    await auth.delete_all_user_data("42")
    gate.set()

    assert await task is LoginOutcome.FAILED
    assert await get_klas_session("42") is None


async def test_a_rejected_password_is_not_sent_again(fake_api):
    """KLAS locks the account after five wrong passwords; each tap on a login
    button used to resend the stored one."""
    api = fake_api(first=WRONG)
    await _register(password="old")

    await auth.process_klas_relogin(_callback())
    second = _callback()
    await auth.process_klas_relogin(second)

    assert api.attempts == 1
    assert _sent_texts(second.bot) == [Strings.get("credentials_expired", Language.EN)]


async def test_register_allows_relogin_again_after_a_rejected_password(fake_api):
    fake_api(first=WRONG)
    await _register(password="old")
    await auth.process_klas_relogin(_callback())

    fake_api()
    await auth.process_password(_message("new"), _state())

    row = await get_klas_session("42")
    assert row.password_rejected is False
    assert row.encrypted_cookies is not None


async def test_register_with_the_same_id_keeps_a_working_session(fake_api):
    """A /register whose push then times out must not throw the session away."""
    await _register()
    await klas_login.klas_session.save_new_session("42", {"JSESSIONID": "kept"})
    fake_api(approval=TWO_FACTOR)

    await auth.process_password(_message("pw"), _state())

    assert (await get_klas_session("42")).encrypted_cookies is not None


async def test_register_with_another_id_drops_the_old_session(fake_api):
    await _register(username="2023111111")
    await klas_login.klas_session.save_new_session("42", {"JSESSIONID": "other"})
    fake_api(approval=TWO_FACTOR)

    await auth.process_password(_message("pw"), _state(username="2024000000"))

    assert await get_klas_session("42") is None


async def test_the_password_message_is_gone_before_the_push_wait(fake_api):
    gate = asyncio.Event()
    api = fake_api(gate=gate)
    message = _message("pw")
    state = _state()

    task = asyncio.create_task(auth.process_password(message, state))
    await asyncio.wait_for(api.waiting.wait(), timeout=5)

    message.delete.assert_awaited()
    state.clear.assert_awaited()
    gate.set()
    await task


async def test_a_failed_delete_still_leaves_the_password_step(fake_api):
    """A user left in the password state would send their next message to
    KLAS as a password."""
    fake_api()
    message = _message("pw")
    message.delete = AsyncMock(side_effect=RuntimeError("message to delete not found"))
    state = _state()

    await auth.process_password(message, state)

    state.clear.assert_awaited()
    message.answer.assert_awaited_with(
        Strings.get("registration_successful", Language.EN)
    )


async def test_register_with_another_id_drops_everything_kept_for_the_old_one(
    fake_api, tmp_path, monkeypatch
):
    from app.database.database import get_sent_notifications, record_sent_notifications
    from app.handlers import student_info, todos
    from app.services import notifications

    monkeypatch.setattr(student_info, "PHOTOS_DIR", tmp_path)
    await _register(username="2023111111")
    await record_sent_notifications("42", [("Algorithms_homeworks_Report", "new")])
    todos._items_cache["42"] = (0.0, [])
    notifications._unrecorded["42"] = [("Algorithms_homeworks_Report", "t3")]
    photo = student_info.student_photo_path("42")
    photo.write_bytes(b"jpeg")
    fake_api(approval=TWO_FACTOR)

    try:
        await auth.process_password(_message("pw"), _state(username="2024000000"))

        assert await get_sent_notifications("42") == {}
        assert "42" not in todos._items_cache
        assert "42" not in notifications._unrecorded
        assert not photo.exists()
    finally:
        todos._items_cache.clear()
        notifications._unrecorded.clear()


async def test_register_with_the_same_id_keeps_the_notification_history(fake_api):
    from app.database.database import get_sent_notifications, record_sent_notifications

    await _register()
    await record_sent_notifications("42", [("Algorithms_homeworks_Report", "new")])
    fake_api(approval=TWO_FACTOR)

    await auth.process_password(_message("pw"), _state())

    assert await get_sent_notifications("42") == {"Algorithms_homeworks_Report": {"new"}}
