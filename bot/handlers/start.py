"""Start/stop/selfsend/broadcast handlers for all users."""

from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from bot.config import settings
from bot.db.engine import async_session
from bot.db.repositories.chat_repo import ChatRepo
from bot.services.subscription import build_subscribe_button, is_premium

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
        "<b>What you get:</b>\n"
        "• Content synced across all your chats\n"
        "• Reply threading — replies follow conversations everywhere\n"
        "• Broadcast control — pause/resume what you send and receive\n"
        "• Sender aliases — identify who sent what without exposing identities\n\n"
        "<b>Commands:</b>\n"
        "/stop — Unregister this chat\n"
        "/selfsend on|off — Toggle self-send\n"
        "/broadcast off|on in|out — Pause/resume broadcasts\n"
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


@start_router.message(Command("broadcast"))
async def cmd_broadcast(message: Message, command: CommandObject) -> None:
    """Control broadcast direction. Usage: /broadcast off|on in|out."""
    args = (command.args or "").strip().lower().split()
    if len(args) != 2 or args[0] not in ("off", "on") or args[1] not in ("in", "out"):
        await message.answer(
            "<b>Usage:</b>\n"
            "/broadcast off out — Pause sending your content to others\n"
            "/broadcast on out  — Resume sending your content to others\n"
            "/broadcast off in  — Pause receiving content from others\n"
            "/broadcast on in   — Resume receiving content from others"
        )
        return

    action, direction = args[0], args[1]
    enabled = action == "on"

    # Premium gating
    redis = _get_redis()
    if redis is None:
        await message.answer("Service temporarily unavailable.")
        return

    async with async_session() as session:
        chat_obj = await ChatRepo(session).get_chat(message.chat.id)
    if chat_obj is None:
        await message.answer("Please /start first to register this chat.")
        return

    if not await is_premium(redis, message.chat.id, chat_obj.registered_at):
        await message.answer(
            "🔒 <b>Broadcast control is a Premium feature.</b>\n\n"
            "Upgrade to manage exactly what you send and receive.\n"
            "Plans start at just <b>~36 ⭐/day</b>.",
            reply_markup=build_subscribe_button(),
        )
        return

    async with async_session() as session:
        repo = ChatRepo(session)
        if direction == "out":
            await repo.toggle_source(message.chat.id, enabled)
            if enabled:
                await message.answer(
                    "🔊 <b>Outgoing broadcast resumed.</b>\n"
                    "Content from this chat will be sent to others again."
                )
            else:
                await message.answer(
                    "🔇 <b>Outgoing broadcast paused.</b>\n"
                    "Content from this chat will no longer be sent to others.\n"
                    "Use /broadcast on out to resume."
                )
        else:  # "in"
            await repo.toggle_destination(message.chat.id, enabled)
            if enabled:
                await message.answer(
                    "🔊 <b>Incoming broadcast resumed.</b>\n"
                    "This chat will receive content from others again."
                )
            else:
                await message.answer(
                    "🔇 <b>Incoming broadcast paused.</b>\n"
                    "This chat will no longer receive content from others.\n"
                    "Use /broadcast on in to resume."
                )


def _get_redis():
    """Get Redis instance from the running distributor (avoids circular imports)."""
    try:
        from bot.services.distributor import get_distributor
        return get_distributor()._redis
    except RuntimeError:
        return None
