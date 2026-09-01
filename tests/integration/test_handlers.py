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
