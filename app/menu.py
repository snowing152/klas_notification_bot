import logging
from app.bot import bot
from aiogram import types


def get_bot_commands(language_code: str) -> list:
    # 7 entries per language on purpose - everything else (/register,
    # /unregister, /lregister, /info, /language, /donate) still works when
    # typed, but is reached through a button on /account, /settings, /show
    # etc. instead of cluttering this list. See CLAUDE.md / the UI overhaul
    # notes for why: a command with no button anywhere is the one exception
    # that's still worth a menu line (there isn't one left in that state).
    commands = {
        "ru": {
            "show": "📋 Показать задания KLAS",
            "qr": "📱 Сгенерировать QR для библиотеки",
            "menu": "🍲 Меню столовой",
            "news": "📰 Новости KW",
            "account": "👤 Мой аккаунт (KLAS, библиотека)",
            "settings": "⚙️ Настройки уведомлений",
            "start": "🏁 Информация о боте",
        },
        "ko": {
            "show": "📋 KLAS 과제 확인",
            "qr": "📱 도서관 QR 코드 생성",
            "menu": "🍲 식당 메뉴 확인",
            "news": "📰 KW 뉴스",
            "account": "👤 내 계정 (KLAS, 도서관)",
            "settings": "⚙️ 알림 설정",
            "start": "🏁 봇 정보",
        },
        "en": {
            "show": "📋 Show KLAS assignments",
            "qr": "📱 Generate QR for library",
            "menu": "🍲 Show dining menu",
            "news": "📰 KW news",
            "account": "👤 My account (KLAS, library)",
            "settings": "⚙️ Notification settings",
            "start": "🏁 Bot info",
        },
        "default": {
            "show": "📋 Display tasks from KLAS (KLAS 과제를 확인).",
            "qr": "📱 Generate a library QR code (도서관 QR 코드를 생성).",
            "menu": "🍲 Check the cafeteria menu (식당 메뉴를 확인).",
            "news": "📰 Show KW website news (광운대 최신 뉴스를 보여줌).",
            "account": "👤 KLAS and library login (KLAS와 도서관 로그인).",
            "settings": "⚙️ Notification settings (알림 설정).",
            "start": "🏁 Show bot info (봇 정보를 보여줌).",
        },
    }

    return [
        types.BotCommand(command=command, description=description)
        for command, description in commands.get(language_code, {}).items()
    ]


async def set_language_commands(language_code: str):
    commands = get_bot_commands(language_code)
    if language_code == "default":
        await bot.set_my_commands(commands)
    else:
        await bot.set_my_commands(commands, language_code=language_code)


async def initialize_bot_menu():
    try:
        await bot.delete_my_commands()
        language_codes = ["ru", "ko", "en", "default"]

        for lang in language_codes:
            await set_language_commands(lang)
            logging.info(f"Commands set for language: {lang}")

        logging.info("Bot menu initialized successfully!")
    except Exception as e:
        logging.error(f"Error in initialize_bot_menu: {e}")
