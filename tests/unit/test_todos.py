import pytest
from unittest.mock import AsyncMock
from aiogram import types

from app.handlers.todos import show_all_assignments


def make_message(user_id="700", language_code="en"):
    from_user = AsyncMock()
    from_user.id = user_id
    from_user.language_code = language_code

    message = AsyncMock(spec=types.Message)
    message.from_user = from_user
    message.answer = AsyncMock()
    return message


@pytest.mark.asyncio
async def test_unregistered_user_gets_a_login_button_not_just_text():
    message = make_message(user_id="no_such_user")

    await show_all_assignments(message)

    markup = message.answer.call_args.kwargs["reply_markup"]
    assert markup.inline_keyboard[0][0].callback_data == "account_klas"
