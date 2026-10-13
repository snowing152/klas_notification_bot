# Known issues

Bugs and limitations that are known and not fixed. One entry each: symptom, cause,
workaround, where the code is. Add an entry when you find something you will not fix
now; remove or update it when you do. Line numbers drift — the symbol is the reference.

`grep -rnE "TODO|FIXME" app main.py tests` finds two markers at the time of writing, both
covered by [Voice and other media are refused](#voice-and-other-media-are-refused).

## Outside our control

### KW endpoints are unofficial and can change without notice

- **Symptom:** a feature stops working for everyone at once: every login fails, a
  category of `/show` goes empty, `/qr`, `/news`, `/menu` or `/search` returns an error
  or nothing. Logs show `Failed to retrieve <url>`, `Non-JSON response from <url>` or a
  parsing error.
- **Cause:** none of `klas.kw.ac.kr`, `mobileid.kw.ac.kr`, `did-3.kw.ac.kr`,
  `kupis.kw.ac.kr` or the scraped pages of `www.kw.ac.kr` is a published API. The bot
  replays what the university's own web and mobile clients send, and parses HTML for
  news and the menu.
- **Workaround:** none until the service module is updated. Follow
  [KLAS.md](KLAS.md#when-klas-changes).
- **Where:** `app/services/kw.py`, `qr.py`, `library.py`, `news.py`, `food.py`; the host
  table is in [ARCHITECTURE.md](ARCHITECTURE.md#what-it-talks-to).

### KW forces a password change every few months

- **Symptom:** a user's notifications stop; after three failed cycles they get the
  "can't log into KLAS with your saved password" message.
- **Cause:** the stored password is no longer valid. The bot cannot know the new one.
- **Workaround:** the user runs `/register` again.
- **Where:** `_handle_login_failure` in `app/services/notifications.py`
  ([NOTIFICATIONS.md](NOTIFICATIONS.md#the-password-warning)).

### The Gemini model name is hard-coded

- **Symptom:** the chat assistant answers every message with "I encountered an error
  processing your request"; the log has `Error generating response:` with a 404.
- **Cause:** `MODEL` in `app/services/llm.py` names one specific model, and providers
  retire model names.
- **Workaround:** change `MODEL` to a current one and redeploy.
- **Where:** `app/services/llm.py`.

### A stalled KLAS endpoint holds the request slots

- **Symptom:** while KLAS accepts connections but does not answer, `/show` waits about a
  minute and a half before reporting a failed login, and a notification cycle gets
  through its users a few at a time instead of five.
- **Cause:** a request keeps its slot of `MAX_CONCURRENT_REQUESTS` through all three
  attempts, 30 seconds each, and the slots are shared by the whole process.
- **Workaround:** none; it clears when KLAS answers again, and nothing is sent wrongly
  in the meantime.
- **Where:** `_post_json` and `_request_with_retries` in `app/services/kw.py`.

## Conversation state

### A restart loses a registration in progress

- **Symptom:** a user who was in the middle of `/register`, `/lregister` or a book
  search when the bot restarted gets their next message answered by the chat assistant
  instead — including, if they were at that step, the password they just typed, which is
  then neither deleted from the chat nor kept out of the assistant's prompt.
- **Cause:** FSM state is in aiogram's `MemoryStorage`.
- **Workaround:** the user starts the command again. Avoid deploying at peak hours.
- **Where:** `storage = MemoryStorage()` in `app/bot.py`; states in
  `app/handlers/auth.py` and `app/handlers/library.py`.

### Registration prompts swallow commands

- **Symptom:** a user at the "enter your student ID" prompt who types `/show` registers
  `/show` as their ID; the same holds for every prompt of `/register` and `/lregister`.
  There is no way to cancel a registration except by finishing it.
- **Cause:** `auth` is registered first, and its state handlers have no filter on the
  text.
- **Workaround:** finish the flow with any input (a wrong login is rejected and clears
  the state), then run the command.
- **Where:** `register_handlers` in `app/handlers/auth.py`; order in `app/bot.py`. The
  book search does not have this problem — see `library.register_search_escape`.

### `/language` does not stick for unregistered users

- **Symptom:** an unregistered user picks a language and is told to `/register` first;
  the bot keeps following their Telegram client locale.
- **Cause:** the language is a column on the `users` row, which only registration
  creates.
- **Workaround:** register first.
- **Where:** `set_user_language` in `app/database/database.py`; the `language_*` branch
  of `app/handlers/callbacks.py`.

## Messages and text

### Voice and other media are refused

- **Symptom:** a voice message, photo, document, video, sticker or location gets
  "... is not available in the chat. Please use the command from the bot menu."
- **Cause:** not implemented; the catch-all only passes plain text to the assistant.
- **Workaround:** none.
- **Where:** `other_message` in `app/handlers/common.py` — `TODO: Add voice support and
  handle other content types` (line 96) and `TODO: Add voice support` (line 127).

### Some replies are English-only

- **Symptom:** a Korean- or Russian-speaking user sees English for: the "not available
  in the chat" replies above, "Processing donation of N⭐...", and the assistant's own
  fallbacks ("The AI assistant is not configured right now", "I encountered an error").
  The admin's `/notify` replies are English too.
- **Cause:** literals written in the handler or service instead of `app/strings.py`.
- **Workaround:** none; move them with the add-string skill when touching that code.
- **Where:** `other_message` in `app/handlers/common.py`, the `donate_` branch of
  `app/handlers/callbacks.py`, `generate_response` in `app/services/llm.py`,
  `cmd_notify` in `app/handlers/admin.py`.

### `/notify` has no pacing and no record

- **Symptom:** with enough users a broadcast can start failing part-way with Telegram
  flood errors; failures are logged per user and the admin still sees "Notification sent
  to all users!". Sending the command twice delivers it twice.
- **Cause:** the loop sends without a delay (the announcement service waits
  `SEND_DELAY_SECONDS` between sends; this one does not) and stores nothing.
- **Workaround:** check the log for `Failed to send message to` after a broadcast. For a
  one-time feature message prefer `ANNOUNCEMENTS`
  ([NOTIFICATIONS.md](NOTIFICATIONS.md#feature-announcements)).
- **Where:** `cmd_notify` in `app/handlers/admin.py`.

## Notifications

### Nothing is checked for the first 30 minutes after a start

- **Symptom:** after a deploy or a crash the next reminders are up to
  `NOTIFICATION_CHECK_INTERVAL` late; several restarts in a row postpone them further.
- **Cause:** `check_todos` sleeps before its first cycle, and the timer starts over with
  the process.
- **Workaround:** none needed for a single deploy; nothing is lost, only late.
- **Where:** `check_todos` in `app/services/notifications.py`.

### A reminder held by quiet hours is not queued

- **Symptom:** a user with quiet hours on never receives, say, the 6h reminder for a
  deadline at 07:00; they get the 2h one at about 05:00 instead. The log has `Held
  notification for <id> (threshold=6h) dropped unsent: no longer due`.
- **Cause:** only the tightest threshold a deadline still fits in is ever built. A held
  reminder is rebuilt each cycle, and once a tighter threshold is reached the wider one
  is past, not pending.
- **Workaround:** none; the 2h and 1h reminders are exempt from quiet hours for this
  reason.
- **Where:** `_collect_messages`, `_log_holds` and `QUIET_HOURS_EXEMPT` in
  `app/services/notifications.py`.

### `/show` does not say when its list is incomplete

- **Symptom:** a `/show` now and then lacks a whole category for one subject and says
  nothing about it; asking again shows it. The log has `Incomplete assignment data for
  subject ...` at that moment.
- **Cause:** a list request KLAS answered with an error status or a non-JSON body is not
  retried and becomes an empty category. The list is marked incomplete, which keeps it
  out of the cache and keeps the notification cycle from pruning on it, but the handler
  has no text to tell the user with.
- **Workaround:** send `/show` again.
- **Where:** `_load_items` and `show_all_assignments` in `app/handlers/todos.py`; the
  flag is set in `_get_subject_todo` in `app/services/kw.py`.

### An assignment is identified by its title

- **Symptom:** an assignment renamed in KLAS is announced again as new and gets its
  reminders again; two assignments of the same type and title in one subject share one
  set of notifications.
- **Cause:** KLAS gives assignments no stable id, so the key is subject + type + title.
- **Workaround:** none.
- **Where:** `assignment_key` in `app/services/notifications.py`.

## Configuration and data

### An empty optional variable stops the bot

- **Symptom:** the bot dies at import with `ZoneInfo keys must be normalized relative
  paths, got:` (empty `KW_TZ`) or `Could not parse SQLAlchemy URL` (empty
  `DATABASE_URL`), or fails on the first password with a Fernet key error
  (`ENCRYPTION_KEY` holding a comment).
- **Cause:** `KW_TZ` is read with a default that only applies when the variable is
  *absent*; `Settings` lets an empty `DATABASE_URL` from the environment replace the
  computed default; and `python-dotenv` reads `NAME=   # note` as the value `# note`.
- **Workaround:** leave unused optional variables out, or commented out on their own
  line, as `.env.example` does.
- **Where:** `app/config.py`, `app/utils/timezone.py`.

### A new column cannot reach an existing database

- **Symptom:** after adding a column to an existing model, every query on that table
  fails in production with "no such column", while tests and fresh installs pass.
- **Cause:** `create_all` only creates missing tables. `DATA_FIXES` could carry an
  `ALTER TABLE ... ADD COLUMN`, but every fix also runs on a fresh database, where the
  column already exists and the statement fails.
- **Workaround:** put the new data in a new table, or extend `_apply_data_fixes` to
  support a guarded column addition before relying on one.
- **Where:** `init_db`, `DATA_FIXES` and `_apply_data_fixes` in
  `app/database/database.py` ([DATABASE.md](DATABASE.md#there-is-no-migration-tool)).

### There is no way to rotate the encryption key

- **Symptom:** if the key may have leaked, there is no command to replace it.
- **Cause:** one Fernet key encrypts every password and nothing re-encrypts rows.
- **Workaround:** a one-off script, run with the bot stopped and a backup taken, that
  decrypts `users.encrypted_password` and `library_users.encrypted_password` with the old
  key and writes them back encrypted with the new one. Otherwise every user has to
  register again.
- **Where:** `app/utils/encryption.py` ([SECURITY.md](SECURITY.md#the-key)).

### The library phone number is stored unencrypted

- **Symptom:** none for users; it matters to whoever holds a copy of the database.
- **Cause:** `library_users.phone_number` is a plain column. The mobile-ID login needs
  the number on every QR request.
- **Workaround:** treat `bot_users.db` as personal data even without the key.
- **Where:** `LibraryUser` in `app/database/models.py`.

### The assistant's chat history outlives `/unregister`

- **Symptom:** after deleting their data, a user's last messages to the assistant (up to
  ten) are still used as context until the bot restarts.
- **Cause:** `chat_history_storage` is a module-level dict that
  `auth.delete_all_user_data` does not clear. It is never written to disk.
- **Workaround:** a restart clears it.
- **Where:** `app/utils/chat_history.py`; `delete_all_user_data` in
  `app/handlers/auth.py`.

### A cached ID photo is never refreshed

- **Symptom:** `/info` keeps showing the photo it fetched the first time.
- **Cause:** `send_student_info` only fetches when `images/photos/student_<id>.jpg` is
  missing. The directory is relative to the working directory, not `DATA_DIR`, so on
  Railway it is refetched after each deploy and on systemd it is kept indefinitely.
- **Workaround:** delete the file, or `/unregister` and register again.
- **Where:** `app/handlers/student_info.py`.

## Operations

### `logs/kwbot.log` grows without limit

- **Symptom:** on a systemd host the log file only ever gets bigger.
- **Cause:** a plain `FileHandler` in append mode, with no rotation.
- **Workaround:** `logrotate` with `copytruncate`, or truncate it by hand. Not an issue
  on Railway, where the file handler is skipped.
- **Where:** `build_handlers` in `app/utils/log_format.py`.

### The systemd restart limit is not applied

- **Symptom:** a bot that fails at every start is restarted every ten seconds
  indefinitely instead of giving up after three attempts in five minutes.
- **Cause:** `StartLimitIntervalSec` sits in `[Service]`, where systemd ignores it
  (`systemd-analyze verify` reports "Unknown key 'StartLimitIntervalSec' in section
  [Service]"); it belongs in `[Unit]`.
- **Workaround:** move `StartLimitIntervalSec` and `StartLimitBurst` to `[Unit]` in the
  installed unit.
- **Where:** `botdaemon.service`.

### Five tests need an encryption key

- **Symptom:** from a clean checkout, `test_encryption` and four library-registration
  tests fail with `No encryption key found`.
- **Cause:** they call the real `encrypt_password`, which loads the key from
  `ENCRYPTION_KEY` or the key file.
- **Workaround:** export a throwaway `ENCRYPTION_KEY`, as CI does
  ([TESTING.md](TESTING.md#what-the-suite-needs-from-the-environment)).
- **Where:** `tests/unit/test_utils.py`, `tests/integration/test_library_registration.py`.
