"""Reply-source detection — populate NormalizedMessage.reply_source_* fields
for replies that target bot-relayed messages.

Lifted out of bot.handlers.messages so the same logic can run for edited
messages (bot.handlers.edits) — without it, edit redistribution silently
loses reply threading. (See B-3 in remediation-plan-2026-04-25.md.)
"""

from __future__ import annotations

import logging

from aiogram.types import Message, User

from bot.db.engine import async_session
from bot.db.repositories.send_log_repo import SendLogRepo
from bot.services.normalizer import NormalizedMessage

logger = logging.getLogger(__name__)


async def populate_reply_source(
    message: Message,
    normalized: NormalizedMessage,
    bot_info: User,
) -> None:
    """If *message* is a reply to a bot-relayed message, populate
    ``normalized.reply_source_chat_id`` / ``reply_source_message_id``
    by reverse-looking-up the send_log row.

    No-op if the message is not a reply, or the reply target does not look
    like a bot-authored message.

    B-7: the "is bot reply" gate is tighter than the previous version — we
    require either an explicit from_user.id == bot_info.id match, OR both
    ``from_user`` AND ``sender_chat`` to be None (which corresponds to
    bot-sent messages whose author info has been stripped). Channel posts
    have ``sender_chat`` populated, so they are correctly excluded.
    """
    reply = message.reply_to_message
    if reply is None:
        return

    is_bot_reply = (
        reply.from_user is not None and reply.from_user.id == bot_info.id
    ) or (
        reply.from_user is None and reply.sender_chat is None
    )
    if not is_bot_reply:
        return

    try:
        async with async_session() as session:
            sl_repo = SendLogRepo(session)
            origin = await sl_repo.reverse_lookup(
                message.chat.id, reply.message_id
            )
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
    except Exception as e:
        logger.debug(
            "Reply reverse-lookup failed for msg %d: %s",
            reply.message_id,
            e,
        )
