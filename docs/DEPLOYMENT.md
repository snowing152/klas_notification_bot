# Deployment

How the bot is run, configured and observed. This file describes what the repository
supports; where the live instance actually runs is in [HANDOFF.md](HANDOFF.md).

## Targets

Two supported targets: systemd (`botdaemon.service`, placeholders to fill in) and Railway
(`railway.json`; worker with no exposed port). On Railway a volume must be mounted and
`DATA_DIR` pointed at it, and `ENCRYPTION_KEY` must be set as a variable. `main.py`
detects `RAILWAY_ENVIRONMENT` and skips the `logs/kwbot.log` file handler there; stdout
logging is always on. (The check itself is `deployed_on_railway` in
`app/utils/log_format.py`, which `main.py` calls through `configure_logging`.)

The bot polls Telegram and listens on nothing, so neither target needs a domain, a port
or a health check. Only one instance may poll a bot token at a time: a second one makes
Telegram reject `getUpdates` with a conflict for both.

## Environment variables

Read at import by `app/config.py` unless noted. `.env` in the project root is loaded
with `python-dotenv`; real environment variables win over it.
[`.env.example`](../.env.example) is the template.

| Variable | Required | Default | Notes |
|---|---|---|---|
| `BOT_TOKEN` | yes | — | from @BotFather; import raises without it |
| `ADMIN_ID` | yes | — | numeric Telegram id allowed to use `/notify`; import raises unless it is an integer |
| `GEMINI_API_KEY` | no | empty | without it the chat assistant answers that it is not configured; everything else works |
| `DATA_DIR` | on Railway | working directory | where `bot_users.db` and `encryption_key.key` live; must exist and be writable ([DATABASE.md](DATABASE.md)) |
| `ENCRYPTION_KEY` | on Railway | empty | Fernet key; when empty, `<DATA_DIR>/encryption_key.key` is used ([SECURITY.md](SECURITY.md)) |
| `DATABASE_URL` | no | SQLite file in `DATA_DIR` | full SQLAlchemy URL; leave unset |
| `NOTIFICATION_CHECK_INTERVAL` | no | `1800` | seconds between notification cycles; a `Settings` field, not an `os.getenv` call |
| `KW_TZ` | no | `Asia/Seoul` | escape hatch, read in `app/config.py` and `app/utils/timezone.py`; leave unset ([TIMEZONE.md](TIMEZONE.md)) |
| `RAILWAY_ENVIRONMENT` | — | — | set by Railway itself, read in `app/utils/log_format.py`; do not set it by hand |

`TZ` is written by `app/config.py`, never read: setting it in a deploy has no effect.

**Leave an unused optional variable out entirely rather than setting it to nothing.** An
empty `KW_TZ` or `DATABASE_URL` is taken literally and stops the bot at startup, and
`python-dotenv` reads `NAME=   # comment` as the value `# comment`
([KNOWN_ISSUES.md](KNOWN_ISSUES.md)).

## Railway

`railway.json` pins the Nixpacks builder, `python main.py` as the start command and an
`ON_FAILURE` restart policy with at most 10 retries.

1. **Attach a volume** and mount it, for example at `/data`. The container filesystem is
   wiped on every deploy; without a volume `bot_users.db` goes with it.
2. **Set the variables**: `BOT_TOKEN`, `ADMIN_ID`, `DATA_DIR` (the mount path),
   `ENCRYPTION_KEY`, and `GEMINI_API_KEY` if the assistant is wanted.
3. **`ENCRYPTION_KEY` has to be a variable here.** A key file on an ephemeral filesystem
   disappears with the deploy, and the next start then has no key for the passwords
   already in the database. Generate it once, before the first user registers:

   ```bash
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```

   Never generate a second one for a database that already has users.
4. **Deploy** with `railway up`, or by connecting the GitHub repository.

If `DATA_DIR` points at a path with no volume behind it, the bot refuses to start and
says so. That is intended.

## systemd

1. Create a virtualenv in the project directory and `pip install -r requirements.txt`.
2. Write `.env` from `.env.example`.
3. Generate the key file once, unless `ENCRYPTION_KEY` is set:

   ```bash
   python -c "from app.utils.encryption import generate_key; generate_key()"
   ```

   This **overwrites** `<DATA_DIR>/encryption_key.key` without asking. Run it on a new
   installation only.
4. Edit `botdaemon.service` — `User`, `Group`, `WorkingDirectory`, `ExecStart` — then:

   ```bash
   sudo cp botdaemon.service /etc/systemd/system/kwbot.service
   sudo systemctl daemon-reload
   sudo systemctl enable --now kwbot.service
   ```

The unit restarts the bot ten seconds after a failure. Its restart limit does not apply
as written — see [KNOWN_ISSUES.md](KNOWN_ISSUES.md). `WorkingDirectory` matters:
`images/` and, by default, the database and the key are all resolved against it.

## Logs

Stdout always. On a Linux host that is not Railway, the bot additionally appends to
`logs/kwbot.log` in the project directory, creating the directory if needed.

On Railway each line goes out as one JSON object
(`app/utils/log_format.py`): a plain line on stdout is "info" to Railway whatever it
says, and only `level` makes an ERROR findable by severity. "On Railway" means
`RAILWAY_ENVIRONMENT` *and* stdout not being a terminal, since `railway run` sets the
variable in a local shell too. Log times are Korean on every host, read from `ZoneInfo`
like `timezone.now()` — the Railway image has no system tz database, so the process `TZ`
there is UTC in practice. `main.py` calls `configure_logging` **before** importing the
rest of `app`, because `app/config.py` raising at import is the likeliest startup failure
and an uncaught exception only becomes a log entry through the `sys.excepthook` installed
there. That is why `app/utils/__init__.py` is empty and must stay so: anything it imports
runs on the way to `app.utils.log_format`, and an import of config there puts the failure
back ahead of the logging.

Where to look:

| Target | Command |
|---|---|
| Railway | `railway logs`, or the dashboard; filter errors with `@level:error` |
| systemd | `sudo journalctl -u kwbot.service -f`, or `logs/kwbot.log` |

Lines worth knowing:

| Line | Means |
|---|---|
| `Notification cycle finished: ...` | one per cycle; INFO when every user was read, WARNING with a failed count otherwise. Ends with the cycle's duration and memory ([NOTIFICATIONS.md](NOTIFICATIONS.md#the-loop)) |
| `Startup memory: N MB` | resident memory once handlers are registered, before polling: the imports and the database. A cycle line far above it, or climbing from one cycle to the next, is where RAM goes |
| `Notification sent to <id> (...)` | a delivery, with its kind |
| `Notification held for <id> (...)` / `Held notification ... dropped unsent` | quiet hours kept a reminder back / a held one stopped being due before it could go out |
| `Uncaught exception` (critical) | the process died; at startup this is usually `app/config.py` refusing a variable |
| `Could not log in as user <id>` | KLAS refused or dropped a login; all users at once points at KLAS |
| `Failed to retrieve <url>. Status code: ...` / `Non-JSON response from <url>` | an endpoint changed or is down ([KLAS.md](KLAS.md#when-klas-changes)) |
| `Incomplete assignment data for subject ...` | one category of one subject could not be read |
| `Error generating response: ...` | the Gemini call failed; a retired model name shows up here |
| `DATA_DIR points at ..., which does not exist` | inside an `Uncaught exception` entry: no volume mounted; the bot did not start |

The memory figures are the Python process alone. Railway's Metrics tab measures the
container, which also counts page cache, so it reads higher; compare when the two move,
not their size.

Logs carry Telegram user ids and event kinds. They must never carry a password
([SECURITY.md](SECURITY.md)).

## What has to survive a redeploy

| Item | Where | If lost |
|---|---|---|
| `bot_users.db` | `DATA_DIR` | every user has to register again |
| The Fernet key | `ENCRYPTION_KEY`, or `encryption_key.key` in `DATA_DIR` | every stored password is undecryptable, permanently |
| `BOT_TOKEN`, `ADMIN_ID`, `GEMINI_API_KEY` | platform variables or `.env` | the bot does not start, or the assistant is off |

Cached ID photos (`images/photos/`) are written under the working directory, not
`DATA_DIR`, and are simply fetched again when missing.

## Repository files

`.gitignore` excludes `*.json` wholesale with `!railway.json` re-added — a new committed
JSON file needs its own negation. It also ignores anything with `cache` in its name, so
avoid that word in a file that has to be committed. `git check-ignore -v <path>` says
which rule caught a file.
