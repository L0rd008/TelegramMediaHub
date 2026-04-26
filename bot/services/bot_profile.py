"""Bot profile sync — keeps the BotFather command list and descriptions in
sync with what the code actually exposes.

Called on startup.  All three Telegram API calls are best-effort — if a
description hasn't changed Telegram returns 400 and we swallow it; if there's
a transient error we log at DEBUG and keep going (the bot still works, the
in-client UI just won't refresh until next restart).

The descriptions deliberately lead with the *value* of the bot (cross-chat
content sync) rather than feature names, and the premium pitch is one
informative line in :func:`_long_description` — not pushy, not buried.  This
matches the user-facing copy guidance from the 2026-04-25 product call.
"""

from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import BotCommand

logger = logging.getLogger(__name__)


# Public commands shown in the BotFather menu (the "/" picker in clients).
# Keep this list aligned with what /help advertises.  Admin-only commands
# (/pause, /signature, /grant etc.) are intentionally NOT listed here — they
# stay invisible to regular users.
PUBLIC_COMMANDS: list[BotCommand] = [
    BotCommand(command="start",     description="Connect this chat / show the guide"),
    BotCommand(command="help",      description="What I can do and how to use me"),
    BotCommand(command="selfsend",  description="Echo your messages back to this chat"),
    BotCommand(command="broadcast", description="Pause / resume sync for this chat"),
    BotCommand(command="stats",     description="Your activity in the network"),
    BotCommand(command="subscribe", description="Go Premium — see the plans"),
    BotCommand(command="plan",      description="Check your current plan"),
    BotCommand(command="stop",      description="Disconnect this chat"),
]


# Short description: appears under the bot name in chat-info.  Max 120 chars
# enforced by Telegram.
SHORT_DESCRIPTION = (
    "Cross-chat relay. Send media or text once, it lands in every chat "
    "you've connected. Originals, never forwards."
)


# Long description: appears on the bot's profile / before-you-start screen.
# Max 512 chars enforced by Telegram.
LONG_DESCRIPTION = (
    "I'm an intermediary between you and every chat you connect me to.\n\n"
    "• Private chat with me: I relay everything — text, media, files — to "
    "your network and bring everything back.\n"
    "• Groups I'm in: all media relayed; text only when it's part of a "
    "thread that started with one of my messages. Each group also gets "
    "its own readable tag so recipients see which group content came from.\n"
    "• Channels: media-only relay.\n\n"
    "Free for the first month. Premium adds Sync Control (pause direction "
    "per chat) and removes daily caps."
)


async def sync_bot_profile(bot: Bot) -> None:
    """Push the current command list, short and long descriptions to Telegram."""
    await _safe(bot.set_my_commands(PUBLIC_COMMANDS), "set_my_commands")
    await _safe(bot.set_my_short_description(SHORT_DESCRIPTION), "set_my_short_description")
    await _safe(bot.set_my_description(LONG_DESCRIPTION), "set_my_description")


async def _safe(awaitable, label: str) -> None:
    """Run ``awaitable`` and treat 'description is the same' as success.

    Telegram's set_my_*description calls return ``400 Bad Request: description
    is not modified`` when the value already matches.  That's fine for our
    purposes — we still consider the profile in sync.
    """
    try:
        await awaitable
    except TelegramBadRequest as e:
        msg = (e.message or "").lower()
        if "not modified" in msg or "is the same" in msg:
            logger.debug("%s: already up-to-date", label)
            return
        logger.debug("%s failed: %s", label, e)
    except Exception as e:
        logger.debug("%s failed: %s", label, e)
