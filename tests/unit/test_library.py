import pytest
from unittest.mock import AsyncMock
from aiogram import types

from app.handlers.library import cmd_qr


def make_message(user_id="800", language_code="en"):
    from_user = AsyncMock()
    from_user.id = user_id
    from_user.language_code = language_code

    message = AsyncMock(spec=types.Message)
    message.from_user = from_user
    message.answer = AsyncMock()
    return message


@pytest.mark.asyncio
async def test_qr_without_a_library_account_gets_a_login_button_not_just_text():
    message = make_message(user_id="no_such_library_user")

    await cmd_qr(message)

    markup = message.answer.call_args.kwargs["reply_markup"]
    assert markup.inline_keyboard[0][0].callback_data == "account_library"
