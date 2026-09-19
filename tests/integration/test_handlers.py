import pytest
from unittest.mock import AsyncMock
from aiogram import types

from app.handlers.common import cmd_start, cmd_language
from app.strings import Strings, Language


def make_message(user_id="789", first_name="Test User", language_code="en"):
    from_user = AsyncMock()
    from_user.id = user_id
    from_user.first_name = first_name
    from_user.language_code = language_code

    message = AsyncMock(spec=types.Message)
    message.from_user = from_user
    # aiogram's reply_photo/answer are builder-style methods, so AsyncMock's
    # spec introspection makes them sync MagicMocks; force them awaitable
    message.reply_photo = AsyncMock()
    message.answer = AsyncMock()
    return message


@pytest.mark.asyncio
async def test_cmd_start():
    message = make_message()

    await cmd_start(message)

    message.reply_photo.assert_called_once()
    assert "Test User" in message.reply_photo.call_args.kwargs["caption"]


@pytest.mark.asyncio
async def test_cmd_language_offers_the_language_keyboard():
    message = make_message(user_id="test123")

    await cmd_language(message)

    message.answer.assert_called_once()
    assert message.answer.call_args[0][0] == Strings.get(
        "language_choice", Language.EN
    )
    # The three language options are presented as inline buttons
    markup = message.answer.call_args.kwargs["reply_markup"]
    callbacks = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert callbacks == ["language_en", "language_ko", "language_ru"]


@pytest.mark.asyncio
async def test_unregistered_user_falls_back_to_client_language():
    """No database row: the Telegram client locale decides the language."""
    message = make_message(user_id="no_such_user", language_code="ko")

    await cmd_language(message)

    assert message.answer.call_args[0][0] == Strings.get(
        "language_choice", Language.KO
    )


@pytest.mark.asyncio
async def test_cmd_unregister_deletes_every_trace_of_the_user(tmp_path, monkeypatch):
    """KLAS row, library row (second password + phone) and cached ID photo."""
    from app.database.database import (
        get_library_user,
        get_sent_notifications,
        get_user,
        record_sent_notifications,
        save_library_user,
        save_user,
    )
    from app.handlers import student_info
    from app.handlers.auth import cmd_unregister

    monkeypatch.setattr(student_info, "PHOTOS_DIR", tmp_path)
    await save_user("42", "2020123456", "enc", Language.EN)
    await save_library_user("42", "2020123456", "enc", "01012345678")
    await record_sent_notifications("42", [("Algorithms_homeworks_Report", "new")])
    photo = student_info.student_photo_path("42")
    photo.write_bytes(b"jpeg")

    message = make_message(user_id="42")
    message.delete = AsyncMock()
    await cmd_unregister(message)

    assert await get_user("42") is None
    assert await get_library_user("42") is None
    assert await get_sent_notifications("42") == {}
    assert not photo.exists()
    assert message.answer.call_args[0][0] == Strings.get("unregistered", Language.EN)


@pytest.mark.asyncio
async def test_cmd_unregister_works_for_a_user_with_nothing_stored(tmp_path, monkeypatch):
    from app.handlers import student_info
    from app.handlers.auth import cmd_unregister

    monkeypatch.setattr(student_info, "PHOTOS_DIR", tmp_path)
    message = make_message(user_id="nobody")
    message.delete = AsyncMock()

    await cmd_unregister(message)

    assert message.answer.call_args[0][0] == Strings.get("unregistered", Language.EN)
