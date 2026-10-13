# Notifications

Everything the bot sends without being asked: the deadline loop
(`app/services/notifications.py`), feature announcements
(`app/services/announcements.py`) and the admin broadcast (`app/handlers/admin.py`).

## The loop

`main()` starts `start_notification_service` next to polling
([ARCHITECTURE.md](ARCHITECTURE.md#two-concurrent-tasks)). The notification loop (`check_todos`) sleeps
`NOTIFICATION_CHECK_INTERVAL` (30 min), then, for every registered user, logs into KLAS
with their decrypted password and emits at most one message per assignment per hour
threshold (24/12/6/3/2/1), plus one when an assignment first appears. What has already
been sent lives in SQLite - see below.

The sleep comes first, so nothing is checked for the first 30 minutes after a start.
`NOTIFICATION_CHECK_INTERVAL` is a `Settings` field in `app/config.py` (seconds, default
1800) and can be overridden from the environment.

One cycle, per user (`_process_user`):

1. Read the user's language, `notification_state`, `user_settings` and everything
   already sent. If the history cannot be read the user is skipped — sending blind would
   repeat what they already have.
2. Log into KLAS. A failure counts towards the password warning below and ends the
   user's cycle.
3. `get_todo_list()`. `None` means KLAS could not be read (a failed user); `[]` means no
   subjects, which is a success with nothing to do.
4. `_collect_messages` builds what is due against a single `timezone.now()`
   ([TIMEZONE.md](TIMEZONE.md)): one "new" body per assignment not yet announced, and for
   each assignment **only the tightest threshold its deadline still fits in**. At 2h30m
   left that is the 3h alert; the 6/12/24h ones are past, not pending. Assignments
   already past their deadline are skipped even though KLAS still lists them.
5. Send, one message per kind — new assignments first, then one per threshold — with
   `SEND_DELAY_SECONDS = 1` between them.
6. Prune `sent_notifications` rows for assignments KLAS no longer lists.

Users are processed `MAX_CONCURRENT_USERS = 5` at a time. That is deliberately small:
KLAS drops connections under light load already, and one user is itself several requests.
`_process_user` handles its own errors, so one broken account never aborts a cycle.

Each cycle ends with one summary line — `Notification cycle finished: all N users
checked, X notifications sent` at INFO, or the same with a failed count at WARNING. Each
delivery logs `Notification sent to <user id> (threshold=3h)` or `(new assignments)`:
the user and the kind, deliberately never the assignment text. Quiet hours log
`Notification held for <user id> (threshold=6h, quiet hours)` once when a hold begins,
and `Held notification for <user id> (threshold=6h) dropped unsent: no longer due` if it
ends without a message.

## Thresholds

| Constant | Value | Meaning |
|---|---|---|
| `TIME_THRESHOLDS` | 24, 12, 6, 3, 2, 1 hours | the full set, each with its emoji |
| `URGENT_THRESHOLDS` | 6, 3, 1 | what "urgent only" keeps |
| `QUIET_HOURS_EXEMPT` | 2, 1 | still sent during quiet hours |
| `LOGIN_FAILURES_BEFORE_WARNING` | 3 | consecutive failed logins before the user is told |
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
work silently, so switching a user on never dumps a semester into the chat) and the
consecutive-login-failure counter behind the "your KLAS password stopped working"
warning. `user_settings` holds the `/settings` switches, defaulting to the old behaviour;
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

- A row is written (`_record_sent`) only after `_send` returned `True`. If the write
  itself fails, the entries stay in `_unrecorded` and count as sent — the message is
  already in the chat.
- `get_sent_notifications` returns `None`, not `{}`, when the database cannot be read:
  an empty dict would look like "nothing was ever sent".
- An assignment has no id in KLAS, so its identity is
  `assignment_key(subject, type, title)`. Renaming one in KLAS makes it a new assignment.
- A "new" notification the user has switched off is still *recorded*, so turning the
  setting back on does not announce everything that piled up in the meantime.
- A notification held by quiet hours is *not* recorded; the first cycle after
  `quiet_end` picks it up — if it is still the tightest threshold by then
  ([KNOWN_ISSUES.md](KNOWN_ISSUES.md)).

## The password warning

KW forces a password change every few months, and the stored one then stops working.
Each failed login increments `notification_state.login_failures`; at three in a row (an
hour and a half apart) the user gets the `credentials_expired` string once
(`credentials_warned`), telling them to run `/register` again. A successful login resets
both. A single failure is usually just KLAS.

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
