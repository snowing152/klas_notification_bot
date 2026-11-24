"""The KLAS session the bot keeps for each user.

KLAS often asks for push approval in its phone app when the bot logs in (it
seems to depend on where the login comes from), so the bot cannot count on
logging in on its own. Instead it stores the cookies of a session that works
(Fernet-encrypted, like the password) and keeps that session alive.

Every operation that reads KLAS for a user goes through open_session(). When
there is no live session it tries one silent login with the saved password,
which never sends a push: if KLAS lets it in, nobody notices. If KLAS wants the
push, it says so and the caller asks the student to approve a login, which only
a tap by the student starts (app/handlers/klas_login.py).
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
from app.services.kw import KwangwoonUniversityApi, LoginStatus
from app.utils import timezone
from app.utils.encryption import decrypt_password, decrypt_secret, encrypt_secret


class SessionStatus(Enum):
    """Whether a user's kept KLAS session can be used right now."""

    ACTIVE = "active"
    # No session, and KLAS wants push approval for a new one: the student has
    # to approve a login.
    NEEDS_LOGIN = "needs_login"
    # KLAS rejected the saved password; only /register can fix that.
    PASSWORD_REJECTED = "password_rejected"
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
    client needs for discussion participation, and its password is what a
    silent login sends. The cookies are written back on the way out, since
    KLAS may rotate them during the operation.
    """
    user_id = user.user_id
    stored = await _load_session(user_id)

    async with KwangwoonUniversityApi() as api:
        api.login_id = user.username
        logged_in_at = None

        if stored is not None:
            api.restore_cookies(stored.cookies)
            alive = await api.keep_alive()
            if alive is None:
                yield KlasAccess(SessionStatus.UNAVAILABLE)
                return
            if alive:
                logged_in_at = stored.logged_in_at
            elif await mark_session_dead(user_id, stored.logged_in_at):
                api.forget_cookies()
            else:
                # Replaced by a newer login while this one was checked: a
                # silent login now could undo it. The next operation uses it.
                yield KlasAccess(SessionStatus.UNAVAILABLE)
                return

        if logged_in_at is None:
            status, logged_in_at = await _silent_login(user, api)
            if status is not SessionStatus.ACTIVE:
                yield KlasAccess(status)
                return

        try:
            yield KlasAccess(SessionStatus.ACTIVE, api)
        finally:
            await _store_cookies(user_id, logged_in_at, api.klas_cookies())


async def save_new_session(user_id: str, cookies: Dict) -> bool:
    """Store the session of a login that just succeeded."""
    return await _save_session(user_id, cookies) is not None


async def _save_session(user_id: str, cookies: Dict) -> Optional[datetime.datetime]:
    """Store a new session; answers its logged_in_at, or None if not stored."""
    now = timezone.now()
    saved = await update_klas_session(
        user_id,
        encrypted_cookies=encrypt_secret(json.dumps(cookies)),
        needs_login=False,
        login_prompt_sent=False,
        password_rejected=False,
        two_factor_required=False,
        logged_in_at=now,
        updated_at=now,
    )
    return now if saved else None


async def _silent_login(
    user, api: KwangwoonUniversityApi
) -> tuple[SessionStatus, Optional[datetime.datetime]]:
    """Log in with the saved password, without a push, if KLAS allows it.

    Tried once per lost session: after KLAS has asked for the push, or has
    rejected the password, the flags stop further tries until the student
    logs in or re-registers. Otherwise every 30-minute cycle would send the
    password again - and a rejected one, five times, locks the account.
    """
    user_id = user.user_id
    row = await get_klas_session(user_id)
    if row is not None and row.password_rejected:
        return SessionStatus.PASSWORD_REJECTED, None
    if row is not None and row.two_factor_required:
        return SessionStatus.NEEDS_LOGIN, None

    try:
        password = decrypt_password(user.encrypted_password)
    except (InvalidToken, ValueError) as e:
        logging.error(
            f"Saved KLAS password of {user_id} is unreadable: {type(e).__name__}"
        )
        return SessionStatus.NEEDS_LOGIN, None

    result = await api.attempt_login(user.username, password)
    if result.status is LoginStatus.OK:
        logged_in_at = await _save_session(user_id, api.klas_cookies())
        if logged_in_at is None:
            logging.error(f"Could not store the KLAS session of {user_id}")
            return SessionStatus.UNAVAILABLE, None
        logging.info(f"Logged {user_id} in to KLAS without a push")
        return SessionStatus.ACTIVE, logged_in_at
    if result.status is LoginStatus.TWO_FACTOR_REQUIRED:
        current = await get_klas_session(user_id)
        if current is not None and current.encrypted_cookies and not current.needs_login:
            # The student approved a login while this one ran; keep theirs.
            return SessionStatus.UNAVAILABLE, None
        await update_klas_session(user_id, needs_login=True, two_factor_required=True)
        logging.info(f"KLAS wants push approval to log {user_id} in")
        return SessionStatus.NEEDS_LOGIN, None
    if result.status is LoginStatus.FAILED:
        await mark_password_rejected(user_id)
        logging.warning(f"KLAS rejected the saved password of {user_id}")
        return SessionStatus.PASSWORD_REJECTED, None
    return SessionStatus.UNAVAILABLE, None


async def mark_session_dead(
    user_id: str, logged_in_at: Optional[datetime.datetime]
) -> bool:
    """Drop a session KLAS has ended, and log how long it lived.

    Only the login `logged_in_at` names is dropped: a newer one the student
    approved in the meantime, or a row deleted by /unregister, is left alone.
    How long KLAS lets a kept session live is not documented; this log line
    is how the bot finds out. Answers whether the session was dropped.
    """
    dropped = await update_current_klas_session(
        user_id,
        logged_in_at,
        encrypted_cookies=None,
        needs_login=True,
        updated_at=timezone.now(),
    )
    if not dropped:
        return False
    if logged_in_at is not None:
        lived = timezone.now() - logged_in_at
        logging.info(
            f"KLAS session of {user_id} ended after "
            f"{lived.total_seconds() / 3600:.1f} hours"
        )
    else:
        logging.info(f"KLAS session of {user_id} ended")
    return True


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
