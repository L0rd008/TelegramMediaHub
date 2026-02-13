"""Message handler – the catch-all router for new messages and channel posts."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import Message

from bot.db.engine import async_session
from bot.db.repositories.chat_repo import ChatRepo
from bot.db.repositories.send_log_repo import SendLogRepo
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
    """Common handler: restriction check → normalize → source check → dedup → distribute/buffer."""
    # 0. Check user restrictions (mute/ban) – drop early to save resources
    user_id = message.from_user.id if message.from_user else None
    if user_id:
        try:
            from bot.services.moderation import is_user_restricted
            _dist = get_distributor()
            if await is_user_restricted(_dist._redis, user_id):
                logger.debug("Dropping message from restricted user %d", user_id)
                return
        except RuntimeError:
            pass  # Distributor not initialized yet

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

    # 5b. Reply detection – if the user replied to a bot message, resolve the
    #     original source via send_log so the distributor can thread replies.
    reply = message.reply_to_message
    if reply and reply.from_user and reply.from_user.id == bot_info.id:
        async with async_session() as session:
            sl_repo = SendLogRepo(session)
            origin = await sl_repo.reverse_lookup(message.chat.id, reply.message_id)
            if origin:
                normalized.reply_source_chat_id = origin[0]
                normalized.reply_source_message_id = origin[1]
                logger.debug(
                    "Reply detected: msg %d replies to bot msg %d → source (%d, %d)",
                    message.message_id,
                    reply.message_id,
                    origin[0],
                    origin[1],
                )

    # 6. Distribute
    await distributor.distribute(normalized)
