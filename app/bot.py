from aiogram import Bot, Dispatcher, F
from aiogram.fsm.storage.memory import MemoryStorage

from app.config import settings
from app.handlers import todos
from app.middleware.antispam import AntiSpamMiddleware

storage = MemoryStorage()
bot = Bot(token=settings.BOT_TOKEN)
dp = Dispatcher(storage=storage)


def setup_guards(dp: Dispatcher):
    """Attach the dispatcher-wide filters and anti-spam middleware."""
    # The bot holds one person's credentials per chat; in a group the replies,
    # and the "deleted" credential prompts, would be visible to everyone there.
    dp.message.filter(F.chat.type == "private")
    # A callback from a message the bot can no longer read has no chat and is
    # dropped too, as is any inline-mode callback.
    dp.callback_query.filter(F.message.chat.type == "private")

    # Messages typed during a registration step are never throttled: see
    # AntiSpamMiddleware for why.
    dp.message.middleware(AntiSpamMiddleware(limit=2, skip_in_state=True))
    # Looser than messages so ordinary tapping through menus still works.
    dp.callback_query.middleware(AntiSpamMiddleware(limit=0.5))


setup_guards(dp)


def setup_handlers(dp: Dispatcher):
    from app.handlers import (
        account,
        auth,
        food,
        common,
        student_info,
        callbacks,
        admin,
        news,
        library,
        settings as settings_handlers,
    )

    # Register all handlers.
    # Ahead of every command: it clears an abandoned "which book?" prompt and
    # then skips, so the command the user typed instead still runs.
    library.register_search_escape(dp)
    auth.register_handlers(dp)
    todos.register_handlers(dp)
    food.register_handlers(dp)
    student_info.register_handlers(dp)
    # Before callbacks: its handler answers every callback query it is offered
    settings_handlers.register_handlers(dp)
    account.register_handlers(dp)
    callbacks.register_handlers(dp)
    admin.register_handlers(dp)
    news.register_handlers(dp)
    library.register_handlers(dp)
    # Common handlers must be registered last
    common.register_handlers(dp)
