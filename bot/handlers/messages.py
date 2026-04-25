"""Message handler – the catch-all router for new messages and channel posts."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import Message, MessageOriginChannel

from bot.db.engine import async_session
from bot.db.repositories.chat_repo import ChatRepo
from bot.db.repositories.send_log_repo import SendLogRepo
from bot.services.dedup import (
    is_duplicate,
    is_duplicate_update,
    is_media_group_seen,
)
from bot.services.distributor import get_distributor
from bot.services.media_group import get_media_group_buffer
from bot.services.normalizer import normalize
from bot.services.replies import populate_reply_source

logger = logging.getLogger(__name__)

messages_router = Router(name="messages")


# ── Bug 5: Auto-forward handler ───────────────────────────────────────────────
# When the bot sends a message to a channel, Telegram automatically forwards
# it to the channel's linked discussion group.  The bot never explicitly sent
# to the discussion group, so no send_log row exists for that chat.
# When a user in the discussion group replies to that auto-forwarded post,
# reverse_lookup(discussion_group_id, message_id) returns None and reply
# threading silently fails.
#
# Fix: when we receive an is_automatic_forward=True message we look up the
# channel entry in send_log to find the original (source_chat, source_msg),
# then insert a secondary row mapping that source to the discussion group
# message.  After this, reply threading works in one step for all dests.
#
# This handler MUST be registered before the generic on_message handler so
# that auto-forwarded messages are intercepted here and not re-distributed.
@messages_router.message(F.is_automatic_forward == True)  # noqa: E712
async def on_auto_forward(message: Message) -> None:
    """Log auto-forwarded channel posts to discussion groups for reply threading."""
    try:
        forward_origin = message.forward_origin
        if not isinstance(forward_origin, MessageOriginChannel):
            return

        channel_id: int = forward_origin.chat.id
        channel_msg_id: int = forward_origin.message_id
        discussion_group_id: int = message.chat.id
        discussion_group_msg_id: int = message.message_id

        async with async_session() as session:
            sl_repo = SendLogRepo(session)
            # Was this channel post one we redistributed?
            origin = await sl_repo.reverse_lookup(channel_id, channel_msg_id)
            if origin is None:
                return  # Not our message – ignore

            source_chat_id, source_message_id = origin
            # Insert secondary mapping: original source → discussion group
            await sl_repo.log_send(
                source_chat_id=source_chat_id,
                source_message_id=source_message_id,
                source_user_id=None,
                dest_chat_id=discussion_group_id,
                dest_message_id=discussion_group_msg_id,
            )
            logger.debug(
                "Auto-forward mapped: channel (%d, %d) -> discussion (%d, %d) "
                "← original source (%d, %d)",
                channel_id, channel_msg_id,
                discussion_group_id, discussion_group_msg_id,
                source_chat_id, source_message_id,
            )
    except Exception as e:
        logger.debug("Auto-forward mapping error: %s", e)


# ── Standard content handlers ─────────────────────────────────────────────────

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
    """Common handler: restriction check → normalize → source check → dedup → distribute/buffer.

    Dedup happens in two distinct phases (see :mod:`bot.services.dedup`):

    - *Update-level* dedup (``is_duplicate_update``) runs first.  It keys on
      ``(chat_id, message_id)`` with a short 60 s TTL and exists purely to
      swallow Telegram webhook redeliveries.  Cheap, precise, no false positives.

    - *Content-level* dedup (``is_duplicate`` / ``is_album_duplicate``) runs
      later in the pipeline and is now scoped per source chat, so common text
      ("ok", "thanks", "good morning") sent in different chats no longer
      collides.  For albums, content dedup is deferred to flush time so we
      judge the whole assembled group instead of dropping individual items
      and producing partial albums.
    """
    # 0. Ignore bot commands so command routers can handle them.
    if message.text and message.entities:
        first = message.entities[0]
        if first.type == "bot_command" and first.offset == 0:
            return

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

    # ── Step 3a: Webhook-retry guard ──────────────────────────────────────────
    # Telegram occasionally redelivers the same update after a network blip.
    # Drop the second delivery silently using a (chat_id, message_id) marker
    # with a 60s TTL — the only correct way to dedup retries since the content
    # fingerprint can match across legitimately distinct messages too.
    if await is_duplicate_update(redis, message.chat.id, message.message_id):
        logger.debug(
            "Dropping webhook-retry update (chat=%d, msg=%d)",
            message.chat.id,
            message.message_id,
        )
        return

    # ── Step 4: Reply detection ──────────────────────────────────────────────
    # Reply detection MUST run for ALL message types — including album items —
    # before the media-group branch returns early.  We need bot_info both here
    # and possibly downstream, so fetch it once and reuse.
    # B-3 / B-7: detection logic lives in bot.services.replies so the same code
    # path runs for edited messages too.
    bot_info = await bot.get_me()
    await populate_reply_source(message, normalized, bot_info)

    # 5. Media group handling
    # Reply fields (reply_source_chat_id / reply_source_message_id) are now
    # populated on `normalized` before buffering, so _flush_group can propagate
    # them to the composite NormalizedMessage.
    #
    # NOTE: per-item content dedup *before* buffering used to live here.  It
    # caused two bugs:
    #   - Partial-album: if a single item's file_unique_id matched a prior
    #     send (single OR another album), that item was dropped and the album
    #     arrived with a hole.
    #   - Re-uploads with a new media_group_id but same files weren't fully
    #     dropped — they fell through whenever any item was new.
    # Whole-album dedup now happens at flush time in MediaGroupBuffer.
    if normalized.media_group_id:
        # Mark seen (informational/observability — return value ignored on the
        # arrival path; the actual dedup is the update-level guard above plus
        # the album-level guard at flush).
        await is_media_group_seen(
            redis, normalized.source_chat_id, normalized.media_group_id
        )

        buffer = get_media_group_buffer()
        await buffer.add(normalized)
        return  # Will be flushed as a group later

    # 6. Content dedup — per source chat
    if await is_duplicate(redis, normalized):
        logger.debug(
            "Dropping duplicate content from chat %d (msg %d)",
            message.chat.id,
            message.message_id,
        )
        return

    # 7. Distribute
    await distributor.distribute(normalized)
