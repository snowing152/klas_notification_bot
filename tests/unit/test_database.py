import pytest
from sqlalchemy import select

from app.database.database import (
    save_user,
    get_user,
    get_user_language,
    set_user_language,
)
from app.database.models import User
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
