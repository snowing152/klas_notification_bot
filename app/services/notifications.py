import asyncio
import logging

from app.bot import bot
from app.config import settings

from app.database.database import (
    get_all_users,
    get_klas_session,
    get_notification_state,
    get_sent_notifications,
    get_user_language,
    get_user_settings,
    prune_sent_notifications,
    record_sent_notifications,
    update_notification_state,
)
from app.keyboards import create_klas_relogin_keyboard
from app.services.klas_session import SessionStatus, open_session, set_login_prompt_sent
from app.strings import Strings, Language
from app.utils import timezone
# One emoji table for /show and for notifications: its keys are also what
# decides which assignment categories get notified about at all.
from app.utils.assignment_render import (
    TYPE_EMOJIS,
    render_assignment_chunks,
    render_reminder_chunks,
)


# Define time thresholds in hours and their corresponding emoji indicators
TIME_THRESHOLDS = {
    1: "🚨",  # Critical
    2: "⚠️",  # Warning
    3: "⏰",  # Alert
    6: "📢",  # Notice
    12: "ℹ️",  # Info
    24: "📅",  # Day notice
}

# How many users a cycle reads from KLAS at once. Deliberately small: KLAS
# drops connections under light load already (that is what the retries in
# kw.py exist for), and one user is itself several requests. The goal is to
# stop a slow user from holding up everyone behind them, not to fan out wide:
# however many users are being read, kw.MAX_CONCURRENT_REQUESTS bounds what
# KLAS actually receives at one moment.
MAX_CONCURRENT_USERS = 5

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
    start = getattr(settings_row, "quiet_start", 1)
    end = getattr(settings_row, "quiet_end", 8)

    if start == end:
        return False
    if start < end:
        # The usual case: 01:00 to 08:00.
        return start <= hour < end
    # A window that crosses midnight, such as 23:00 to 08:00.
    return hour >= start or hour < end


def assignment_key(subject_name: str, assignment_type: str, title: str) -> str:
    """Stable identity for an assignment; KLAS gives them no id of their own."""
    return f"{subject_name}_{assignment_type}_{title}"


async def send_notification(
    items: list[dict],
    user_id: str,
    urgency_level: int,
    user_lang: Language = Language.EN,
    now=None,
) -> int:
    """Send the deadline reminder for one threshold. Returns how many messages
    Telegram accepted.

    A long list is split into several messages. Each one's entries are written
    down as soon as it is accepted, and the first refusal stops the rest, so
    what is left stays unrecorded and is retried next cycle - nothing is
    announced twice, and nothing is marked sent that was not.
    """
    emoji = TIME_THRESHOLDS.get(urgency_level, "📌")
    header = Strings.get("reminder_header", user_lang, emoji=emoji, hours=urgency_level)
    chunks = render_reminder_chunks(items, header, user_lang, now or timezone.now())
    return await _deliver(chunks, user_id, f" (threshold={urgency_level}h)")


async def send_new_assignments(
    items: list[dict], user_id: str, user_lang: Language = Language.EN, now=None
) -> int:
    """Announce assignments that just appeared in KLAS; same delivery contract
    as `send_notification`.

    The deadline thresholds only start at 24 hours, so work posted a week in
    advance stayed invisible until its last day. For the same reason every day
    stays open here: /show's collapsed "Later" block would hide exactly the
    work this alert exists to announce.
    """
    chunks = render_assignment_chunks(
        items,
        user_lang,
        now or timezone.now(),
        header_key="new_assignments_header",
        horizon_days=None,
    )
    return await _deliver(chunks, user_id, " (new assignments)")


async def _deliver(chunks, user_id: str, detail: str) -> int:
    """Send chunks in order, recording each one's entries right after Telegram
    accepted it. Stops at the first refusal; returns how many went out."""
    delivered = 0
    for text, items in chunks:
        if delivered:
            # Same courtesy as between kinds: no burst of messages in one go.
            await asyncio.sleep(SEND_DELAY_SECONDS)
        if not await _send(text, user_id, detail, parse_mode="HTML"):
            break
        await _record_sent(user_id, [item["entry"] for item in items])
        delivered += 1
    return delivered


async def _send(
    text: str,
    user_id: str,
    detail: str = "",
    reply_markup=None,
    parse_mode: str | None = None,
) -> bool:
    """Deliver one message. `detail` names the kind for the log line only.

    `parse_mode` is passed per message and only when given, because other
    texts (the login prompts) contain literal `<` and go out as plain text.

    A week of production logs had 332 cycles and not one line saying a
    notification was ever sent - only the failure path logged anything. What
    goes into the log is deliberately just the user and the kind, never the
    assignment text the message carries.
    """
    options = {"parse_mode": parse_mode} if parse_mode else {}
    try:
        await bot.send_message(
            chat_id=user_id, text=text, reply_markup=reply_markup, **options
        )
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


async def _ask_for_login(
    user_id: str, user_lang: Language, user_settings, status: SessionStatus
) -> None:
    """Tell the user, once, what it takes to get KLAS read again.

    NEEDS_LOGIN: approve a login. The bot never sends the push itself - an
    unprompted one would be a surprise approval request on the student's
    phone - so the message carries the button that does. PASSWORD_REJECTED:
    the saved password stopped working, and only /register fixes that.
    """
    row = await get_klas_session(user_id)
    if row is not None and row.login_prompt_sent:
        return

    if in_quiet_hours(user_settings):
        # Held for the first daytime cycle; nothing is recorded, so it comes
        # up again until it has been sent.
        logging.info(f"KLAS login prompt for {user_id} held (quiet hours)")
        return

    if status is SessionStatus.PASSWORD_REJECTED:
        sent = await _send(
            Strings.get("credentials_expired", user_lang),
            user_id,
            " (KLAS password rejected)",
        )
    else:
        sent = await _send(
            Strings.get("klas_login_required", user_lang),
            user_id,
            " (KLAS login required)",
            reply_markup=create_klas_relogin_keyboard(user_lang),
        )
    if sent:
        await set_login_prompt_sent(user_id)


def _collect_messages(todo_list, sent: dict, now, thresholds=None) -> tuple:
    """Pick what this user is due, without rendering or sending anything yet.

    Returns (new_items, threshold_items, current_keys). Items are the dicts the
    renderer takes (type, subject, title, expire_at) plus "entry", the
    (assignment_key, kind) pair to record once the message carrying the item
    was accepted by Telegram.

    `now` is one instant for the whole cycle: reading KLAS takes seconds on a
    slow day, so deadlines are carried around as absolute times and only
    turned into a remaining time at render.
    """
    thresholds = sorted(TIME_THRESHOLDS) if thresholds is None else thresholds
    new_items = []
    threshold_items = {threshold: [] for threshold in TIME_THRESHOLDS}
    current_keys = set()

    for subject in todo_list:
        subject_name = subject.get("name", "Unknown Subject")

        for assignment_type in TYPE_EMOJIS:
            for assignment in subject["todo"].get(assignment_type, []):
                title = assignment.get("title", "")
                key = assignment_key(subject_name, assignment_type, title)
                current_keys.add(key)

                already_sent = sent.get(key, set())
                left_time = assignment["expire_at"] - now

                if left_time.total_seconds() <= 0:
                    # Past its deadline: KLAS still lists it, but there is
                    # nothing left to warn about.
                    continue

                item = {
                    "type": assignment_type,
                    "subject": subject_name,
                    "title": title,
                    "expire_at": assignment["expire_at"],
                }

                if NEW_ASSIGNMENT_KIND not in already_sent:
                    new_items.append({**item, "entry": (key, NEW_ASSIGNMENT_KIND)})

                # Only the tightest threshold the deadline still fits in: at
                # 2h30m left that is the 3h alert, and the 6/12/24h ones are
                # past, not pending. Breaking out also covers the last hour,
                # which the old bounds check skipped entirely - with less than
                # an hour left nothing matched "more than 0 hours".
                hours_left = left_time.total_seconds() / 3600
                for threshold in thresholds:
                    if hours_left <= threshold:
                        if f"t{threshold}" not in already_sent:
                            threshold_items[threshold].append(
                                {**item, "entry": (key, f"t{threshold}")}
                            )
                        break

    return new_items, threshold_items, current_keys


# What quiet hours are keeping back right now, per user, under the labels the
# "Notification sent" lines use. It exists for the log alone: a hold is checked
# again every cycle of the night, and this is what makes it one line when it
# begins and one more if it ends with nothing sent, instead of fourteen
# repeats. In memory on purpose - nothing reads it to decide what to send, and
# after a restart the worst case is a hold announced a second time.
_holding: dict[str, set[str]] = {}

# Delivered, but the database refused the row. Kept here and treated as sent,
# or the same reminder would go out again every cycle until a write succeeds.
_unrecorded: dict[str, list] = {}


def _log_holds(user_id: str, held: set, attempted: set) -> None:
    """Say which holds began this cycle and which ended without a message.

    A held reminder is not a queued one. _collect_messages keeps only the
    tightest threshold a deadline still fits in, so a 6h reminder held at 01:00
    is gone once 3h is reached, and work handed in overnight leaves nothing to
    say in the morning. `attempted` is what a send was tried for this cycle:
    those have their own "Notification sent" or "Error sending" line.
    """
    before = _holding.get(user_id, set())

    for label in sorted(held - before):
        logging.info(f"Notification held for {user_id} ({label}, quiet hours)")
    for label in sorted(before - held - attempted):
        logging.info(
            f"Held notification for {user_id} ({label}) dropped unsent: "
            f"no longer due"
        )

    if held:
        _holding[user_id] = held
    else:
        _holding.pop(user_id, None)


async def _record_sent(user_id: str, entries) -> None:
    """Write down what was delivered, together with anything still owed.

    The message is already in the chat when this runs, so a failed write must
    not be forgotten: the next cycle would find no row and send it again.
    """
    pending = _unrecorded.pop(user_id, []) + list(entries)
    if pending and not await record_sent_notifications(user_id, pending):
        _unrecorded[user_id] = pending
        logging.error(
            f"Could not record {len(pending)} sent notifications for {user_id}; "
            f"kept in memory until the database takes them"
        )


async def _process_user(user) -> tuple[bool, int]:
    """Check one user's assignments and send whatever is due.

    Returns (success, notifications_sent). success is False when the user's
    data could not be read at all - the caller only counts those, so one
    broken account never aborts a cycle.
    """
    user_id = user.user_id
    # Outside the try: whatever went out before something broke was still sent.
    sent_count = 0

    try:
        user_lang = await get_user_language(user_id) or Language.EN
        state = await get_notification_state(user_id)
        user_settings = await get_user_settings(user_id)

        # First, whatever an earlier cycle delivered and could not write down.
        await _record_sent(user_id, ())

        sent = await get_sent_notifications(user_id)
        if sent is None:
            # The database could not be read. Sending now would repeat
            # notifications this user already has, so skip the cycle instead.
            logging.warning(f"Could not read notification history for {user_id}")
            return False, 0

        for key, kind in _unrecorded.get(user_id, ()):
            sent.setdefault(key, set()).add(kind)

        async with open_session(user) as access:
            if access.status is SessionStatus.UNAVAILABLE:
                logging.warning(f"KLAS unavailable for user {user_id}")
                return False, 0

            if access.status in (
                SessionStatus.NEEDS_LOGIN,
                SessionStatus.PASSWORD_REJECTED,
            ):
                # open_session already tried a silent login, so this user
                # really has to act. Existing users have no session row after
                # this shipped; where KLAS wants the push, each gets one prompt
                # on the first daytime cycle. Not a failed check: counting
                # everyone who has to act would make every summary a warning.
                logging.info(f"User {user_id} has to act before KLAS can be read")
                await _ask_for_login(user_id, user_lang, user_settings, access.status)
                return True, 0

            todo_list = await access.api.get_todo_list()

            # None means KLAS could not be read; an empty list means the
            # student genuinely has no subjects this semester.
            if todo_list is None:
                logging.warning(f"Could not retrieve assignments for user {user_id}")
                return False, 0

            if not todo_list:
                logging.debug(f"No subjects found for user {user_id}")
                _log_holds(user_id, set(), set())
                return True, 0

            # A category KLAS refused arrives as an empty one, which is also
            # what "all of it handed in" looks like. What was read is still
            # worth reminding about; what must not happen is acting on the
            # absence of the rest.
            incomplete = any(subject.get("incomplete") for subject in todo_list)
            seeded = bool(getattr(state, "seeded", False))
            if incomplete and not seeded:
                # Seeding from part of the picture would leave the rest to be
                # announced as brand new on the next cycle.
                logging.warning(
                    f"Incomplete assignment data for user {user_id}; "
                    f"not seeding from it"
                )
                return False, 0

            thresholds = (
                enabled_thresholds(user_settings)
                if getattr(user_settings, "deadline_alerts", True)
                else []
            )
            now = timezone.now()
            new_items, threshold_items, current_keys = _collect_messages(
                todo_list, sent, now, thresholds
            )

            quiet = in_quiet_hours(user_settings)
            wants_new = getattr(user_settings, "new_assignment_alerts", True)

            held = set()
            attempted = set()
            if not seeded:
                # First cycle for this user: remember what they already have so
                # the whole semester's backlog is not announced as brand new.
                new_entries = [item["entry"] for item in new_items]
                await _record_sent(user_id, new_entries)
                await update_notification_state(user_id, seeded=True)
                logging.info(
                    f"Seeded {len(new_entries)} existing assignments for {user_id}"
                )
            elif new_items and not wants_new:
                # Switched off: record it so turning the setting back on does
                # not announce everything that piled up in the meantime.
                await _record_sent(user_id, [item["entry"] for item in new_items])
            elif new_items and quiet:
                # Held until the quiet hours are over; nothing is recorded, so
                # the next daytime cycle picks it up.
                held.add("new assignments")
            elif new_items:
                attempted.add("new assignments")
                # Recording happens inside, message by message.
                sent_count += await send_new_assignments(
                    new_items, user_id, user_lang, now
                )
                await asyncio.sleep(SEND_DELAY_SECONDS)

            for threshold, items in threshold_items.items():
                if items and quiet and threshold not in QUIET_HOURS_EXEMPT:
                    held.add(f"threshold={threshold}h")
                elif items:
                    attempted.add(f"threshold={threshold}h")
                    sent_count += await send_notification(
                        items, user_id, threshold, user_lang, now
                    )
                    await asyncio.sleep(SEND_DELAY_SECONDS)

            _log_holds(user_id, held, attempted)

        # Forget assignments KLAS no longer lists: submitted, or long expired.
        # Not after a partial read: work in a category KLAS refused would lose
        # its rows and be announced, and reminded about, all over again.
        if incomplete:
            logging.info(
                f"Kept the notification history of {user_id} as it is: "
                f"KLAS answered incompletely"
            )
        else:
            await prune_sent_notifications(user_id, current_keys)

        return True, sent_count
    except Exception as e:
        logging.error(f"Error processing user {user_id}: {e}", exc_info=True)
        return False, sent_count


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
            logging.error(f"User check task failed: {result!r}", exc_info=result)
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
            logging.error(f"Error in check_todos: {e}", exc_info=True)
