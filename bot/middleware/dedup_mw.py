"""Self-message middleware – drops bot's own messages early to prevent loops."""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message

logger = logging.getLogger(__name__)


class SelfMessageMiddleware(BaseMiddleware):
    """Drop messages originating from the bot itself to prevent re-distribution loops.

    Registered on ``dp.message`` and ``dp.channel_post`` outer middleware.
    """

    async def __call__(
        self,
        handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: dict[str, Any],
    ) -> Any:
        # Channel posts have no from_user – check via sender_chat if it matches bot info
        if event.from_user and event.from_user.is_bot:
            bot = data.get("bot") or event.bot
            if bot:
                bot_info = await bot.get_me()
                if event.from_user.id == bot_info.id:
                    logger.debug(
                        "Dropping self-message %d in chat %d",
                        event.message_id,
                        event.chat.id,
                    )
                    return  # Swallow – do not call handler

        return await handler(event, data)
