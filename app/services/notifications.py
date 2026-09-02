import asyncio
import logging

from app.bot import bot
from app.config import settings

from app.utils.encryption import decrypt_password
from app.database.database import get_all_users, get_user_language
from app.services.kw import KwangwoonUniversityApi
from app.strings import Strings, Language


# Define time thresholds in hours and their corresponding emoji indicators
TIME_THRESHOLDS = {
    1: "🚨",  # Critical
    2: "⚠️",  # Warning
    3: "⏰",  # Alert
    6: "📢",  # Notice
    12: "ℹ️",  # Info
    24: "📅",  # Day notice
}

# Assignment type emojis
TYPE_EMOJIS = {
    "lectures": "📚",
    "homeworks": "📝",
    "quizzes": "🧠",
    "team_projects": "🚧",
}


async def send_notification(
    message: str, user_id: str, urgency_level: int, user_lang: Language = Language.EN
):
    try:
        emoji = TIME_THRESHOLDS.get(urgency_level, "📌")
        prefix = Strings.get(
            "notification_header", user_lang, emoji=emoji, hours=urgency_level
        )
        postfix = Strings.get("notification_footer", user_lang)
        await bot.send_message(chat_id=user_id, text=prefix + message + postfix)
    except Exception as e:
        logging.error(f"Error sending notification to {user_id}: {e}")


async def start_notification_service():
    # Creates a task that runs independently
    notification_task = asyncio.create_task(check_todos())
    notification_task.set_name("notification_checker")

    try:
        await notification_task
    except Exception as e:
        logging.error(f"Notification task failed: {e}")


async def check_todos():
    notification_tracker = {}

    while True:
        try:
            # Wait before checking notifications to avoid immediate execution on bot startup
            await asyncio.sleep(settings.NOTIFICATION_CHECK_INTERVAL)
            
            users = await get_all_users()
            failed_users = 0

            for user in users:
                try:
                    await asyncio.sleep(0)
                    user_id = user.user_id

                    if user_id not in notification_tracker:
                        notification_tracker[user_id] = {}

                    user_lang = await get_user_language(user_id) or Language.EN

                    async with KwangwoonUniversityApi() as kw:
                        await kw.login(
                            user.username, decrypt_password(user.encrypted_password)
                        )
                        todo_list = await kw.get_todo_list()

                        threshold_messages = {
                            threshold: "" for threshold in TIME_THRESHOLDS.keys()
                        }

                        # None means KLAS could not be read; an empty list means
                        # the student genuinely has no subjects this semester.
                        if todo_list is None:
                            logging.warning(
                                f"Could not retrieve assignments for user {user_id}"
                            )
                            failed_users += 1
                            continue

                        if not todo_list:
                            logging.debug(f"No subjects found for user {user_id}")
                            continue

                        for subject in todo_list:
                            subject_name = subject.get("name", "Unknown Subject")

                            # Check each type of assignment
                            for assignment_type, emoji in TYPE_EMOJIS.items():
                                assignments = subject["todo"].get(assignment_type, [])
                                if assignments:
                                    for assignment in assignments:
                                        # Create unique assignment identifier
                                        assignment_id = f"{subject_name}_{assignment_type}_{assignment.get('title', '')}"

                                        # Initialize assignment tracker if not exists
                                        if (
                                            assignment_id
                                            not in notification_tracker[user_id]
                                        ):
                                            notification_tracker[user_id][
                                                assignment_id
                                            ] = set()

                                        left_time = assignment["left_time"].seconds
                                        days_left = assignment["left_time"].days
                                        hours_left = left_time // 3600
                                        title = assignment["title"]

                                        if abs(days_left) > 0:
                                            continue

                                        for threshold in TIME_THRESHOLDS.keys():
                                            if (
                                                hours_left <= threshold
                                                and hours_left
                                                > max(
                                                    [
                                                        t
                                                        for t in TIME_THRESHOLDS.keys()
                                                        if t < threshold
                                                    ],
                                                    default=0,
                                                )
                                                and threshold
                                                not in notification_tracker[user_id][
                                                    assignment_id
                                                ]
                                            ):  # Check if notification wasn't sent

                                                type_label = Strings.get(
                                                    f"type_{assignment_type}", user_lang
                                                )
                                                time_str = (
                                                    f"{hours_left}h "
                                                    f"{left_time % 3600 // 60}m"
                                                )
                                                threshold_messages[threshold] += (
                                                    f"{emoji} {subject_name}\n"
                                                    f"{type_label}: {title}\n"
                                                    + Strings.get(
                                                        "time_left",
                                                        user_lang,
                                                        time_str=time_str,
                                                    )
                                                    + "\n\n"
                                                )
                                                # Mark this threshold as notified for this assignment
                                                notification_tracker[user_id][
                                                    assignment_id
                                                ].add(threshold)

                        # Send notifications for each threshold that has messages
                        for threshold, message in threshold_messages.items():
                            if message:
                                await send_notification(
                                    message, user_id, threshold, user_lang
                                )
                                await asyncio.sleep(1)

                    # Clean up old assignments from tracker
                    current_assignments = {
                        f"{subject['name']}_{type_}_{assignment.get('title', '')}"
                        for subject in todo_list
                        for type_ in TYPE_EMOJIS.keys()
                        for assignment in subject["todo"].get(type_, [])
                    }

                    notification_tracker[user_id] = {
                        assignment_id: thresholds
                        for assignment_id, thresholds in notification_tracker[
                            user_id
                        ].items()
                        if assignment_id in current_assignments
                    }
                except Exception as e:
                    logging.error(f"Error processing user {user_id}: {e}")
                    failed_users += 1
                    continue  # Skip to next user if there's an error

            if failed_users:
                logging.warning(
                    f"Notification cycle finished: {len(users) - failed_users}"
                    f"/{len(users)} users checked, {failed_users} failed"
                )
            else:
                logging.info(
                    f"Notification cycle finished: all {len(users)} users checked"
                )
        except Exception as e:
            logging.error(f"Error in check_todos: {e}")
