import logging
from aiogram.filters import Command
from aiogram import Dispatcher, F, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from app.utils.encryption import decrypt_password, encrypt_password
from app.services.qr import (
    library_login,
    get_secret_key,
    LibraryLoginError,
    LibraryLoginRejected,
)
from app.database.database import (
    delete_klas_session,
    delete_library_user,
    delete_notification_data,
    delete_user,
    delete_user_settings,
    get_klas_session,
    get_user,
    save_user,
    save_library_user,
)
from app.strings import Language, Strings
from app.keyboards import KLAS_RELOGIN_CALLBACK, create_login_klas_keyboard
from app.handlers.klas_login import LoginOutcome, run_push_login
from app.services import login_limiter
from app.services.klas_session import clear_password_rejected, mark_password_rejected
from app.utils.language_utils import get_user_language_with_fallback
from app.handlers.student_info import student_photo_path
from app.handlers.todos import forget_cached_items
from app.services.notifications import forget_user as forget_notifications


# Define states for registration
class RegistrationStates(StatesGroup):
    waiting_for_username = State()
    waiting_for_password = State()


class LibraryRegistrationStates(StatesGroup):
    # The password is deliberately the last step: it is used straight from the
    # message that carries it, so it is never kept in the FSM data.
    waiting_for_username = State()
    waiting_for_phone_number = State()
    waiting_for_password = State()


async def cmd_register(message: types.Message, state: FSMContext):
    try:
        user_lang = await get_user_language_with_fallback(message)

        await state.set_state(RegistrationStates.waiting_for_username)
        await message.answer(Strings.get("enter_username", user_lang))
        await message.delete()
    except Exception as e:
        logging.error(f"Error in cmd_register: {e}")
        await message.answer(Strings.get("unexpected_error", user_lang))
        await state.clear()


async def process_username(message: types.Message, state: FSMContext):
    try:
        user_lang = await get_user_language_with_fallback(message)

        await state.update_data(username=message.text)
        await state.set_state(RegistrationStates.waiting_for_password)
        await message.answer(Strings.get("enter_password", user_lang))
        await message.delete()
    except Exception as e:
        logging.error(f"Error in process_username: {e}")
        await message.answer(Strings.get("unexpected_error", user_lang))
        await state.clear()


async def process_password(message: types.Message, state: FSMContext):
    """Check the credentials, save them, and log in with the push approval."""
    user_lang = Language.EN
    try:
        user_lang = await get_user_language_with_fallback(message)
        username = (await state.get_data())["username"]
        password = message.text
    except Exception as e:
        logging.error(f"Error in process_password: {type(e).__name__}: {e}")
        await message.answer(Strings.get("unexpected_error", user_lang))
        return
    finally:
        # Before anything slow: the push approval can take two minutes, and
        # the password must not sit in the chat or keep the FSM waiting. The
        # state goes first: a user left in it would have their next message
        # sent to KLAS as a password.
        await state.clear()
        try:
            await message.delete()
        except Exception as e:
            logging.error(f"Could not delete a password message: {type(e).__name__}: {e}")

    user_id = str(message.from_user.id)

    if login_limiter.is_locked(username):
        # KLAS locks an account after five wrong passwords; do not let a
        # stranger's guesses at this number reach that.
        logging.warning(f"Refusing /register for {user_id}: too many failed logins")
        await message.answer(Strings.get("register_attempts_limited", user_lang))
        return

    async def save_credentials() -> bool:
        existing = await get_user(user_id)
        switched = existing is not None and existing.username != username
        if existing is None or switched:
            # A session kept for another student number must not outlive
            # the switch, whatever happens to the login that follows.
            await delete_klas_session(user_id)
        else:
            # Same student, new password: the kept session still works, and
            # re-login is allowed again now that the password is fresh.
            await clear_password_rejected(user_id)
        saved = await save_user(user_id, username, encrypt_password(password), user_lang)
        if not saved:
            logging.error(f"Could not save the KLAS credentials of {user_id}")
        elif switched:
            # Only once the new account is saved: if the save failed the old
            # account is still the registered one, and wiping its history
            # would send it every reminder again. What is cleared belongs to
            # the old student number: the notification history (it would
            # count the new account's assignments as already announced), the
            # /show cache and the ID photo.
            await delete_notification_data(user_id)
            forget_notifications(user_id)
            forget_cached_items(user_id)
            student_photo_path(user_id).unlink(missing_ok=True)
        return saved

    try:
        outcome = await run_push_login(
            message.bot,
            message.chat.id,
            user_id,
            username,
            password,
            user_lang,
            on_password_accepted=save_credentials,
        )
        if outcome is LoginOutcome.LOGGED_IN:
            await message.answer(Strings.get("registration_successful", user_lang))
            logging.info(f"User {user_id} registered successfully")
        elif outcome is LoginOutcome.WRONG_CREDENTIALS:
            # Only a password KLAS rejected counts, never a network failure.
            login_limiter.record_failure(username)
            await message.answer(Strings.get("invalid_credentials", user_lang))
        if outcome in (
            LoginOutcome.LOGGED_IN,
            LoginOutcome.TIMED_OUT,
            LoginOutcome.NO_DEVICE,
        ):
            # KLAS accepted the password before it asked for the push.
            login_limiter.reset(username)
    except Exception as e:
        logging.error(
            f"Error in process_password: {type(e).__name__}: {e}", exc_info=True
        )
        await message.answer(Strings.get("registration_failed", user_lang))


async def process_klas_relogin(callback_query: types.CallbackQuery):
    """The "Log in to KLAS" button: a new session from the saved password."""
    user_lang = Language.EN
    try:
        user_lang = await get_user_language_with_fallback(callback_query)
        await callback_query.answer()
        user_id = str(callback_query.from_user.id)
        chat_id = callback_query.message.chat.id
        bot = callback_query.bot

        user = await get_user(user_id)
        if not user:
            await bot.send_message(
                chat_id,
                Strings.get("need_to_register", user_lang),
                reply_markup=create_login_klas_keyboard(user_lang),
            )
            return

        session_row = await get_klas_session(user_id)
        if session_row is not None and session_row.password_rejected:
            # KLAS locks the account after five wrong passwords; every tap
            # would resend the one it already rejected.
            await bot.send_message(chat_id, Strings.get("credentials_expired", user_lang))
            return

        outcome = await run_push_login(
            bot,
            chat_id,
            user_id,
            user.username,
            decrypt_password(user.encrypted_password),
            user_lang,
        )
        if outcome is LoginOutcome.LOGGED_IN:
            await bot.send_message(chat_id, Strings.get("klas_login_success", user_lang))
        elif outcome is LoginOutcome.WRONG_CREDENTIALS:
            # The saved password no longer works; only /register can fix that.
            await mark_password_rejected(user_id)
            await bot.send_message(chat_id, Strings.get("credentials_expired", user_lang))
    except Exception as e:
        logging.error(
            f"Error in process_klas_relogin: {type(e).__name__}: {e}", exc_info=True
        )
        await callback_query.message.answer(Strings.get("unexpected_error", user_lang))


async def delete_all_user_data(user_id: str) -> bool:
    """"Delete my data" means all of it: the library row holds a second
    password and the phone number, the cached ID photo is a face, and the
    in-memory assignment cache is read before the database.
    Shared by /unregister and the /account screen's delete-confirm button.

    Every step runs even when an earlier one failed. Returns False when any
    of them did, so the caller can tell the user the deletion is incomplete."""
    results = [
        await delete_user(user_id),
        await delete_klas_session(user_id),
        await delete_library_user(user_id),
        await delete_notification_data(user_id),
        await delete_user_settings(user_id),
    ]
    try:
        student_photo_path(user_id).unlink(missing_ok=True)
    except OSError as e:
        logging.error(f"Could not delete the ID photo of {user_id}: {e}")
        results.append(False)
    forget_cached_items(user_id)
    forget_notifications(user_id)
    return all(results)


async def cmd_unregister(message: types.Message, state: FSMContext):
    try:
        user_lang = await get_user_language_with_fallback(message)

        user_id = str(message.from_user.id)
        # An abandoned registration step must not keep what was typed so far.
        await state.clear()
        if await delete_all_user_data(user_id):
            await message.answer(Strings.get("unregistered", user_lang))
            logging.info(f"User {message.from_user.id} unregistered")
        else:
            logging.error(f"Deleting the data of {user_id} failed")
            await message.answer(Strings.get("delete_data_failed", user_lang))
        await message.delete()
    except Exception as e:
        logging.error(f"Error in cmd_unregister: {e}")
        await message.answer(Strings.get("unexpected_error", user_lang))


async def cmd_library_register(message: types.Message, state: FSMContext):
    try:
        user_lang = await get_user_language_with_fallback(message)

        await state.set_state(LibraryRegistrationStates.waiting_for_username)
        await message.answer(Strings.get("library_enter_username", user_lang))
        await message.delete()
    except Exception as e:
        logging.error(f"Error in cmd_library_register: {e}")
        await message.answer(Strings.get("unexpected_error", user_lang))


async def process_library_username(message: types.Message, state: FSMContext):
    try:
        user_lang = await get_user_language_with_fallback(message)

        await state.update_data(username=message.text)
        await state.set_state(LibraryRegistrationStates.waiting_for_phone_number)
        await message.answer(Strings.get("enter_phone_number", user_lang))
        await message.delete()
    except Exception as e:
        logging.error(f"Error in process_library_username: {e}")
        await message.answer(Strings.get("unexpected_error", user_lang))


async def process_library_phone_number(message: types.Message, state: FSMContext):
    try:
        user_lang = await get_user_language_with_fallback(message)

        await state.update_data(phone_number=message.text)
        await state.set_state(LibraryRegistrationStates.waiting_for_password)
        await message.answer(Strings.get("library_enter_password", user_lang))
        await message.delete()
    except Exception as e:
        logging.error(f"Error in process_library_phone_number: {e}")
        await message.answer(Strings.get("unexpected_error", user_lang))


async def process_library_password(message: types.Message, state: FSMContext):
    """Last step: log in with the password straight from this message.

    The password never enters the FSM data, so an abandoned or crashed flow
    cannot leave it in storage."""
    user_lang = Language.EN
    try:
        user_lang = await get_user_language_with_fallback(message)

        user_data = await state.get_data()
        username = user_data["username"]
        phone_number = user_data["phone_number"]
        password = message.text
        user_id = str(message.from_user.id)

        encrypted_password = encrypt_password(password)
        secret = await get_secret_key("0" + username)
        try:
            auth_key = await library_login(username, phone_number, password, secret)
        except LibraryLoginRejected as e:
            # result_code=1 is KLAS's "this isn't the phone number registered
            # to this library account" - common enough (registered phone
            # changed, or a typo) to tell the user exactly what to fix rather
            # than the generic failure message.
            if e.result_code == "1":
                await message.answer(
                    Strings.get("library_login_phone_mismatch", user_lang)
                )
            else:
                await message.answer(Strings.get("library_login_failed", user_lang))
            return
        except LibraryLoginError:
            await message.answer(Strings.get("library_login_failed", user_lang))
            return

        if await save_library_user(user_id, username, encrypted_password, phone_number):
            await message.answer(
                Strings.get("library_registration_successful", user_lang)
            )
            logging.info(f"User {user_id} registered in library successfully")
        else:
            await message.answer(Strings.get("failed_to_save_credentials", user_lang))
    except Exception as e:
        logging.error(f"Error in process_library_password: {type(e).__name__}: {e}")
        await message.answer(Strings.get("unexpected_error", user_lang))
    finally:
        # State first: if the delete fails, a user left in the state would
        # have their next message taken for a password.
        await state.clear()
        try:
            await message.delete()
        except Exception as e:
            logging.error(f"Could not delete a password message: {type(e).__name__}: {e}")


def register_handlers(dp: Dispatcher):
    # Command handlers
    dp.message.register(cmd_register, Command("register"))
    dp.message.register(cmd_unregister, Command("unregister"))
    dp.message.register(cmd_library_register, Command("lregister"))
    # Before callbacks.register_handlers, whose unfiltered handler takes
    # every callback it is offered.
    dp.callback_query.register(process_klas_relogin, F.data == KLAS_RELOGIN_CALLBACK)

    # State handlers
    dp.message.register(process_username, RegistrationStates.waiting_for_username)
    dp.message.register(process_password, RegistrationStates.waiting_for_password)
    dp.message.register(
        process_library_username, LibraryRegistrationStates.waiting_for_username
    )
    dp.message.register(
        process_library_phone_number, LibraryRegistrationStates.waiting_for_phone_number
    )
    dp.message.register(
        process_library_password, LibraryRegistrationStates.waiting_for_password
    )
