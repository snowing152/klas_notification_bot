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

# KLAS titles and subject names are free text typed by professors. Capping them
# keeps any one line far below MESSAGE_LIMIT: html.escape can grow a character
# to 5 ("&" -> "&amp;"), so 200 units escape to at most 1000. Without the cap a
# single oversized line was sent whole, refused by Telegram, left unrecorded
# and retried every cycle.
MAX_TEXT_UNITS = 200
ELLIPSIS = "\u2026"

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


def _shorten(text: str, max_units: int = MAX_TEXT_UNITS) -> str:
    """`text` cut to at most `max_units` UTF-16 units, ending in an ellipsis.

    Cuts on code points, so a surrogate pair (an emoji) is never split in half.
    """
    if _tg_len(text) <= max_units:
        return text
    budget = max_units - _tg_len(ELLIPSIS)
    kept: list[str] = []
    used = 0
    for char in text:
        width = _tg_len(char)
        if used + width > budget:
            break
        kept.append(char)
        used += width
    return "".join(kept).rstrip() + ELLIPSIS


def _escape(text: str) -> str:
    """KLAS text, capped and made safe for parse_mode="HTML"."""
    return html.escape(_shorten(text), quote=False)


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


def _sort_items(items: list[dict]) -> list[dict]:
    return sorted(items, key=lambda item: (item["expire_at"], _type_rank(item)))


def _group_subjects(ordered: list[dict]) -> Subjects:
    """Subjects in order of their earliest item; `ordered` is already sorted."""
    by_subject: dict[str, list[dict]] = {}
    # A subject's first item is its earliest, and dict insertion order gives
    # the subject order.
    for item in ordered:
        by_subject.setdefault(item["subject"], []).append(item)
    return list(by_subject.items())


def group_items(items: list[dict]) -> list[tuple[datetime.date, Subjects]]:
    """Group items into days (ascending), each holding subjects ordered by their
    earliest deadline, each holding items ordered by deadline then type.

    The day is the calendar date of `expire_at`, which is already Seoul time.
    """
    ordered = _sort_items(items)
    return [
        (day, _group_subjects(list(day_items)))
        for day, day_items in groupby(ordered, key=lambda item: item["expire_at"].date())
    ]


def render_item(item: dict, user_lang, now: datetime.datetime) -> str:
    """One item line: type emoji, title, and the exact time left when the
    deadline falls today or tomorrow.

    The cut-off is the calendar day, not "24 hours": under one "Tomorrow"
    header every item shows its time, rather than only those that happen to
    be less than 24 hours away at the moment of rendering.
    """
    emoji = TYPE_EMOJIS.get(item["type"], FALLBACK_EMOJI)
    line = f"{INDENT}{emoji} {_escape(item['title'])}"
    left = item["expire_at"] - now
    days_away = (item["expire_at"].date() - now.date()).days
    if left > datetime.timedelta(0) and days_away <= 1:
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
    lines = [_escape(subject)]
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


# A message chunk and the items it carries. Callers that must know what was
# delivered (notifications) record exactly these once Telegram accepted the text.
Chunk = tuple[str, list[dict]]


def _pack(pieces: list[Chunk], separator: str, limit: int) -> list[Chunk]:
    """Greedily join pieces into chunks of at most `limit` UTF-16 units."""
    packed: list[Chunk] = []
    text = ""
    items: list[dict] = []
    for piece_text, piece_items in pieces:
        candidate = f"{text}{separator}{piece_text}" if text else piece_text
        if _tg_len(candidate) > limit and text:
            packed.append((text, items))
            text, items = piece_text, list(piece_items)
        else:
            text = candidate
            items = items + piece_items
    if text:
        packed.append((text, items))
    return packed


def _pieces_under_header(
    header: str,
    subjects: Subjects,
    user_lang,
    now: datetime.datetime,
    limit: int,
) -> list[Chunk]:
    """`header` followed by subject blocks, split at subject boundaries and, for
    a subject larger than a message, at item lines. Every piece repeats the
    header (and the subject line, for item-level splits) so it stands alone and
    its tags stay balanced."""
    units: list[Chunk] = []
    for subject, items in subjects:
        block = render_subject(subject, items, user_lang, now)
        if _tg_len(header) + 1 + _tg_len(block) <= limit:
            units.append((block, items))
            continue
        subject_line = _escape(subject)
        lines = [(render_item(item, user_lang, now), [item]) for item in items]
        room = limit - _tg_len(header) - 1 - _tg_len(subject_line) - 1
        units.extend(
            (f"{subject_line}\n{text}", chunk_items)
            for text, chunk_items in _pack(lines, "\n", room)
        )
    body_limit = limit - _tg_len(header) - 1
    return [
        (f"{header}\n{text}", items) for text, items in _pack(units, "\n", body_limit)
    ]


def _day_pieces(
    day: datetime.date,
    subjects: Subjects,
    user_lang,
    now: datetime.datetime,
    limit: int,
) -> list[Chunk]:
    """The day as one piece, or - only when it alone exceeds `limit` - several."""
    whole = render_day_group(day, subjects, user_lang, now)
    if _tg_len(whole) <= limit:
        return [(whole, [item for _, items in subjects for item in items])]
    header = render_day_header(day, user_lang, now)
    return _pieces_under_header(header, subjects, user_lang, now, limit)


def _bold(text: str) -> str:
    return f"<b>{html.escape(text, quote=False)}</b>"


def render_reminder_chunks(
    items: list[dict],
    header_text: str,
    user_lang,
    now: datetime.datetime,
    limit: int = MESSAGE_LIMIT,
) -> list[Chunk]:
    """A deadline reminder: bold header, then subject -> items, no day headers
    (everything in it is due within the next day anyway).

    Split only at subject boundaries (a subject larger than `limit` is the one
    exception). Every chunk repeats the header and comes with the items it
    carries, so a caller can record exactly what each accepted chunk delivered.
    """
    subjects = _group_subjects(_sort_items(items))
    return _pieces_under_header(_bold(header_text), subjects, user_lang, now, limit)


def render_assignment_chunks(
    items: list[dict],
    user_lang,
    now: datetime.datetime,
    limit: int = MESSAGE_LIMIT,
    header_key: str = "show_header",
    horizon_days: int | None = HORIZON_DAYS,
) -> list[Chunk]:
    """Day -> subject -> items as (html, items in that chunk) pairs, each text at
    most `limit` UTF-16 units. `header_key` names the localized bold title,
    which every chunk starts with.

    Splits only at day boundaries (a day larger than `limit` is the one
    exception). Days before today + `horizon_days` are open; the rest sit in one
    expandable blockquote at the end, which is never split unless it alone
    exceeds `limit`. `horizon_days=None` keeps every day open - an alert about
    new work must show it, not fold it away. The chunks' items together are
    exactly `items`.
    """
    if not items:
        return [(Strings.get("no_assignments", user_lang), [])]

    header = _bold(Strings.get(header_key, user_lang))
    days = group_items(items)
    if horizon_days is None:
        open_days, later_days = days, []
    else:
        horizon = now.date() + datetime.timedelta(days=horizon_days)
        open_days = [(d, s) for d, s in days if d < horizon]
        later_days = [(d, s) for d, s in days if d >= horizon]

    # Every chunk starts with the header, so each unit must fit next to it.
    day_limit = limit - _tg_len(header) - _tg_len(DAY_SEPARATOR)
    units: list[Chunk] = []
    for day, subjects in open_days:
        units.extend(_day_pieces(day, subjects, user_lang, now, day_limit))

    if later_days:
        count = sum(len(i) for _, subjects in later_days for _, i in subjects)
        title = _bold(Strings.get("show_later", user_lang, count=count))
        wrapper = _tg_len(_BLOCKQUOTE_OPEN) + _tg_len(_BLOCKQUOTE_CLOSE)
        inner_limit = day_limit - wrapper - _tg_len(title) - _tg_len(DAY_SEPARATOR)
        later_units: list[Chunk] = []
        for day, subjects in later_days:
            later_units.extend(_day_pieces(day, subjects, user_lang, now, inner_limit))
        bodies = _pack(later_units, DAY_SEPARATOR, inner_limit)
        # The title is only on the first blockquote; later ones exist solely
        # when the block alone is over the limit.
        for index, (body, body_items) in enumerate(bodies):
            prefix = f"{title}{DAY_SEPARATOR}" if index == 0 else ""
            units.append(
                (f"{_BLOCKQUOTE_OPEN}{prefix}{body}{_BLOCKQUOTE_CLOSE}", body_items)
            )

    chunks: list[Chunk] = []
    current = header
    current_items: list[dict] = []
    for text, unit_items in units:
        candidate = f"{current}{DAY_SEPARATOR}{text}"
        if _tg_len(candidate) > limit and current_items:
            # A follow-up message without the title reads like an unrequested
            # /show reply, so it repeats the header.
            chunks.append((current, current_items))
            current = f"{header}{DAY_SEPARATOR}{text}"
            current_items = list(unit_items)
        else:
            current = candidate
            current_items = current_items + unit_items
    chunks.append((current, current_items))
    return chunks


def render_assignments(
    items: list[dict],
    user_lang,
    now: datetime.datetime,
    limit: int = MESSAGE_LIMIT,
    header_key: str = "show_header",
    horizon_days: int | None = HORIZON_DAYS,
) -> list[str]:
    """HTML message chunks for a list of items, each at most `limit` UTF-16 units.

    The text-only view of `render_assignment_chunks`, which `/show` uses.
    """
    chunks = render_assignment_chunks(
        items, user_lang, now, limit, header_key, horizon_days
    )
    return [text for text, _ in chunks]
