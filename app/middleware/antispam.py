import time
import logging

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message

from app.strings import Strings
from app.utils.language_utils import get_user_language_with_fallback

# Above this many tracked users, entries too old to throttle anyone are dropped
# so the dict does not grow for as long as the process runs.
_PRUNE_AFTER_ENTRIES = 1000


class AntiSpamMiddleware(BaseMiddleware):
    """Drop events from a user who sends them faster than `limit` seconds apart.

    Registered as an inner middleware, so it runs after the handler's filters
    matched. Use one instance per event type: each keeps its own clock.

    `exempt_states` names FSM states in which a plain text message is never
    throttled. A throttled message is neither handled nor deleted, so
    throttling a credential step would leave a quickly pasted password sitting
    in the chat. Commands are not exempt (they re-set the state, and an exempt
    command could be repeated without limit), and neither is any other state.
    """

    def __init__(self, limit: float = 2, exempt_states: frozenset[str] = frozenset()):
        super().__init__()
        self.limit = limit
        self.exempt_states = frozenset(exempt_states)
        self.user_last_message_time: dict[int, float] = {}

    async def __call__(self, handler, event: Message | CallbackQuery, data: dict):
        if await self._is_throttled(event, data):
            return None
        # Outside the try on purpose: a handler that raises must not run twice.
        return await handler(event, data)

    async def _is_throttled(self, event, data: dict) -> bool:
        """Record this event and say whether it came too soon. Fails open."""
        try:
            if self._is_credential_step(event, data):
                return False

            user_id = event.from_user.id
            now = time.monotonic()
            last = self.user_last_message_time.get(user_id)
            if last is not None and now - last < self.limit:
                user_lang = await get_user_language_with_fallback(event)
                await event.answer(Strings.get("too_many_messages", user_lang))
                return True

            self._prune(now)
            self.user_last_message_time[user_id] = now
            return False
        except Exception as e:
            logging.error(f"Error in AntiSpamMiddleware: {type(e).__name__}: {e}")
            return False

    def _is_credential_step(self, event, data: dict) -> bool:
        if data.get("raw_state") not in self.exempt_states:
            return False
        text = getattr(event, "text", None)
        return isinstance(text, str) and not text.startswith("/")

    def _prune(self, now: float) -> None:
        if len(self.user_last_message_time) < _PRUNE_AFTER_ENTRIES:
            return
        self.user_last_message_time = {
            uid: t
            for uid, t in self.user_last_message_time.items()
            if now - t < self.limit
        }
