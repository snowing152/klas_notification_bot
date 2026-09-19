from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

from app.strings import Strings, Language


def create_quick_access_keyboard(user_lang: Language):
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text=Strings.get("button_todos", user_lang)),
                KeyboardButton(text=Strings.get("button_qr", user_lang)),
            ],
            [
                KeyboardButton(text=Strings.get("button_menu", user_lang)),
                KeyboardButton(text=Strings.get("button_news", user_lang)),
            ],
        ],
        resize_keyboard=True,
        is_persistent=True,
        one_time_keyboard=False,
        input_field_placeholder=Strings.get("input_field_placeholder", user_lang)
    )
    return keyboard


def quick_access_labels(key: str) -> frozenset:
    """Every language's label for a quick-access button, e.g. for a message
    filter. The reply keyboard is client-side and only refreshes on the next
    message carrying reply_markup, so a user who just switched language may
    still be looking at labels in the old one - matching against all three
    survives that instead of routing the tap to the LLM.
    """
    return frozenset(Strings.get(key, lang) for lang in Language)


def create_language_keyboard():
    language_keyboard = InlineKeyboardBuilder()
    language_keyboard.button(text=Language.EN.value, callback_data="language_en")
    language_keyboard.button(text=Language.KO.value, callback_data="language_ko")
    language_keyboard.button(text=Language.RU.value, callback_data="language_ru")
    language_keyboard.adjust(1)
    return language_keyboard.as_markup()


def create_food_menu_keyboard(user_lang: Language):
    builder = InlineKeyboardBuilder()
    builder.button(
        text=Strings.get("tomorrow_menu", user_lang), callback_data="filter_tomorrow"
    )
    builder.button(
        text=Strings.get("info", user_lang), callback_data="filter_food_info"
    )
    builder.adjust(1)  # Adjust the number of buttons per row

    # Convert the builder to InlineKeyboardMarkup
    return builder.as_markup()


def create_back_to_food_menu_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙", callback_data="filter_today")
    builder.adjust(1)

    return builder.as_markup()


def create_news_keyboard(user_lang: Language):
    builder = InlineKeyboardBuilder()
    builder.button(
        text=Strings.get("foreigners_news", user_lang),
        callback_data="filter_foreigners_news",
    )
    builder.button(
        text=Strings.get("all_news", user_lang), callback_data="filter_all_news"
    )
    builder.adjust(2)

    return builder.as_markup()


def create_donation_keyboard(user_lang: Language):
    builder = InlineKeyboardBuilder()
    
    # Add different donation amount options
    builder.button(text="1⭐", callback_data="donate_1")
    builder.button(text="5⭐", callback_data="donate_5")
    builder.button(text="10⭐", callback_data="donate_10")
    builder.button(text="20⭐", callback_data="donate_20")
    builder.button(text="50⭐", callback_data="donate_50")
    builder.button(text="100⭐", callback_data="donate_100")
    
    # Adjust to have 3 buttons per row
    builder.adjust(3)
    
    return builder.as_markup()


def create_settings_keyboard(user_lang: Language, settings_row):
    """One row per preference; the label carries its current state."""

    def state(value: bool) -> str:
        return Strings.get("state_on" if value else "state_off", user_lang)

    builder = InlineKeyboardBuilder()
    builder.button(
        text=Strings.get(
            "settings_new_assignments",
            user_lang,
            state=state(settings_row.new_assignment_alerts),
        ),
        callback_data="settings_new",
    )
    builder.button(
        text=Strings.get(
            "settings_deadline_alerts",
            user_lang,
            state=state(settings_row.deadline_alerts),
        ),
        callback_data="settings_deadline",
    )
    builder.button(
        text=Strings.get(
            "settings_urgent_only",
            user_lang,
            state=state(settings_row.urgent_thresholds_only),
        ),
        callback_data="settings_urgent",
    )
    builder.button(
        text=Strings.get(
            "settings_quiet_hours",
            user_lang,
            state=state(settings_row.quiet_hours),
            start=settings_row.quiet_start,
            end=settings_row.quiet_end,
        ),
        callback_data="settings_quiet",
    )
    builder.button(
        text=Strings.get("settings_language_button", user_lang),
        callback_data="settings_language",
    )
    builder.adjust(1)

    return builder.as_markup()


def create_account_keyboard(user_lang: Language, has_klas: bool, has_library: bool):
    """One row per action; login rows reword to "log in again" once connected,
    and info/search/delete only appear once there's something to act on."""
    builder = InlineKeyboardBuilder()
    builder.button(
        text=Strings.get(
            "account_relogin_klas" if has_klas else "account_login_klas", user_lang
        ),
        callback_data="account_klas",
    )
    builder.button(
        text=Strings.get(
            "account_relogin_library" if has_library else "account_login_library",
            user_lang,
        ),
        callback_data="account_library",
    )
    if has_klas:
        builder.button(
            text=Strings.get("account_student_info", user_lang),
            callback_data="account_info",
        )
    if has_library:
        builder.button(
            text=Strings.get("account_search_book", user_lang),
            callback_data="account_search",
        )
    if has_klas or has_library:
        builder.button(
            text=Strings.get("account_delete", user_lang),
            callback_data="account_delete",
        )
    builder.adjust(1)

    return builder.as_markup()


def create_account_delete_confirm_keyboard(user_lang: Language):
    builder = InlineKeyboardBuilder()
    builder.button(
        text=Strings.get("account_delete_yes", user_lang),
        callback_data="account_delete_yes",
    )
    builder.button(
        text=Strings.get("account_delete_no", user_lang),
        callback_data="account_delete_no",
    )
    builder.adjust(2)

    return builder.as_markup()


def create_announcement_keyboard(user_lang: Language, key: str):
    """Opt in or out of the feature an announcement is about, in one tap."""
    builder = InlineKeyboardBuilder()
    builder.button(
        text=Strings.get("announcement_enable", user_lang),
        callback_data=f"announce_on_{key}",
    )
    builder.button(
        text=Strings.get("announcement_dismiss", user_lang),
        callback_data=f"announce_off_{key}",
    )
    builder.adjust(2)

    return builder.as_markup()
