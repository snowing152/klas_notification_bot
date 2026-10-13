# Localization

The bot speaks English, Korean and Russian. This is where its text lives and how a
handler picks a language. For the step-by-step of adding, renaming or removing a string,
use the add-string skill: [.claude/skills/add-string/SKILL.md](../.claude/skills/add-string/SKILL.md).

## Strings

`app/strings.py` holds every user-facing string in one nested dict
keyed by the `Language` enum (EN/KO/RU). `Strings.get()` never raises: it falls back
EN → the key itself, and returns the unformatted template on a bad placeholder, because
handlers call it from inside their own `except` blocks. Adding a string means adding it
to all three language dicts.

That fallback is why a missing translation is a silent bug: a key absent from KO or RU
shows English to the user, and a key absent everywhere shows the key itself in the chat.
Both are logged as errors and neither fails anything. Two things catch it before a user
does:

```bash
python3 .claude/skills/add-string/scripts/check_strings.py   # parity + placeholders
pytest tests/unit/test_strings.py --no-cov -q
```

The checker parses the file with `ast` rather than importing it, so it runs without
`BOT_TOKEN`/`ADMIN_ID`. Both enforce the same three rules — no duplicate keys, every key
in all three languages, identical placeholder sets — so a rule changed in one has to be
changed in the other. `nonexistent_key` is deliberately EN-only; `test_string_fallback`
uses it.

Each assignment category also needs a `type_<name>` string in all three languages
([KLAS.md](KLAS.md#assignment-categories)).

## Resolving the user's language

`get_user_language_with_fallback()`
(`app/utils/language_utils.py`) is the only way handlers resolve language — DB row first,
then the Telegram client locale, then EN — and is likewise exception-proof, since every
handler binds `user_lang` as the first statement in its `try` and reads it in `except`.

The handler shape that depends on it:

```python
async def cmd_something(message: types.Message):
    try:
        user_lang = await get_user_language_with_fallback(message)
        ...
    except Exception as e:
        logging.error(f"Error in cmd_something: {e}")
        await message.answer(Strings.get("unexpected_error", user_lang))
```

Anything placed before that first statement and able to raise leaves `user_lang` unbound
in the `except`.

The language is stored on the `users` row as the enum name. A user who has not
registered has no row, so `/language` cannot persist a choice for them and the client
locale keeps deciding.

## Slash-command descriptions

Slash-command *descriptions* are separate, in `app/menu.py`, pushed to Telegram at
startup via `set_my_commands`. A new command needs an entry in both places: its
description in `app/menu.py`, its replies in `app/strings.py`. The menu lists seven
commands per language on purpose — a command that has a button does not also need a line
there; one reachable only by typing it does.

## Quick-access buttons

Quick-access reply-keyboard buttons are
matched with `F.text.in_(quick_access_labels(key, *legacy))`, which spans **all three
languages plus superseded label text**: the keyboard is client-side and only refreshes on
the next message carrying `reply_markup`, so after a language switch or a deploy that
renames a button, taps arrive with the old text and must still route.

So when a button label changes, the old text is appended as a `legacy` argument at the
call site rather than dropped — `quick_access_labels("button_todos", "📋 Todos")` in
`app/handlers/todos.py` and `quick_access_labels("button_qr", "🔍 QR")` in
`app/handlers/library.py` both carry one. A tap whose text matches nothing falls through
to `common.other_message` and is answered by the LLM.

## Text that is not localized

A few replies are written as English literals outside `app/strings.py`; they are listed
in [KNOWN_ISSUES.md](KNOWN_ISSUES.md). Do not add to them.
