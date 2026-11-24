"""A KLAS login the student approves on their phone, run from a chat.

KLAS often asks for push approval in its app (docs/KLAS.md). This
module runs that flow for one user and tells them in the chat what to do and
how it went. Only a user action starts it - /register or the "Log in to KLAS"
button - never the notification loop: a push nobody asked for every half
hour would be spam, and would teach students to approve pushes they did not
start.

On success the session is stored with klas_session.save_new_session(), and
every later operation reuses it through klas_session.open_session().
"""

import logging
from enum import Enum
from typing import Awaitable, Callable, Optional

from app.database.database import get_user
from app.keyboards import create_klas_relogin_keyboard
from app.services import klas_session
from app.services.kw import KwangwoonUniversityApi, LoginStatus, PushStatus
from app.strings import Language, Strings


class LoginOutcome(Enum):
    """How a push login ended, for the caller's own reply."""

    LOGGED_IN = "logged_in"
    WRONG_CREDENTIALS = "wrong_credentials"
    NO_DEVICE = "no_device"
    TIMED_OUT = "timed_out"
    ALREADY_RUNNING = "already_running"
    FAILED = "failed"


# Telegram user ids with a login waiting for approval. A second tap would send
# a second push, and the two would race for the same KLAS account.
_logins_in_progress: set[str] = set()


async def run_push_login(
    bot,
    chat_id: int,
    user_id: str,
    login_id: str,
    password: str,
    user_lang: Language,
    on_password_accepted: Optional[Callable[[], Awaitable[bool]]] = None,
) -> LoginOutcome:
    """Log in to KLAS with the student's approval and store the session.

    Tells the user to approve the push and, when something goes wrong, why
    and what to do; a successful login is left to the caller to announce,
    because /register and the re-login button say different things.

    `on_password_accepted` runs once KLAS has accepted the password and
    before the push is sent; /register saves the credentials there, so a
    push that times out can be retried with the button instead of typing
    them again. If it answers False the login stops with FAILED.
    """
    if user_id in _logins_in_progress:
        await bot.send_message(chat_id, Strings.get("klas_login_in_progress", user_lang))
        return LoginOutcome.ALREADY_RUNNING

    _logins_in_progress.add(user_id)
    try:
        outcome = await _push_login(
            bot, chat_id, user_id, login_id, password, user_lang, on_password_accepted
        )
    finally:
        _logins_in_progress.discard(user_id)

    logging.info(f"KLAS push login for {user_id}: {outcome.value}")
    await _tell_outcome(bot, chat_id, outcome, user_lang)
    return outcome


async def _push_login(
    bot, chat_id, user_id, login_id, password, user_lang, on_password_accepted
) -> LoginOutcome:
    async with KwangwoonUniversityApi() as api:
        result = await api.attempt_login(login_id, password)
        if result.status is LoginStatus.FAILED:
            return LoginOutcome.WRONG_CREDENTIALS
        if result.status is LoginStatus.UNAVAILABLE:
            return LoginOutcome.FAILED

        if on_password_accepted is not None and not await on_password_accepted():
            return LoginOutcome.FAILED

        if result.status is LoginStatus.TWO_FACTOR_REQUIRED:
            push = await api.request_push()
            if push.status is PushStatus.NO_DEVICE:
                return LoginOutcome.NO_DEVICE
            if push.status is not PushStatus.SENT:
                return LoginOutcome.FAILED

            await bot.send_message(chat_id, Strings.get("klas_push_sent", user_lang))
            result = await api.wait_for_push_approval(password, push)
            if result.status is LoginStatus.TWO_FACTOR_REQUIRED:
                return LoginOutcome.TIMED_OUT
            if result.status is not LoginStatus.OK:
                return LoginOutcome.FAILED

        if await get_user(user_id) is None:
            # Deleted their data while the push was waiting: storing the
            # session now would leave it behind with nothing to clear it.
            logging.info(f"User {user_id} unregistered during a KLAS login")
            return LoginOutcome.FAILED
        if not await klas_session.save_new_session(user_id, api.klas_cookies()):
            logging.error(f"Could not store the new KLAS session of {user_id}")
            return LoginOutcome.FAILED
        return LoginOutcome.LOGGED_IN


async def _tell_outcome(bot, chat_id, outcome: LoginOutcome, user_lang) -> None:
    """Explain a login that did not finish. Success is the caller's to announce."""
    if outcome is LoginOutcome.TIMED_OUT:
        await bot.send_message(
            chat_id,
            Strings.get("klas_push_timeout", user_lang),
            reply_markup=create_klas_relogin_keyboard(user_lang),
        )
    elif outcome is LoginOutcome.NO_DEVICE:
        await bot.send_message(
            chat_id,
            Strings.get("klas_no_push_device", user_lang),
            reply_markup=create_klas_relogin_keyboard(user_lang),
        )
    elif outcome is LoginOutcome.FAILED:
        await bot.send_message(chat_id, Strings.get("klas_login_failed", user_lang))
