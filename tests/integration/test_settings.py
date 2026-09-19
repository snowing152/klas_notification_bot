import pytest
from unittest.mock import AsyncMock
from aiogram import types

from app.database.database import get_user_settings, save_user, update_user_settings
from app.handlers.settings import (
    cmd_settings,
    process_announcement_callback,
    process_settings_callback,
)
from app.services import announcements
from app.strings import Language, Strings


def make_message(user_id="555", language_code="en"):
    from_user = AsyncMock()
    from_user.id = user_id
    from_user.language_code = language_code

    message = AsyncMock(spec=types.Message)
    message.from_user = from_user
    message.answer = AsyncMock()
    return message


def make_callback(data, user_id="555", language_code="en"):
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


@pytest.mark.asyncio
async def test_settings_screen_shows_every_switch():
    message = make_message()

    await cmd_settings(message)

    markup = message.answer.call_args.kwargs["reply_markup"]
    callbacks = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert callbacks == [
        "settings_new",
        "settings_deadline",
        "settings_urgent",
        "settings_quiet",
        "settings_language",
    ]


@pytest.mark.asyncio
async def test_defaults_match_the_previous_behaviour():
    """Nobody should have to open /settings to keep what they already had."""
    settings_row = await get_user_settings("555")

    assert settings_row.new_assignment_alerts is True
    assert settings_row.deadline_alerts is True
    assert settings_row.urgent_thresholds_only is False
    assert settings_row.quiet_hours is True
    assert (settings_row.quiet_start, settings_row.quiet_end) == (23, 8)


@pytest.mark.asyncio
async def test_tapping_a_line_switches_it_and_redraws():
    callback = make_callback("settings_quiet")

    await process_settings_callback(callback)

    assert (await get_user_settings("555")).quiet_hours is False
    label = callback.message.edit_text.call_args.kwargs["reply_markup"]
    quiet_button = [
        b for row in label.inline_keyboard for b in row if b.callback_data == "settings_quiet"
    ][0]
    assert Strings.get("state_off", Language.EN) in quiet_button.text

    await process_settings_callback(make_callback("settings_quiet"))
    assert (await get_user_settings("555")).quiet_hours is True


@pytest.mark.asyncio
async def test_language_button_opens_the_language_choice():
    callback = make_callback("settings_language")

    await process_settings_callback(callback)

    markup = callback.message.edit_text.call_args.kwargs["reply_markup"]
    callbacks = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert callbacks == ["language_en", "language_ko", "language_ru"]


@pytest.mark.asyncio
async def test_an_unknown_callback_is_answered_and_ignored():
    callback = make_callback("settings_nonsense")

    await process_settings_callback(callback)

    callback.answer.assert_awaited()
    callback.message.edit_text.assert_not_called()


@pytest.mark.asyncio
async def test_announcement_button_turns_the_feature_on(monkeypatch):
    monkeypatch.setitem(
        announcements.ANNOUNCEMENTS,
        "urgent_v1",
        {"text_key": "settings_header", "setting": "urgent_thresholds_only"},
    )
    await save_user("555", "2020123456", "enc", Language.EN)

    await process_announcement_callback(make_callback("announce_on_urgent_v1"))

    assert (await get_user_settings("555")).urgent_thresholds_only is True


@pytest.mark.asyncio
async def test_announcement_dismissal_leaves_the_feature_off(monkeypatch):
    monkeypatch.setitem(
        announcements.ANNOUNCEMENTS,
        "urgent_v1",
        {"text_key": "settings_header", "setting": "urgent_thresholds_only"},
    )
    await update_user_settings("555", urgent_thresholds_only=True)

    callback = make_callback("announce_off_urgent_v1")
    await process_announcement_callback(callback)

    assert (await get_user_settings("555")).urgent_thresholds_only is False
    assert callback.message.edit_text.call_args[0][0] == Strings.get(
        "announcement_dismissed", Language.EN
    )
