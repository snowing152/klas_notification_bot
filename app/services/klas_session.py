"""The KLAS session the bot keeps for each user.

KLAS asks for push approval in its phone app on every login, so the bot cannot
log in on its own any more. Instead the student approves one login, the bot
stores that session's cookies (Fernet-encrypted, like the password) and keeps
the session alive; only once KLAS has ended it does the student approve again.

Every operation that reads KLAS for a user goes through open_session(). It
never logs in: when there is no live session it says so, and the caller asks
the student to approve a new login.
"""

import datetime
import json
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import Enum
from typing import AsyncIterator, Dict, Optional

from cryptography.fernet import InvalidToken

from app.database.database import (
    get_klas_session,
    update_current_klas_session,
    update_klas_session,
)
from app.services.kw import KwangwoonUniversityApi
from app.utils import timezone
from app.utils.encryption import decrypt_secret, encrypt_secret


class SessionStatus(Enum):
    """Whether a user's kept KLAS session can be used right now."""

    ACTIVE = "active"
    # No session, or KLAS ended it: the student has to approve a new login.
    NEEDS_LOGIN = "needs_login"
    # KLAS could not be reached; the session may well still be alive.
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class KlasAccess:
    """What open_session() yields. `api` is set only for SessionStatus.ACTIVE."""

    status: SessionStatus
    api: Optional[KwangwoonUniversityApi] = None


@dataclass(frozen=True)
class _StoredSession:
    cookies: Dict
    # Identifies the login these cookies belong to; every write made at the
    # end of an operation is conditional on it (see _store_cookies).
    logged_in_at: Optional[datetime.datetime]


@asynccontextmanager
async def open_session(user) -> AsyncIterator[KlasAccess]:
    """Open the user's kept KLAS session, extending it on the way in.

    `user` is the `users` row: its username is the student number, which the
    client needs for 토론 participation. The cookies are written back on the
    way out, since KLAS may rotate them during the operation.
    """
    user_id = user.user_id
    stored = await _load_session(user_id)
    if stored is None:
        yield KlasAccess(SessionStatus.NEEDS_LOGIN)
        return

    async with KwangwoonUniversityApi() as api:
        api.login_id = user.username
        api.restore_cookies(stored.cookies)

        alive = await api.keep_alive()
        if alive is None:
            yield KlasAccess(SessionStatus.UNAVAILABLE)
            return
        if not alive:
            await mark_session_dead(user_id, stored.logged_in_at)
            yield KlasAccess(SessionStatus.NEEDS_LOGIN)
            return

        try:
            yield KlasAccess(SessionStatus.ACTIVE, api)
        finally:
            await _store_cookies(user_id, stored.logged_in_at, api.klas_cookies())


async def save_new_session(user_id: str, cookies: Dict) -> bool:
    """Store the session of a login the student just approved."""
    now = timezone.now()
    return await update_klas_session(
        user_id,
        encrypted_cookies=encrypt_secret(json.dumps(cookies)),
        needs_login=False,
        login_prompt_sent=False,
        password_rejected=False,
        logged_in_at=now,
        updated_at=now,
    )


async def mark_session_dead(
    user_id: str, logged_in_at: Optional[datetime.datetime]
) -> None:
    """Drop a session KLAS has ended, and log how long it lived.

    Only the login `logged_in_at` names is dropped: a newer one the student
    approved in the meantime, or a row deleted by /unregister, is left alone.
    How long KLAS lets a kept session live is not documented; this log line
    is how the bot finds out.
    """
    dropped = await update_current_klas_session(
        user_id,
        logged_in_at,
        encrypted_cookies=None,
        needs_login=True,
        updated_at=timezone.now(),
    )
    if not dropped:
        return
    if logged_in_at is not None:
        lived = timezone.now() - logged_in_at
        logging.info(
            f"KLAS session of {user_id} ended after "
            f"{lived.total_seconds() / 3600:.1f} hours"
        )
    else:
        logging.info(f"KLAS session of {user_id} ended")


async def set_login_prompt_sent(user_id: str, sent: bool = True) -> bool:
    """Remember that the user was asked to approve a new login."""
    return await update_klas_session(user_id, login_prompt_sent=sent)


async def mark_password_rejected(user_id: str) -> bool:
    """Remember that KLAS rejected the saved password on a re-login."""
    return await update_klas_session(user_id, password_rejected=True)


async def clear_password_rejected(user_id: str) -> bool:
    """Allow re-login again: /register saved a password KLAS accepted."""
    return await update_klas_session(user_id, password_rejected=False)


async def _load_session(user_id: str) -> Optional[_StoredSession]:
    row = await get_klas_session(user_id)
    if row is None or row.needs_login or not row.encrypted_cookies:
        return None

    try:
        cookies = json.loads(decrypt_secret(row.encrypted_cookies))
    except (InvalidToken, ValueError) as e:
        # A token the current key cannot read is a session the bot cannot
        # use; asking for a new login is the only way forward.
        logging.error(
            f"Stored KLAS session of {user_id} is unreadable: {type(e).__name__}"
        )
        await mark_session_dead(user_id, row.logged_in_at)
        return None

    if not isinstance(cookies, dict) or not cookies:
        logging.error(f"Stored KLAS session of {user_id} holds no cookies")
        await mark_session_dead(user_id, row.logged_in_at)
        return None
    return _StoredSession(cookies, row.logged_in_at)


async def _store_cookies(
    user_id: str, logged_in_at: Optional[datetime.datetime], cookies: Dict
) -> None:
    if not cookies:
        # Nothing to keep; the next operation finds out whether it still works.
        logging.warning(f"KLAS session of {user_id} came back without cookies")
        return
    stored = await update_current_klas_session(
        user_id,
        logged_in_at,
        encrypted_cookies=encrypt_secret(json.dumps(cookies)),
        updated_at=timezone.now(),
    )
    if not stored:
        # Deleted, ended, or replaced by a newer login while this operation
        # ran; writing these cookies back would undo that.
        logging.info(f"Did not write back the KLAS session of {user_id}: it changed")
