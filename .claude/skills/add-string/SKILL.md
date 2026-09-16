---
name: add-string
description: >
  Add, rename, or remove a user-facing string in app/strings.py, keeping the
  EN/KO/RU tables in sync. Use whenever a change introduces text the bot sends
  to a user - a new handler, a new error message, a new button label, a reworded
  reply - or whenever a string key is renamed or deleted. Also use when asked to
  translate or audit the bot's strings, or when Strings.get() is returning a raw
  key or an unformatted template.
---

# Adding a user-facing string

Every string the bot sends lives in one nested dict in `app/strings.py`, keyed by
the `Language` enum. A key added to only one table is a silent bug: `Strings.get`
never raises, so a missing KO/RU entry quietly falls back to English and a missing
key renders as the key itself in the user's chat.

Handlers own all user-facing text. If you are writing a literal string into a
handler, a service, or a keyboard, it belongs here instead.

## Locate the tables

Line numbers drift; find the three blocks first:

```bash
grep -n "Language\.\(EN\|KO\|RU\): {" app/strings.py
```

Each block is `Language.XX: { "key": "template", ... }`, in EN, KO, RU order.

## Steps

1. **Pick a key.** `snake_case`, describing the situation rather than the wording
   (`library_login_failed`, not `sorry_try_again`). Check it is free:
   `grep -n '"your_key"' app/strings.py`.
2. **Add it to all three tables**, in the same relative position in each so the
   blocks stay diffable. Write real Korean and Russian - do not leave the English
   text as a placeholder, and do not skip a language intending to "add it later":
   the fallback will hide the omission from you and show English to the user.
3. **Keep the placeholder set identical** across the three templates. `{name}` in
   EN must be `{name}` in KO and RU - a typo'd placeholder makes `Strings.get`
   log an error and return the raw, unformatted template to the user.
4. **Multi-line templates** use `"""..."""` and keep their trailing newlines; match
   the surrounding style, including the emoji, which are part of the voice here.
5. **Call it** as `Strings.get("your_key", user_lang, name=...)`. In a handler,
   `user_lang` comes from `get_user_language_with_fallback()`
   (`app/utils/language_utils.py`) and must be bound as the first statement in the
   `try`, so the `except` block can use it too.

## Verify

```bash
python3 .claude/skills/add-string/scripts/check_strings.py   # parity + placeholders
pytest tests/unit/test_strings.py --no-cov -q
```

The checker parses the file with `ast` instead of importing it, because importing
anything under `app/` raises without `BOT_TOKEN`/`ADMIN_ID`. It reports duplicate
keys, keys missing from a language, and placeholder sets that disagree. Pass a
path to check a different file. `nonexistent_key` is allowlisted as EN-only - it
is what `test_string_fallback` uses to exercise the fallback.

`tests/unit/test_strings.py` enforces the same three rules, so a mismatch fails the
suite too; the script just gives you the answer without a pytest run. If you change
one of these rules, change it in both places.

## Renaming or removing a key

Callers pass keys as bare strings, so nothing but a grep will find them:

```bash
grep -rn '"old_key"' app/ tests/
```

Update every call site, then rerun the checker and the test above.

## Not in this file

Slash-command *descriptions* are separate - they live in `app/menu.py` and are
pushed to Telegram at startup via `set_my_commands`. A new command needs an entry
in both places: its description in `app/menu.py`, its replies here.
