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


def test_no_duplicate_keys_in_language_tables():
    """The EN table once defined 31 keys twice, silently discarding the first set."""
    import ast

    tree = ast.parse(open("app/strings.py", encoding="utf-8").read())
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "_strings":
            for lang_key, lang_value in zip(node.value.keys, node.value.values):
                keys = [ast.literal_eval(k) for k in lang_value.keys]
                assert len(keys) == len(set(keys)), (
                    f"duplicate keys in {ast.unparse(lang_key)}: "
                    f"{sorted({k for k in keys if keys.count(k) > 1})}"
                )


def test_notification_strings_present_in_every_language():
    for lang in Language:
        assert Strings.get("notification_footer", lang)
        assert "{" not in Strings.get(
            "notification_header", lang, emoji="⏰", hours=3
        )
