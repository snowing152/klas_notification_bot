# Notifications

Everything the bot sends without being asked: the deadline loop
(`app/services/notifications.py`), feature announcements
(`app/services/announcements.py`) and the admin broadcast (`app/handlers/admin.py`).

## The loop

`main()` starts `start_notification_service` next to polling
([ARCHITECTURE.md](ARCHITECTURE.md#two-concurrent-tasks)). The notification loop (`check_todos`) sleeps
`NOTIFICATION_CHECK_INTERVAL` (30 min), then, for every registered user, opens their kept KLAS
session ([KLAS.md](KLAS.md)) and emits at most one message per assignment per hour
threshold (24/12/6/3/2/1), plus one when an assignment first appears. What has already
been sent lives in SQLite - see below.

The sleep comes first, so nothing is checked for the first 30 minutes after a start.
`NOTIFICATION_CHECK_INTERVAL` is a `Settings` field in `app/config.py` (seconds, default
1800) and can be overridden from the environment.

One cycle, per user (`_process_user`):

1. Read the user's language, `notification_state`, `user_settings` and everything
   already sent. If the history cannot be read the user is skipped — sending blind would
   repeat what they already have.
2. `open_session(user)` (`app/services/klas_session.py`). It extends the user's kept
   session, and when there is none it tries one silent login (no push;
   [KLAS.md](KLAS.md#login-and-the-second-factor)). The cycle itself never sends a push.
   `UNAVAILABLE` (KLAS unreachable) is a failed user. `NEEDS_LOGIN` and
   `PASSWORD_REJECTED` end the user's cycle as a success: once, outside quiet hours, they
   get `klas_login_required` with a "Log in to KLAS" button, or `credentials_expired`
   without one and `klas_sessions.login_prompt_sent` is set (only after
   Telegram accepted it; a successful login resets it). Existing users have no session row
   after this shipped, so each got one prompt on the first daytime cycle.
3. `get_todo_list()`. `None` means KLAS could not be read (a failed user); `[]` means no
   subjects, which is a success with nothing to do. A subject marked `incomplete` had a
   category KLAS refused ([KLAS.md](KLAS.md#the-client)): the cycle goes on with what was
   read, except for a user not seeded yet, who is left for the next cycle as a failed
   one — seeding from part of the picture would announce the rest as new.
4. `_collect_messages` picks what is due against a single `timezone.now()`
   ([TIMEZONE.md](TIMEZONE.md)): one "new" item per assignment not yet announced, and for
   each assignment **only the tightest threshold its deadline still fits in**. At 2h30m
   left that is the 3h alert; the 6/12/24h ones are past, not pending. Assignments
   already past their deadline are skipped even though KLAS still lists them.
5. Render and send, one message per kind (more when it does not fit, see below) — new
   assignments first, then one per threshold — with `SEND_DELAY_SECONDS = 1` between them.
6. Prune `sent_notifications` rows for assignments KLAS no longer lists — unless the
   read was incomplete, when a missing assignment may only be one KLAS did not answer
   about. The log then has `Kept the notification history of <user id> as it is`.

Users are processed `MAX_CONCURRENT_USERS = 5` at a time. That is deliberately small:
KLAS drops connections under light load already, and one user is itself several requests.
However many users are being read, `MAX_CONCURRENT_REQUESTS` in `kw.py` bounds what KLAS
receives at one moment ([KLAS.md](KLAS.md#the-client)); a `/show` during a cycle shares
those slots and is slower for it.
`_process_user` handles its own errors, so one broken account never aborts a cycle.

Each cycle ends with one summary line — `Notification cycle finished: all N users
checked, X notifications sent` at INFO, or the same with a failed count at WARNING. Each
delivery logs `Notification sent to <user id> (threshold=3h)` or `(new assignments)`:
the user and the kind, deliberately never the assignment text. Quiet hours log
`Notification held for <user id> (threshold=6h, quiet hours)` once when a hold begins,
and `Held notification for <user id> (threshold=6h) dropped unsent: no longer due` if it
ends without a message.

## Message format and chunking

Items are plain dicts (`type`, `subject`, `title`, `expire_at`, plus `entry`, the
`(assignment_key, kind)` pair to record) and are rendered by `app/utils/assignment_render.py`
as Telegram HTML, sent with `parse_mode="HTML"` per message. KLAS values are escaped there.

- **New in KLAS** - the `/show` layout (day, subject, items) under the bold
  `new_assignments_header`, but with every day open: `/show` folds deadlines a week or
  more away into a collapsed "Later" block, and most work is posted that far ahead, so the
  alert would hide what it announces (`render_assignment_chunks(..., header_key=...,
  horizon_days=None)`).
- **Deadline reminder** - one bold `reminder_header` (urgency emoji from `TIME_THRESHOLDS`,
  the threshold in hours), then subject, then items, each with the exact time left. No day
  headers, no footer: everything in it is due within 24 hours (`render_reminder_chunks`).

A message over 4096 UTF-16 units is split - new assignments at day boundaries, reminders at
subject boundaries, a single day or subject larger than a message at item lines. Each chunk
repeats the bold title (and the day header, when a day is split) and comes with the items
it carries. Titles and subject names are capped at `MAX_TEXT_UNITS` (200 UTF-16 units,
ending in `…`), so one line can never outgrow a message on its own.
`_deliver` sends the chunks in order and calls `_record_sent` for a chunk's entries right
after Telegram accepted it; the first refusal stops the rest. What was not sent stays
unrecorded, so the next cycle selects only those items again: nothing is announced twice,
and nothing is recorded that was not delivered. Before this, one oversized message was
refused by Telegram on every cycle, for ever.

## Thresholds

| Constant | Value | Meaning |
|---|---|---|
| `TIME_THRESHOLDS` | 24, 12, 6, 3, 2, 1 hours | the full set, each with its emoji |
| `URGENT_THRESHOLDS` | 6, 3, 1 | what "urgent only" keeps |
| `QUIET_HOURS_EXEMPT` | 2, 1 | still sent during quiet hours |
| `NEW_ASSIGNMENT_KIND` | `"new"` | the kind recorded for the first-appearance message |

A threshold fires when the remaining time is *at or under* it, on the next cycle — so
with a 30-minute interval a "less than 3h" message can arrive with anything between 3h
and 2h30m left.

## Notification state is in SQLite, not in memory

`sent_notifications` holds one row
per (user, assignment, kind) already delivered - `new` for the announcement when the
assignment appeared, `t<hours>` for each deadline threshold - so a restart cannot repeat
a message, and a send Telegram refused is left unrecorded and retried next cycle.
`notification_state` carries the per-user `seeded` flag (the first cycle records existing
work silently, so switching a user on never dumps a semester into the chat).
(`login_failures` and `credentials_warned` are no longer written.) `user_settings` holds the `/settings` switches, defaulting to the old behaviour;
quiet hours are read through `timezone.now()`, so they are Korean local hours.

Two things the loop does keep in memory, neither of
which decides on its own what a restart sends: `_unrecorded` (delivered, but the database
refused the row — treated as sent and written on a later cycle, so a locked database
cannot repeat a reminder every 30 minutes) and `_holding`, which only turns quiet hours
into one "held" log line when a hold begins and one "dropped unsent" line if it ends with
no message. A held reminder is not a queued one: only the tightest threshold a deadline
fits in is ever built, so a 6h reminder held overnight is gone once 3h is reached.

The schema of these tables, and how to change it for existing users, is in
[DATABASE.md](DATABASE.md).

Rules that follow from this, and that a change here has to keep:

- A row is written (`_record_sent`) only after `_send` returned `True`, per chunk. If the write
  itself fails, the entries stay in `_unrecorded` and count as sent — the message is
  already in the chat. The batch is de-duplicated and inserted with `ON CONFLICT DO
  NOTHING`, so one repeated pair can never make every later write fail.
- `get_notification_state` and `get_user_settings` return `None` when the database cannot
  be read and for an unregistered user. `_process_user` then skips the user for the cycle:
  reading `None` as "not seeded" would re-seed (record new work as announced without
  sending it), and as "no settings" would ignore the user's switches.
- `notifications.forget_user` clears `_unrecorded` and `_holding` for a user; it is called
  by `auth.delete_all_user_data` and when `/register` switches to another student number.
- `get_sent_notifications` returns `None`, not `{}`, when the database cannot be read:
  an empty dict would look like "nothing was ever sent".
- An assignment has no id in KLAS, so its identity is
  `assignment_key(subject, type, title)`. Renaming one in KLAS makes it a new assignment.
- A "new" notification the user has switched off is still *recorded*, so turning the
  setting back on does not announce everything that piled up in the meantime.
- A notification held by quiet hours is *not* recorded; the first cycle after
  `quiet_end` picks it up — if it is still the tightest threshold by then
  ([KNOWN_ISSUES.md](KNOWN_ISSUES.md)).

## The login prompt

A wrong or expired saved password is found by the silent login (or by the login button)
and answered with `credentials_expired`, which points to `/register`. The prompt is one
message per lost session, not per cycle. Quiet
hours hold it (it is not recorded, so it goes out on the first daytime cycle).

## Settings

`/settings` (`app/handlers/settings.py`) flips columns of `user_settings`:

| Setting | Column | Default | Effect |
|---|---|---|---|
| New assignments | `new_assignment_alerts` | on | announce work when it appears |
| Deadline reminders | `deadline_alerts` | on | the threshold messages |
| Urgent only | `urgent_thresholds_only` | off | keeps only 6, 3 and 1 hours |
| Quiet hours | `quiet_hours` | on | holds everything except the 2h and 1h messages |
| | `quiet_start`, `quiet_end` | 1, 8 | the window, in Korean local hours; not editable in the UI |

The window opens at 01:00 rather than in the evening because KLAS deadlines are usually
23:59, so the 24h warning falls due at midnight; a window opening at 23:00 held it until
morning, when sixteen hours were left. `in_quiet_hours` also handles a window that
crosses midnight.

## Feature announcements

`ANNOUNCEMENTS` in `app/services/announcements.py` is a dict of one-time messages keyed
by name; `announce_pending()` runs at every startup and sends each entry to every user
who has not seen it. `announcements_seen` is keyed by announcement name, which is what makes
`app/services/announcements.py` safe to run on every startup. A redeploy cannot repeat
one, and a user who registers later still gets it. A row is written only after the send succeeded. An entry
with a `"setting"` gets buttons that switch that `user_settings` column; without one it
is plain text. The dict is empty at the moment.

## Admin broadcast

`/notify en: Hello | ko: 안녕하세요` (`app/handlers/admin.py`) sends a message, or a photo
with that caption, to every row in `users`. Only the account whose id equals `ADMIN_ID`
may use it. Each user gets the part matching their language, falling back to `en:`.
Nothing is recorded, so sending it twice sends it twice.
