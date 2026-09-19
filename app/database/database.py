import logging
from sqlalchemy import delete, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession

from app.database.models import (
    AnnouncementSeen,
    Base,
    LibraryUser,
    NotificationState,
    SentNotification,
    User,
    UserSettings,
)
from app.strings import Language
from app.config import settings

# Create an async engine
engine = create_async_engine(settings.DATABASE_URL, echo=False)

# Create an async session
AsyncSessionLocal = sessionmaker(
    bind=engine, class_=AsyncSession, expire_on_commit=False
)


async def init_db():
    """Initialize the database, creating all tables"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logging.info("Database initialized successfully")


async def save_user(user_id: str, username: str, encrypted_password: str, language: Language):
    """Save or update user in database"""
    async with AsyncSessionLocal() as session:
        async with session.begin():
            try:
                user = await session.get(User, user_id)
                if user:
                    user.username = username
                    user.encrypted_password = encrypted_password
                else:
                    user = User(
                        user_id=user_id,
                        username=username,
                        encrypted_password=encrypted_password,
                        language=language.name,
                    )
                    session.add(user)
                await session.commit()
                return True
            except SQLAlchemyError as e:
                await session.rollback()
                logging.error(f"Error saving user: {e}")
                return False


async def set_user_language(user_id: str, language: str):
    async with AsyncSessionLocal() as session:
        async with session.begin():
            user = await session.get(User, user_id)
            if user:
                user.language = language
                await session.commit()
                return True
            return False


async def get_user_language(user_id: str):
    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_id)
        if user and user.language:
            try:
                return Language[user.language]
            except (KeyError, ValueError):
                # Fallback for handling legacy language codes
                language_map = {"en": "EN", "ko": "KO", "ru": "RU"}
                if user.language in language_map:
                    return Language[language_map[user.language]]
                return Language.EN
        return None


async def get_user(user_id: str):
    """Get user from database"""
    async with AsyncSessionLocal() as session:
        try:
            user = await session.get(User, user_id)
            return user
        except SQLAlchemyError as e:
            logging.error(f"Error getting user: {e}")
            return None


async def get_all_users():
    """Get all users from database"""
    async with AsyncSessionLocal() as session:
        try:
            result = await session.execute(select(User))
            return result.scalars().all()
        except SQLAlchemyError as e:
            logging.error(f"Error getting all users: {e}")
            return []


async def delete_user(user_id: str):
    """Delete user from database"""
    async with AsyncSessionLocal() as session:
        async with session.begin():
            try:
                user = await session.get(User, user_id)
                if user:
                    await session.delete(user)
                    await session.commit()
                    return True
                return False
            except SQLAlchemyError as e:
                await session.rollback()
                logging.error(f"Error deleting user: {e}")
                return False


async def save_library_user(
    user_id: str, username: str, encrypted_password: str, phone_number: str
):
    """Save or update library user in database"""
    async with AsyncSessionLocal() as session:
        async with session.begin():
            try:
                user = await session.get(LibraryUser, user_id)
                if user:
                    user.username = username
                    user.encrypted_password = encrypted_password
                    user.phone_number = phone_number
                else:
                    user = LibraryUser(
                        user_id=user_id,
                        username=username,
                        encrypted_password=encrypted_password,
                        phone_number=phone_number,
                    )
                    session.add(user)
                await session.commit()
                return True
            except SQLAlchemyError as e:
                await session.rollback()
                logging.error(f"Error saving library user: {e}")
                return False


async def get_library_user(user_id: str):
    """Get library user from database"""
    async with AsyncSessionLocal() as session:
        try:
            user = await session.get(LibraryUser, user_id)
            return user
        except SQLAlchemyError as e:
            logging.error(f"Error getting library user: {e}")
            return None


async def get_all_library_users():
    """Get all library users from database"""
    async with AsyncSessionLocal() as session:
        try:
            result = await session.execute(select(LibraryUser))
            return result.scalars().all()
        except SQLAlchemyError as e:
            logging.error(f"Error getting all library users: {e}")
            return []


async def delete_library_user(user_id: str):
    """Delete library user from database"""
    async with AsyncSessionLocal() as session:
        async with session.begin():
            try:
                user = await session.get(LibraryUser, user_id)
                if user:
                    await session.delete(user)
                    await session.commit()
                    return True
                return False
            except SQLAlchemyError as e:
                await session.rollback()
                logging.error(f"Error deleting library user: {e}")
                return False


async def get_sent_notifications(user_id: str) -> dict[str, set[str]]:
    """Every notification already delivered to this user, keyed by assignment."""
    async with AsyncSessionLocal() as session:
        try:
            result = await session.execute(
                select(SentNotification).where(SentNotification.user_id == user_id)
            )
            sent: dict[str, set[str]] = {}
            for row in result.scalars():
                sent.setdefault(row.assignment_key, set()).add(row.kind)
            return sent
        except SQLAlchemyError as e:
            logging.error(f"Error getting sent notifications: {e}")
            # An empty dict would look like "nothing was ever sent" and repeat
            # every notification; None tells the caller to skip this user.
            return None


async def record_sent_notifications(user_id: str, entries) -> bool:
    """Mark (assignment_key, kind) pairs as delivered. Called after a send."""
    if not entries:
        return True

    async with AsyncSessionLocal() as session:
        async with session.begin():
            try:
                session.add_all(
                    SentNotification(
                        user_id=user_id, assignment_key=key, kind=kind
                    )
                    for key, kind in entries
                )
                await session.commit()
                return True
            except SQLAlchemyError as e:
                await session.rollback()
                logging.error(f"Error recording sent notifications: {e}")
                return False


async def prune_sent_notifications(user_id: str, current_keys) -> bool:
    """Drop rows for assignments KLAS no longer lists (submitted or expired)."""
    async with AsyncSessionLocal() as session:
        async with session.begin():
            try:
                statement = delete(SentNotification).where(
                    SentNotification.user_id == user_id
                )
                if current_keys:
                    statement = statement.where(
                        SentNotification.assignment_key.not_in(list(current_keys))
                    )
                await session.execute(statement)
                await session.commit()
                return True
            except SQLAlchemyError as e:
                await session.rollback()
                logging.error(f"Error pruning sent notifications: {e}")
                return False


async def get_notification_state(user_id: str):
    """The user's notification bookkeeping row, created on first use."""
    async with AsyncSessionLocal() as session:
        async with session.begin():
            try:
                state = await session.get(NotificationState, user_id)
                if state is None:
                    state = NotificationState(
                        user_id=user_id,
                        seeded=False,
                        login_failures=0,
                        credentials_warned=False,
                    )
                    session.add(state)
                    await session.commit()
                return state
            except SQLAlchemyError as e:
                await session.rollback()
                logging.error(f"Error getting notification state: {e}")
                return None


async def update_notification_state(user_id: str, **fields) -> bool:
    """Set columns on the user's notification state row, creating it if needed."""
    async with AsyncSessionLocal() as session:
        async with session.begin():
            try:
                state = await session.get(NotificationState, user_id)
                if state is None:
                    state = NotificationState(user_id=user_id)
                    session.add(state)
                for name, value in fields.items():
                    setattr(state, name, value)
                await session.commit()
                return True
            except SQLAlchemyError as e:
                await session.rollback()
                logging.error(f"Error updating notification state: {e}")
                return False


async def delete_notification_data(user_id: str) -> bool:
    """Forget a user's notification history - part of /unregister."""
    async with AsyncSessionLocal() as session:
        async with session.begin():
            try:
                await session.execute(
                    delete(SentNotification).where(
                        SentNotification.user_id == user_id
                    )
                )
                await session.execute(
                    delete(NotificationState).where(
                        NotificationState.user_id == user_id
                    )
                )
                await session.commit()
                return True
            except SQLAlchemyError as e:
                await session.rollback()
                logging.error(f"Error deleting notification data: {e}")
                return False


async def get_user_settings(user_id: str):
    """The user's notification preferences, created with defaults on first use."""
    async with AsyncSessionLocal() as session:
        async with session.begin():
            try:
                settings_row = await session.get(UserSettings, user_id)
                if settings_row is None:
                    settings_row = UserSettings(user_id=user_id)
                    session.add(settings_row)
                    await session.commit()
                return settings_row
            except SQLAlchemyError as e:
                await session.rollback()
                logging.error(f"Error getting user settings: {e}")
                return None


async def update_user_settings(user_id: str, **fields) -> bool:
    async with AsyncSessionLocal() as session:
        async with session.begin():
            try:
                settings_row = await session.get(UserSettings, user_id)
                if settings_row is None:
                    settings_row = UserSettings(user_id=user_id)
                    session.add(settings_row)
                for name, value in fields.items():
                    setattr(settings_row, name, value)
                await session.commit()
                return True
            except SQLAlchemyError as e:
                await session.rollback()
                logging.error(f"Error updating user settings: {e}")
                return False


async def has_seen_announcement(user_id: str, key: str) -> bool:
    async with AsyncSessionLocal() as session:
        try:
            seen = await session.get(AnnouncementSeen, {"user_id": user_id, "key": key})
            return seen is not None
        except SQLAlchemyError as e:
            logging.error(f"Error reading announcement state: {e}")
            # Treat an unreadable row as seen: a repeated announcement is worse
            # than a missed one.
            return True


async def mark_announcement_seen(user_id: str, key: str) -> bool:
    async with AsyncSessionLocal() as session:
        async with session.begin():
            try:
                session.add(AnnouncementSeen(user_id=user_id, key=key))
                await session.commit()
                return True
            except SQLAlchemyError as e:
                await session.rollback()
                logging.error(f"Error marking announcement as seen: {e}")
                return False


async def delete_user_settings(user_id: str) -> bool:
    """Forget a user's preferences and announcement history - part of /unregister."""
    async with AsyncSessionLocal() as session:
        async with session.begin():
            try:
                await session.execute(
                    delete(UserSettings).where(UserSettings.user_id == user_id)
                )
                await session.execute(
                    delete(AnnouncementSeen).where(AnnouncementSeen.user_id == user_id)
                )
                await session.commit()
                return True
            except SQLAlchemyError as e:
                await session.rollback()
                logging.error(f"Error deleting user settings: {e}")
                return False
