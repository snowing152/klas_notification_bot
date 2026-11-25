"""Rendering of assignment lists as Telegram HTML: day -> subject -> items.

Everything here is pure: no I/O, and "now" is always passed in as a naive Seoul
datetime (what `app.utils.timezone.now()` returns). Nothing reads the host clock
or timezone, so a process running in California renders the same text as one in
Seoul. The per-item and per-group helpers are public so other renderers (deadline
notifications) can reuse them.

Output is meant for parse_mode="HTML". Every value that comes from KLAS goes
through html.escape; the localized templates contain no raw `<`, `>` or `&`.
"""

import datetime
import html
from itertools import groupby

from app.strings import Strings

TYPE_EMOJIS = {
    "lectures": "📚",
    "homeworks": "📝",
    "quizzes": "🧠",
    "team_projects": "👥",
    "discussions": "💬",
}
FALLBACK_EMOJI = "📌"

# Order used to break ties between items due at the same minute.
TYPE_ORDER = ("lectures", "homeworks", "quizzes", "team_projects", "discussions")

# Today plus the following six calendar days are shown open.
HORIZON_DAYS = 7
MESSAGE_LIMIT = 4096

# Non-breaking spaces: Telegram trims ordinary leading spaces on a line, and
# these survive on every client without needing a monospace block.
INDENT = "   "

DAY_SEPARATOR = "\n\n"
_BLOCKQUOTE_OPEN = "<blockquote expandable>"
_BLOCKQUOTE_CLOSE = "</blockquote>"

Subjects = list[tuple[str, list[dict]]]


def _tg_len(text: str) -> int:
    """Length as Telegram counts it against the 4096 limit: UTF-16 code units.

    Python's len() counts code points, and every emoji used here sits outside
    the BMP and costs two units, so a long run of item lines could pass a
    len() check and still be refused by Telegram.
    """
    return len(text.encode("utf-16-le")) // 2


def format_time_left(left_time: datetime.timedelta, user_lang) -> str:
    """Time left as "1d 0h 58m" / "3h 20m" / "5h" / "45m"; negative spans count as 0m."""
    total_seconds = int(left_time.total_seconds())
    days, remainder = divmod(max(total_seconds, 0), 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes = remainder // 60

    units = [("time_days", days), ("time_hours", hours), ("time_minutes", minutes)]
    # Leading zero units are dropped, and so are trailing ones: "5h 0m" says
    # nothing "5h" does not. A zero between two printed units stays - dropping
    # it turns "1d 0h 58m" into "1d 58m", which reads as an hour less than the
    # deadline actually leaves. Below a minute, "0m" is all there is to say.
    nonzero = [index for index, (_, value) in enumerate(units) if value]
    if not nonzero:
        return Strings.get("time_minutes", user_lang, count=0)
    return " ".join(
        Strings.get(key, user_lang, count=value)
        for key, value in units[nonzero[0] : nonzero[-1] + 1]
    )


def _type_rank(item: dict) -> int:
    try:
        return TYPE_ORDER.index(item["type"])
    except ValueError:
        return len(TYPE_ORDER)


def group_items(items: list[dict]) -> list[tuple[datetime.date, Subjects]]:
    """Group items into days (ascending), each holding subjects ordered by their
    earliest deadline, each holding items ordered by deadline then type.

    The day is the calendar date of `expire_at`, which is already Seoul time.
    """
    ordered = sorted(items, key=lambda item: (item["expire_at"], _type_rank(item)))
    days = []
    for day, day_items in groupby(ordered, key=lambda item: item["expire_at"].date()):
        by_subject: dict[str, list[dict]] = {}
        # day_items is already in deadline order, so a subject's first item is
        # its earliest and dict insertion order gives the subject order.
        for item in day_items:
            by_subject.setdefault(item["subject"], []).append(item)
        days.append((day, list(by_subject.items())))
    return days


def render_item(item: dict, user_lang, now: datetime.datetime) -> str:
    """One item line: type emoji, title, and the exact time left when the
    deadline is less than 24 hours away."""
    emoji = TYPE_EMOJIS.get(item["type"], FALLBACK_EMOJI)
    line = f"{INDENT}{emoji} {html.escape(item['title'], quote=False)}"
    left = item["expire_at"] - now
    if datetime.timedelta(0) < left <= datetime.timedelta(hours=24):
        in_time = Strings.get(
            "show_in_time", user_lang, time_str=format_time_left(left, user_lang)
        )
        line += f" · {in_time}"
    return line


def render_subject(
    subject: str, items: list[dict], user_lang, now: datetime.datetime
) -> str:
    """A subject line followed by its item lines.

    The subject is plain text on purpose: the day header above it is bold, and
    two bold lines in a row read as one level of the hierarchy.
    """
    lines = [html.escape(subject, quote=False)]
    lines.extend(render_item(item, user_lang, now) for item in items)
    return "\n".join(lines)


def render_day_header(
    day: datetime.date, user_lang, now: datetime.datetime
) -> str:
    """Bold "Today" / "Tomorrow" / "Mon, 1 Dec · in 5 days" for a Seoul calendar day."""
    offset = (day - now.date()).days
    if offset == 0:
        text = Strings.get("show_day_today", user_lang)
    elif offset == 1:
        text = Strings.get("show_day_tomorrow", user_lang)
    else:
        text = Strings.get(
            "show_day_date",
            user_lang,
            weekday=Strings.get(f"weekday_short_{day.weekday()}", user_lang),
            day=day.day,
            month=Strings.get(f"month_short_{day.month}", user_lang),
            count=offset,
        )
    return f"<b>{html.escape(text, quote=False)}</b>"


def render_day_group(
    day: datetime.date, subjects: Subjects, user_lang, now: datetime.datetime
) -> str:
    """A whole day: header, then each subject block."""
    blocks = [render_day_header(day, user_lang, now)]
    blocks.extend(
        render_subject(subject, items, user_lang, now) for subject, items in subjects
    )
    return "\n".join(blocks)


def _pack(units: list[str], separator: str, limit: int) -> list[str]:
    """Greedily join units into strings of at most `limit` characters."""
    packed: list[str] = []
    current = ""
    for unit in units:
        candidate = f"{current}{separator}{unit}" if current else unit
        if _tg_len(candidate) > limit and current:
            packed.append(current)
            current = unit
        else:
            current = candidate
    if current:
        packed.append(current)
    return packed


def _day_pieces(
    day: datetime.date,
    subjects: Subjects,
    user_lang,
    now: datetime.datetime,
    limit: int,
) -> list[str]:
    """The day as one string, or - only when it alone exceeds `limit` - several,
    split at subject boundaries and then at item lines. Every piece repeats the
    day header (and the subject line, for item-level splits) so it stands alone
    and its tags stay balanced."""
    whole = render_day_group(day, subjects, user_lang, now)
    if _tg_len(whole) <= limit:
        return [whole]

    header = render_day_header(day, user_lang, now)
    units: list[str] = []
    for subject, items in subjects:
        block = render_subject(subject, items, user_lang, now)
        if _tg_len(header) + 1 + _tg_len(block) <= limit:
            units.append(block)
            continue
        subject_line = html.escape(subject, quote=False)
        lines = [render_item(item, user_lang, now) for item in items]
        units.extend(
            f"{subject_line}\n{chunk}"
            for chunk in _pack(lines, "\n", limit - _tg_len(header) - 1 - _tg_len(subject_line) - 1)
        )
    return [f"{header}\n{piece}" for piece in _pack(units, "\n", limit - _tg_len(header) - 1)]


def render_assignments(
    items: list[dict],
    user_lang,
    now: datetime.datetime,
    limit: int = MESSAGE_LIMIT,
) -> list[str]:
    """HTML message chunks for a list of items, each at most `limit` characters.

    Splits only at day boundaries (a day larger than `limit` is the one
    exception). Days before today + HORIZON_DAYS are open; the rest sit in one
    expandable blockquote at the end, which is never split unless it alone
    exceeds `limit`.
    """
    if not items:
        return [Strings.get("no_assignments", user_lang)]

    header = f"<b>{html.escape(Strings.get('show_header', user_lang), quote=False)}</b>"
    horizon = now.date() + datetime.timedelta(days=HORIZON_DAYS)
    days = group_items(items)
    open_days = [(d, s) for d, s in days if d < horizon]
    later_days = [(d, s) for d, s in days if d >= horizon]

    # Room for the header even in the first chunk, so an oversized day split
    # never produces a piece that cannot sit next to it.
    day_limit = limit - _tg_len(header) - _tg_len(DAY_SEPARATOR)
    units: list[str] = []
    for day, subjects in open_days:
        units.extend(_day_pieces(day, subjects, user_lang, now, day_limit))

    if later_days:
        count = sum(len(i) for _, subjects in later_days for _, i in subjects)
        title = (
            f"<b>{html.escape(Strings.get('show_later', user_lang, count=count), quote=False)}</b>"
        )
        wrapper = _tg_len(_BLOCKQUOTE_OPEN) + _tg_len(_BLOCKQUOTE_CLOSE)
        inner_limit = day_limit - wrapper - _tg_len(title) - _tg_len(DAY_SEPARATOR)
        later_units: list[str] = []
        for day, subjects in later_days:
            later_units.extend(_day_pieces(day, subjects, user_lang, now, inner_limit))
        bodies = _pack(later_units, DAY_SEPARATOR, inner_limit)
        # The title is only on the first blockquote; later ones exist solely
        # when the block alone is over the limit.
        for index, body in enumerate(bodies):
            prefix = f"{title}{DAY_SEPARATOR}" if index == 0 else ""
            units.append(f"{_BLOCKQUOTE_OPEN}{prefix}{body}{_BLOCKQUOTE_CLOSE}")

    chunks: list[str] = []
    current = header
    for unit in units:
        candidate = f"{current}{DAY_SEPARATOR}{unit}"
        if _tg_len(candidate) > limit:
            chunks.append(current)
            current = unit
        else:
            current = candidate
    chunks.append(current)
    return chunks
