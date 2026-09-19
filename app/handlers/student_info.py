import logging
from pathlib import Path

from aiogram import Dispatcher, types
from aiogram.filters import Command
from aiogram.types import FSInputFile

from app.strings import Strings
from app.database.database import get_user
from app.services.kw import KwangwoonUniversityApi
from app.utils.encryption import decrypt_password
from app.utils.language_utils import get_user_language_with_fallback

PHOTOS_DIR = Path("images/photos")


def student_photo_path(user_id) -> Path:
    """Where /info caches a student's ID photo; /unregister deletes it."""
    return PHOTOS_DIR / f"student_{user_id}.jpg"


async def send_student_info(bot, chat_id: int, user_id: str, user_lang) -> None:
    """Look up and send the student profile. Shared by /info and the /account
    screen's "Student info" button, so it takes a bot+chat_id rather than a
    Message - the account button has no message from the user to reply to."""
    user = await get_user(user_id)

    if not user:
        await bot.send_message(chat_id, Strings.get("need_to_register", user_lang))
        return

    async with KwangwoonUniversityApi() as kw:
        if not await kw.login(user.username, decrypt_password(user.encrypted_password)):
            await bot.send_message(chat_id, Strings.get("unexpected_error", user_lang))
            return

        student_info = await kw.get_student_info()

        if not student_info:
            await bot.send_message(
                chat_id, Strings.get("failed_to_fetch_student_info", user_lang)
            )
            return

        PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
        photo_path = student_photo_path(user_id)

        if not photo_path.exists():
            student_photo = await kw.get_student_photo()
            if student_photo:
                photo_path.write_bytes(student_photo)

        msg = Strings.get(
            "student_info",
            user_lang,
            uid=student_info["uid"],
            name=student_info["name"],
            major=student_info["major"],
            grade=student_info["grade"],
            semester=student_info["semester"],
            total_credits=student_info["credits"]["total"],
            required_credits=student_info["credits"]["required"],
            major_credits_total=student_info["major_credits"]["total"],
            major_credits_required=student_info["major_credits"]["required"],
            elective_credits_total=student_info["elective_credits"]["total"],
            elective_credits_required=student_info["elective_credits"]["required"],
            average_score=student_info["average_score"],
            credits_per_semester=student_info["credits_for_each_semester"],
            major_credits_per_semester=student_info["major_credits_for_each_semester"],
        )

        try:
            if photo_path.exists():
                await bot.send_photo(
                    chat_id, photo=FSInputFile(str(photo_path)), caption=msg
                )
            else:
                await bot.send_message(chat_id, msg)
        except Exception as e:
            logging.error(f"Error sending student info with photo: {e}")
            await bot.send_message(chat_id, msg)


async def cmd_info(message: types.Message):
    try:
        user_lang = await get_user_language_with_fallback(message)
        logging.info(f"User {message.from_user.id} used /info command")
        await send_student_info(
            message.bot, message.chat.id, str(message.from_user.id), user_lang
        )
    except Exception as e:
        logging.error(f"Unexpected error in cmd_info: {e}")
        await message.answer(Strings.get("unexpected_error", user_lang))


def register_handlers(dp: Dispatcher):
    dp.message.register(cmd_info, Command("info"))
