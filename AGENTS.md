# AGENTS.md

Guidance for coding agents working in this repository. This file is a router: it holds
what has to be known before touching anything and points at `docs/` for the rest.
`CLAUDE.md` only imports it.

## Project

Telegram bot (aiogram 3, asyncio) for Kwangwoon University students: KLAS assignments,
deadline notifications and grades, the library QR pass, campus news and the cafeteria
menu, plus a Google Gemini chat assistant. It calls unofficial KW endpoints on the user's
behalf and keeps their KLAS and library passwords Fernet-encrypted in SQLite.
Python 3.12 (`.python-version`); `requirements.txt` pins exact versions. A fork of
ChoiVadim/klas_notification_bot, continued with the original author's permission.

## Commands

```bash
pip install -r requirements-dev.txt   # runtime + test deps
python main.py                        # run the bot
pytest                                # full suite (pytest.ini already adds -v and coverage)
pytest tests/unit/test_qr.py -v       # one file
pytest tests/unit/test_qr.py::test_get_qr_returns_none_on_failure   # one test
pytest --no-cov -q                    # skip the htmlcov/ report
```

**Importing anything under `app/` requires `BOT_TOKEN` and `ADMIN_ID`** — `app/config.py`
raises `RuntimeError` at import time when either is missing, so the test suite needs a
`.env` (or those env vars) even though it never contacts Telegram. Five tests also need
an encryption key. Start from `.env.example`; details in [docs/TESTING.md](docs/TESTING.md).

## Critical rules

Each of these breaks production or loses data when ignored. The linked doc has the why.

- **Seoul time.** Date-sensitive code calls `timezone.now()` from `app/utils/timezone.py`,
  never `datetime.now()`. Do not make the bot respect the host `TZ`.
  → [docs/TIMEZONE.md](docs/TIMEZONE.md)
- **Deadlines, not countdowns.** Assignments carry `expire_at`; nothing stores "time
  left". Subtract a single `timezone.now()` at render. → [docs/TIMEZONE.md](docs/TIMEZONE.md)
- **Never regenerate `ENCRYPTION_KEY`** (or `encryption_key.key`) on an existing
  database: every stored password becomes permanently undecryptable.
  → [docs/SECURITY.md](docs/SECURITY.md)
- **Handler order.** `auth` is registered first and `common` last; `common.other_message`
  is a catch-all, so anything registered after it never fires. Modules owning callbacks
  go before `callbacks`. → [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- **Three languages.** Every user-facing string goes into EN, KO and RU in
  `app/strings.py`; a missing one falls back silently. A renamed quick-access button
  keeps its old text as a legacy label. → [docs/LOCALIZATION.md](docs/LOCALIZATION.md)
- **No migrations.** `init_db` is `create_all`, which only adds tables. A change that must
  reach existing users goes into `DATA_FIXES`. → [docs/DATABASE.md](docs/DATABASE.md)
- **`DATA_DIR` is validated, not created.** A missing directory must keep raising; do not
  "fix" it with `makedirs`. → [docs/DATABASE.md](docs/DATABASE.md)
- **Tests never touch the real `bot_users.db`** and never make real requests. Keep the
  autouse database fixture autouse. → [docs/TESTING.md](docs/TESTING.md)
- **Record a notification only after Telegram accepted it.** What was sent lives in
  SQLite, so that a restart cannot repeat it. → [docs/NOTIFICATIONS.md](docs/NOTIFICATIONS.md)
- **Credentials.** Never log a decrypted password, delete the user's message after
  credential input, never commit `.env`, `*.key` or `*.db`. A new per-user store must be
  cleared by `auth.delete_all_user_data`. → [docs/SECURITY.md](docs/SECURITY.md)
- **KLAS endpoints are not guessed.** A new `.do` URL comes from a request captured in
  the browser; a wrong one fails quietly. → [docs/KLAS.md](docs/KLAS.md)

## Where to go

| Situation | Read first |
|---|---|
| Found or fixing a bug | [docs/KNOWN_ISSUES.md](docs/KNOWN_ISSUES.md) |
| Changing notifications or deadlines | [docs/NOTIFICATIONS.md](docs/NOTIFICATIONS.md), [docs/TIMEZONE.md](docs/TIMEZONE.md) |
| Adding a handler, command or button | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/LOCALIZATION.md](docs/LOCALIZATION.md) |
| Adding or changing user text | the add-string skill ([.claude/skills/add-string/SKILL.md](.claude/skills/add-string/SKILL.md)) |
| KLAS changed, or adding a KLAS endpoint or assignment category | [docs/KLAS.md](docs/KLAS.md) |
| Changing DB models | [docs/DATABASE.md](docs/DATABASE.md) |
| Writing tests | [docs/TESTING.md](docs/TESTING.md) |
| Deploying or debugging prod | [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) |
| Adding or changing an env var | [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md), [.env.example](.env.example) |
| Credentials or personal data | [docs/SECURITY.md](docs/SECURITY.md) |
| Taking over the project | [docs/HANDOFF.md](docs/HANDOFF.md) |

## Keeping docs current

- When you find a bug you will not fix now, add it to
  [docs/KNOWN_ISSUES.md](docs/KNOWN_ISSUES.md).
- When you fix a bug listed there, remove or update its entry.
- When you change behaviour described in a `docs/` file, update that file in the same
  change.
- Do not grow this file; put new detail in `docs/` and add a link. It must stay under
  200 lines (`wc -l AGENTS.md`).
- Never write a fact about the live deployment that the repository cannot confirm. Leave
  a `<!-- FILL IN: ... -->` placeholder for the maintainer instead.
