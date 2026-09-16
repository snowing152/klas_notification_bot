import ast
import re

import pytest
from app.strings import Strings, Language


def test_string_get():
    # Test getting string with default language
    welcome_msg = Strings.get("test_string", Language.EN, name="Test User")
    assert "Welcome, Test User!" in welcome_msg

    # Test getting string with Korean language
    welcome_msg_ko = Strings.get("test_string", Language.KO, name="Test User")
    assert "환영합니다!" in welcome_msg_ko

    # Test getting string with Russian language
    welcome_msg_ru = Strings.get("test_string", Language.RU, name="Test User")
    assert "Добро пожаловать!" in welcome_msg_ru


def test_string_fallback():
    # Test fallback to English when translation not found
    msg = Strings.get("nonexistent_key", Language.KO)
    assert msg == Strings.get("nonexistent_key", Language.EN)


def test_missing_key_does_not_raise():
    """Strings.get is used inside handlers' error paths, so it must not raise."""
    assert Strings.get("no_such_key_anywhere", Language.EN) == "no_such_key_anywhere"


def test_missing_format_argument_does_not_raise():
    # "time_left" expects {time_str}; omitting it returns the raw template
    assert "{time_str}" in Strings.get("time_left", Language.EN)


# Deliberately English-only: test_string_fallback above needs a key that is
# absent from KO/RU to exercise the fallback.
EN_ONLY_KEYS = {"nonexistent_key"}

PLACEHOLDER = re.compile(r"\{(\w+)")


def language_tables():
    """{"Language.EN": [(key, template), ...], ...}, duplicates preserved.

    Read with ast rather than through Strings._strings: the dict literal is
    where a duplicate key is still visible, since Python discards the earlier
    value on the way into the dict.
    """
    tree = ast.parse(open("app/strings.py", encoding="utf-8").read())
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "_strings":
            return {
                ast.unparse(lang_node): [
                    (ast.literal_eval(k), ast.literal_eval(v))
                    for k, v in zip(table.keys, table.values)
                ]
                for lang_node, table in zip(node.value.keys, node.value.values)
            }
    raise AssertionError("could not find the _strings table in app/strings.py")


def test_no_duplicate_keys_in_language_tables():
    """The EN table once defined 31 keys twice, silently discarding the first set."""
    for lang, pairs in language_tables().items():
        keys = [k for k, _ in pairs]
        assert len(keys) == len(set(keys)), (
            f"duplicate keys in {lang}: {sorted({k for k in keys if keys.count(k) > 1})}"
        )


def test_every_key_is_present_in_every_language():
    """A key missing from one table falls back to EN instead of failing loudly,
    so nothing but this test notices a half-finished translation."""
    tables = {lang: dict(pairs) for lang, pairs in language_tables().items()}
    all_keys = set().union(*(t.keys() for t in tables.values())) - EN_ONLY_KEYS

    missing = {
        key: sorted(lang for lang, t in tables.items() if key not in t)
        for key in sorted(all_keys)
    }
    missing = {k: v for k, v in missing.items() if v}
    assert not missing, f"keys missing from a language table: {missing}"


def test_placeholders_match_across_languages():
    """Strings.get logs and returns the raw template when format() is given the
    wrong names, so a typo'd placeholder reaches the user as literal braces."""
    tables = {lang: dict(pairs) for lang, pairs in language_tables().items()}
    all_keys = set().union(*(t.keys() for t in tables.values())) - EN_ONLY_KEYS

    for key in sorted(all_keys):
        found = {
            lang: set(PLACEHOLDER.findall(t[key])) for lang, t in tables.items() if key in t
        }
        assert len({frozenset(v) for v in found.values()}) == 1, (
            f"placeholders for {key!r} differ between languages: "
            + "; ".join(f"{lang}={sorted(v)}" for lang, v in sorted(found.items()))
        )


def test_notification_strings_present_in_every_language():
    for lang in Language:
        assert Strings.get("notification_footer", lang)
        assert "{" not in Strings.get(
            "notification_header", lang, emoji="⏰", hours=3
        )
