import unittest.mock as mock
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from app.database.database import save_user
from app.handlers import student_info
from app.handlers.student_info import send_student_info
from app.keyboards import KLAS_RELOGIN_CALLBACK
from app.services.klas_session import KlasAccess, SessionStatus
from app.strings import Language, Strings

STUDENT_INFO = {
    "uid": "2020000001",
    "name": "Kim",
    "major": "CS",
    "grade": 3,
    "semester": 1,
    "credits": {"total": 100, "required": 130},
    "major_credits": {"total": 60, "required": 66},
    "elective_credits": {"total": 40, "required": 64},
    "average_score": "4.0",
    "credits_for_each_semester": "18",
    "major_credits_for_each_semester": "9",
}


class FakeApi:
    """Stands in for the KwangwoonUniversityApi that open_session yields."""

    def __init__(self, info=STUDENT_INFO, photo=None):
        self._info = info
        self._photo = photo
        self.photo_requests = 0

    async def get_student_info(self):
        return self._info

    async def get_student_photo(self):
        self.photo_requests += 1
        return self._photo


def patch_session(api, status=SessionStatus.ACTIVE):
    @asynccontextmanager
    async def fake_open_session(user):
        yield KlasAccess(status, api if status is SessionStatus.ACTIVE else None)

    return mock.patch.object(student_info, "open_session", fake_open_session)


@pytest.fixture(autouse=True)
def _photos_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(student_info, "PHOTOS_DIR", tmp_path / "photos")


@pytest.mark.asyncio
async def test_send_student_info_asks_unregistered_user_to_register():
    bot = AsyncMock()

    await send_student_info(bot, chat_id=42, user_id="no_such_user", user_lang=Language.EN)

    assert bot.send_message.call_args[0] == (
        42,
        Strings.get("need_to_register", Language.EN),
    )
    # A button leads straight into the registration FSM instead of leaving
    # the user to type /register themselves
    markup = bot.send_message.call_args.kwargs["reply_markup"]
    assert markup.inline_keyboard[0][0].callback_data == "account_klas"
    bot.send_photo.assert_not_called()


@pytest.mark.asyncio
async def test_missing_session_asks_for_a_login_with_a_button():
    await save_user("801", "student801", "enc", Language.EN)
    bot = AsyncMock()

    with patch_session(None, SessionStatus.NEEDS_LOGIN):
        await send_student_info(bot, chat_id=42, user_id="801", user_lang=Language.EN)

    assert bot.send_message.call_args[0] == (
        42,
        Strings.get("klas_login_required", Language.EN),
    )
    markup = bot.send_message.call_args.kwargs["reply_markup"]
    assert markup.inline_keyboard[0][0].callback_data == KLAS_RELOGIN_CALLBACK
    bot.send_photo.assert_not_called()


@pytest.mark.asyncio
async def test_unreachable_klas_says_to_try_later():
    await save_user("802", "student802", "enc", Language.EN)
    bot = AsyncMock()

    with patch_session(None, SessionStatus.UNAVAILABLE):
        await send_student_info(bot, chat_id=42, user_id="802", user_lang=Language.EN)

    bot.send_message.assert_awaited_once_with(
        42, Strings.get("klas_unavailable", Language.EN)
    )


@pytest.mark.asyncio
async def test_active_session_sends_the_profile_and_caches_the_photo():
    await save_user("803", "student803", "enc", Language.EN)
    bot = AsyncMock()
    api = FakeApi(photo=b"jpeg-bytes")

    with patch_session(api):
        await send_student_info(bot, chat_id=42, user_id="803", user_lang=Language.EN)

    assert student_info.student_photo_path("803").read_bytes() == b"jpeg-bytes"
    caption = bot.send_photo.call_args.kwargs["caption"]
    assert "Kim" in caption


@pytest.mark.asyncio
async def test_missing_student_info_reports_the_fetch_failure():
    await save_user("804", "student804", "enc", Language.EN)
    bot = AsyncMock()

    with patch_session(FakeApi(info=None)):
        await send_student_info(bot, chat_id=42, user_id="804", user_lang=Language.EN)

    bot.send_message.assert_awaited_once_with(
        42, Strings.get("failed_to_fetch_student_info", Language.EN)
    )
