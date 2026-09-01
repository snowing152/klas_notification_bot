# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Telegram bot (aiogram 3, asyncio) for Kwangwoon University students. It scrapes/calls
three unofficial KW endpoints on the user's behalf — KLAS (`klas.kw.ac.kr`, assignments
and grades), the mobile-ID service (`mobileid.kw.ac.kr`, library QR), and the public
`kw.ac.kr` site (news, cafeteria menu) — plus Google Gemini for the chat assistant.
Python 3.12 (`.python-version`); `requirements.txt` pins exact versions.

This is a fork of ChoiVadim/klas_notification_bot, continued with the original author's
permission.

## Commands

```bash
pip install -r requirements-dev.txt   # runtime + test deps
python main.py                        # run the bot
pytest                                # full suite (pytest.ini already adds -v and coverage)
pytest tests/unit/test_qr.py -v       # one file
pytest tests/unit/test_qr.py::test_get_qr_returns_none_on_failure   # one test
pytest --no-cov -q                    # skip the htmlcov/ report
```

`asyncio_mode = auto` is set, so `async def test_*` needs no decorator.

**Importing anything under `app/` requires `BOT_TOKEN` and `ADMIN_ID`** — `app/config.py`
raises `RuntimeError` at import time when either is missing, so the test suite needs a
`.env` (or those env vars) even though it never contacts Telegram.

## Architecture

**Layering.** `main.py` → `app/bot.py` (module-level `bot`/`dp` singletons) →
`app/handlers/*` (aiogram routing, all user-facing text) → `app/services/*` (external
I/O, no aiogram types) → `app/database/*`. Handlers own presentation; services return
plain dicts/lists. Note that `app/services/notifications.py` breaks the direction and
imports `bot` directly, because it pushes messages without an incoming update.

**Two concurrent tasks.** `main()` runs `dp.start_polling` and
`start_notification_service` under `asyncio.wait(..., FIRST_COMPLETED)` — whichever
finishes first cancels the other. The notification loop (`check_todos`) sleeps
`NOTIFICATION_CHECK_INTERVAL` (30 min), then, for every registered user, logs into KLAS
with their decrypted password and emits at most one message per assignment per hour
threshold (24/12/6/3/2/1). The already-sent set lives in an in-memory dict, so a restart
re-sends the current thresholds.

**Handler registration order matters.** `setup_handlers` registers `common` *last*
because `common.other_message` is a catch-all `dp.message.register` with no filter — it
swallows anything reaching it and routes free text to the LLM. Anything registered after
it would never fire. Registration and library-registration flows are aiogram FSM states
(`app/handlers/auth.py`) with `MemoryStorage`, so an in-progress registration is lost on
restart.

**Localization.** `app/strings.py` holds every user-facing string in one nested dict
keyed by the `Language` enum (EN/KO/RU). `Strings.get()` never raises: it falls back
EN → the key itself, and returns the unformatted template on a bad placeholder, because
handlers call it from inside their own `except` blocks. Adding a string means adding it
to all three language dicts. `get_user_language_with_fallback()`
(`app/utils/language_utils.py`) is the only way handlers resolve language — DB row first,
then the Telegram client locale, then EN — and is likewise exception-proof, since every
handler binds `user_lang` as the first statement in its `try` and reads it in `except`.
Slash-command *descriptions* are separate, in `app/menu.py`, pushed to Telegram at
startup via `set_my_commands`.

**Credentials.** KLAS and library passwords are stored Fernet-encrypted in SQLite
(`users`, `library_users`, keyed by Telegram user id as a string). The key comes from
`ENCRYPTION_KEY` if set, else `<DATA_DIR>/encryption_key.key`. Losing the key makes every
stored password permanently undecryptable — never regenerate it on an existing database.
Handlers `await message.delete()` after credential input so the password does not stay in
the chat.

**Timezone is load-bearing.** KLAS returns Korean-local date strings with no offset, and
`app/services/kw.py` compares/subtracts them against a naive "now"; running on any other
clock computes every deadline hours off. **Date-sensitive code calls `timezone.now()`
from `app/utils/timezone.py`** (naive Seoul time via `ZoneInfo`, escape hatch `KW_TZ`) —
never `datetime.now()`. That covers `kw.py` and `food.py`'s weekday lookups.
`app/config.py` additionally overwrites the process `TZ` at import as a fallback for
everything else; it's a fallback because it depends on system tzdata a slim container may
not have, and on config being imported before the first date is resolved. Don't "fix"
either by respecting the host `TZ` — some platforms export `TZ=UTC` themselves, which is
exactly what this defends against. The `tzdata` pin in `requirements.txt` is what makes
`ZoneInfo` resolve `Asia/Seoul` without a system tz database.

**`DATA_DIR`** is where `bot_users.db` and the key file live. It is validated, not
created: a missing directory raises, because on a PaaS it almost always means an unmounted
volume, and silently writing to ephemeral storage loses every user on the next deploy.

**Sessions.** `app/services/kw.py` (`KwangwoonUniversityApi`) is an async context manager
— always use `async with`, one instance per operation. `app/services/qr.py` instead keeps
a module-level shared `aiohttp` session that `main.py` closes in its `finally`.
`app/services/news.py` and `food.py` hold module-level caches (news TTL: 1 hour).

## Testing conventions

`tests/conftest.py` has an **autouse** fixture that monkeypatches `db.engine` and
`db.AsyncSessionLocal` to a per-test tmp SQLite file. This is autouse on purpose: the DB
functions resolve `AsyncSessionLocal` at call time, so a test that forgot the fixture
would write to the real `bot_users.db`. Network-facing services are tested by
monkeypatching the module's own functions (see `tests/unit/test_qr.py`); nothing in the
suite makes real requests. Handler tests build `AsyncMock(spec=types.Message)` and must
re-assign `answer`/`reply_photo` as `AsyncMock` — aiogram's builder-style methods
otherwise come back as sync `MagicMock`s.

## Deployment

Two supported targets: systemd (`botdaemon.service`, placeholders to fill in) and Railway
(`railway.json`; worker with no exposed port). On Railway a volume must be mounted and
`DATA_DIR` pointed at it, and `ENCRYPTION_KEY` must be set as a variable. `main.py`
detects `RAILWAY_ENVIRONMENT` and skips the `logs/kwbot.log` file handler there; stdout
logging is always on.

`.gitignore` excludes `*.json` wholesale with `!railway.json` re-added — a new committed
JSON file needs its own negation.
