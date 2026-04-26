"""Authorization helpers — Telegram chat-admin checks for sensitive commands.

Toggles like ``/selfsend`` and ``/broadcast`` change how *every* member of a
group experiences the bot.  They must therefore be restricted to chat
administrators (creator or administrator role).  Private chats are exempt
because the user is the only participant.

The check uses ``getChatMember`` which is a single Bot API call.  Results
are intentionally NOT cached: admin status can change at any moment, and
toggle commands are infrequent enough that the round-trip cost is negligible.
"""

from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.types import Message

logger = logging.getLogger(__name__)

# Telegram chat-member statuses that count as "can manage the bot for this chat".
ADMIN_STATUSES = {"creator", "administrator"}

# Chat types where the admin check applies.  Private chats are 1:1 between the
# user and the bot, so the user is implicitly authorised.
ADMIN_REQUIRED_CHAT_TYPES = {"group", "supergroup", "channel"}


async def caller_can_manage(message: Message) -> bool:
    """Return True if the caller is allowed to change chat-level settings.

    Rules:

    - Private chat → always True (user manages their own DM).
    - Group / supergroup → True iff caller is creator or administrator.
    - Channel → True iff caller is creator or administrator.  In channels
      most posts are anonymous (``from_user`` is ``None``); in that case we
      assume the poster IS an admin (Telegram only lets admins post in
      channels) and return True.
    - Anonymous group admin posts (``from_user.id == 1087968824``,
      ``GroupAnonymousBot``) → True; same reasoning as channel.

    Errors during the API call resolve to False (deny by default) and are
    logged.  This is the safer side to err on for a permission gate.
    """
    chat = message.chat
    if chat.type not in ADMIN_REQUIRED_CHAT_TYPES:
        return True  # private DM

    user = message.from_user

    # Channel posts (and some anon-admin group posts) lack a from_user.  Only
    # admins are allowed to publish in channels, so absence is a positive
    # signal here.
    if user is None:
        return True

    # Anonymous admin posting in a group: the GroupAnonymousBot user id.
    # By definition only group admins can post anonymously.
    if user.id == 1087968824:
        return True

    bot: Bot | None = message.bot
    if bot is None:
        # Should never happen in normal handler flow, but be defensive.
        logger.debug("caller_can_manage: message.bot is None for chat %d", chat.id)
        return False

    try:
        member = await bot.get_chat_member(chat.id, user.id)
        return member.status in ADMIN_STATUSES
    except Exception as e:
        logger.debug(
            "caller_can_manage: get_chat_member failed for chat=%d user=%d: %s",
            chat.id, user.id, e,
        )
        return False
