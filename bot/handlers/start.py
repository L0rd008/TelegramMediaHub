"""Start/stop/selfsend/broadcast handlers for all users."""

from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from bot.config import settings
from bot.db.engine import async_session
from bot.db.repositories.chat_repo import ChatRepo
from bot.services.keyboards import (
    build_broadcast_panel,
    build_main_menu,
    build_selfsend_result,
    build_stop_confirm,
)
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
        "• Sender aliases — identify who sent what without exposing identities",
        reply_markup=build_main_menu(),
    )


@start_router.message(Command("stop"))
async def cmd_stop(message: Message) -> None:
    """Show confirmation before unregistering this chat."""
    await message.answer(
        "⚠️ <b>Are you sure you want to unregister this chat?</b>\n\n"
        "You will stop sending and receiving distributed content.",
        reply_markup=build_stop_confirm(),
    )


@start_router.message(Command("selfsend"))
async def cmd_selfsend(message: Message, command: CommandObject) -> None:
    """Toggle self-send for this chat."""
    args = (command.args or "").strip().lower()

    # No args → show button panel
    if args not in ("on", "off"):
        async with async_session() as session:
            chat = await ChatRepo(session).get_chat(message.chat.id)
        if chat is None:
            await message.answer("Please /start first to register this chat.")
            return
        status = "enabled ✅" if chat.allow_self_send else "disabled ❌"
        kb = build_selfsend_result(chat.allow_self_send)
        await message.answer(
            f"🔄 Self-send is currently <b>{status}</b>", reply_markup=kb
        )
        return

    enabled = args == "on"

    async with async_session() as session:
        repo = ChatRepo(session)
        await repo.toggle_self_send(message.chat.id, enabled)

    status = "enabled ✅" if enabled else "disabled ❌"
    kb = build_selfsend_result(enabled)
    await message.answer(f"🔄 Self-send {status} for this chat.", reply_markup=kb)


@start_router.message(Command("broadcast"))
async def cmd_broadcast(message: Message, command: CommandObject) -> None:
    """Control broadcast direction. Usage: /broadcast off|on in|out, or no args for panel."""
    raw_args = (command.args or "").strip().lower().split()

    # No args → show button panel
    if not raw_args or len(raw_args) != 2 or raw_args[0] not in ("off", "on") or raw_args[1] not in ("in", "out"):
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

        out_status = "🔊 ON" if chat_obj.is_source else "🔇 PAUSED"
        in_status = "🔊 ON" if chat_obj.is_destination else "🔇 PAUSED"
        kb = build_broadcast_panel(chat_obj.is_source, chat_obj.is_destination)
        await message.answer(
            f"📡 <b>Broadcast Control</b>\n\n"
            f"Outgoing: <b>{out_status}</b>\n"
            f"Incoming: <b>{in_status}</b>",
            reply_markup=kb,
        )
        return

    action, direction = raw_args[0], raw_args[1]
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
        else:
            await repo.toggle_destination(message.chat.id, enabled)

    # Re-fetch and show panel with updated state
    async with async_session() as session:
        chat_obj = await ChatRepo(session).get_chat(message.chat.id)

    out_status = "🔊 ON" if chat_obj.is_source else "🔇 PAUSED"
    in_status = "🔊 ON" if chat_obj.is_destination else "🔇 PAUSED"
    kb = build_broadcast_panel(chat_obj.is_source, chat_obj.is_destination)
    await message.answer(
        f"📡 <b>Broadcast Control</b>\n\n"
        f"Outgoing: <b>{out_status}</b>\n"
        f"Incoming: <b>{in_status}</b>",
        reply_markup=kb,
    )


def _get_redis():
    """Get Redis instance from the running distributor (avoids circular imports)."""
    try:
        from bot.services.distributor import get_distributor
        return get_distributor()._redis
    except RuntimeError:
        return None
