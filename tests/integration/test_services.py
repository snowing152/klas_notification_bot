import datetime

import pytest
from unittest.mock import patch
from app.services.notifications import send_notification
from app.services.kw import KwangwoonUniversityApi
from app.strings import Language


NOW = datetime.datetime(2026, 9, 20, 12, 0)


def _items(title="Test assignment"):
    return [
        {
            "type": "homeworks",
            "subject": "Algorithms",
            "title": title,
            "expire_at": NOW + datetime.timedelta(minutes=45),
            "entry": ("Algorithms_homeworks_" + title, "t1"),
        }
    ]


@pytest.mark.asyncio
async def test_send_notification():
    with patch("app.bot.bot.send_message") as mock_send, patch(
        "app.services.notifications._record_sent"
    ):
        sent = await send_notification(_items(), "test_user", 1, now=NOW)
        assert sent == 1
        mock_send.assert_called_once()
        kwargs = mock_send.call_args[1]
        assert "Less than 1h left" in kwargs["text"]
        assert "Test assignment" in kwargs["text"]
        assert kwargs["parse_mode"] == "HTML"


@pytest.mark.asyncio
async def test_send_notification_uses_the_users_language():
    with patch("app.bot.bot.send_message") as mock_send, patch(
        "app.services.notifications._record_sent"
    ):
        await send_notification(_items(), "test_user", 3, Language.KO, NOW)
        text = mock_send.call_args[1]["text"]

    assert "마감까지 3시간 미만" in text


@pytest.mark.asyncio
async def test_kw_api():
    async with KwangwoonUniversityApi() as api:
        with patch.object(api, "login", return_value=True):
            result = await api.login("test_user", "test_pass")
            assert result is True
