"""Logging middleware – structured logging per update."""

from __future__ import annotations

import logging
import time
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update

logger = logging.getLogger("bot.updates")


class LoggingMiddleware(BaseMiddleware):
    """Log each update with timing and basic metadata."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        start = time.perf_counter()

        update: Update | None = data.get("event_update")
        update_type = "unknown"
        chat_id = None

        if isinstance(event, Update):
            update = event

        if update:
            if update.message:
                update_type = "message"
                chat_id = update.message.chat.id
            elif update.channel_post:
                update_type = "channel_post"
                chat_id = update.channel_post.chat.id
            elif update.edited_message:
                update_type = "edited_message"
                chat_id = update.edited_message.chat.id
            elif update.edited_channel_post:
                update_type = "edited_channel_post"
                chat_id = update.edited_channel_post.chat.id
            elif update.my_chat_member:
                update_type = "my_chat_member"
                chat_id = update.my_chat_member.chat.id

        try:
            result = await handler(event, data)
            elapsed = (time.perf_counter() - start) * 1000
            logger.info(
                "update=%s chat=%s elapsed=%.1fms",
                update_type,
                chat_id,
                elapsed,
            )
            return result
        except Exception as e:
            elapsed = (time.perf_counter() - start) * 1000
            logger.error(
                "update=%s chat=%s elapsed=%.1fms error=%s",
                update_type,
                chat_id,
                elapsed,
                e,
            )
            raise
