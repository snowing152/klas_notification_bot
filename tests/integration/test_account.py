import pytest
from unittest.mock import AsyncMock
from aiogram import types

from app.database.database import get_user, save_library_user, save_user
from app.handlers.account import cmd_account, process_account_callback
from app.handlers.auth import LibraryRegistrationStates, RegistrationStates
from app.handlers.library import SearchStates
from app.strings import Language, Strings


def make_message(user_id="900", language_code="en"):
    from_user = AsyncMock()
    from_user.id = user_id
    from_user.language_code = language_code

    message = AsyncMock(spec=types.Message)
    message.from_user = from_user
    message.answer = AsyncMock()
    return message


def make_callback(data, user_id="900", language_code="en"):
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


def make_state():
    state = AsyncMock()
    return state


@pytest.mark.asyncio
async def test_account_screen_with_nothing_connected_only_offers_logins():
    message = make_message(user_id="901")

    await cmd_account(message)

    text = message.answer.call_args[0][0]
    assert Strings.get("account_klas_missing", Language.EN) in text
    assert Strings.get("account_library_missing", Language.EN) in text

    markup = message.answer.call_args.kwargs["reply_markup"]
    callbacks = [b.callback_data for row in markup.inline_keyboard for b in row]
    # Nothing is connected yet, so info/search/delete have nothing to act on
    assert callbacks == ["account_klas", "account_library"]


@pytest.mark.asyncio
async def test_account_screen_with_klas_connected_shows_username_and_info_button():
    await save_user("902", "2020123456", "enc", Language.EN)
    message = make_message(user_id="902")

    await cmd_account(message)

    text = message.answer.call_args[0][0]
    assert "2020123456" in text

    markup = message.answer.call_args.kwargs["reply_markup"]
    callbacks = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert "account_info" in callbacks
    assert "account_delete" in callbacks
    assert "account_search" not in callbacks

    klas_button = [b for row in markup.inline_keyboard for b in row if b.callback_data == "account_klas"][0]
    assert klas_button.text == Strings.get("account_relogin_klas", Language.EN)


@pytest.mark.asyncio
async def test_account_screen_with_library_connected_shows_search_button():
    await save_library_user("903", "2020123456", "enc", "01012345678")
    message = make_message(user_id="903")

    await cmd_account(message)

    markup = message.answer.call_args.kwargs["reply_markup"]
    callbacks = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert "account_search" in callbacks
    assert "account_info" not in callbacks


@pytest.mark.asyncio
async def test_account_klas_button_starts_the_registration_fsm():
    callback = make_callback("account_klas", user_id="904")
    state = make_state()

    await process_account_callback(callback, state)

    state.set_state.assert_awaited_once_with(RegistrationStates.waiting_for_username)
    assert callback.message.edit_text.call_args[0][0] == Strings.get(
        "enter_username", Language.EN
    )


@pytest.mark.asyncio
async def test_account_library_button_starts_the_library_registration_fsm():
    callback = make_callback("account_library", user_id="905")
    state = make_state()

    await process_account_callback(callback, state)

    state.set_state.assert_awaited_once_with(
        LibraryRegistrationStates.waiting_for_username
    )
    assert callback.message.edit_text.call_args[0][0] == Strings.get(
        "library_enter_username", Language.EN
    )


@pytest.mark.asyncio
async def test_account_search_button_starts_the_search_fsm():
    callback = make_callback("account_search", user_id="906")
    state = make_state()

    await process_account_callback(callback, state)

    state.set_state.assert_awaited_once_with(SearchStates.waiting_for_query)
    assert callback.message.edit_text.call_args[0][0] == Strings.get(
        "enter_book_name", Language.EN
    )


@pytest.mark.asyncio
async def test_account_info_button_reaches_send_student_info(monkeypatch):
    calls = {}

    async def fake_send_student_info(bot, chat_id, user_id, user_lang):
        calls["args"] = (chat_id, user_id, user_lang)

    monkeypatch.setattr(
        "app.handlers.account.send_student_info", fake_send_student_info
    )
    callback = make_callback("account_info", user_id="907")
    callback.message.chat = AsyncMock()
    callback.message.chat.id = 12345

    await process_account_callback(callback, make_state())

    assert calls["args"] == (12345, "907", Language.EN)


@pytest.mark.asyncio
async def test_account_delete_asks_for_confirmation_first():
    await save_user("908", "2020123456", "enc", Language.EN)
    callback = make_callback("account_delete", user_id="908")

    await process_account_callback(callback, make_state())

    assert callback.message.edit_text.call_args[0][0] == Strings.get(
        "account_delete_confirm", Language.EN
    )
    markup = callback.message.edit_text.call_args.kwargs["reply_markup"]
    callbacks = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert callbacks == ["account_delete_yes", "account_delete_no"]
    # Tapping "delete" itself must not delete anything - only confirming does
    assert await get_user("908") is not None


@pytest.mark.asyncio
async def test_account_delete_yes_actually_deletes_everything():
    await save_user("909", "2020123456", "enc", Language.EN)
    await save_library_user("909", "2020123456", "enc", "01012345678")
    callback = make_callback("account_delete_yes", user_id="909")

    await process_account_callback(callback, make_state())

    assert await get_user("909") is None
    assert callback.message.edit_text.call_args[0][0] == Strings.get(
        "unregistered", Language.EN
    )


@pytest.mark.asyncio
async def test_account_delete_no_redraws_the_account_screen():
    await save_user("910", "2020123456", "enc", Language.EN)
    callback = make_callback("account_delete_no", user_id="910")

    await process_account_callback(callback, make_state())

    text = callback.message.edit_text.call_args[0][0]
    assert Strings.get("account_header", Language.EN) in text
    assert await get_user("910") is not None
