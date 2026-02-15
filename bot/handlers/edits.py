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

    # Normalize and redistribute as a new message
    normalized = normalize(message)
    if normalized is None:
        return

    # Check source
    async with async_session() as session:
        repo = ChatRepo(session)
        if not await repo.is_active_source(message.chat.id):
            return

    distributor = get_distributor()
    await distributor.distribute(normalized)
    logger.info("Edit redistributed: message %d in chat %d", message.message_id, message.chat.id)
