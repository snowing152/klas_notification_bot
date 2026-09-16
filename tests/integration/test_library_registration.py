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


def make_state(username="2021123456", password="pw"):
    state = AsyncMock()
    state.get_data = AsyncMock(
        return_value={"username": username, "password": password}
    )
    return state


@pytest.mark.asyncio
async def test_wrong_phone_number_gets_a_specific_reason(monkeypatch):
    """Regression: a rejected library login used to show the same generic
    'check your details' message no matter why it failed."""

    async def fake_get_secret_key(real_id):
        return "sixteencharkeyxx"

    async def fake_library_login(std_number, phone, password, secret):
        raise qr_service.LibraryLoginRejected("1", "not the registered phone")

    monkeypatch.setattr(auth_handlers, "get_secret_key", fake_get_secret_key)
    monkeypatch.setattr(auth_handlers, "library_login", fake_library_login)

    message = make_message("01099999999")
    state = make_state()

    await auth_handlers.process_library_phone_number(message, state)

    message.answer.assert_called_once_with(
        Strings.get("library_login_phone_mismatch", Language.EN)
    )
    state.clear.assert_called_once()


@pytest.mark.asyncio
async def test_other_rejection_reasons_keep_the_generic_message(monkeypatch):
    async def fake_get_secret_key(real_id):
        return "sixteencharkeyxx"

    async def fake_library_login(std_number, phone, password, secret):
        raise qr_service.LibraryLoginRejected("9", "some other reason")

    monkeypatch.setattr(auth_handlers, "get_secret_key", fake_get_secret_key)
    monkeypatch.setattr(auth_handlers, "library_login", fake_library_login)

    message = make_message("01099999999")
    state = make_state()

    await auth_handlers.process_library_phone_number(message, state)

    message.answer.assert_called_once_with(
        Strings.get("library_login_failed", Language.EN)
    )


@pytest.mark.asyncio
async def test_transport_failure_shows_the_generic_message(monkeypatch):
    async def fake_get_secret_key(real_id):
        return "sixteencharkeyxx"

    async def fake_library_login(std_number, phone, password, secret):
        raise qr_service.LibraryLoginError("http_status:503")

    monkeypatch.setattr(auth_handlers, "get_secret_key", fake_get_secret_key)
    monkeypatch.setattr(auth_handlers, "library_login", fake_library_login)

    message = make_message("01099999999")
    state = make_state()

    await auth_handlers.process_library_phone_number(message, state)

    message.answer.assert_called_once_with(
        Strings.get("library_login_failed", Language.EN)
    )


@pytest.mark.asyncio
async def test_successful_login_still_saves_and_confirms(monkeypatch):
    async def fake_get_secret_key(real_id):
        return "sixteencharkeyxx"

    async def fake_library_login(std_number, phone, password, secret):
        return "auth-key"

    monkeypatch.setattr(auth_handlers, "get_secret_key", fake_get_secret_key)
    monkeypatch.setattr(auth_handlers, "library_login", fake_library_login)

    message = make_message("01099999999")
    state = make_state()

    await auth_handlers.process_library_phone_number(message, state)

    message.answer.assert_called_once_with(
        Strings.get("library_registration_successful", Language.EN)
    )
