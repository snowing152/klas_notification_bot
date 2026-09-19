import pytest
from unittest.mock import AsyncMock

from app.handlers.student_info import send_student_info
from app.strings import Language, Strings


@pytest.mark.asyncio
async def test_send_student_info_asks_unregistered_user_to_register():
    bot = AsyncMock()

    await send_student_info(bot, chat_id=42, user_id="no_such_user", user_lang=Language.EN)

    bot.send_message.assert_awaited_once_with(
        42, Strings.get("need_to_register", Language.EN)
    )
    bot.send_photo.assert_not_called()
