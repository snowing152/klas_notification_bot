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

In memory only: credentials typed so far in a registration flow, each user's assignment
list for five minutes, and the last ten messages of each chat with the assistant
([ARCHITECTURE.md](ARCHITECTURE.md#what-lives-only-in-memory)).

Grades and the student record shown by `/info` are fetched on request and not stored.

## What leaves the bot

- **KW services** receive the user's own credentials; that is the feature.
- **Google Gemini** receives every free-text message sent to the bot, along with that
  chat's last ten messages. Nothing from the database is added to the prompt. A user who
  types personal data into the chat sends it to Google.
- **Telegram** carries everything. The bot deletes the user's message after credential
  input and deletes the QR photo it sent after 30 seconds.

## Credentials

KLAS and library passwords are stored Fernet-encrypted in SQLite
(`users`, `library_users`, keyed by Telegram user id as a string). The key comes from
`ENCRYPTION_KEY` if set, else `<DATA_DIR>/encryption_key.key`. Losing the key makes every
stored password permanently undecryptable — never regenerate it on an existing database.
Handlers `await message.delete()` after credential input so the password does not stay in
the chat.

Passwords have to be recoverable, not hashed: the bot logs into KLAS as the user every
30 minutes. That makes the key the whole of the protection.

## The key

- One Fernet key (`app/utils/encryption.py`) encrypts every password. It is loaded once
  per process and cached.
- `ENCRYPTION_KEY` takes precedence over the key file. On a host with an ephemeral
  filesystem it must be the variable ([DEPLOYMENT.md](DEPLOYMENT.md#railway)).
- `generate_key()` overwrites `<DATA_DIR>/encryption_key.key` without asking. Running it
  against a database that has users destroys their passwords for good.
- **The database and the key together are every user's password in plain text.** Keep
  backups of the two in different places, and never send both over the same channel.
  How to hand the key to another person is in [HANDOFF.md](HANDOFF.md#transferring-the-encryption-key).
- There is no rotation tooling ([KNOWN_ISSUES.md](KNOWN_ISSUES.md)).

## Rules

- **Never commit `.env`, `*.key` or `*.db`.** `.gitignore` covers them, together with
  `logs/`, `images/photos/` and `images/qr*.png`; check a new secret-bearing file with
  `git check-ignore -v <path>` before the first commit. `.env.example` holds names and
  comments only.
- **Never log a decrypted password**, a phone number, or a response body from a KW
  service. `app/services/qr.py` builds its exceptions so that only a result code reaches
  the log, because the service echoes the student id and phone number back;
  `notifications._send` logs the user and the kind of message, never its text.
- **Never log or print `BOT_TOKEN`, `ENCRYPTION_KEY` or `GEMINI_API_KEY`**, and do not
  paste them into an issue, a commit message or a chat with an assistant.
- **Delete the user's message after credential input**, as every step of `/register`
  and `/lregister` does.
- **"Delete my data" means all of it.** `auth.delete_all_user_data` is the one place; a
  new table, file or cache holding something per user is added there in the same change.
- **A captured KLAS request contains live session cookies.** Delete it when done; never
  commit it ([KLAS.md](KLAS.md#when-klas-changes)).
- Only `ADMIN_ID` may broadcast. The check is `message.from_user.id != settings.ADMIN_ID`
  in `app/handlers/admin.py`; there is no other privileged command.

## If something leaks

| Leaked | Do |
|---|---|
| `BOT_TOKEN` | revoke it in @BotFather (`/revoke`), set the new token, redeploy. Users and data are unaffected. |
| `GEMINI_API_KEY` | delete the key in the Google console, create a new one, redeploy. |
| The database without the key | passwords stay encrypted; logins, phone numbers and assignment titles are exposed. Tell the users. |
| The key, or the database with the key | every stored password is exposed. Stop the bot, tell the users to change their KLAS and library passwords, and start over with a new key and an empty database. |

<!-- FILL IN: who decides on and sends a breach notice to users, and through which channel -->
