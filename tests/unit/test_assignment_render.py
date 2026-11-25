import datetime
import os
import re
import time

import pytest

from app.strings import Language
from app.utils import assignment_render as render
from app.utils.assignment_render import render_assignments

# A Wednesday, half past midnight in Seoul: the moment at which a host running
# in California is still on the previous calendar day.
NOW = datetime.datetime(2026, 11, 25, 0, 30)


def item(type_, subject, title, **delta):
    """An item due `delta` after NOW (timedelta kwargs)."""
    return {
        "type": type_,
        "subject": subject,
        "title": title,
        "expire_at": NOW + datetime.timedelta(**delta),
    }


def at(type_, subject, title, day, hour=12, minute=0):
    """An item due on the given calendar day of NOW's month offset."""
    base = NOW.date() + datetime.timedelta(days=day)
    return {
        "type": type_,
        "subject": subject,
        "title": title,
        "expire_at": datetime.datetime.combine(base, datetime.time(hour, minute)),
    }


def render_one(items, lang=Language.EN, now=NOW, limit=4096):
    chunks = render_assignments(items, lang, now, limit)
    assert len(chunks) == 1
    return chunks[0]


def telegram_len(text):
    """Telegram counts the 4096 limit in UTF-16 code units, not code points."""
    return len(text.encode("utf-16-le")) // 2


def balanced(chunk):
    for tag in ("b", "blockquote"):
        opened = len(re.findall(rf"<{tag}[ >]", chunk))
        closed = chunk.count(f"</{tag}>")
        assert opened == closed, (tag, chunk)


def test_groups_by_day_then_subject_then_item():
    text = render_one(
        [
            at("homeworks", "Networks", "N-report", 0, 18),
            at("homeworks", "Algorithms", "A-report", 0, 20),
            at("quizzes", "Networks", "N-quiz", 0, 22),
            at("lectures", "Algorithms", "A-lecture", 2, 9),
        ]
    )
    lines = text.split("\n")
    nbsp = render.INDENT
    assert lines[0] == "<b>📋 Tasks</b>"
    assert lines[2] == "<b>Today</b>"
    assert lines[3:8] == [
        "Networks",
        f"{nbsp}📝 N-report · in 17h 30m",
        f"{nbsp}🧠 N-quiz · in 21h 30m",
        "Algorithms",
        f"{nbsp}📝 A-report · in 19h 30m",
    ]
    assert "<b>Fri, 27 Nov · in 2 days</b>" in text


def test_subjects_are_ordered_by_their_earliest_deadline():
    text = render_one(
        [
            at("homeworks", "Zebra", "z1", 1, 8),
            at("homeworks", "Alpha", "a1", 1, 9),
            at("homeworks", "Zebra", "z2", 1, 23),
        ]
    )
    assert text.index("Zebra") < text.index("Alpha")
    assert text.index("z1") < text.index("z2") < text.index("Alpha") < text.index("a1")


def test_ties_are_broken_by_type_order():
    same = datetime.datetime(2026, 11, 28, 12, 0)
    types = ["discussions", "team_projects", "quizzes", "homeworks", "lectures"]
    items = [
        {"type": t, "subject": "S", "title": t, "expire_at": same} for t in types
    ]
    text = render_one(items)
    positions = [text.index(f" {t}") for t in render.TYPE_ORDER]
    assert positions == sorted(positions)


def test_day_headers_today_tomorrow_and_date():
    text = render_one(
        [
            at("homeworks", "S", "a", 0),
            at("homeworks", "S", "b", 1),
            at("homeworks", "S", "c", 3),
        ]
    )
    assert "<b>Today</b>" in text
    assert "<b>Tomorrow</b>" in text
    assert "<b>Sat, 28 Nov · in 3 days</b>" in text


def test_horizon_day_six_is_open_and_day_seven_goes_to_later():
    chunks = render_assignments(
        [at("homeworks", "S", "six", 6), at("homeworks", "S", "seven", 7)],
        Language.EN,
        NOW,
    )
    text = chunks[0]
    quote_start = text.index("<blockquote expandable>")
    assert text.index("six") < quote_start < text.index("seven")
    assert "<b>🗓 Later (1)</b>" in text
    assert text.endswith("</blockquote>")


def test_later_block_counts_items_and_keeps_day_structure():
    text = render_one(
        [
            at("homeworks", "S", "now", 0),
            at("homeworks", "A", "x", 10),
            at("quizzes", "B", "y", 10),
            at("homeworks", "A", "z", 12),
        ]
    )
    inside = text[text.index("<blockquote expandable>") :]
    assert "Later (3)" in inside
    assert inside.count("<b>") == 1 + 2  # title and 2 day headers; subjects are plain
    assert "\nA\n" in inside and "\nB\n" in inside
    assert "now" not in inside


def test_only_the_next_24_hours_get_an_exact_time():
    text = render_one(
        [
            item("homeworks", "S", "soon", hours=3, minutes=20),
            item("homeworks", "S", "edge", hours=24),
            item("homeworks", "S", "far", hours=24, minutes=1),
        ]
    )
    assert "soon · in 3h 20m" in text
    assert "edge · in 1d\n" in text
    assert "far\n" in text + "\n"
    assert not re.search(r"far.*in ", text)


def test_zero_unit_rule_is_kept():
    left = datetime.timedelta(days=1, minutes=58)
    assert render.format_time_left(left, Language.EN) == "1d 0h 58m"
    assert render.format_time_left(datetime.timedelta(minutes=45), Language.EN) == "45m"
    assert render.format_time_left(datetime.timedelta(seconds=-30), Language.EN) == "0m"


def test_trailing_zero_units_are_dropped():
    fmt = render.format_time_left
    assert fmt(datetime.timedelta(hours=5), Language.EN) == "5h"
    assert fmt(datetime.timedelta(days=1), Language.EN) == "1d"
    assert fmt(datetime.timedelta(days=2, hours=3), Language.EN) == "2d 3h"
    assert fmt(datetime.timedelta(hours=5), Language.RU) == "5ч"
    assert fmt(datetime.timedelta(hours=5), Language.KO) == "5시간"


def test_subject_line_is_plain_under_a_bold_day():
    text = render_one([item("homeworks", "Networks", "HW", days=3)])
    assert "\nNetworks\n" in text
    assert "<b>Networks</b>" not in text


@pytest.fixture
def host_tz():
    """Set the process TZ for the test, undoing it afterwards. tzset does not
    exist on Windows; there only the env var changes, which is still enough to
    catch code that reads it, and the assertions run either way."""
    original = os.environ.get("TZ")

    def _set(name):
        os.environ["TZ"] = name
        if hasattr(time, "tzset"):
            time.tzset()

    yield _set
    if original is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = original
    if hasattr(time, "tzset"):
        time.tzset()


def test_rendering_does_not_depend_on_the_host_timezone(host_tz):
    items = [
        at("homeworks", "S", "a", 0, 8),
        at("homeworks", "S", "b", 1, 8),
        at("homeworks", "S", "c", 4, 8),
        at("homeworks", "S", "d", 9, 8),
    ]
    host_tz("Asia/Seoul")
    seoul = render_assignments(items, Language.EN, NOW)
    host_tz("America/Los_Angeles")
    california = render_assignments(items, Language.EN, NOW)

    assert california == seoul
    assert "<b>Today</b>" in california[0]


def test_html_in_klas_values_is_escaped():
    text = render_one(
        [
            {
                "type": "homeworks",
                "subject": "<script>alert(1)</script>",
                "title": "a < b & c",
                "expire_at": NOW + datetime.timedelta(hours=5),
            }
        ]
    )
    assert "a &lt; b &amp; c" in text
    assert "\n&lt;script&gt;alert(1)&lt;/script&gt;\n" in text
    assert "<script>" not in text
    balanced(text)


def _many_items(days, per_day):
    items = []
    for d in range(days):
        for n in range(per_day):
            items.append(
                at("homeworks", f"Subject {n % 4}", f"Task {d}-{n} " + "x" * 40, d, 8 + n % 12)
            )
    return items


def test_chunks_respect_the_limit_and_never_split_a_day():
    items = _many_items(days=6, per_day=25) + [
        at("homeworks", "L", f"later {n}", 20 + n) for n in range(3)
    ]
    chunks = render_assignments(items, Language.EN, NOW)

    assert len(chunks) > 1
    for chunk in chunks:
        assert telegram_len(chunk) <= 4096
        balanced(chunk)
    open_headers = ["<b>Today</b>", "<b>Tomorrow</b>"] + [
        f"<b>{h}" for h in ("Fri, 27", "Sat, 28", "Sun, 29", "Mon, 30")
    ]
    for header in open_headers:
        assert sum(chunk.count(header) for chunk in chunks) == 1
    assert chunks[0].startswith("<b>📋 Tasks</b>")
    # The Later block is intact and the last thing sent.
    assert chunks[-1].count("<blockquote expandable>") == 1
    assert chunks[-1].endswith("</blockquote>")
    assert "Later (3)" in chunks[-1]
    assert sum(c.count("<blockquote expandable>") for c in chunks) == 1


def test_later_block_moves_whole_to_a_new_chunk_when_it_does_not_fit():
    open_items = [at("homeworks", "S", "o" * 150 + str(n), 0, 8 + n % 10) for n in range(20)]
    later = [at("homeworks", "L", "later" + "y" * 150, 10 + n) for n in range(8)]
    chunks = render_assignments(open_items + later, Language.EN, NOW)

    assert len(chunks) == 2
    assert chunks[1].startswith("<blockquote expandable>")
    assert "<blockquote" not in chunks[0]


def test_an_oversized_day_is_split_with_balanced_tags():
    items = [
        at("homeworks", f"Subject {s}", f"T{s}-{n} " + "w" * 60, 1, 8 + n % 10)
        for s in range(6)
        for n in range(15)
    ]
    chunks = render_assignments(items, Language.EN, NOW)
    assert len(chunks) > 1
    for chunk in chunks:
        assert telegram_len(chunk) <= 4096
        balanced(chunk)
    joined = "\n".join(chunks)
    for s in range(6):
        for n in range(15):
            assert f"T{s}-{n} " in joined


def test_one_subject_larger_than_a_message_is_split_at_item_lines():
    items = [at("homeworks", "Big", f"T{n} " + "w" * 100, 1, 8 + n % 10) for n in range(80)]
    chunks = render_assignments(items, Language.EN, NOW)
    assert len(chunks) > 1
    for chunk in chunks:
        assert telegram_len(chunk) <= 4096
        balanced(chunk)
        assert "\nBig\n" in chunk or chunk.startswith("<b>📋")
    assert all(f"T{n} " in "\n".join(chunks) for n in range(80))


def test_emoji_heavy_lines_are_measured_in_utf16_units():
    # Short titles make the emoji a large share of every line; each one is two
    # UTF-16 units, so a check by len() would pack well past what Telegram takes.
    items = [at("lectures", "Big", f"t{n}", 1, 8 + n % 10) for n in range(1500)]
    chunks = render_assignments(items, Language.EN, NOW)
    assert len(chunks) > 1
    for chunk in chunks:
        assert telegram_len(chunk) <= 4096
        balanced(chunk)


def test_an_oversized_later_block_is_split_into_balanced_blockquotes():
    later = [at("homeworks", f"S{n % 5}", f"t{n} " + "z" * 80, 10 + n // 3) for n in range(150)]
    chunks = render_assignments(later, Language.EN, NOW)
    assert len(chunks) > 1
    for chunk in chunks:
        assert telegram_len(chunk) <= 4096
        balanced(chunk)


def test_empty_list_gives_the_celebration_text():
    chunks = render_assignments([], Language.EN, NOW)
    assert len(chunks) == 1
    assert "<" not in chunks[0]


@pytest.mark.parametrize("lang", list(Language))
def test_every_language_renders_without_raw_keys(lang):
    items = [
        at("homeworks", "S", "a", 0, 8),
        at("quizzes", "S", "b", 1, 8),
        at("lectures", "S", "c", 3, 8),
        at("discussions", "S", "d", 12, 8),
    ]
    text = render_one(items, lang)
    for key in ("show_", "weekday_short", "month_short"):
        assert key not in text
    assert "{" not in text and "}" not in text
    balanced(text)


def test_localized_headers_are_natural():
    items = [at("homeworks", "S", "c", 5, 8), at("homeworks", "S", "d", 9, 8)]
    ko = render_one(items, Language.KO)
    ru = render_one(items, Language.RU)
    assert "<b>11월 30일 (월) · 5일 후</b>" in ko
    assert "<b>Пн, 30 ноя · через 5 дн.</b>" in ru
    assert "이후 (1)" in ko
    assert "Позже (1)" in ru
