import pytest

from app.database.database import (
    has_seen_announcement,
    mark_announcement_seen,
    save_user,
)
from app.services import announcements
from app.strings import Language


class FakeBot:
    def __init__(self, fail_for=None):
        self.sent = []
        self.fail_for = fail_for

    async def send_message(self, chat_id, text, reply_markup=None):
        if chat_id == self.fail_for:
            raise RuntimeError("blocked by the user")
        self.sent.append((chat_id, text, reply_markup))


@pytest.fixture
def entry(monkeypatch):
    monkeypatch.setitem(
        announcements.ANNOUNCEMENTS,
        "urgent_v1",
        {"text_key": "settings_header", "setting": "urgent_thresholds_only"},
    )
    monkeypatch.setattr(announcements, "SEND_DELAY_SECONDS", 0)


@pytest.mark.asyncio
async def test_nothing_is_sent_without_announcements(monkeypatch):
    monkeypatch.setattr(announcements, "ANNOUNCEMENTS", {})
    bot = FakeBot()
    monkeypatch.setattr(announcements, "bot", bot)

    assert await announcements.announce_pending() == 0
    assert bot.sent == []


@pytest.mark.asyncio
async def test_every_user_hears_about_a_feature_once(monkeypatch, entry):
    bot = FakeBot()
    monkeypatch.setattr(announcements, "bot", bot)
    await save_user("1", "2020000001", "enc", Language.EN)
    await save_user("2", "2020000002", "enc", Language.RU)

    assert await announcements.announce_pending() == 2
    assert sorted(chat_id for chat_id, _, _ in bot.sent) == ["1", "2"]
    assert bot.sent[0][2] is not None, "an opt-in feature needs its buttons"

    # A redeploy runs this again and must stay quiet
    assert await announcements.announce_pending() == 0
    assert len(bot.sent) == 2


@pytest.mark.asyncio
async def test_a_user_who_already_saw_it_is_skipped(monkeypatch, entry):
    bot = FakeBot()
    monkeypatch.setattr(announcements, "bot", bot)
    await save_user("1", "2020000001", "enc", Language.EN)
    await mark_announcement_seen("1", "urgent_v1")

    assert await announcements.announce_pending() == 0


@pytest.mark.asyncio
async def test_a_failed_send_is_retried_next_start(monkeypatch, entry):
    """A user who was offline, or who blocked and unblocked the bot."""
    bot = FakeBot(fail_for="1")
    monkeypatch.setattr(announcements, "bot", bot)
    await save_user("1", "2020000001", "enc", Language.EN)
    await save_user("2", "2020000002", "enc", Language.EN)

    assert await announcements.announce_pending() == 1
    assert await has_seen_announcement("1", "urgent_v1") is False

    working = FakeBot()
    monkeypatch.setattr(announcements, "bot", working)
    assert await announcements.announce_pending() == 1
    assert [chat_id for chat_id, _, _ in working.sent] == ["1"]
