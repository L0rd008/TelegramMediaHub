"""Start/stop/selfsend/broadcast/stats handlers for all users."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from bot.config import settings
from bot.db.engine import async_session
from bot.db.repositories.chat_repo import ChatRepo
from bot.db.repositories.restriction_repo import RestrictionRepo
from bot.db.repositories.send_log_repo import SendLogRepo
from bot.db.repositories.subscription_repo import SubscriptionRepo
from bot.services.keyboards import (
    build_broadcast_panel,
    build_main_menu,
    build_selfsend_result,
    build_stats_actions,
    build_stop_confirm,
)
from bot.services.subscription import (
    build_subscribe_button,
    get_trial_days_remaining,
    is_premium,
)

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


# ── /stats ───────────────────────────────────────────────────────────


def _is_admin(user_id: int | None) -> bool:
    if user_id is None:
        return False
    return user_id in settings.admin_ids


@start_router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    """Show per-chat stats for everyone; global stats appended for admins."""
    chat_id = message.chat.id
    user_id = message.from_user.id if message.from_user else None
    admin = _is_admin(user_id)

    # ── Per-chat stats ───────────────────────────────────────────────
    async with async_session() as session:
        chat_repo = ChatRepo(session)
        log_repo = SendLogRepo(session)
        sub_repo = SubscriptionRepo(session)

        chat = await chat_repo.get_chat(chat_id)
        if chat is None:
            await message.answer("This chat is not registered. Use /start first.")
            return

        sent_count = await log_repo.count_messages_from_chat(chat_id)
        recv_count = await log_repo.count_messages_to_chat(chat_id)
        active_sub = await sub_repo.get_active_subscription(chat_id)

    # Chat name
    name = chat.title or chat.username or str(chat.chat_id)

    # Days since registration
    reg_date = chat.registered_at
    if reg_date.tzinfo is None:
        reg_date = reg_date.replace(tzinfo=timezone.utc)
    days_active = max(0, (datetime.now(timezone.utc) - reg_date).days)

    # Alias
    alias_text = ""
    if user_id:
        redis = _get_redis()
        if redis:
            from bot.services.alias import get_alias
            alias = await get_alias(redis, user_id)
            alias_text = f"\nAlias: <code>[{alias}]</code>"

    # Broadcast state
    src = "🔊 ON" if chat.is_source else "🔇 Paused"
    dst = "🔊 ON" if chat.is_destination else "🔇 Paused"

    # Plan one-liner
    if active_sub:
        remaining = (active_sub.expires_at - datetime.now(timezone.utc)).days
        plan_line = f"⭐ Premium — {active_sub.plan.capitalize()} ({remaining}d left)"
    else:
        trial_left = get_trial_days_remaining(chat.registered_at)
        if trial_left > 0:
            plan_line = f"🆓 Free Trial ({trial_left}d left)"
        else:
            plan_line = "🔒 Trial Expired"

    # Missed messages (only meaningful if trial expired and no sub)
    missed_line = ""
    if not active_sub and get_trial_days_remaining(chat.registered_at) <= 0:
        redis = _get_redis()
        if redis:
            from bot.services.subscription import get_missed_today
            missed = await get_missed_today(redis, chat_id)
            if missed > 0:
                missed_line = f"\n⚠️ Missed today: <b>{missed:,}</b> messages"

    lines = [
        "📈 <b>Your Stats</b>",
        "",
        f"Chat: <b>{name}</b> ({chat.chat_type})",
        f"Registered: {chat.registered_at.strftime('%d %b %Y')} ({days_active}d ago)"
        f"{alias_text}",
        "",
        "<b>Last 48h:</b>",
        f"  Sent: <b>{sent_count:,}</b> messages",
        f"  Received: <b>{recv_count:,}</b> messages",
        "",
        f"Broadcast: Sending {src} · Receiving {dst}",
        f"Plan: {plan_line}{missed_line}",
    ]

    # ── Global stats (admin only) ────────────────────────────────────
    if admin:
        async with async_session() as session:
            chat_repo = ChatRepo(session)
            sub_repo = SubscriptionRepo(session)
            log_repo = SendLogRepo(session)
            res_repo = RestrictionRepo(session)

            total_active = await chat_repo.count_active()
            type_counts = await chat_repo.count_by_type()
            source_count = await chat_repo.count_sources()
            dest_count = await chat_repo.count_destinations()
            premium_count = await sub_repo.count_premium_chats()
            sub_breakdown = await sub_repo.count_subscription_breakdown()
            total_dist = await log_repo.count_total_distributed()
            unique_senders = await log_repo.count_unique_senders()
            restrictions = await res_repo.count_active_restrictions()

        # Trial vs expired: active - premium = non-premium active chats
        # Among those, we check trial status heuristically
        non_premium = total_active - premium_count

        # Type breakdown line
        type_parts = []
        for t in ("private", "group", "supergroup", "channel"):
            c = type_counts.get(t, 0)
            if c > 0:
                type_parts.append(f"{t.capitalize()}: {c}")
        type_line = " | ".join(type_parts) if type_parts else "None"

        # Sub breakdown line
        sub_parts = []
        for p in ("week", "month", "year"):
            c = sub_breakdown.get(p, 0)
            if c > 0:
                sub_parts.append(f"{p.capitalize()}: {c}")
        sub_line = " | ".join(sub_parts) if sub_parts else "None"

        muted = restrictions.get("mute", 0)
        banned = restrictions.get("ban", 0)

        from bot.services.distributor import get_distributor
        queue_size = get_distributor().queue_size

        lines.extend([
            "",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
            "🌐 <b>Global Stats</b>",
            "",
            f"Chats: <b>{total_active}</b> active",
            f"  {type_line}",
            f"  Sources: {source_count} | Destinations: {dest_count}",
            "",
            f"Subscriptions: <b>{premium_count}</b> premium | {non_premium} free/trial",
            f"  ({sub_line})",
            "",
            "<b>Last 48h:</b>",
            f"  Distributed: <b>{total_dist:,}</b> messages",
            f"  Unique senders: <b>{unique_senders}</b>",
            "",
            f"Moderation: 🔇 Muted: {muted} | ⛔ Banned: {banned}",
            f"Queue: {queue_size}",
        ])

    kb = build_stats_actions(admin)
    await message.answer("\n".join(lines), reply_markup=kb)


def _get_redis():
    """Get Redis instance from the running distributor (avoids circular imports)."""
    try:
        from bot.services.distributor import get_distributor
        return get_distributor()._redis
    except RuntimeError:
        return None
