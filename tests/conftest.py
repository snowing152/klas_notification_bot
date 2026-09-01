import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from app.database import database as db
from app.database.models import Base


@pytest_asyncio.fixture(scope="function", autouse=True)
async def isolated_database(tmp_path, monkeypatch):
    """Point every test at a throwaway SQLite file, never the production one.

    autouse matters here: the functions under test (save_user, get_user, ...)
    resolve the module-level AsyncSessionLocal at call time, so a test that
    forgot to request this fixture would happily write to bot_users.db.
    """
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'test.db'}", future=True
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(db, "engine", engine)
    monkeypatch.setattr(db, "AsyncSessionLocal", session_factory)

    yield engine

    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def test_engine(isolated_database):
    """Explicit alias for tests that want to name the dependency."""
    return isolated_database


@pytest_asyncio.fixture(scope="function")
async def test_session(isolated_database):
    """Session bound to the throwaway test database."""
    async_session = sessionmaker(
        isolated_database, class_=AsyncSession, expire_on_commit=False
    )
    async with async_session() as session:
        yield session
