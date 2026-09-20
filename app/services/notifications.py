import asyncio
import logging

from app.bot import bot
from app.config import settings

from app.utils.encryption import decrypt_password
from app.database.database import (
    get_all_users,
    get_notification_state,
    get_sent_notifications,
    get_user_language,
    get_user_settings,
    prune_sent_notifications,
    record_sent_notifications,
    update_notification_state,
)
from app.services.kw import KwangwoonUniversityApi
from app.strings import Strings, Language
from app.utils import timezone


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
    "discussions": "💬",
}

# How many users a cycle reads from KLAS at once. Deliberately small: KLAS
# drops connections under light load already (that is what the retries in
# kw.py exist for), and one user is itself several requests. The goal is to
# stop a slow user from holding up everyone behind them, not to fan out wide.
MAX_CONCURRENT_USERS = 5

# A single failed login is usually KLAS being KLAS. Three in a row (an hour and
# a half apart) is the saved password no longer working - KW forces a change
# every few months, and until now the bot just went quiet without saying why.
LOGIN_FAILURES_BEFORE_WARNING = 3

# Marks an assignment as already announced when it first appeared, as opposed
# to the "t<hours>" kinds recorded for each deadline threshold.
NEW_ASSIGNMENT_KIND = "new"

# What "urgent only" keeps: one warning a day before nothing, then the last
# hours. The full set is every key of TIME_THRESHOLDS.
URGENT_THRESHOLDS = (1, 3, 6)

# Quiet hours hold everything except these: a deadline one or two hours away
# is exactly the case worth waking someone for.
QUIET_HOURS_EXEMPT = (1, 2)

# Pause between two messages to the same user, so a student with work due in
# several brackets does not get them in one burst.
SEND_DELAY_SECONDS = 1


def enabled_thresholds(settings_row) -> list:
    """The deadline thresholds this user wants, tightest first."""
    if getattr(settings_row, "urgent_thresholds_only", False):
        return sorted(URGENT_THRESHOLDS)
    return sorted(TIME_THRESHOLDS)


def in_quiet_hours(settings_row, now=None) -> bool:
    """Is it the user's night right now? Times are Korean local, like KLAS."""
    if not getattr(settings_row, "quiet_hours", False):
        return False

    hour = (now or timezone.now()).hour
    start = getattr(settings_row, "quiet_start", 23)
    end = getattr(settings_row, "quiet_end", 8)

    if start == end:
        return False
    if start < end:
        return start <= hour < end
    # The usual case: the window crosses midnight (23:00 to 08:00).
    return hour >= start or hour < end


def assignment_key(subject_name: str, assignment_type: str, title: str) -> str:
    """Stable identity for an assignment; KLAS gives them no id of their own."""
    return f"{subject_name}_{assignment_type}_{title}"


def format_left_time(left_time) -> str:
    """Human-readable remaining time, e.g. "3d 4h 5m" or "45m"."""
    total_seconds = int(left_time.total_seconds())
    days, remainder = divmod(max(total_seconds, 0), 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes = remainder // 60

    # Same rule as the /show formatter: a zero unit stays once a bigger one is
    # shown, so "1d 0h 58m" never collapses into the hour-short "1d 58m".
    parts = []
    if days:
        parts.append(f"{days}d")
    if days or hours:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return " ".join(parts)


async def send_notification(
    message: str, user_id: str, urgency_level: int, user_lang: Language = Language.EN
) -> bool:
    """Send one deadline notification. Returns whether it actually went out.

    The caller only records the notification as delivered when this says True,
    so a message Telegram refused is retried on the next cycle instead of being
    silently marked as sent.
    """
    emoji = TIME_THRESHOLDS.get(urgency_level, "📌")
    prefix = Strings.get(
        "notification_header", user_lang, emoji=emoji, hours=urgency_level
    )
    postfix = Strings.get("notification_footer", user_lang)
    return await _send(
        prefix + message + postfix, user_id, f" (threshold={urgency_level}h)"
    )


async def send_new_assignments(
    message: str, user_id: str, user_lang: Language = Language.EN
) -> bool:
    """Announce assignments that just appeared in KLAS.

    The deadline thresholds only start at 24 hours, so work posted a week in
    advance stayed invisible until its last day.
    """
    return await _send(
        Strings.get("new_assignments_header", user_lang) + message,
        user_id,
        " (new assignments)",
    )


async def _send(text: str, user_id: str, detail: str = "") -> bool:
    """Deliver one message. `detail` names the kind for the log line only.

    A week of production logs had 332 cycles and not one line saying a
    notification was ever sent - only the failure path logged anything. What
    goes into the log is deliberately just the user and the kind, never the
    assignment text the message carries.
    """
    try:
        await bot.send_message(chat_id=user_id, text=text)
        logging.info(f"Notification sent to {user_id}{detail}")
        return True
    except Exception as e:
        logging.error(f"Error sending notification to {user_id}: {e}")
        return False


async def start_notification_service():
    # Creates a task that runs independently
    notification_task = asyncio.create_task(check_todos())
    notification_task.set_name("notification_checker")

    try:
        await notification_task
    except Exception as e:
        logging.error(f"Notification task failed: {e}")


async def _handle_login_failure(user_id: str, state, user_lang: Language) -> None:
    """Count a failed KLAS login and, once it is clearly the password, say so."""
    failures = (getattr(state, "login_failures", 0) or 0) + 1
    already_warned = bool(getattr(state, "credentials_warned", False))
    await update_notification_state(user_id, login_failures=failures)

    if failures < LOGIN_FAILURES_BEFORE_WARNING or already_warned:
        return

    if await _send(Strings.get("credentials_expired", user_lang), user_id):
        await update_notification_state(user_id, credentials_warned=True)
        logging.info(f"Warned user {user_id} that their KLAS password fails")


async def _clear_login_failures(user_id: str, state) -> None:
    if getattr(state, "login_failures", 0) or getattr(state, "credentials_warned", False):
        await update_notification_state(
            user_id, login_failures=0, credentials_warned=False
        )


def _collect_messages(
    todo_list, sent: dict, user_lang: Language, thresholds=None
) -> tuple:
    """Build the messages this user is due, without sending anything yet.

    Returns (new_assignments_message, new_entries, threshold_messages,
    threshold_entries, current_keys) where the *_entries lists are the
    (assignment_key, kind) pairs to record once the matching message is sent.
    """
    thresholds = sorted(TIME_THRESHOLDS) if thresholds is None else thresholds
    # One instant for the whole cycle: KLAS is read subject by subject over
    # tens of seconds, so deadlines are carried around as absolute times and
    # turned into a remaining time only here.
    now = timezone.now()
    new_message = ""
    new_entries = []
    threshold_messages = {threshold: "" for threshold in TIME_THRESHOLDS}
    threshold_entries = {threshold: [] for threshold in TIME_THRESHOLDS}
    current_keys = set()

    for subject in todo_list:
        subject_name = subject.get("name", "Unknown Subject")

        for assignment_type, emoji in TYPE_EMOJIS.items():
            for assignment in subject["todo"].get(assignment_type, []):
                title = assignment.get("title", "")
                key = assignment_key(subject_name, assignment_type, title)
                current_keys.add(key)

                already_sent = sent.get(key, set())
                left_time = assignment["expire_at"] - now
                type_label = Strings.get(f"type_{assignment_type}", user_lang)

                if left_time.total_seconds() <= 0:
                    # Past its deadline: KLAS still lists it, but there is
                    # nothing left to warn about.
                    continue

                body = (
                    f"{emoji} {subject_name}\n"
                    f"{type_label}: {title}\n"
                    + Strings.get(
                        "time_left", user_lang, time_str=format_left_time(left_time)
                    )
                    + "\n\n"
                )

                if NEW_ASSIGNMENT_KIND not in already_sent:
                    new_message += body
                    new_entries.append((key, NEW_ASSIGNMENT_KIND))

                # Only the tightest threshold the deadline still fits in: at
                # 2h30m left that is the 3h alert, and the 6/12/24h ones are
                # past, not pending. Breaking out also covers the last hour,
                # which the old bounds check skipped entirely - with less than
                # an hour left nothing matched "more than 0 hours".
                hours_left = left_time.total_seconds() / 3600
                for threshold in thresholds:
                    if hours_left <= threshold:
                        if f"t{threshold}" not in already_sent:
                            threshold_messages[threshold] += body
                            threshold_entries[threshold].append(
                                (key, f"t{threshold}")
                            )
                        break

    return (
        new_message,
        new_entries,
        threshold_messages,
        threshold_entries,
        current_keys,
    )


async def _process_user(user) -> tuple[bool, int]:
    """Check one user's assignments and send whatever is due.

    Returns (success, notifications_sent). success is False when the user's
    data could not be read at all - the caller only counts those, so one
    broken account never aborts a cycle.
    """
    user_id = user.user_id

    try:
        user_lang = await get_user_language(user_id) or Language.EN
        state = await get_notification_state(user_id)
        user_settings = await get_user_settings(user_id)

        sent = await get_sent_notifications(user_id)
        if sent is None:
            # The database could not be read. Sending now would repeat
            # notifications this user already has, so skip the cycle instead.
            logging.warning(f"Could not read notification history for {user_id}")
            return False, 0

        async with KwangwoonUniversityApi() as kw:
            # An unchecked login was the most common way a cycle failed: the
            # login died on the wire, cookies stayed empty, and get_todo_list
            # reported "No cookies found" as if the credentials were the
            # problem.
            if not await kw.login(
                user.username, decrypt_password(user.encrypted_password)
            ):
                logging.warning(f"Could not log in as user {user_id}")
                await _handle_login_failure(user_id, state, user_lang)
                return False, 0

            await _clear_login_failures(user_id, state)

            todo_list = await kw.get_todo_list()

            # None means KLAS could not be read; an empty list means the
            # student genuinely has no subjects this semester.
            if todo_list is None:
                logging.warning(f"Could not retrieve assignments for user {user_id}")
                return False, 0

            if not todo_list:
                logging.debug(f"No subjects found for user {user_id}")
                return True, 0

            thresholds = (
                enabled_thresholds(user_settings)
                if getattr(user_settings, "deadline_alerts", True)
                else []
            )
            (
                new_message,
                new_entries,
                threshold_messages,
                threshold_entries,
                current_keys,
            ) = _collect_messages(todo_list, sent, user_lang, thresholds)

            quiet = in_quiet_hours(user_settings)
            wants_new = getattr(user_settings, "new_assignment_alerts", True)

            sent_count = 0
            seeded = bool(getattr(state, "seeded", False))
            if not seeded:
                # First cycle for this user: remember what they already have so
                # the whole semester's backlog is not announced as brand new.
                await record_sent_notifications(user_id, new_entries)
                await update_notification_state(user_id, seeded=True)
                logging.info(
                    f"Seeded {len(new_entries)} existing assignments for {user_id}"
                )
            elif new_message and not wants_new:
                # Switched off: record it so turning the setting back on does
                # not announce everything that piled up in the meantime.
                await record_sent_notifications(user_id, new_entries)
            elif new_message and quiet:
                # Held until the quiet hours are over; nothing is recorded, so
                # the next daytime cycle picks it up.
                logging.debug(f"Holding new assignments for {user_id} (quiet hours)")
            elif new_message:
                if await send_new_assignments(new_message, user_id, user_lang):
                    await record_sent_notifications(user_id, new_entries)
                    sent_count += 1
                await asyncio.sleep(SEND_DELAY_SECONDS)

            for threshold, message in threshold_messages.items():
                if message and quiet and threshold not in QUIET_HOURS_EXEMPT:
                    logging.debug(
                        f"Holding the {threshold}h notification for {user_id} "
                        f"(quiet hours)"
                    )
                elif message:
                    if await send_notification(message, user_id, threshold, user_lang):
                        await record_sent_notifications(
                            user_id, threshold_entries[threshold]
                        )
                        sent_count += 1
                    await asyncio.sleep(SEND_DELAY_SECONDS)

        # Forget assignments KLAS no longer lists: submitted, or long expired.
        await prune_sent_notifications(user_id, current_keys)

        return True, sent_count
    except Exception as e:
        logging.error(f"Error processing user {user_id}: {e}")
        return False, 0


async def _process_user_limited(user, semaphore) -> tuple[bool, int]:
    async with semaphore:
        return await _process_user(user)


async def run_notification_cycle(users) -> tuple[int, int]:
    """Check every user, MAX_CONCURRENT_USERS at a time.

    Returns (failed_users, notifications_sent).

    Users used to be checked strictly one after another, so the cycle cost the
    sum of everyone's KLAS round trips — and a single user stuck in a retry
    delayed everybody behind them.
    """
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_USERS)

    results = await asyncio.gather(
        *(_process_user_limited(user, semaphore) for user in users),
        return_exceptions=True,
    )

    failed = 0
    sent = 0
    for result in results:
        # _process_user handles its own errors, so an exception reaching here
        # means the task itself broke - count it rather than let it vanish.
        if isinstance(result, BaseException):
            logging.error(f"User check task failed: {result!r}")
            failed += 1
            continue

        success, sent_count = result
        sent += sent_count
        if not success:
            failed += 1

    return failed, sent


async def check_todos():
    while True:
        try:
            # Wait before checking notifications to avoid immediate execution on bot startup
            await asyncio.sleep(settings.NOTIFICATION_CHECK_INTERVAL)

            users = await get_all_users()
            failed_users, sent_count = await run_notification_cycle(users)

            if failed_users:
                logging.warning(
                    f"Notification cycle finished: {len(users) - failed_users}"
                    f"/{len(users)} users checked, {failed_users} failed, "
                    f"{sent_count} notifications sent"
                )
            else:
                logging.info(
                    f"Notification cycle finished: all {len(users)} users checked, "
                    f"{sent_count} notifications sent"
                )
        except Exception as e:
            logging.error(f"Error in check_todos: {e}")
