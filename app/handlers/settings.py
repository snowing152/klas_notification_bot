import logging

from aiogram import Dispatcher, F, types
from aiogram.filters import Command
from aiogram.exceptions import TelegramBadRequest

from app.strings import Strings
from app.keyboards import create_settings_keyboard, create_language_keyboard
from app.database.database import get_user_settings, update_user_settings
from app.services.announcements import ANNOUNCEMENTS
from app.utils.language_utils import get_user_language_with_fallback


# Callback data -> the column it flips. Every entry is a plain on/off switch,
# which is why one handler covers the whole screen.
TOGGLES = {
    "settings_new": "new_assignment_alerts",
    "settings_deadline": "deadline_alerts",
    "settings_urgent": "urgent_thresholds_only",
    "settings_quiet": "quiet_hours",
}


async def cmd_settings(message: types.Message):
    try:
        user_lang = await get_user_language_with_fallback(message)
        logging.info(f"User {message.from_user.id} used /settings command")

        settings_row = await get_user_settings(str(message.from_user.id))
        if settings_row is None:
            await message.answer(Strings.get("unexpected_error", user_lang))
            return

        await message.answer(
            Strings.get("settings_header", user_lang),
            reply_markup=create_settings_keyboard(user_lang, settings_row),
        )
    except Exception as e:
        logging.error(f"Error in cmd_settings: {e}")
        await message.answer(Strings.get("unexpected_error", user_lang))


async def process_settings_callback(callback_query: types.CallbackQuery):
    try:
        user_lang = await get_user_language_with_fallback(callback_query)
        user_id = str(callback_query.from_user.id)

        if callback_query.data == "settings_language":
            await callback_query.message.edit_text(
                Strings.get("language_choice", user_lang),
                reply_markup=create_language_keyboard(),
            )
            await callback_query.answer()
            return

        field = TOGGLES.get(callback_query.data)
        if field is None:
            await callback_query.answer()
            return

        settings_row = await get_user_settings(user_id)
        if settings_row is None:
            await callback_query.answer()
            return

        await update_user_settings(user_id, **{field: not getattr(settings_row, field)})
        settings_row = await get_user_settings(user_id)

        try:
            await callback_query.message.edit_text(
                Strings.get("settings_header", user_lang),
                reply_markup=create_settings_keyboard(user_lang, settings_row),
            )
        except TelegramBadRequest:
            # Telegram rejects an edit that changes nothing; the keyboard did
            # change here, so this only fires on a double tap.
            pass

        await callback_query.answer()
    except Exception as e:
        logging.error(f"Error in process_settings_callback: {e}")
        await callback_query.answer()


async def process_announcement_callback(callback_query: types.CallbackQuery):
    """The two buttons under a feature announcement."""
    try:
        user_lang = await get_user_language_with_fallback(callback_query)
        data = callback_query.data

        if data.startswith("announce_on_"):
            key, enable = data.removeprefix("announce_on_"), True
        elif data.startswith("announce_off_"):
            key, enable = data.removeprefix("announce_off_"), False
        else:
            await callback_query.answer()
            return

        setting = ANNOUNCEMENTS.get(key, {}).get("setting")
        if setting:
            await update_user_settings(
                str(callback_query.from_user.id), **{setting: enable}
            )

        await callback_query.message.edit_text(
            Strings.get(
                "announcement_enabled" if enable else "announcement_dismissed",
                user_lang,
            )
        )
        await callback_query.answer()
    except Exception as e:
        logging.error(f"Error in process_announcement_callback: {e}")
        await callback_query.answer()


def register_handlers(dp: Dispatcher):
    dp.message.register(cmd_settings, Command("settings"))
    # Registered with a filter and before callbacks.py, whose handler takes
    # every callback query that reaches it.
    dp.callback_query.register(
        process_settings_callback, F.data.startswith("settings_")
    )
    dp.callback_query.register(
        process_announcement_callback, F.data.startswith("announce_")
    )
