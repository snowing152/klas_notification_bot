from app.menu import get_bot_commands

# /register, /unregister, /lregister, /info, /language and /donate are
# deliberately absent: each is reachable through a button (mostly on
# /account) instead of a menu line, and still works when typed directly.
EXPECTED_COMMANDS = {"show", "qr", "menu", "news", "account", "settings", "start"}


def test_menu_has_exactly_the_seven_daily_commands():
    for language_code in ("ru", "ko", "en", "default"):
        commands = {c.command for c in get_bot_commands(language_code)}
        assert commands == EXPECTED_COMMANDS, language_code


def test_every_language_gives_every_command_a_description():
    for language_code in ("ru", "ko", "en", "default"):
        for command in get_bot_commands(language_code):
            assert command.description.strip()
