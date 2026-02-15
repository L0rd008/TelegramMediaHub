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
    build_help_menu,
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

    # Resolve the user's alias for the welcome message
    alias_line = ""
    if message.from_user:
        redis = _get_redis()
        if redis:
            from bot.services.alias import get_alias
            alias = await get_alias(redis, message.from_user.id)
            alias_line = (
                f"\n\nYour tag is <b>{alias}</b> — it appears on your "
                "messages across the network."
            )

    await message.answer(
        "Hey! 👋 <b>This chat is now connected.</b>\n\n"
        "Anything you send here will show up in your other connected "
        "chats — and their messages will appear here. Everything looks "
        "like an original message, never a forward.\n\n"
        f"You have full access to everything. Explore the options below.{alias_line}",
        reply_markup=build_main_menu(),
    )


@start_router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    """Show role-aware help with drill-down buttons."""
    user_id = message.from_user.id if message.from_user else None
    admin = _is_admin(user_id)

    lines = [
        "📖 <b>Help</b>",
        "",
        "I sync your messages across all your connected Telegram chats "
        "— they arrive as originals, never as forwards.",
        "",
        "<b>Commands</b>",
        "/start — Connect this chat",
        "/stop — Disconnect this chat",
        "/selfsend — Echo your own messages back",
        "/broadcast — Control what you send and receive",
        "/subscribe — Go Premium",
        "/plan — Check your current plan",
        "/stats — Your activity and stats",
        "/help — This guide",
    ]

    kb = build_help_menu(admin)
    await message.answer("\n".join(lines), reply_markup=kb)


@start_router.message(Command("stop"))
async def cmd_stop(message: Message) -> None:
    """Show confirmation before unregistering this chat."""
    await message.answer(
        "You're about to <b>disconnect</b> this chat.\n\n"
        "It will stop sending and receiving synced messages. "
        "You can always reconnect with /start.",
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
        status = "ON ✅" if chat.allow_self_send else "OFF"
        kb = build_selfsend_result(chat.allow_self_send)
        await message.answer(
            f"🔄 <b>Echo is currently {status}</b>\n\n"
            "When echo is on, messages you send here also come back "
            "to this chat from your other connected chats.",
            reply_markup=kb,
        )
        return

    enabled = args == "on"

    async with async_session() as session:
        repo = ChatRepo(session)
        await repo.toggle_self_send(message.chat.id, enabled)

    status = "ON ✅" if enabled else "OFF"
    kb = build_selfsend_result(enabled)
    await message.answer(f"🔄 Echo is now <b>{status}</b>", reply_markup=kb)


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
                "<b>Sync Control</b> is a Premium feature.\n\n"
                "Choose exactly what this chat sends and receives. "
                "Plans start at about <b>1 star per hour</b>.",
                reply_markup=build_subscribe_button(),
            )
            return

        out_status = "ON" if chat_obj.is_source else "PAUSED"
        in_status = "ON" if chat_obj.is_destination else "PAUSED"
        kb = build_broadcast_panel(chat_obj.is_source, chat_obj.is_destination)
        await message.answer(
            "<b>Sync Control</b>\n\n"
            f"Sending: <b>{out_status}</b> — content from here goes to your other chats\n"
            f"Receiving: <b>{in_status}</b> — content from other chats arrives here",
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
            "<b>Sync Control</b> is a Premium feature.\n\n"
            "Choose exactly what this chat sends and receives. "
            "Plans start at about <b>1 star per hour</b>.",
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

    out_status = "ON" if chat_obj.is_source else "PAUSED"
    in_status = "ON" if chat_obj.is_destination else "PAUSED"
    kb = build_broadcast_panel(chat_obj.is_source, chat_obj.is_destination)
    await message.answer(
        "<b>Sync Control</b>\n\n"
        f"Sending: <b>{out_status}</b> — content from here goes to your other chats\n"
        f"Receiving: <b>{in_status}</b> — content from other chats arrives here",
        reply_markup=kb,
    )


# ── /stats ───────────────────────────────────────────────────────────


def _is_admin(user_id: int | None) -> bool:
    if user_id is None:
        return False
    return user_id in settings.admin_ids


@start_router.message(Command("stats"))
@start_router.channel_post(Command("stats"))
async def cmd_stats(message: Message) -> None:
    """Show per-chat stats for everyone; global stats appended for admins."""
    chat_id = message.chat.id
    user_id = message.from_user.id if message.from_user else None
    admin = _is_admin(user_id)

    try:
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
    except Exception as e:
        logger.exception("Stats error for chat %d: %s", chat_id, e)
        await message.answer("Stats are temporarily unavailable. Please try again in a bit.")
        return

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
            try:
                from bot.services.alias import get_alias
                alias = await get_alias(redis, user_id)
                alias_text = f"\nYour ID tag: <code>[{alias}]</code>"
            except Exception as e:
                logger.debug("Alias lookup failed for %d: %s", user_id, e)

    # Broadcast state
    src = "ON" if chat.is_source else "Paused"
    dst = "ON" if chat.is_destination else "Paused"

    # Plan one-liner
    trial_left = get_trial_days_remaining(chat.registered_at)
    if active_sub:
        expires_at = active_sub.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        remaining = max(0, (expires_at - datetime.now(timezone.utc)).days)
        plan_line = f"Premium — {active_sub.plan.capitalize()} ({remaining}d left)"
    else:
        if trial_left > 0:
            plan_line = f"Full access ({trial_left}d left)"
        else:
            plan_line = "Free access ended"

    # Missed messages (only meaningful if trial expired and no sub)
    missed_line = ""
    if not active_sub and trial_left <= 0:
        redis = _get_redis()
        if redis:
            from bot.services.subscription import get_missed_today
            missed = await get_missed_today(redis, chat_id)
            if missed > 0:
                missed_line = (
                    f"\n\n{missed:,} new message{'s' if missed != 1 else ''} "
                    "waiting in your network."
                )

    lines = [
        "<b>Your Activity</b>",
        "",
        f"Chat: <b>{name}</b> ({chat.chat_type})",
        f"Connected since: {chat.registered_at.strftime('%d %b %Y')} ({days_active}d ago)"
        f"{alias_text}",
        "",
        "<b>Last 48 hours:</b>",
        f"  Sent out: <b>{sent_count:,}</b> messages",
        f"  Received: <b>{recv_count:,}</b> messages",
        "",
        f"Sync: Sending {src} · Receiving {dst}",
        f"Plan: {plan_line}{missed_line}",
    ]

    # —— Global stats (admin only) --------------------------------------------------
    if admin:
        try:
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
            non_premium = max(0, total_active - premium_count)

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

            try:
                from bot.services.distributor import get_distributor
                queue_size = get_distributor().queue_size
            except Exception as e:
                logger.debug("Queue size unavailable: %s", e)
                queue_size = "N/A"

            lines.extend([
                "",
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                "",
                "<b>Network Overview</b>",
                "",
                f"Connected chats: <b>{total_active}</b>",
                f"  {type_line}",
                f"  Sending: {source_count} | Receiving: {dest_count}",
                "",
                f"Premium members: <b>{premium_count}</b> | Free: {non_premium}",
                f"  ({sub_line})",
                "",
                "<b>Last 48 hours:</b>",
                f"  Messages synced: <b>{total_dist:,}</b>",
                f"  Active senders: <b>{unique_senders}</b>",
                "",
                f"Moderation: {muted} silenced · {banned} blocked",
                f"Queue: {queue_size}",
            ])
        except Exception as e:
            logger.exception("Stats error (admin global): %s", e)
            lines.extend([
                "",
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                "",
                "<b>Network Overview</b>",
                "",
                "Stats are temporarily unavailable.",
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
