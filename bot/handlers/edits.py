"""Edit handler – redistribute edited messages if enabled."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import Message

from bot.db.engine import async_session
from bot.db.repositories.chat_repo import ChatRepo
from bot.db.repositories.config_repo import ConfigRepo
from bot.services.distributor import get_distributor
from bot.services.normalizer import normalize
from bot.services.replies import populate_reply_source
from bot.services.threads import is_in_bot_thread, mark_in_thread
from bot.utils.enums import MessageType

logger = logging.getLogger(__name__)

edits_router = Router(name="edits")


@edits_router.edited_message(
    F.text | F.photo | F.video | F.animation | F.audio | F.document | F.voice | F.video_note | F.sticker
)
async def on_edited_message(message: Message) -> None:
    """Handle an edited message."""
    await _handle_edit(message)


@edits_router.edited_channel_post(
    F.text | F.photo | F.video | F.animation | F.audio | F.document | F.voice | F.video_note | F.sticker
)
async def on_edited_channel_post(message: Message) -> None:
    """Handle an edited channel post."""
    await _handle_edit(message)


async def _handle_edit(message: Message) -> None:
    """Check edit mode and redistribute if 'resend' mode is enabled."""
    async with async_session() as session:
        config_repo = ConfigRepo(session)
        edit_mode = await config_repo.get_value("edit_redistribution")

    if edit_mode != "resend":
        return  # Edit redistribution is off

    # Check user restrictions (mute/ban) – drop edits from restricted users
    user_id = message.from_user.id if message.from_user else None
    if user_id:
        try:
            from bot.services.moderation import is_user_restricted

            distributor = get_distributor()
            if await is_user_restricted(distributor._redis, user_id):
                logger.debug("Dropping edit from restricted user %d", user_id)
                return
        except RuntimeError:
            pass  # Distributor not initialized yet

    # Chat-level ban: drop edits originating in a banned chat.
    try:
        from bot.services.moderation import is_chat_restricted

        distributor = get_distributor()
        if await is_chat_restricted(distributor._redis, message.chat.id):
            logger.debug("Dropping edit from restricted chat %d", message.chat.id)
            return
    except RuntimeError:
        pass

    # Normalize and redistribute as a new message
    normalized = normalize(message)
    if normalized is None:
        return

    # Check source and get registered_at for premium check
    async with async_session() as session:
        repo = ChatRepo(session)
        source_chat = await repo.get_chat(message.chat.id)
        if source_chat is None or not source_chat.is_source:
            return

    # H-3: Source-side premium check for cross-chat edit redistribution.
    # Without this, a free-tier source chat with an expired trial can send
    # unlimited messages by editing previous ones, bypassing the paywall entirely.
    distributor = get_distributor()
    try:
        from bot.services.subscription import is_premium
        if not await is_premium(distributor._redis, message.chat.id, source_chat.registered_at):
            logger.debug(
                "Dropping edit redistribution from non-premium source chat %d",
                message.chat.id,
            )
            return
    except Exception as e:
        logger.debug("Premium check failed for edit from chat %d: %s", message.chat.id, e)
        return

    # B-3 fix: reply detection used to run only in messages.py:_handle_content,
    # so edited replies lost their threading anchor and arrived in every
    # destination as a top-level message. Run the same helper here so edited
    # replies preserve their thread.
    bot = message.bot
    if bot is not None:
        try:
            bot_info = await bot.get_me()
            await populate_reply_source(message, normalized, bot_info)
        except Exception as e:
            logger.debug("Reply detection on edit failed for msg %d: %s", message.message_id, e)

    # ── Chat-type / text gate (mirrors bot/handlers/messages.py) ────────────
    # Edits must obey the same relay policy as fresh messages — otherwise a
    # group member could bypass the no-text-relay rule by sending a media
    # message and then editing it into pure text, or sending an off-thread
    # message and editing it after the bot picked up an in-thread copy.
    chat_type = message.chat.type
    if normalized.message_type == MessageType.TEXT:
        if chat_type == "channel":
            logger.debug("Dropping edited text in channel %d", message.chat.id)
            return
        if chat_type in ("group", "supergroup"):
            reply = message.reply_to_message
            in_thread = False
            if reply is not None:
                in_thread = await is_in_bot_thread(
                    distributor._redis, message.chat.id, reply.message_id
                )
            if not in_thread:
                logger.debug(
                    "Dropping edited group text msg %d (not in bot-rooted thread)",
                    message.message_id,
                )
                return
            await mark_in_thread(
                distributor._redis, message.chat.id, message.message_id
            )

    await distributor.distribute(normalized)
    logger.info("Edit redistributed: message %d in chat %d", message.message_id, message.chat.id)
