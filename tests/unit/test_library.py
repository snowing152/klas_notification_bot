import pytest
from unittest.mock import AsyncMock
from aiogram import types
from aiogram.dispatcher.event.bases import SkipHandler

from app.handlers.library import cancel_search_on_command, cmd_qr


def make_message(user_id="800", language_code="en"):
    from_user = AsyncMock()
    from_user.id = user_id
    from_user.language_code = language_code

    message = AsyncMock(spec=types.Message)
    message.from_user = from_user
    message.answer = AsyncMock()
    return message


@pytest.mark.asyncio
async def test_a_command_at_the_book_prompt_clears_the_state_and_skips():
    """Commands are registered ahead of the search state, so without this the
    state outlives the prompt and the user's next ordinary message is read as
    a book title."""
    state = AsyncMock()

    with pytest.raises(SkipHandler):
        await cancel_search_on_command(make_message(), state)

    state.clear.assert_awaited_once()


@pytest.mark.asyncio
async def test_qr_without_a_library_account_gets_a_login_button_not_just_text():
    message = make_message(user_id="no_such_library_user")

    await cmd_qr(message)

    markup = message.answer.call_args.kwargs["reply_markup"]
    assert markup.inline_keyboard[0][0].callback_data == "account_library"
