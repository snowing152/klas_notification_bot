# Testing

## Running

```bash
pip install -r requirements-dev.txt   # runtime + test deps
pytest                                # full suite (pytest.ini already adds -v and coverage)
pytest tests/unit/test_qr.py -v       # one file
pytest tests/unit/test_qr.py::test_get_qr_returns_none_on_failure   # one test
pytest --no-cov -q                    # skip the htmlcov/ report
```

`asyncio_mode = auto` is set, so `async def test_*` needs no decorator.

`tests/unit/` covers one module each; `tests/integration/` drives handlers and services
together. Neither contacts Telegram, KLAS or any other host.

## What the suite needs from the environment

**Importing anything under `app/` requires `BOT_TOKEN` and `ADMIN_ID`** — `app/config.py`
raises `RuntimeError` at import time when either is missing, so the test suite needs a
`.env` (or those env vars) even though it never contacts Telegram.

Any non-empty `BOT_TOKEN` and any integer `ADMIN_ID` will do. On top of that, five tests
encrypt a password for real — `tests/unit/test_utils.py::test_encryption` and four in
`tests/integration/test_library_registration.py` — and fail with `No encryption key
found` unless `ENCRYPTION_KEY` is set or `encryption_key.key` exists in `DATA_DIR`. On a
developer machine they pick up the real key file, which they only read. From a clean
checkout:

```bash
export BOT_TOKEN=123456:test ADMIN_ID=1
export ENCRYPTION_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
pytest --no-cov -q
```

`DATA_DIR` needs no value: unset, it is the working directory, which exists, and the
tests never write the database there.

## Conventions

`tests/conftest.py` has an **autouse** fixture that monkeypatches `db.engine` and
`db.AsyncSessionLocal` to a per-test tmp SQLite file. This is autouse on purpose: the DB
functions resolve `AsyncSessionLocal` at call time, so a test that forgot the fixture
would write to the real `bot_users.db`. Network-facing services are tested by
monkeypatching the module's own functions (see `tests/unit/test_qr.py`); nothing in the
suite makes real requests. Handler tests build `AsyncMock(spec=types.Message)` and must
re-assign `answer`/`reply_photo` as `AsyncMock` — aiogram's builder-style methods
otherwise come back as sync `MagicMock`s.

More of the same kind:

- The fixture builds the schema with `create_all` only; `DATA_FIXES` are not applied to
  a test database ([DATABASE.md](DATABASE.md#adding-a-data-fix)).
- Code that imports a database function by name (`from app.database.database import
  get_user`) still reaches the test database, because the function looks up
  `AsyncSessionLocal` in its own module when called. A module that built its *own*
  engine or session factory would not.
- Anything date-dependent takes its clock from `app/utils/timezone.py`; tests replace
  `timezone.now` (or the module's `timezone` reference) rather than depending on the
  real date ([TIMEZONE.md](TIMEZONE.md)). A test that needs the *process* on another
  zone uses the `host_timezone` fixture in `tests/conftest.py`, which puts `TZ` back
  afterwards.
- Module-level caches (`todos._items_cache`, the news and food caches) outlive a test
  unless the test clears them.
- `kw._request_slots` is module-level too, and an `asyncio.Semaphore` that ever made a
  caller wait is bound to that test's event loop. `tests/unit/test_kw.py` replaces it
  per test with an autouse fixture; a test elsewhere that drives the real `_post_json`
  concurrently needs the same.
- `tests/unit/test_strings.py` enforces the three-language rules
  ([LOCALIZATION.md](LOCALIZATION.md)).

## CI

`.github/workflows/test.yml` runs `pytest --no-cov -q` on every push and pull request,
on the Python version read from `.python-version`. It sets a dummy `BOT_TOKEN` and
`ADMIN_ID` and generates a throwaway `ENCRYPTION_KEY` for the run; no repository secret
is involved, and `DATA_DIR` is left unset.
