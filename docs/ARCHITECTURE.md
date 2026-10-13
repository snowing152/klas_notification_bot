# Architecture

How the bot is put together: what it talks to, how the code is layered, what runs
concurrently, the order handlers are registered in, and what is held in memory.

## What it talks to

Telegram bot (aiogram 3, asyncio) for Kwangwoon University students. It scrapes/calls
three unofficial KW endpoints on the user's behalf — KLAS (`klas.kw.ac.kr`, assignments
and grades), the mobile-ID service (`mobileid.kw.ac.kr`, library QR), and the public
`kw.ac.kr` site (news, cafeteria menu) — plus Google Gemini for the chat assistant.
Python 3.12 (`.python-version`); `requirements.txt` pins exact versions.

This is a fork of ChoiVadim/klas_notification_bot, continued with the original author's
permission.

By module, as the code stands:

| Host | Module | Used for |
|---|---|---|
| `klas.kw.ac.kr` | `app/services/kw.py` | login, assignments, grades and student info ([KLAS.md](KLAS.md)) |
| `did-3.kw.ac.kr` | `app/services/kw.py` (`get_student_photo`) | the student ID photo shown by `/info` |
| `mobileid.kw.ac.kr` | `app/services/qr.py` | library login and the QR pass |
| `kupis.kw.ac.kr` | `app/services/library.py` | `/search`, the library catalogue |
| `www.kw.ac.kr` | `app/services/news.py`, `app/services/food.py` | news and the cafeteria menu |
| Google Gemini | `app/services/llm.py` | the chat assistant (`GEMINI_API_KEY`, optional) |
| Telegram Bot API | `app/bot.py` | everything the user sees |

None of the KW hosts is an official API; see [KNOWN_ISSUES.md](KNOWN_ISSUES.md).

## Layering

`main.py` → `app/bot.py` (module-level `bot`/`dp` singletons) →
`app/handlers/*` (aiogram routing, all user-facing text) → `app/services/*` (external
I/O, no aiogram types) → `app/database/*`. Handlers own presentation; services return
plain dicts/lists. Note that `app/services/notifications.py` breaks the direction and
imports `bot` directly, because it pushes messages without an incoming update.
`app/services/announcements.py` does the same, for the same reason.

Around those: `app/config.py` (settings, read at import), `app/strings.py`
([LOCALIZATION.md](LOCALIZATION.md)), `app/keyboards.py`, `app/menu.py` (slash-command
descriptions), `app/middleware/antispam.py` and `app/utils/*`.

## Two concurrent tasks

`main()` runs `dp.start_polling` and
`start_notification_service` under `asyncio.wait(..., FIRST_COMPLETED)` — whichever
finishes first cancels the other. The notification loop itself is described in
[NOTIFICATIONS.md](NOTIFICATIONS.md).

Before any of that, `main.py` sets up logging — ahead of every other import from `app`,
for the reason given in [DEPLOYMENT.md](DEPLOYMENT.md#logs).

Startup, in order: `initialize_bot_menu()` pushes the command descriptions, `init_db()`
creates tables and applies data fixes ([DATABASE.md](DATABASE.md)), `setup_handlers(dp)`
registers handlers, `announce_pending()` is started as a task so polling does not wait
on it, and then the two tasks above. All of it sits inside one `try`, and a failure is
re-raised so the process exits non-zero — which is what lets the `ON_FAILURE` restart
policy in `railway.json` and `Restart=on-failure` in `botdaemon.service` act on it. The
`finally` closes the shared QR session and the bot session.

## Handler registration order matters

`setup_handlers` registers `common` *last*
because `common.other_message` is a catch-all `dp.message.register` with no filter — it
swallows anything reaching it and routes free text to the LLM. Anything registered after
it would never fire. `callbacks.process_callback_query` is likewise unfiltered for callback queries, so
every module owning its own callbacks — `settings`, `account`, and `todos`' `show_*`
filter buttons — is registered before it; anything registered after would never see a tap.
Registration and library-registration flows are aiogram FSM states
(`app/handlers/auth.py`) with `MemoryStorage`, so an in-progress registration is lost on
restart. `auth` is registered *first*, so its states swallow even commands: a user at the
"enter your student ID" prompt who types `/show` registers `/show` as their ID. The book
search state (`library.SearchStates`) sits after the commands instead, so
`library.register_search_escape` is registered ahead of everything to clear an abandoned
prompt and `SkipHandler` on to the command the user actually typed.

The order in `app/bot.py` today:

1. `library.register_search_escape`
2. `auth`
3. `todos`
4. `food`
5. `student_info`
6. `settings`
7. `account`
8. `callbacks` (unfiltered for callback queries)
9. `admin`
10. `news`
11. `library`
12. `common` (unfiltered for messages)

A new module with message handlers goes before `common`; one with callback handlers goes
before `callbacks`.

`AntiSpamMiddleware(limit=2)` sits on `dp.message`: a second message from the same user
within two seconds gets the `too_many_messages` reply and reaches no handler. It lets
aiogram's `SkipHandler`/`CancelHandler` through untouched, which the search escape above
depends on.

## Two entry points per action

Anything reachable from a button is also reachable by
command, and the handler for it takes no `Message` it can reply to — `/info`'s body lives
in `student_info.send_student_info(bot, chat_id, ...)` because the `/account` button that
calls it has only the bot's own message in hand. How the reply-keyboard buttons are
matched to handlers is in [LOCALIZATION.md](LOCALIZATION.md#quick-access-buttons).

## Sessions

`app/services/kw.py` (`KwangwoonUniversityApi`) is an async context manager
— always use `async with`, one instance per operation. `app/services/qr.py` instead keeps
a module-level shared `aiohttp` session that `main.py` closes in its `finally`.

## Caches

`app/services/news.py` and `food.py` hold module-level caches (news TTL: 1 hour), as does
`app/handlers/todos.py` (`_items_cache`, 5 min): `/show`'s filter buttons re-render from it
rather than repeating `get_todo_list()`, which is a login plus five requests per subject.
It is read *before* the database, so `auth.delete_all_user_data` must drop the user's entry
(`todos.forget_cached_items`) or a deleted account keeps being served from memory.

## What lives only in memory

Everything here is gone after a restart, by design or by omission:

| State | Where | Notes |
|---|---|---|
| FSM state of a registration or book search | `MemoryStorage` in `app/bot.py` | includes credentials typed so far |
| Assignment list per user | `todos._items_cache` | 5 minutes, dropped on account deletion |
| News | `app/services/news.py` | 1 hour |
| Cafeteria menu | `app/services/food.py` | module-level cache |
| Chat-assistant history | `app/utils/chat_history.py` | last 10 messages per chat |
| Anti-spam timestamps | `AntiSpamMiddleware` | last message time per user |
| Sent but not yet written down, and current quiet-hours holds | `_unrecorded`, `_holding` in `app/services/notifications.py` | see [NOTIFICATIONS.md](NOTIFICATIONS.md#notification-state-is-in-sqlite-not-in-memory) |
| Shared library session | `app/services/qr.py` | closed by `main.py` |

What must survive a restart is in SQLite instead — see [DATABASE.md](DATABASE.md) and
[NOTIFICATIONS.md](NOTIFICATIONS.md).
