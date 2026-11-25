import pytest
from unittest.mock import AsyncMock

from aiogram import types

from app.handlers import auth as auth_handlers
from app.services import qr as qr_service
from app.strings import Strings, Language


def make_message(text, user_id="1001", language_code="en"):
    from_user = AsyncMock()
    from_user.id = user_id
    from_user.language_code = language_code

    message = AsyncMock(spec=types.Message)
    message.from_user = from_user
    message.text = text
    message.answer = AsyncMock()
    message.delete = AsyncMock()
    return message


def make_state(username="2021123456", phone_number="01099999999"):
    # The password is deliberately not in the FSM data: it is the last step.
    state = AsyncMock()
    state.get_data = AsyncMock(
        return_value={"username": username, "phone_number": phone_number}
    )
    return state


@pytest.mark.asyncio
async def test_wrong_phone_number_gets_a_specific_reason(monkeypatch):
    """Regression: a rejected library login used to show the same generic
    'check your details' message no matter why it failed."""

    async def fake_get_secret_key(real_id, session=None):
        return "sixteencharkeyxx"

    async def fake_library_login(std_number, phone, password, secret, session=None):
        raise qr_service.LibraryLoginRejected("1", "not the registered phone")

    monkeypatch.setattr(auth_handlers, "get_secret_key", fake_get_secret_key)
    monkeypatch.setattr(auth_handlers, "library_login", fake_library_login)

    message = make_message("pw")
    state = make_state()

    await auth_handlers.process_library_password(message, state)

    message.answer.assert_called_once_with(
        Strings.get("library_login_phone_mismatch", Language.EN)
    )
    state.clear.assert_called_once()


@pytest.mark.asyncio
async def test_other_rejection_reasons_keep_the_generic_message(monkeypatch):
    async def fake_get_secret_key(real_id, session=None):
        return "sixteencharkeyxx"

    async def fake_library_login(std_number, phone, password, secret, session=None):
        raise qr_service.LibraryLoginRejected("9", "some other reason")

    monkeypatch.setattr(auth_handlers, "get_secret_key", fake_get_secret_key)
    monkeypatch.setattr(auth_handlers, "library_login", fake_library_login)

    message = make_message("pw")
    state = make_state()

    await auth_handlers.process_library_password(message, state)

    message.answer.assert_called_once_with(
        Strings.get("library_login_failed", Language.EN)
    )


@pytest.mark.asyncio
async def test_transport_failure_shows_the_generic_message(monkeypatch):
    async def fake_get_secret_key(real_id, session=None):
        return "sixteencharkeyxx"

    async def fake_library_login(std_number, phone, password, secret, session=None):
        raise qr_service.LibraryLoginError("http_status:503")

    monkeypatch.setattr(auth_handlers, "get_secret_key", fake_get_secret_key)
    monkeypatch.setattr(auth_handlers, "library_login", fake_library_login)

    message = make_message("pw")
    state = make_state()

    await auth_handlers.process_library_password(message, state)

    message.answer.assert_called_once_with(
        Strings.get("library_login_failed", Language.EN)
    )


@pytest.mark.asyncio
async def test_successful_login_still_saves_and_confirms(monkeypatch):
    async def fake_get_secret_key(real_id, session=None):
        return "sixteencharkeyxx"

    async def fake_library_login(std_number, phone, password, secret, session=None):
        return "auth-key"

    monkeypatch.setattr(auth_handlers, "get_secret_key", fake_get_secret_key)
    monkeypatch.setattr(auth_handlers, "library_login", fake_library_login)

    message = make_message("pw")
    state = make_state()

    await auth_handlers.process_library_password(message, state)

    message.answer.assert_called_once_with(
        Strings.get("library_registration_successful", Language.EN)
    )


@pytest.mark.asyncio
async def test_username_step_asks_for_the_phone_number_next():
    message = make_message("2021123456")
    state = AsyncMock()

    await auth_handlers.process_library_username(message, state)

    state.set_state.assert_awaited_once_with(
        auth_handlers.LibraryRegistrationStates.waiting_for_phone_number
    )
    message.answer.assert_called_once_with(
        Strings.get("enter_phone_number", Language.EN)
    )
    message.delete.assert_awaited()


@pytest.mark.asyncio
async def test_phone_step_asks_for_the_password_and_never_stores_one():
    message = make_message("01099999999")
    state = AsyncMock()

    await auth_handlers.process_library_phone_number(message, state)

    state.update_data.assert_awaited_once_with(phone_number="01099999999")
    state.set_state.assert_awaited_once_with(
        auth_handlers.LibraryRegistrationStates.waiting_for_password
    )
    message.answer.assert_called_once_with(
        Strings.get("library_enter_password", Language.EN)
    )
    message.delete.assert_awaited()


@pytest.mark.asyncio
async def test_password_step_deletes_the_message_and_clears_the_state(monkeypatch):
    async def fake_get_secret_key(real_id, session=None):
        return "sixteencharkeyxx"

    async def fake_library_login(std_number, phone, password, secret, session=None):
        assert (std_number, phone, password) == ("2021123456", "01099999999", "pw")
        return "auth-key"

    monkeypatch.setattr(auth_handlers, "get_secret_key", fake_get_secret_key)
    monkeypatch.setattr(auth_handlers, "library_login", fake_library_login)
    message = make_message("pw")
    state = make_state()

    await auth_handlers.process_library_password(message, state)

    message.delete.assert_awaited()
    state.clear.assert_awaited_once()
    state.update_data.assert_not_called()


@pytest.mark.asyncio
async def test_password_message_is_deleted_before_any_network_call(monkeypatch):
    message = make_message("pw")
    state = make_state()
    order = []
    message.delete = AsyncMock(side_effect=lambda: order.append("delete"))
    state.clear = AsyncMock(side_effect=lambda: order.append("clear"))

    async def fake_get_secret_key(real_id, session=None):
        order.append("network")
        return "sixteencharkeyxx"

    async def fake_library_login(std_number, phone, password, secret, session=None):
        order.append("network")
        return "auth-key"

    monkeypatch.setattr(auth_handlers, "get_secret_key", fake_get_secret_key)
    monkeypatch.setattr(auth_handlers, "library_login", fake_library_login)

    await auth_handlers.process_library_password(message, state)

    assert order[:2] == ["clear", "delete"]
    assert order.index("delete") < order.index("network")


@pytest.mark.asyncio
async def test_a_non_text_library_password_keeps_the_state():
    message = make_message(None)
    state = make_state()

    await auth_handlers.process_library_password(message, state)

    state.clear.assert_not_called()
    message.answer.assert_called_once_with(
        Strings.get("library_enter_password", Language.EN)
    )
