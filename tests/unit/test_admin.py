from types import SimpleNamespace

from app.handlers.admin import get_message_for_user, parse_multilanguage_message


def test_parse_multilanguage_message():
    parsed = parse_multilanguage_message("en: Hello | ko: 안녕하세요 | ru: Привет")
    assert parsed == {"en": "Hello", "ko": "안녕하세요", "ru": "Привет"}


def test_user_language_lookup_is_case_insensitive():
    """Regression: rows store the enum name ("KO"), parsed keys are lowercase,
    so every user used to fall through to the English message."""
    messages = {"en": "Hello", "ko": "안녕하세요"}

    assert get_message_for_user(SimpleNamespace(language="KO"), messages) == "안녕하세요"
    assert get_message_for_user(SimpleNamespace(language="ko"), messages) == "안녕하세요"


def test_falls_back_to_english_for_untranslated_language():
    messages = {"en": "Hello"}
    assert get_message_for_user(SimpleNamespace(language="RU"), messages) == "Hello"


def test_handles_user_without_language():
    messages = {"en": "Hello"}
    assert get_message_for_user(SimpleNamespace(language=None), messages) == "Hello"
    assert get_message_for_user(SimpleNamespace(), messages) == "Hello"
