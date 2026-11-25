import pytest
from sqlalchemy import select

from app.database import database as db
from app.database.database import (
    get_notification_state,
    get_sent_notifications,
    init_db,
    RecordResult,
    record_sent_notifications,
    update_notification_state,
    save_user,
    get_user,
    get_user_language,
    get_user_settings,
    set_user_language,
    update_user_settings,
)
from app.database.models import NotificationState, User, UserSettings
from app.strings import Language


@pytest.mark.asyncio
async def test_save_user(test_session):
    user_id = "test123"
    username = "testuser"
    password = "encrypted_pass"

    # Save user
    assert await save_user(user_id, username, password, Language.EN)

    # Verify user was saved
    stmt = select(User).where(User.user_id == user_id)
    result = await test_session.execute(stmt)
    user = result.scalar_one_or_none()

    assert user is not None
    assert user.username == username
    assert user.encrypted_password == password
    assert user.language == Language.EN.name


@pytest.mark.asyncio
async def test_get_user_returns_none_when_missing(test_engine):
    assert await get_user("nobody") is None


@pytest.mark.asyncio
async def test_set_user_language(test_engine):
    user_id = "lang123"

    # Create user first
    await save_user(user_id, "languser", "testpass", Language.EN)

    # Language is stored by enum name, not by its display value
    assert await set_user_language(user_id, Language.KO.name)

    language = await get_user_language(user_id)
    assert language is Language.KO


@pytest.mark.asyncio
async def test_set_user_language_fails_for_unregistered_user(test_engine):
    """The /language handler relies on this False to warn the user."""
    assert await set_user_language("never_registered", Language.RU.name) is False


@pytest.mark.asyncio
async def test_init_db_moves_existing_users_off_the_old_quiet_start(test_engine):
    """A changed column default reaches new rows only, and every user who was
    registered before it still had 23 written in theirs."""
    await update_user_settings("old_user", quiet_start=23)

    await init_db()

    assert (await get_user_settings("old_user")).quiet_start == 1


@pytest.mark.asyncio
async def test_init_db_applies_a_data_fix_only_once(test_engine):
    """Every start runs init_db. A 23 written after the fix is not the old
    default any more and must survive the next deploy."""
    await init_db()
    await update_user_settings("night_owl", quiet_start=23)

    await init_db()

    assert (await get_user_settings("night_owl")).quiet_start == 23


@pytest.mark.asyncio
async def test_a_batch_with_an_internal_duplicate_is_stored_whole(test_engine):
    """Two same-titled assignments give the same (key, kind) pair; one
    rejected row used to reject the whole batch."""
    await save_user("1", "student", "enc", Language.EN)
    batch = [("A_homeworks_Report", "new"), ("A_homeworks_Report", "new"), ("B_quizzes_Q", "t3")]

    assert await record_sent_notifications("1", batch) is RecordResult.RECORDED

    assert await get_sent_notifications("1") == {
        "A_homeworks_Report": {"new"},
        "B_quizzes_Q": {"t3"},
    }


@pytest.mark.asyncio
async def test_a_batch_with_an_already_stored_pair_is_stored_whole(test_engine):
    await save_user("1", "student", "enc", Language.EN)
    await record_sent_notifications("1", [("A_homeworks_Report", "new")])

    ok = await record_sent_notifications(
        "1", [("A_homeworks_Report", "new"), ("A_homeworks_Report", "t24")]
    )

    assert ok is RecordResult.RECORDED
    assert await get_sent_notifications("1") == {"A_homeworks_Report": {"new", "t24"}}


@pytest.mark.asyncio
async def test_nothing_is_recorded_for_an_unregistered_user(test_engine):
    """A cycle running while the user deleted their data must not bring
    assignment titles back."""
    assert (
        await record_sent_notifications("gone", [("A_homeworks_Report", "new")])
        is RecordResult.USER_GONE
    )

    assert await get_sent_notifications("gone") == {}


@pytest.mark.asyncio
async def test_state_and_settings_are_not_created_for_an_unregistered_user(test_engine):
    assert await get_notification_state("gone") is None
    assert await get_user_settings("gone") is None
    async with db.AsyncSessionLocal() as session:
        assert await session.get(NotificationState, "gone") is None
        assert await session.get(UserSettings, "gone") is None


@pytest.mark.asyncio
async def test_state_and_settings_are_created_for_a_registered_user(test_engine):
    await save_user("1", "student", "enc", Language.EN)

    assert (await get_notification_state("1")).seeded is False
    assert (await get_user_settings("1")).quiet_hours is True


@pytest.mark.asyncio
async def test_notification_state_is_not_written_for_an_unregistered_user(test_engine):
    assert await update_notification_state("gone", seeded=True) is False

    async with db.AsyncSessionLocal() as session:
        assert await session.get(NotificationState, "gone") is None


@pytest.mark.asyncio
async def test_a_large_backlog_is_recorded_in_full(test_engine):
    """A seeding cycle can carry more pairs than one compound SELECT holds."""
    await save_user("1", "student", "enc", Language.EN)
    batch = [(f"A_homeworks_Report {i}", "new") for i in range(650)]

    assert await record_sent_notifications("1", batch) is RecordResult.RECORDED

    assert len(await get_sent_notifications("1")) == 650
