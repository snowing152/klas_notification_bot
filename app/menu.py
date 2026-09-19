import logging
from app.bot import bot
from aiogram import types


def get_bot_commands(language_code: str) -> list:
    commands = {
        "ru": {
            "qr": "📱 Сгенерировать QR для библиотеки",
            "show": "📋 Показать задания KLAS",
            "menu": "🍲 Меню столовой",
            "news": "📰 Новости KW",
            "info": "ℹ️ Информация о студенте",
            "register": "💁‍♂️ Войти в KLAS",
            "unregister": "🚫 Удалить данные",
            "lregister": "📚 Войти в KW библиотеку",
            "language": "🌍 Изменить язык",
            "settings": "⚙️ Настройки уведомлений",
            "start": "🏁 Информация о боте",
            "donate": "💰 Поддержать разработчика",
        },
        "ko": {
            "qr": "📱 도서관 QR 코드 생성",
            "show": "📋 KLAS 과제 확인",
            "menu": "🍲 식당 메뉴 확인",
            "news": "📰 KW 뉴스",
            "info": "ℹ️ 학생 정보 확인",
            "register": "💁‍♂️ KLAS 로그인",
            "unregister": "🚫 인증 정보 삭제",
            "lregister": "📚 KW 도서관 로그인",
            "language": "🌍 언어 변경",
            "settings": "⚙️ 알림 설정",
            "start": "🏁 봇 정보",
            "donate": "💰 개발자 후원",
        },
        "en": {
            "qr": "📱 Generate QR for library",
            "show": "📋 Show KLAS assignments",
            "menu": "🍲 Show dining menu",
            "news": "📰 KW news",
            "info": "ℹ️ Student info",
            "register": "💁‍♂️ Login to KLAS",
            "unregister": "🚫 Delete credentials",
            "lregister": "📚 Login to library",
            "language": "🌍 Change language",
            "settings": "⚙️ Notification settings",
            "start": "🏁 Bot info",
            "donate": "💰 Donate to developer",
        },
        "default": {
            "qr": "📱 Generate a library QR code (도서관 QR 코드를 생성).",
            "show": "📋 Display tasks from KLAS (KLAS 과제를 확인).",
            "menu": "🍲 Check the cafeteria menu (식당 메뉴를 확인).",
            "news": "📰 Show KW website news (광운대 최신 뉴스를 보여줌).",
            "info": "ℹ️ View your student info (학생 정보를 확인).",
            "register": "💁‍♂️ Log in to KLAS (KLAS에 로그인).",
            "unregister": "🚫 Delete your credentials (인증 정보를 삭제).",
            "lregister": "📚 Log in to KW library (도서관에 로그인).",
            "language": "🌍 Change the bot’s language (언어를 변경).",
            "settings": "⚙️ Notification settings (알림 설정).",
            "start": "🏁 Show bot info (봇 정보를 보여줌).",
            "donate": "💰 Support the developer (개발자를 후원).",
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
