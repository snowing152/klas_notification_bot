import logging
from aiogram import Dispatcher, types
from aiogram.filters import Command

from app.config import settings
from app.database.database import get_all_users
from app.strings import Strings, Language


async def cmd_notify(message: types.Message):
    try:
        # Check if the user is an admin
        if message.from_user.id != settings.ADMIN_ID:
            await message.answer("You are not authorized to use this command.")
            return

        # Parse the message format: /notify en: English message | ko: 한국어 메시지
        raw = message.caption if message.photo else message.text
        _, separator, content = (raw or "").partition("/notify ")

        # Parse messages for different languages
        messages = parse_multilanguage_message(content) if separator else {}

        if not messages:
            await message.answer(
                "Please use the format:\n/notify en: English message | ko: 한국어 메시지"
            )
            return

        # Fetch all users from the database
        users = await get_all_users()

        if message.photo:
            photo = message.photo[-1].file_id
            for user in users:
                try:
                    await message.bot.send_photo(
                        chat_id=user.user_id,
                        photo=photo,
                        caption=get_message_for_user(user, messages),
                    )
                except Exception as e:
                    logging.error(f"Failed to send message to {user.user_id}: {e}")
        else:
            for user in users:
                try:
                    await message.bot.send_message(
                        chat_id=user.user_id,
                        text=get_message_for_user(user, messages),
                    )
                except Exception as e:
                    logging.error(f"Failed to send message to {user.user_id}: {e}")

        await message.answer("Notification sent to all users!")

    except Exception as e:
        logging.error(f"Failed to send notification: {e}")
        await message.answer(Strings.get("unexpected_error", Language.EN))


def get_message_for_user(user, messages: dict[str, str]) -> str:
    """Pick the message matching the user's language, falling back to English.

    User rows store the Language enum name ("EN"/"KO"/"RU") while the parsed
    message keys are lowercased, so the lookup has to be case-insensitive.
    """
    user_lang = (getattr(user, "language", None) or "en").lower()
    return messages.get(user_lang) or messages.get("en", "")


def parse_multilanguage_message(content: str) -> dict[str, str]:
    """
    Parse a message with multiple language versions
    Format: en: English message | ko: 한국어 메시지
    """
    try:
        messages = {}
        parts = content.split("|")

        for part in parts:
            part = part.strip()
            if ":" in part:
                lang, msg = part.split(":", 1)
                lang = lang.strip().lower()
                msg = msg.strip()
                messages[lang] = msg

        return messages
    except Exception as e:
        logging.error(f"Error parsing multilanguage message: {e}")
        return {}


def register_handlers(dp: Dispatcher):
    dp.message.register(cmd_notify, Command("notify"))
