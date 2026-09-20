import logging

from aiogram import Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

from app.strings import Strings
from app.keyboards import create_account_keyboard, create_account_delete_confirm_keyboard
from app.database.database import get_library_user, get_user
from app.handlers.auth import (
    LibraryRegistrationStates,
    RegistrationStates,
    delete_all_user_data,
)
from app.handlers.library import SearchStates
from app.handlers.student_info import send_student_info
from app.utils.language_utils import get_user_language_with_fallback


async def render_account_screen(user_id: str, user_lang):
    """Text + keyboard for the account screen, given the current state of
    both logins. Shared by /account and the "cancel delete" callback, which
    both need to redraw the same screen from scratch."""
    klas_user = await get_user(user_id)
    library_user = await get_library_user(user_id)

    lines = [Strings.get("account_header", user_lang), ""]
    lines.append(
        Strings.get(
            "account_klas_connected" if klas_user else "account_klas_missing",
            user_lang,
            username=klas_user.username if klas_user else None,
        )
    )
    lines.append("")
    lines.append(
        Strings.get(
            "account_library_connected" if library_user else "account_library_missing",
            user_lang,
            username=library_user.username if library_user else None,
        )
    )

    text = "\n".join(lines)
    keyboard = create_account_keyboard(
        user_lang, has_klas=bool(klas_user), has_library=bool(library_user)
    )
    return text, keyboard


async def cmd_account(message: types.Message):
    try:
        user_lang = await get_user_language_with_fallback(message)
        logging.info(f"User {message.from_user.id} used /account command")

        text, keyboard = await render_account_screen(
            str(message.from_user.id), user_lang
        )
        await message.answer(text, reply_markup=keyboard)
    except Exception as e:
        logging.error(f"Error in cmd_account: {e}")
        await message.answer(Strings.get("unexpected_error", user_lang))


async def process_account_callback(callback_query: types.CallbackQuery, state: FSMContext):
    try:
        user_lang = await get_user_language_with_fallback(callback_query)
        user_id = str(callback_query.from_user.id)

        if callback_query.data == "account_klas":
            await state.set_state(RegistrationStates.waiting_for_username)
            await callback_query.message.edit_text(Strings.get("enter_username", user_lang))

        elif callback_query.data == "account_library":
            await state.set_state(LibraryRegistrationStates.waiting_for_username)
            await callback_query.message.edit_text(
                Strings.get("library_enter_username", user_lang)
            )

        elif callback_query.data == "account_info":
            await send_student_info(
                callback_query.bot, callback_query.message.chat.id, user_id, user_lang
            )

        elif callback_query.data == "account_search":
            await state.set_state(SearchStates.waiting_for_query)
            await callback_query.message.edit_text(Strings.get("enter_book_name", user_lang))

        elif callback_query.data == "account_delete":
            await callback_query.message.edit_text(
                Strings.get("account_delete_confirm", user_lang),
                reply_markup=create_account_delete_confirm_keyboard(user_lang),
            )

        elif callback_query.data == "account_delete_yes":
            await delete_all_user_data(user_id)
            logging.info(f"User {user_id} unregistered via /account")
            await callback_query.message.edit_text(Strings.get("unregistered", user_lang))

        elif callback_query.data == "account_delete_no":
            text, keyboard = await render_account_screen(user_id, user_lang)
            await callback_query.message.edit_text(text, reply_markup=keyboard)

        await callback_query.answer()
    except Exception as e:
        logging.error(f"Error in process_account_callback: {e}")
        await callback_query.message.answer(Strings.get("unexpected_error", user_lang))
        await callback_query.answer()


def register_handlers(dp: Dispatcher):
    dp.message.register(cmd_account, Command("account"))
    # Unfiltered callbacks.register_handlers answers every callback it's
    # offered, so - like settings - this must be registered before it.
    dp.callback_query.register(
        process_account_callback, F.data.startswith("account_")
    )
