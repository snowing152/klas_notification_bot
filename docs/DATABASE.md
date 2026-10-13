# Database

One SQLite file, `bot_users.db`, through SQLAlchemy's async engine (`aiosqlite`). Models
are in `app/database/models.py`, every query in `app/database/database.py`.

## Where the file lives: `DATA_DIR`

**`DATA_DIR`** is where `bot_users.db` and the key file live. It is validated, not
created: a missing directory raises, because on a PaaS it almost always means an unmounted
volume, and silently writing to ephemeral storage loses every user on the next deploy.

Unset, it is the working directory. `app/config.py` also refuses a directory the process
cannot write to. `DATABASE_URL` overrides the whole SQLAlchemy URL and exists for tests
and experiments; `DATA_FIXES` below is SQLite-only (`PRAGMA user_version`), so pointing it
at another database engine is not supported.

## Tables

Every `user_id` is the Telegram user id, stored as a string.

| Table | Model | Holds |
|---|---|---|
| `users` | `User` | KLAS login (the student number), Fernet-encrypted KLAS password, language (`EN`/`KO`/`RU`) |
| `library_users` | `LibraryUser` | library login, encrypted library password, phone number |
| `sent_notifications` | `SentNotification` | one row per (user, assignment key, kind) already delivered |
| `notification_state` | `NotificationState` | `seeded`, `login_failures`, `credentials_warned` |
| `user_settings` | `UserSettings` | the `/settings` switches and the quiet-hours window |
| `announcements_seen` | `AnnouncementSeen` | which feature announcements a user has been shown |

How the notification tables are used is in [NOTIFICATIONS.md](NOTIFICATIONS.md); what
counts as personal data and how the passwords are encrypted is in
[SECURITY.md](SECURITY.md).

Rows in `notification_state` and `user_settings` are created with defaults on first
read (`get_notification_state`, `get_user_settings`). The database functions catch
`SQLAlchemyError` themselves and return `None`, `False` or an empty list instead of
raising — read each one's fallback before relying on it: `get_sent_notifications`
returns `None` on purpose, and `has_seen_announcement` answers `True` on an error
because a repeated announcement is worse than a missed one.

## There is no migration tool

`init_db` is `create_all`, which only adds tables, and a default
changed in `models.py` reaches new rows alone. A change that must reach existing users
goes into `DATA_FIXES` in `app/database/database.py` — each runs once per database,
counted in SQLite's `user_version`.

What that means in practice:

- **A new table** just works: `create_all` adds it on the next start.
- **A new column on an existing table** is *not* added by `create_all`. The model will
  have it, the production table will not, and every query on that table fails.
- **A changed default** applies to rows created afterwards. Existing rows keep the old
  value unless a data fix rewrites them — fix 1 is exactly that, moving `quiet_start`
  from 23 to 1.

### Adding a data fix

`DATA_FIXES` is a tuple of SQL statements, numbered from 1 by position.
`_apply_data_fixes` runs, inside the `init_db` transaction, every statement whose number
is above the database's `user_version`, and bumps the version after each.

- Append only. Never reorder, edit or remove an entry that has shipped: the number *is*
  the position, and production has already counted past it.
- A brand-new database starts at `user_version = 0`, so **every fix also runs on a fresh
  install**, right after `create_all`. A statement has to be harmless there. An `UPDATE`
  on an empty table is; an `ALTER TABLE ... ADD COLUMN` is not, because `create_all` has
  just created the table with that column and SQLite then fails on the duplicate. There
  is no mechanism for that case yet — see [KNOWN_ISSUES.md](KNOWN_ISSUES.md).
- Say in a comment why the fix is safe to apply to every row, the way fix 1 does.
- The test fixture builds its database with `create_all` alone and does not run the
  fixes ([TESTING.md](TESTING.md)); test a fix by calling `_apply_data_fixes` yourself.

## Deleting a user

`auth.delete_all_user_data(user_id)` is the single place that does it, shared by
`/unregister` and the `/account` delete button: `users`, `library_users`,
`sent_notifications`, `notification_state`, `user_settings`, `announcements_seen`, the
cached ID photo and the in-memory assignment cache. A new table or cache keyed by user
has to be added there.

## Backups

The file can be copied while the bot runs, but a plain `cp` of a live SQLite file can
catch it mid-write. Use SQLite's own backup, which is available wherever Python is:

```bash
python -c "
import sqlite3, sys
src = sqlite3.connect(sys.argv[1])
dst = sqlite3.connect(sys.argv[2])
src.backup(dst)
dst.close(); src.close()
" /path/to/bot_users.db /path/to/backup.db
```

A backup is useless without the encryption key that wrote it, and the two together are
every user's password — store them apart. See [SECURITY.md](SECURITY.md) and
[HANDOFF.md](HANDOFF.md).
