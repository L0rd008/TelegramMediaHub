"""Managed Bot handler stubs – Bot API 9.6 managed-bot service message support.

Bot API 9.6 introduced ManagedBot* service message types that fire when a
business account delegates bot control.  These handlers ensure all new update
types are observable (logged) instead of silently discarded, and provide
extension points for future managed-bot integrations.

Reference: https://core.telegram.org/bots/api#managedbot
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import Message

logger = logging.getLogger(__name__)

managed_bot_router = Router(name="managed_bot")


# ── ManagedBot service messages ──────────────────────────────────────────────
# These arrive as Message objects with a specific service-message field set.


@managed_bot_router.message(F.managed_bot_created)
async def on_managed_bot_created(message: Message) -> None:
    """Fired when a business user creates a managed bot connection.

    A business account has delegated this bot to manage their account.
    Implement onboarding logic here (e.g., store the business chat_id,
    register as a source/destination, send a welcome message).
    """
    logger.info(
        "ManagedBotCreated: chat_id=%d — bot now manages this business account.",
        message.chat.id,
    )
    # TODO: implement onboarding for managed-bot business account connections


@managed_bot_router.message(F.managed_bot_paused)
async def on_managed_bot_paused(message: Message) -> None:
    """Fired when the business user pauses the managed bot connection."""
    logger.info(
        "ManagedBotPaused: chat_id=%d — managed-bot connection paused.",
        message.chat.id,
    )
    # TODO: suspend distribution for this business chat if needed


@managed_bot_router.message(F.managed_bot_resumed)
async def on_managed_bot_resumed(message: Message) -> None:
    """Fired when the business user resumes the managed bot connection."""
    logger.info(
        "ManagedBotResumed: chat_id=%d — managed-bot connection resumed.",
        message.chat.id,
    )
    # TODO: resume distribution for this business chat if needed
