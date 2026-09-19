import asyncio
import logging

from app.bot import bot
from app.database.database import (
    get_all_users,
    get_user_language,
    has_seen_announcement,
    mark_announcement_seen,
)
from app.keyboards import create_announcement_keyboard
from app.strings import Strings, Language


# Feature announcements, keyed so a redeploy cannot repeat one and a user who
# registers later still gets it. Each entry names the string to send and, for
# an opt-in feature, the user_settings column its buttons switch:
#
#   "timetable_v1": {"text_key": "announce_timetable", "setting": "timetable_reminders"}
#
# An entry without "setting" is sent as plain text, with no buttons.
ANNOUNCEMENTS: dict[str, dict] = {}

# Telegram starts refusing bulk sends past ~30 messages a second.
SEND_DELAY_SECONDS = 0.1


async def announce_pending() -> int:
    """Send every announcement no one has seen yet. Returns how many went out.

    Runs at startup, so a deploy that adds an entry above tells everyone about
    the feature once, without a manual /notify.
    """
    if not ANNOUNCEMENTS:
        return 0

    sent = 0
    try:
        users = await get_all_users()
    except Exception as e:
        logging.error(f"Could not read users for announcements: {e}")
        return 0

    for key, announcement in ANNOUNCEMENTS.items():
        for user in users:
            try:
                if await has_seen_announcement(user.user_id, key):
                    continue

                user_lang = await get_user_language(user.user_id) or Language.EN
                setting = announcement.get("setting")
                await bot.send_message(
                    chat_id=user.user_id,
                    text=Strings.get(announcement["text_key"], user_lang),
                    reply_markup=(
                        create_announcement_keyboard(user_lang, key)
                        if setting
                        else None
                    ),
                )
                # Marked after the send, so a user Telegram refused is offered
                # the announcement again on the next start.
                await mark_announcement_seen(user.user_id, key)
                sent += 1
                await asyncio.sleep(SEND_DELAY_SECONDS)
            except Exception as e:
                logging.error(f"Could not announce {key} to {user.user_id}: {e}")

    if sent:
        logging.info(f"Announcements sent: {sent}")
    return sent
