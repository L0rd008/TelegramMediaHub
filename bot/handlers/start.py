"""Start/stop/selfsend handlers for all users."""

from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from bot.config import settings
from bot.db.engine import async_session
from bot.db.repositories.chat_repo import ChatRepo
from bot.services.subscription import build_subscribe_button

logger = logging.getLogger(__name__)

start_router = Router(name="start")


@start_router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    """Register this chat as source+destination."""
    chat = message.chat

    async with async_session() as session:
        repo = ChatRepo(session)
        await repo.upsert_chat(
            chat_id=chat.id,
            chat_type=chat.type,
            title=chat.title,
            username=chat.username,
        )

    logger.info("Chat registered via /start: %d", chat.id)
    await message.answer(
        "👋 <b>Welcome to TelegramMediaHub!</b>\n\n"
        "This chat is now registered. Content sent here will be "
        "distributed to all other registered chats, and vice versa.\n\n"
        f"🎁 You have a <b>{settings.TRIAL_DAYS}-day free trial</b> with "
        "full access to every feature — no payment needed to get started.\n\n"
        "<b>Commands:</b>\n"
        "/stop — Unregister this chat\n"
        "/selfsend on|off — Toggle self-send\n"
        "/subscribe — View premium plans\n"
        "/plan — Check your subscription status",
        reply_markup=build_subscribe_button(),
    )


@start_router.message(Command("stop"))
async def cmd_stop(message: Message) -> None:
    """Unregister this chat."""
    async with async_session() as session:
        repo = ChatRepo(session)
        await repo.deactivate_chat(message.chat.id)

    logger.info("Chat deactivated via /stop: %d", message.chat.id)
    await message.answer(
        "🛑 <b>Chat unregistered.</b>\n"
        "This chat will no longer send or receive distributed content.\n"
        "Use /start to re-register."
    )


@start_router.message(Command("selfsend"))
async def cmd_selfsend(message: Message, command: CommandObject) -> None:
    """Toggle self-send for this chat."""
    args = (command.args or "").strip().lower()

    if args not in ("on", "off"):
        await message.answer("Usage: /selfsend on|off")
        return

    enabled = args == "on"

    async with async_session() as session:
        repo = ChatRepo(session)
        await repo.toggle_self_send(message.chat.id, enabled)

    status = "enabled ✅" if enabled else "disabled ❌"
    await message.answer(f"Self-send {status} for this chat.")
