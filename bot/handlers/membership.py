"""Membership handler – auto-register/deactivate chats on my_chat_member updates,
and handle group→supergroup migration service messages."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import ChatMemberUpdated, Message

from bot.db.engine import async_session
from bot.db.repositories.chat_repo import ChatRepo

logger = logging.getLogger(__name__)

membership_router = Router(name="membership")


# ── Migration service messages ────────────────────────────────────────


@membership_router.message(F.migrate_to_chat_id)
async def on_migrate_to_chat(message: Message) -> None:
    """Handle group→supergroup migration service message.

    When a group upgrades, Telegram sends a service message with
    ``migrate_to_chat_id`` set to the new supergroup ID.
    """
    if message.migrate_to_chat_id is None:
        return  # Not a migration message – skip (will fall through to other routers)

    old_id = message.chat.id
    new_id = message.migrate_to_chat_id

    async with async_session() as session:
        repo = ChatRepo(session)
        await repo.update_chat_id(old_id, new_id)

    logger.info("Chat migrated: %d → %d (service message)", old_id, new_id)


@membership_router.my_chat_member()
async def on_my_chat_member(event: ChatMemberUpdated) -> None:
    """Handle bot being added/removed from a chat."""
    new_status = event.new_chat_member.status
    chat = event.chat

    async with async_session() as session:
        repo = ChatRepo(session)

        if new_status in ("member", "administrator"):
            # Bot was added (or promoted) – register / reactivate
            await repo.upsert_chat(
                chat_id=chat.id,
                chat_type=chat.type,
                title=chat.title,
                username=chat.username,
            )
            logger.info(
                "Chat registered: %d (%s) type=%s",
                chat.id,
                chat.title or chat.username or "DM",
                chat.type,
            )
            # Send confirmation (best effort – may fail in channels)
            try:
                from aiogram import Bot

                bot: Bot = event.bot  # type: ignore[assignment]
                await bot.send_message(
                    chat.id,
                    "<b>Connected!</b> This chat is now part of your network.\n\n"
                    "Messages sent here will sync to your other chats, "
                    "and vice versa. Tap /stop to disconnect.",
                )
            except Exception:
                pass  # Can't send to some chat types

        elif new_status in ("kicked", "left"):
            # Bot was removed – deactivate
            await repo.deactivate_chat(chat.id)
            logger.info("Chat deactivated: %d (bot removed)", chat.id)
