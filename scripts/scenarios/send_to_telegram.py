"""Send rendered notification and /show scenarios to a real Telegram chat.

A manual check for what unit tests cannot show: how the HTML actually looks on
a phone. Every scenario goes through the production code path (the renderer,
`send_new_assignments` / `send_notification` and `_deliver`), with fake KLAS
data instead of real requests.

Safety:
- the database is a throwaway SQLite file in a temp directory (DATA_DIR is set
  before `app` is imported), so the real bot_users.db is never opened;
- nothing talks to KLAS or the library;
- messages go only to ADMIN_ID from .env, or to --chat-id.

Usage (from the repository root):
    python scripts/scenarios/send_to_telegram.py --list
    python scripts/scenarios/send_to_telegram.py --dry-run        # print, send nothing
    python scripts/scenarios/send_to_telegram.py                  # send all
    python scripts/scenarios/send_to_telegram.py new_far long_titles

Each scenario also runs structural checks on its chunks (length in UTF-16
units, balanced tags, header on every chunk, every item delivered once) and
prints PASS/FAIL. The exit code is 1 if any check failed.
"""

import argparse
import asyncio
import datetime
import itertools
import os
import re
import sys
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# Must happen before anything under app/ is imported: app/config.py reads
# DATA_DIR at import time and the database engine is created from it.
_TEMP_DIR = tempfile.TemporaryDirectory(prefix="kwbot-scenarios-", ignore_cleanup_errors=True)
os.environ["DATA_DIR"] = _TEMP_DIR.name
os.environ.pop("DATABASE_URL", None)
if not os.getenv("ENCRYPTION_KEY"):
    # Nothing here encrypts, but a module may load the cipher on import; a
    # throwaway key keeps it away from the real encryption_key.key.
    from cryptography.fernet import Fernet

    os.environ["ENCRYPTION_KEY"] = Fernet.generate_key().decode()

from app.bot import bot  # noqa: E402
from app.config import settings  # noqa: E402
from app.database.database import engine, get_sent_notifications, init_db  # noqa: E402
from app.services import notifications  # noqa: E402
from app.strings import Language, Strings  # noqa: E402
from app.utils import timezone  # noqa: E402
from app.utils.assignment_render import (  # noqa: E402
    MESSAGE_LIMIT,
    render_assignment_chunks,
    render_reminder_chunks,
)

LIMIT = MESSAGE_LIMIT
_ENTRY_IDS = itertools.count(1)


def tg_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def make_item(subject, type_, title, expire_at, kind="new"):
    """A renderer item, as `_collect_messages` builds it."""
    # Scenarios reuse the same fake assignments; a run-unique prefix keeps
    # their records from colliding on the (user, key, kind) unique constraint.
    key = f"{next(_ENTRY_IDS)}:{notifications.assignment_key(subject, type_, title)}"
    return {
        "type": type_,
        "subject": subject,
        "title": title,
        "expire_at": expire_at,
        "entry": (key, kind),
    }


def due(now, days=0, hours=0, minutes=0, at=None):
    """A deadline `days` calendar days after now, at `at` (h, m) or now+offset."""
    if at is not None:
        day = (now + datetime.timedelta(days=days)).date()
        return datetime.datetime.combine(day, datetime.time(*at))
    return now + datetime.timedelta(days=days, hours=hours, minutes=minutes)


# --- checks -----------------------------------------------------------------


@dataclass
class Report:
    failures: list[str]

    def check(self, condition: bool, message: str) -> None:
        if not condition:
            self.failures.append(message)


def check_chunks(report, chunks, header, expected_items, folded_allowed):
    """Structural checks every chunked message has to pass."""
    seen = []
    for index, (text, items) in enumerate(chunks, 1):
        report.check(tg_len(text) <= LIMIT, f"chunk {index} is {tg_len(text)} units > {LIMIT}")
        report.check(text.startswith(f"<b>{header}</b>"), f"chunk {index} lacks the header")
        for tag in ("b", "blockquote"):
            opened = len(re.findall(rf"<{tag}[ >]", text))
            report.check(opened == text.count(f"</{tag}>"), f"chunk {index}: unbalanced <{tag}>")
        if not folded_allowed:
            report.check("<blockquote" not in text, f"chunk {index} folds items away")
        seen.extend(item["entry"] for item in items)
    expected = sorted(item["entry"] for item in expected_items)
    report.check(sorted(seen) == expected, "chunks do not carry exactly the given items")


async def check_recorded(report, user_id, items):
    sent = await get_sent_notifications(user_id)
    missing = [key for key, kind in (i["entry"] for i in items) if kind not in sent.get(key, set())]
    report.check(not missing, f"{len(missing)} delivered items were not recorded")


# --- scenarios --------------------------------------------------------------

Scenario = Callable[[str, bool, Report], Awaitable[None]]
SCENARIOS: dict[str, tuple[str, Scenario]] = {}


def scenario(name, description):
    def register(func):
        SCENARIOS[name] = (description, func)
        return func

    return register


async def _send_new(user_id, items, lang, dry_run, report):
    """Render, check, then (unless dry-run) send through the real path."""
    now = timezone.now()
    header = Strings.get("new_assignments_header", lang)
    chunks = render_assignment_chunks(
        items, lang, now, header_key="new_assignments_header", horizon_days=None
    )
    check_chunks(report, chunks, header, items, folded_allowed=False)
    if dry_run:
        _print_chunks(chunks)
        return
    delivered = await notifications.send_new_assignments(items, user_id, lang, now)
    report.check(delivered == len(chunks), f"sent {delivered} of {len(chunks)} chunks")
    await check_recorded(report, user_id, items)


async def _send_reminder(user_id, items, threshold, lang, dry_run, report):
    now = timezone.now()
    emoji = notifications.TIME_THRESHOLDS[threshold]
    header = Strings.get("reminder_header", lang, emoji=emoji, hours=threshold)
    chunks = render_reminder_chunks(items, header, lang, now)
    check_chunks(report, chunks, header, items, folded_allowed=False)
    if dry_run:
        _print_chunks(chunks)
        return
    delivered = await notifications.send_notification(items, user_id, threshold, lang, now)
    report.check(delivered == len(chunks), f"sent {delivered} of {len(chunks)} chunks")
    await check_recorded(report, user_id, items)


def _print_chunks(chunks):
    for index, (text, items) in enumerate(chunks, 1):
        print(f"  --- chunk {index}: {tg_len(text)} units, {len(items)} items ---")
        print("  " + text.replace("\n", "\n  "))


def _week_of_work(now, kind="new"):
    return [
        make_item("Algorithms", "homeworks", "Report 3: sorting", due(now, hours=5), kind),
        make_item("Algorithms", "lectures", "Week 12 lecture", due(now, days=1, at=(23, 59)), kind),
        make_item("Computer Networks", "quizzes", "Quiz 4", due(now, days=1, at=(10, 0)), kind),
        make_item("Computer Networks", "team_projects", "Term project draft", due(now, days=4, at=(18, 0)), kind),
        make_item("English", "discussions", "Discuss the article", due(now, days=12, at=(23, 59)), kind),
        make_item("Databases", "homeworks", "ER diagram", due(now, days=20, at=(23, 59)), kind),
    ]


@scenario("new_mixed", "New in KLAS: today, tomorrow, +4, +12, +20 days, EN")
async def new_mixed(user_id, dry_run, report):
    await _send_new(user_id, _week_of_work(timezone.now()), Language.EN, dry_run, report)


@scenario("new_far", "New in KLAS: only deadlines 10+ days away (must stay open)")
async def new_far(user_id, dry_run, report):
    now = timezone.now()
    items = [
        make_item("Operating Systems", "homeworks", "Shell project", due(now, days=10, at=(23, 59))),
        make_item("Operating Systems", "lectures", "Week 14 lecture", due(now, days=14, at=(23, 59))),
    ]
    await _send_new(user_id, items, Language.EN, dry_run, report)


@scenario("new_many", "New in KLAS: 70 items, split over several messages")
async def new_many(user_id, dry_run, report):
    now = timezone.now()
    subjects = ["Algorithms", "Computer Networks", "Databases", "English", "Physics"]
    types = ["homeworks", "lectures", "quizzes", "team_projects", "discussions"]
    items = [
        make_item(
            subjects[n % 5],
            types[n % 5],
            f"Item {n:02d}: a reasonably long assignment title to fill the message",
            # From tomorrow on: a fixed hour today may already be past, and
            # the notification cycle never sends past items.
            due(now, days=1 + n // 5, at=(9 + n % 12, 0)),
        )
        for n in range(70)
    ]
    await _send_new(user_id, items, Language.EN, dry_run, report)


@scenario("long_titles", "Huge title, 500 ampersands, HTML-looking text, emoji title")
async def long_titles(user_id, dry_run, report):
    now = timezone.now()
    items = [
        make_item("Algorithms", "homeworks", "Very long title " + "lorem ipsum " * 300, due(now, hours=6)),
        make_item("R&D <Lab>", "homeworks", "&" * 500, due(now, days=2, at=(12, 0))),
        make_item("Web", "quizzes", "<b>not bold</b> & <i>not italic</i> <script>x</script>", due(now, days=3, at=(9, 0))),
        make_item("Fun", "lectures", "😀" * 400, due(now, days=5, at=(15, 0))),
    ]
    await _send_new(user_id, items, Language.EN, dry_run, report)


@scenario("korean", "New in KLAS in Korean, with Korean titles")
async def korean(user_id, dry_run, report):
    now = timezone.now()
    items = [
        make_item("자료구조", "homeworks", "과제 3: 힙 구현", due(now, hours=9)),
        make_item("자료구조", "lectures", "12주차 강의", due(now, days=1, at=(23, 59))),
        make_item("영어", "discussions", "논문을 읽고 토론", due(now, days=9, at=(23, 59))),
    ]
    await _send_new(user_id, items, Language.KO, dry_run, report)


@scenario("russian", "New in KLAS in Russian")
async def russian(user_id, dry_run, report):
    await _send_new(user_id, _week_of_work(timezone.now()), Language.RU, dry_run, report)


@scenario("reminder_3h", "Deadline reminder, 3h threshold, two subjects")
async def reminder_3h(user_id, dry_run, report):
    now = timezone.now()
    items = [
        make_item("Algorithms", "homeworks", "Report 3", due(now, hours=2, minutes=40), "t3"),
        make_item("Algorithms", "quizzes", "Quiz 5", due(now, hours=2, minutes=55), "t3"),
        make_item("Databases", "lectures", "Week 12 lecture", due(now, hours=2, minutes=10), "t3"),
    ]
    await _send_reminder(user_id, items, 3, Language.EN, dry_run, report)


@scenario("reminder_1h_ko", "Deadline reminder, 1h threshold, Korean")
async def reminder_1h_ko(user_id, dry_run, report):
    now = timezone.now()
    items = [make_item("자료구조", "homeworks", "과제 3: 힙 구현", due(now, minutes=45), "t1")]
    await _send_reminder(user_id, items, 1, Language.KO, dry_run, report)


@scenario("show_view", "/show layout: open week plus the collapsed Later block")
async def show_view(user_id, dry_run, report):
    now = timezone.now()
    items = _week_of_work(now)
    header = Strings.get("show_header", Language.EN)
    chunks = render_assignment_chunks(items, Language.EN, now)
    check_chunks(report, chunks, header, items, folded_allowed=True)
    report.check("<blockquote expandable>" in chunks[-1][0], "/show lost its Later block")
    if dry_run:
        _print_chunks(chunks)
        return
    for text, _ in chunks:
        await bot.send_message(chat_id=user_id, text=text, parse_mode="HTML")


@scenario("refusal", "Telegram refuses chunk 2: only chunk 1 is recorded (no message sent)")
async def refusal(user_id, dry_run, report):
    # Runs entirely against a fake bot; it checks the delivery contract on the
    # real _deliver and the real database code.
    now = timezone.now()
    items = [
        make_item(f"Subject {n % 4}", "homeworks", f"t{n} " + "x" * 150, due(now, days=n % 6, at=(12, 0)))
        for n in range(80)
    ]
    chunks = render_assignment_chunks(
        items, Language.EN, now, header_key="new_assignments_header", horizon_days=None
    )
    report.check(len(chunks) >= 2, "scenario needs at least two chunks")

    calls = {"n": 0}

    async def flaky_send(chat_id, text, reply_markup=None, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("simulated Telegram refusal")

    fake_user = "999000111"
    original_send, original_delay = notifications.bot.send_message, notifications.SEND_DELAY_SECONDS
    notifications.bot.send_message = flaky_send
    notifications.SEND_DELAY_SECONDS = 0
    try:
        delivered = await notifications.send_new_assignments(items, fake_user, Language.EN, now)
    finally:
        notifications.bot.send_message = original_send
        notifications.SEND_DELAY_SECONDS = original_delay

    report.check(delivered == 1, f"expected 1 delivered chunk, got {delivered}")
    sent = await get_sent_notifications(fake_user)
    first_keys = {key for key, _ in (i["entry"] for i in chunks[0][1])}
    report.check(set(sent) == first_keys, "recorded keys differ from the first chunk's items")
    if dry_run:
        print(f"  {len(chunks)} chunks, chunk 1 recorded ({len(first_keys)} items), rest left for retry")


# --- runner -----------------------------------------------------------------


async def run(names, chat_id, dry_run) -> int:
    await init_db()
    failed = 0
    try:
        for name in names:
            description, func = SCENARIOS[name]
            print(f"[{name}] {description}")
            report = Report([])
            try:
                await func(chat_id, dry_run, report)
            except Exception as e:  # report and continue with the next scenario
                report.failures.append(f"raised {type(e).__name__}: {e}")
            if report.failures:
                failed += 1
                for failure in report.failures:
                    print(f"  FAIL: {failure}")
            else:
                print("  PASS")
            if not dry_run:
                # Stay well under Telegram's per-chat rate limit.
                await asyncio.sleep(1.5)
    finally:
        await bot.session.close()
        # SQLite keeps the file open until the pool is closed; on Windows that
        # blocks deleting the temp directory.
        await engine.dispose()
    print(f"\n{len(names) - failed} passed, {failed} failed")
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("names", nargs="*", help="scenarios to run (default: all)")
    parser.add_argument("--list", action="store_true", help="list scenarios and exit")
    parser.add_argument("--dry-run", action="store_true", help="print instead of sending")
    parser.add_argument("--chat-id", default=str(settings.ADMIN_ID), help="default: ADMIN_ID")
    args = parser.parse_args()

    if args.list:
        for name, (description, _) in SCENARIOS.items():
            print(f"{name:16} {description}")
        return 0
    unknown = [n for n in args.names if n not in SCENARIOS]
    if unknown:
        parser.error(f"unknown scenario(s): {', '.join(unknown)}")

    try:
        return asyncio.run(run(args.names or list(SCENARIOS), args.chat_id, args.dry_run))
    finally:
        _TEMP_DIR.cleanup()


if __name__ == "__main__":
    sys.exit(main())
