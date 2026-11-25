# Security

What the bot holds about its users, how it is protected, and the rules a change has to
keep. The bot stores other people's university passwords; treat everything here as
load-bearing.

## Reporting a vulnerability

<!-- FILL IN: private contact for security reports (e-mail or Telegram handle) and the expected response time -->

Do not open a public issue for anything that exposes user data or credentials.

## What is stored

In `bot_users.db` ([DATABASE.md](DATABASE.md)), keyed by Telegram user id:

| Data | Table | Protection |
|---|---|---|
| KLAS login (the student number) | `users` | plain |
| KLAS password | `users.encrypted_password` | Fernet |
| KLAS session cookies — a logged-in session | `klas_sessions.encrypted_cookies` | Fernet |
| Language | `users` | plain |
| Library login | `library_users` | plain |
| Library password | `library_users.encrypted_password` | Fernet |
| Phone number | `library_users.phone_number` | plain |
| Subject names and assignment titles, as notification keys | `sent_notifications` | plain |
| Notification switches, login-failure count, announcements seen | `user_settings`, `notification_state`, `announcements_seen` | plain |

On disk outside the database, relative to the working directory:

| Data | Path | Lifetime |
|---|---|---|
| The student's ID photo — a face | `images/photos/student_<telegram id>.jpg` | until `/unregister` |
| The library QR pass — it encodes library credentials | `images/qr_<telegram id>_*.png` | deleted right after sending; one can be left behind by a crash |
| Log lines with Telegram user ids and event kinds | `logs/kwbot.log` (not on Railway) | never rotated |
| Deleted rows | free pages of `bot_users.db` | zeroed on delete (`PRAGMA secure_delete=ON`, set on every connection) |

In memory only: credentials typed so far in a registration flow (for `/lregister` never the
password, which is the last step and goes straight to the login), each user's assignment
list for five minutes, and the failed-login counter below
([ARCHITECTURE.md](ARCHITECTURE.md#what-lives-only-in-memory)).

Grades and the student record shown by `/info` are fetched on request and not stored.

## What leaves the bot

- **KW services** receive the user's own credentials; that is the feature.
- **Telegram** carries everything. The bot deletes the user's message after credential
  input and deletes the QR photo it sent after 30 seconds.

## Credentials

KLAS and library passwords are stored Fernet-encrypted in SQLite
(`users`, `library_users`, keyed by Telegram user id as a string). The key comes from
`ENCRYPTION_KEY` if set, else `<DATA_DIR>/encryption_key.key`. Losing the key makes every
stored password permanently undecryptable — never regenerate it on an existing database.
Handlers `await message.delete()` after credential input so the password does not stay in
the chat.

Passwords have to be recoverable, not hashed: the second step of a KLAS login sends the
password again after the student approved the push ([KLAS.md](KLAS.md#login-and-the-second-factor)).
That makes the key the whole of the protection.

The kept KLAS session (`klas_sessions`, `app/services/klas_session.py`) is encrypted with
the same key. While it lives it is as good as the password, and it skips the second
factor, so it is never logged either. Only cookies for `klas.kw.ac.kr` are stored.

## Abuse limits

- **Private chats only.** `app/bot.py` filters every message and callback query on
  `chat.type == "private"`; group, supergroup and channel updates reach no handler, so a
  prompt for credentials is never shown to a group.
- **Anti-spam.** `AntiSpamMiddleware` throttles messages (2 seconds) and callback queries
  (0.5 seconds) per user. It never throttles a message while the user has an FSM state: a
  throttled message is neither handled nor deleted, which would leave a quickly pasted
  password in the chat.
- **Failed `/register` logins.** `app/services/login_limiter.py` allows 3 passwords KLAS
  rejected per student number per hour, counted across all Telegram accounts, because
  KLAS locks an account after 5 and anyone can type anyone's student number. Only a real
  rejection counts, never a network failure; a login KLAS accepts clears the count. It is
  in memory, so a restart resets it. The price: someone who fails three times on
  purpose blocks that student's `/register` for an hour (their saved login keeps working).
- **SQL parameters** are hidden in SQLAlchemy errors (`hide_parameters=True`), so a logged
  `StatementError` carries no password hash, phone number or student number.

## The key

- One Fernet key (`app/utils/encryption.py`) encrypts every password. It is loaded once
  per process and cached.
- `ENCRYPTION_KEY` takes precedence over the key file. On a host with an ephemeral
  filesystem it must be the variable ([DEPLOYMENT.md](DEPLOYMENT.md#railway)).
- `generate_key()` refuses to overwrite an existing `<DATA_DIR>/encryption_key.key`
  (`FileExistsError`; the file is created with `O_EXCL` and mode `0o600`). It cannot see
  the database or `ENCRYPTION_KEY`, though: do not run it on a host whose key lives in
  the variable, and never replace the file by hand on a database that has users.
- **The database and the key together are every user's password in plain text.** Keep
  backups of the two in different places, and never send both over the same channel.
  How to hand the key to another person is in [HANDOFF.md](HANDOFF.md#transferring-the-encryption-key).
- There is no rotation tooling ([KNOWN_ISSUES.md](KNOWN_ISSUES.md)).

## Rules

- **Never commit `.env`, `*.key` or `*.db`.** `.gitignore` covers them, together with
  `logs/`, `images/photos/` and `images/qr*.png`; check a new secret-bearing file with
  `git check-ignore -v <path>` before the first commit. `.env.example` holds names and
  comments only.
- **Never log a decrypted password**, session cookies, a phone number, or a response body from a KW
  service. `app/services/qr.py` builds its exceptions so that only a result code reaches
  the log, because the service echoes the student id and phone number back;
  `notifications._send` logs the user and the kind of message, never its text.
- **Never log or print `BOT_TOKEN` or `ENCRYPTION_KEY`**, and do not
  paste them into an issue, a commit message or a chat with an AI tool.
- **Delete the user's message after credential input**, as every step of `/register`
  and `/lregister` does.
- **Never keep a password in FSM data.** `/register` and `/lregister` take the password as
  their last step and use it from the message itself.
- **HTTP calls have a timeout and verify TLS.** Every `aiohttp` session sets
  `ClientTimeout(total=30)`; no call passes `ssl=False` or a context that skips
  verification. `kupis.kw.ac.kr` (book search) sends no intermediate certificate, so
  `app/services/library.py` adds the public Sectigo intermediate shipped in
  `app/certs/` to the default trust store (`library_ssl_context()`); it expires
  2036-03-21, and a "unable to get local issuer certificate" error in book search means
  the site changed its CA and the file needs replacing from the certificate's AIA URL.
  `app/services/qr.py` opens a session per request (`new_session()`), so
  cookies of different users never share a jar.
- **"Delete my data" means all of it.** `auth.delete_all_user_data` is the one place; a
  new table, file or cache holding something per user is added there in the same change
  (that includes the in-memory state: `todos.forget_cached_items`,
  `notifications.forget_user`). `/register` with another student number clears the
  notification history, the `/show` cache and the ID photo of the old account too.
  It runs every step, returns False if any failed, and the callers (`/unregister`, the
  account delete button) then tell the user. Both also clear the FSM state. A helper added
  to it must return True on success, and also when there was nothing to delete.
- **A captured KLAS request contains live session cookies.** Delete it when done; never
  commit it ([KLAS.md](KLAS.md#when-klas-changes)).
- Only `ADMIN_ID` may broadcast. The check is `message.from_user.id != settings.ADMIN_ID`
  in `app/handlers/admin.py`; there is no other privileged command.

## If something leaks

| Leaked | Do |
|---|---|
| `BOT_TOKEN` | revoke it in @BotFather (`/revoke`), set the new token, redeploy. Users and data are unaffected. |
| The database without the key | passwords and sessions stay encrypted; logins, phone numbers and assignment titles are exposed. Tell the users. |
| The key, or the database with the key | every stored password and every live KLAS session is exposed. Stop the bot, tell the users to change their KLAS and library passwords, and start over with a new key and an empty database. |

<!-- FILL IN: who decides on and sends a breach notice to users, and through which channel -->
