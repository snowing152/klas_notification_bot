"""The KLAS session the bot keeps for each user.

KLAS asks for push approval in its phone app on every login, so the bot cannot
log in on its own any more. Instead the student approves one login, the bot
stores that session's cookies (Fernet-encrypted, like the password) and keeps
the session alive; only once KLAS has ended it does the student approve again.

Every operation that reads KLAS for a user goes through open_session(). It
never logs in: when there is no live session it says so, and the caller asks
the student to approve a new login.
"""

import json
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import Enum
from typing import AsyncIterator, Dict, Optional

from cryptography.fernet import InvalidToken

from app.database.database import get_klas_session, update_klas_session
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


@asynccontextmanager
async def open_session(user) -> AsyncIterator[KlasAccess]:
    """Open the user's kept KLAS session, extending it on the way in.

    `user` is the `users` row: its username is the student number, which the
    client needs for 토론 participation. The cookies are written back on the
    way out, since KLAS may rotate them during the operation.
    """
    user_id = user.user_id
    cookies = await _load_cookies(user_id)
    if cookies is None:
        yield KlasAccess(SessionStatus.NEEDS_LOGIN)
        return

    async with KwangwoonUniversityApi() as api:
        api.login_id = user.username
        api.restore_cookies(cookies)

        alive = await api.keep_alive()
        if alive is None:
            yield KlasAccess(SessionStatus.UNAVAILABLE)
            return
        if not alive:
            await mark_session_dead(user_id)
            yield KlasAccess(SessionStatus.NEEDS_LOGIN)
            return

        try:
            yield KlasAccess(SessionStatus.ACTIVE, api)
        finally:
            await _store_cookies(user_id, api.klas_cookies())


async def save_new_session(user_id: str, cookies: Dict) -> bool:
    """Store the session of a login the student just approved."""
    now = timezone.now()
    return await update_klas_session(
        user_id,
        encrypted_cookies=encrypt_secret(json.dumps(cookies)),
        needs_login=False,
        login_prompt_sent=False,
        logged_in_at=now,
        updated_at=now,
    )


async def mark_session_dead(user_id: str) -> None:
    """Drop a session KLAS has ended, and log how long it lived.

    How long KLAS lets a kept session live is not documented; this log line
    is how the bot finds out.
    """
    row = await get_klas_session(user_id)
    if row is not None and row.logged_in_at is not None:
        lived = timezone.now() - row.logged_in_at
        logging.info(
            f"KLAS session of {user_id} ended after "
            f"{lived.total_seconds() / 3600:.1f} hours"
        )
    else:
        logging.info(f"KLAS session of {user_id} ended")

    await update_klas_session(
        user_id,
        encrypted_cookies=None,
        needs_login=True,
        updated_at=timezone.now(),
    )


async def set_login_prompt_sent(user_id: str, sent: bool = True) -> bool:
    """Remember that the user was asked to approve a new login."""
    return await update_klas_session(user_id, login_prompt_sent=sent)


async def _load_cookies(user_id: str) -> Optional[Dict]:
    row = await get_klas_session(user_id)
    if row is None or row.needs_login or not row.encrypted_cookies:
        return None

    try:
        cookies = json.loads(decrypt_secret(row.encrypted_cookies))
    except (InvalidToken, ValueError) as e:
        # A token the current key cannot read is a session the bot cannot
        # use; asking for a new login is the only way forward.
        logging.error(f"Stored KLAS session of {user_id} is unreadable: {e!r}")
        await mark_session_dead(user_id)
        return None

    if not isinstance(cookies, dict) or not cookies:
        logging.error(f"Stored KLAS session of {user_id} holds no cookies")
        await mark_session_dead(user_id)
        return None
    return cookies


async def _store_cookies(user_id: str, cookies: Dict) -> None:
    if not cookies:
        # Nothing to keep; the next operation finds out whether it still works.
        logging.warning(f"KLAS session of {user_id} came back without cookies")
        return
    saved = await update_klas_session(
        user_id,
        encrypted_cookies=encrypt_secret(json.dumps(cookies)),
        updated_at=timezone.now(),
    )
    if not saved:
        logging.error(f"Could not store the KLAS session of {user_id}")
