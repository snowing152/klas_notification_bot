"""Dispatcher-wide guards: private chats only, and the anti-spam middleware."""

import time
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from app.bot import setup_guards
from app.middleware.antispam import AntiSpamMiddleware

USER = User(id=42, is_bot=False, first_name="T", language_code="en")


def make_dispatcher():
    dp = Dispatcher(storage=MemoryStorage())
    setup_guards(dp)
    seen = []

    async def on_message(message: Message):
        seen.append(("message", message.chat.type))

    async def on_callback(callback: CallbackQuery):
        seen.append(("callback", callback.message.chat.type))

    dp.message.register(on_message)
    dp.callback_query.register(on_callback)
    return dp, seen


def message_for(chat_type, update_id=1):
    chat = Chat(id=-100 if chat_type != "private" else 42, type=chat_type)
    message = Message(message_id=update_id, date=int(time.time()), chat=chat,
                      from_user=USER, text="hi")
    return Update(update_id=update_id, message=message)


def callback_for(chat_type, update_id=1):
    chat = Chat(id=-100 if chat_type != "private" else 42, type=chat_type)
    message = Message(message_id=1, date=int(time.time()), chat=chat, text="x")
    callback = CallbackQuery(id=str(update_id), from_user=USER, chat_instance="c",
                             message=message, data="d")
    return Update(update_id=update_id, callback_query=callback)


@pytest.fixture
def bot():
    return Bot(token="123456:test")


@pytest.mark.parametrize("chat_type", ["group", "supergroup", "channel"])
async def test_non_private_messages_are_ignored(bot, chat_type):
    dp, seen = make_dispatcher()
    await dp.feed_update(bot, message_for(chat_type))
    assert seen == []


@pytest.mark.parametrize("chat_type", ["group", "supergroup", "channel"])
async def test_non_private_callbacks_are_ignored(bot, chat_type):
    dp, seen = make_dispatcher()
    await dp.feed_update(bot, callback_for(chat_type))
    assert seen == []


async def test_private_chat_is_handled(bot):
    dp, seen = make_dispatcher()
    await dp.feed_update(bot, message_for("private", update_id=1))
    await dp.feed_update(bot, callback_for("private", update_id=2))
    assert ("message", "private") in seen
    assert ("callback", "private") in seen


def fake_event(user_id=1):
    event = AsyncMock()
    event.from_user = AsyncMock()
    event.from_user.id = user_id
    event.from_user.language_code = "en"
    event.answer = AsyncMock()
    return event


async def test_second_message_inside_the_limit_is_throttled():
    mw = AntiSpamMiddleware(limit=60)
    handler = AsyncMock()
    event = fake_event()
    await mw(handler, event, {})
    await mw(handler, event, {})
    assert handler.await_count == 1
    event.answer.assert_awaited_once()


async def test_message_during_a_registration_step_is_never_throttled():
    mw = AntiSpamMiddleware(limit=60, skip_in_state=True)
    handler = AsyncMock()
    event = fake_event()
    data = {"raw_state": "RegistrationStates:waiting_for_password"}
    await mw(handler, event, data)
    await mw(handler, event, data)
    assert handler.await_count == 2
    event.answer.assert_not_awaited()


async def test_state_is_ignored_when_skip_in_state_is_off():
    mw = AntiSpamMiddleware(limit=60)
    handler = AsyncMock()
    event = fake_event()
    data = {"raw_state": "X:y"}
    await mw(handler, event, data)
    await mw(handler, event, data)
    assert handler.await_count == 1


async def test_failing_handler_runs_once_and_its_error_propagates():
    mw = AntiSpamMiddleware(limit=0)
    handler = AsyncMock(side_effect=RuntimeError("boom"))
    with pytest.raises(RuntimeError):
        await mw(handler, fake_event(), {})
    assert handler.await_count == 1


async def test_middleware_own_failure_lets_the_event_through():
    mw = AntiSpamMiddleware(limit=60)
    handler = AsyncMock(return_value="ok")
    event = fake_event()
    event.from_user = None  # makes the middleware's own logic raise
    assert await mw(handler, event, {}) == "ok"
    assert handler.await_count == 1


async def test_callbacks_are_throttled_with_their_own_clock():
    messages = AntiSpamMiddleware(limit=60)
    callbacks = AntiSpamMiddleware(limit=60)
    handler = AsyncMock()
    event = fake_event()
    await messages(handler, event, {})
    await callbacks(handler, event, {})
    assert handler.await_count == 2
    await callbacks(handler, event, {})
    assert handler.await_count == 2
