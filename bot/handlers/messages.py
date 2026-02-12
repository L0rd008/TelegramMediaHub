"""Message handler – the catch-all router for new messages and channel posts."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import Message

from bot.db.engine import async_session
from bot.db.repositories.chat_repo import ChatRepo
from bot.services.dedup import is_duplicate, is_media_group_seen
from bot.services.distributor import get_distributor
from bot.services.media_group import get_media_group_buffer
from bot.services.normalizer import normalize

logger = logging.getLogger(__name__)

messages_router = Router(name="messages")


# Handler for regular messages
@messages_router.message(
    F.text | F.photo | F.video | F.animation | F.audio | F.document | F.voice | F.video_note | F.sticker
)
async def on_message(message: Message) -> None:
    """Handle an incoming message – normalize, dedup, and distribute."""
    await _handle_content(message)


# Handler for channel posts
@messages_router.channel_post(
    F.text | F.photo | F.video | F.animation | F.audio | F.document | F.voice | F.video_note | F.sticker
)
async def on_channel_post(message: Message) -> None:
    """Handle an incoming channel post – same flow as regular messages."""
    await _handle_content(message)


async def _handle_content(message: Message) -> None:
    """Common handler: normalize → source check → dedup → distribute/buffer."""
    # 1. Normalize
    normalized = normalize(message)
    if normalized is None:
        return  # Unsupported message type

    # 2. Check if the chat is a registered source
    async with async_session() as session:
        repo = ChatRepo(session)
        if not await repo.is_active_source(message.chat.id):
            return  # Not a registered source – ignore

    # 3. Get services from the running bot's dispatcher
    bot = message.bot
    if bot is None:
        return

    # Access redis from bot's data (set in app.py)
    from aiogram import Dispatcher

    # Since we can't easily get dp from message context, use the singleton
    distributor = get_distributor()
    redis = distributor._redis  # Use the same Redis instance

    # 4. Media group handling
    if normalized.media_group_id:
        # Mark this media_group_id as seen (24h TTL in Redis).
        # is_media_group_seen returns False for the first item (marking it),
        # True for subsequent items in the same group.  All items are buffered
        # regardless – the marker prevents duplicate processing if the same
        # media_group_id is replayed (e.g. webhook retry).
        await is_media_group_seen(redis, normalized.media_group_id)
        buffer = get_media_group_buffer()
        await buffer.add(normalized)
        return  # Will be flushed as a group later

    # 5. Dedup check
    bot_info = await bot.get_me()
    if await is_duplicate(redis, normalized, bot_info.id):
        logger.debug("Dropping duplicate message %d", message.message_id)
        return

    # 6. Distribute
    await distributor.distribute(normalized)
