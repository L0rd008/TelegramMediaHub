"""Unified callback query handler for all non-subscription inline buttons."""

from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.types import CallbackQuery

from bot.config import settings
from bot.db.engine import async_session
from bot.db.repositories.alias_repo import AliasRepo
from bot.db.repositories.chat_repo import ChatRepo
from bot.db.repositories.config_repo import ConfigRepo
from bot.db.repositories.restriction_repo import RestrictionRepo
from bot.db.repositories.send_log_repo import SendLogRepo
from bot.db.repositories.subscription_repo import SubscriptionRepo
from bot.services.keyboards import (
    build_admin_panel,
    build_ban_confirm,
    build_broadcast_panel,
    build_chat_detail,
    build_chat_list_nav,
    build_edits_panel,
    build_grant_plans,
    build_help_back,
    build_help_menu,
    build_moderation_actions,
    build_mute_presets,
    build_pause_feedback,
    build_remove_confirm,
    build_resume_feedback,
    build_revoke_confirm,
    build_selfsend_result,
    build_settings_panel,
    build_status_actions,
    build_stop_confirm,
    build_unban_undo,
    build_unmute_undo,
)
from bot.services.moderation import (
    format_duration,
    invalidate_restriction_cache,
    parse_duration,
)
from bot.services.subscription import PLANS, invalidate_cache, is_premium

logger = logging.getLogger(__name__)

callbacks_router = Router(name="callbacks")

PAGE_SIZE = 20


def _is_admin(user_id: int | None) -> bool:
    if user_id is None:
        return False
    return user_id in settings.admin_ids


def _get_redis():
    try:
        from bot.services.distributor import get_distributor
        return get_distributor()._redis
    except RuntimeError:
        return None


# ── Noop (dismiss) ───────────────────────────────────────────────────


@callbacks_router.callback_query(F.data == "noop")
async def cb_noop(callback: CallbackQuery) -> None:
    """Dismiss / cancel – just acknowledge and remove keyboard."""
    try:
        await callback.message.edit_reply_markup(reply_markup=None)  # type: ignore[union-attr]
    except Exception:
        pass
    await callback.answer("Dismissed.")


# ── Settings panel ───────────────────────────────────────────────────


@callbacks_router.callback_query(F.data == "settings")
async def cb_settings(callback: CallbackQuery) -> None:
    chat_id = callback.message.chat.id if callback.message else 0
    if not chat_id:
        await callback.answer("Error.", show_alert=True)
        return

    async with async_session() as session:
        chat = await ChatRepo(session).get_chat(chat_id)

    if chat is None:
        await callback.answer("Chat not registered. Use /start first.", show_alert=True)
        return

    kb = build_settings_panel(chat.allow_self_send, chat.is_source, chat.is_destination)
    try:
        await callback.message.edit_text("⚙️ <b>Settings</b>", reply_markup=kb)  # type: ignore[union-attr]
    except Exception:
        await callback.message.answer("⚙️ <b>Settings</b>", reply_markup=kb)  # type: ignore[union-attr]
    await callback.answer()


# ── My Plan (inline) ────────────────────────────────────────────────


@callbacks_router.callback_query(F.data == "myplan")
async def cb_myplan(callback: CallbackQuery) -> None:
    """Show plan status inline (same as /plan but via button)."""
    chat_id = callback.message.chat.id if callback.message else 0
    if not chat_id:
        await callback.answer("Error.", show_alert=True)
        return

    async with async_session() as session:
        chat = await ChatRepo(session).get_chat(chat_id)
        sub = await SubscriptionRepo(session).get_active_subscription(chat_id)

    if chat is None:
        await callback.answer("Chat not registered.", show_alert=True)
        return

    from bot.services.keyboards import build_plan_active_actions, build_plan_trial_actions
    from bot.services.subscription import build_subscribe_button, get_trial_days_remaining

    if sub:
        _exp = sub.expires_at
        if _exp.tzinfo is None:
            _exp = _exp.replace(tzinfo=timezone.utc)
        remaining = max(0, (_exp - datetime.now(timezone.utc)).days)
        src = "ON" if chat.is_source else "Paused"
        dst = "ON" if chat.is_destination else "Paused"
        text = (
            "<b>You're a Premium member</b>\n\n"
            f"Plan: <b>{sub.plan.capitalize()}</b>\n"
            f"Active until: <b>{sub.expires_at.strftime('%d %b %Y')}</b> "
            f"({remaining} days)\n\n"
            f"Sync: Sending {src} · Receiving {dst}"
        )
        kb = build_plan_active_actions()
    else:
        trial_left = get_trial_days_remaining(chat.registered_at)
        if trial_left > 0:
            src = "ON" if chat.is_source else "Paused"
            dst = "ON" if chat.is_destination else "Paused"
            text = (
                f"<b>Full access — {trial_left} days left</b>\n\n"
                f"Sync: Sending {src} · Receiving {dst}\n\n"
                "Enjoying it? Plans start at <b>250 stars</b>."
            )
            kb = build_plan_trial_actions()
        else:
            text = (
                "<b>Your free access has ended</b>\n\n"
                "Get messages from your full network again — "
                "about <b>1 star per hour</b>."
            )
            kb = build_subscribe_button()

    try:
        await callback.message.edit_text(text, reply_markup=kb)  # type: ignore[union-attr]
    except Exception:
        await callback.message.answer(text, reply_markup=kb)  # type: ignore[union-attr]
    await callback.answer()


# ── Self-send toggle ─────────────────────────────────────────────────


@callbacks_router.callback_query(F.data.in_({"ss:0", "ss:1"}))
async def cb_selfsend(callback: CallbackQuery) -> None:
    chat_id = callback.message.chat.id if callback.message else 0
    enabled = callback.data == "ss:1"

    async with async_session() as session:
        repo = ChatRepo(session)
        await repo.toggle_self_send(chat_id, enabled)

    status = "ON ✅" if enabled else "OFF"
    kb = build_selfsend_result(enabled)
    try:
        await callback.message.edit_text(  # type: ignore[union-attr]
            f"🔄 Echo is now <b>{status}</b>", reply_markup=kb
        )
    except Exception:
        await callback.message.answer(  # type: ignore[union-attr]
            f"🔄 Echo is now <b>{status}</b>", reply_markup=kb
        )
    await callback.answer(f"Echo {status}")


# ── Broadcast control panel ─────────────────────────────────────────


@callbacks_router.callback_query(F.data == "bc:panel")
async def cb_broadcast_panel(callback: CallbackQuery) -> None:
    chat_id = callback.message.chat.id if callback.message else 0

    redis = _get_redis()
    if redis is None:
        await callback.answer("Service unavailable.", show_alert=True)
        return

    async with async_session() as session:
        chat = await ChatRepo(session).get_chat(chat_id)
    if chat is None:
        await callback.answer("Chat not registered.", show_alert=True)
        return

    if not await is_premium(redis, chat_id, chat.registered_at):
        from bot.services.subscription import build_subscribe_button
        try:
            await callback.message.edit_text(  # type: ignore[union-attr]
                "<b>Sync Control</b> is a Premium feature.\n\n"
                "Choose exactly what this chat sends and receives. "
                "Plans start at about <b>1 star per hour</b>.",
                reply_markup=build_subscribe_button(),
            )
        except Exception:
            pass
        await callback.answer()
        return

    out_status = "ON" if chat.is_source else "PAUSED"
    in_status = "ON" if chat.is_destination else "PAUSED"
    kb = build_broadcast_panel(chat.is_source, chat.is_destination)
    try:
        await callback.message.edit_text(  # type: ignore[union-attr]
            "<b>Sync Control</b>\n\n"
            f"Sending: <b>{out_status}</b> — content from here goes to your other chats\n"
            f"Receiving: <b>{in_status}</b> — content from other chats arrives here",
            reply_markup=kb,
        )
    except Exception:
        pass
    await callback.answer()


@callbacks_router.callback_query(F.data.in_({"bc:0o", "bc:1o", "bc:0i", "bc:1i"}))
async def cb_broadcast_toggle(callback: CallbackQuery) -> None:
    chat_id = callback.message.chat.id if callback.message else 0
    data = callback.data or ""

    redis = _get_redis()
    if redis is None:
        await callback.answer("Service unavailable.", show_alert=True)
        return

    async with async_session() as session:
        chat = await ChatRepo(session).get_chat(chat_id)
    if chat is None:
        await callback.answer("Chat not registered.", show_alert=True)
        return

    if not await is_premium(redis, chat_id, chat.registered_at):
        await callback.answer("Premium required.", show_alert=True)
        return

    enabled = data[3] == "1"
    direction = data[4]  # "o" or "i"

    async with async_session() as session:
        repo = ChatRepo(session)
        if direction == "o":
            await repo.toggle_source(chat_id, enabled)
        else:
            await repo.toggle_destination(chat_id, enabled)

    # Re-fetch to show updated state
    async with async_session() as session:
        chat = await ChatRepo(session).get_chat(chat_id)

    out_status = "ON" if chat.is_source else "PAUSED"
    in_status = "ON" if chat.is_destination else "PAUSED"
    kb = build_broadcast_panel(chat.is_source, chat.is_destination)
    try:
        await callback.message.edit_text(  # type: ignore[union-attr]
            "<b>Sync Control</b>\n\n"
            f"Sending: <b>{out_status}</b> — content from here goes to your other chats\n"
            f"Receiving: <b>{in_status}</b> — content from other chats arrives here",
            reply_markup=kb,
        )
    except Exception:
        pass
    label = "Sending" if direction == "o" else "Receiving"
    state = "resumed" if enabled else "paused"
    await callback.answer(f"{label} {state}.")


# ── Stop confirmation ────────────────────────────────────────────────


@callbacks_router.callback_query(F.data == "stop:y")
async def cb_stop_confirm(callback: CallbackQuery) -> None:
    chat_id = callback.message.chat.id if callback.message else 0

    async with async_session() as session:
        repo = ChatRepo(session)
        await repo.deactivate_chat(chat_id)

    try:
        await callback.message.edit_text(  # type: ignore[union-attr]
            "🛑 <b>Chat unregistered.</b>\n"
            "This chat will no longer send or receive distributed content.\n"
            "Use /start to re-register.",
        )
    except Exception:
        pass
    await callback.answer("Chat unregistered.")


# ══════════════════════════════════════════════════════════════════════
# ADMIN CALLBACKS
# ══════════════════════════════════════════════════════════════════════


# ── Admin: Status refresh ────────────────────────────────────────────


@callbacks_router.callback_query(F.data == "ap:status")
async def cb_admin_status(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("Admin only.", show_alert=True)
        return

    from bot.services.distributor import get_distributor
    distributor = get_distributor()

    async with async_session() as session:
        chat_repo = ChatRepo(session)
        config_repo = ConfigRepo(session)
        sub_repo = SubscriptionRepo(session)
        active_count = await chat_repo.count_active()
        premium_count = await sub_repo.count_premium_chats()
        config = await config_repo.get_all()

    paused = config.get("paused", "false") == "true"
    sig_enabled = config.get("signature_enabled", "true") == "true"
    edit_mode = config.get("edit_redistribution", "off")

    lines = [
        "📊 <b>TelegramMediaHub Status</b>",
        "",
        f"Active chats: <b>{active_count}</b>",
        f"Premium chats: <b>{premium_count}</b>",
        f"Queue size: <b>{distributor.queue_size}</b>",
        f"Paused: <b>{'Yes ⏸️' if paused else 'No ▶️'}</b>",
        f"Edit mode: <b>{edit_mode}</b>",
        f"Signature: <b>{'ON' if sig_enabled else 'OFF'}</b>",
    ]

    kb = build_status_actions(paused, edit_mode, sig_enabled)
    try:
        await callback.message.edit_text("\n".join(lines), reply_markup=kb)  # type: ignore[union-attr]
    except Exception:
        await callback.message.answer("\n".join(lines), reply_markup=kb)  # type: ignore[union-attr]
    await callback.answer()


# ── Admin: Pause / Resume ───────────────────────────────────────────


@callbacks_router.callback_query(F.data == "ap:pause")
async def cb_admin_pause(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("Admin only.", show_alert=True)
        return

    async with async_session() as session:
        await ConfigRepo(session).set_value("paused", "true")

    kb = build_pause_feedback()
    try:
        await callback.message.edit_text("⏸️ <b>Distribution paused.</b>", reply_markup=kb)  # type: ignore[union-attr]
    except Exception:
        pass
    await callback.answer("Paused.")


@callbacks_router.callback_query(F.data == "ap:resume")
async def cb_admin_resume(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("Admin only.", show_alert=True)
        return

    async with async_session() as session:
        await ConfigRepo(session).set_value("paused", "false")

    kb = build_resume_feedback()
    try:
        await callback.message.edit_text("▶️ <b>Distribution resumed.</b>", reply_markup=kb)  # type: ignore[union-attr]
    except Exception:
        pass
    await callback.answer("Resumed.")


# ── Admin: Edits toggle ─────────────────────────────────────────────


@callbacks_router.callback_query(F.data.in_({"ap:e:off", "ap:e:res"}))
async def cb_admin_edits(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("Admin only.", show_alert=True)
        return

    mode = "off" if callback.data == "ap:e:off" else "resend"
    async with async_session() as session:
        await ConfigRepo(session).set_value("edit_redistribution", mode)

    kb = build_edits_panel(mode)
    try:
        await callback.message.edit_text(  # type: ignore[union-attr]
            f"📝 <b>Edit redistribution: {mode.upper()}</b>", reply_markup=kb
        )
    except Exception:
        pass
    await callback.answer(f"Edit mode: {mode}")


# ── Admin: Signature off ─────────────────────────────────────────────


@callbacks_router.callback_query(F.data == "ap:soff")
async def cb_admin_sigoff(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("Admin only.", show_alert=True)
        return

    async with async_session() as session:
        await ConfigRepo(session).set_value("signature_enabled", "false")

    await callback.answer("Signature disabled.")
    # Refresh status view
    await cb_admin_status(callback)


# ── Admin: Chat list ─────────────────────────────────────────────────


@callbacks_router.callback_query(F.data.startswith("ls:"))
async def cb_chat_list(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("Admin only.", show_alert=True)
        return

    try:
        page = int((callback.data or "ls:1").split(":")[1])
    except (ValueError, IndexError):
        page = 1

    async with async_session() as session:
        repo = ChatRepo(session)
        total = await repo.count_active()
        chats = await repo.list_all_active(offset=(page - 1) * PAGE_SIZE, limit=PAGE_SIZE)

    if not chats:
        await callback.answer("No active chats.", show_alert=True)
        return

    total_pages = max(1, math.ceil(total / PAGE_SIZE))

    lines = [f"📋 <b>Active Chats</b> (page {page}/{total_pages}, {total} total)\n"]
    for c in chats:
        name = c.title or c.username or str(c.chat_id)
        flags = []
        if c.is_source:
            flags.append("📤")
        if c.is_destination:
            flags.append("📥")
        if c.allow_self_send:
            flags.append("🔄")
        lines.append(f"• <code>{c.chat_id}</code> {name} {''.join(flags)}")

    lines.append("\nTap a chat ID above, then use /remove, /grant, or /revoke.")
    lines.append("Or tap a button below to manage a chat by ID.")

    kb = build_chat_list_nav(page, total_pages)
    try:
        await callback.message.edit_text("\n".join(lines), reply_markup=kb)  # type: ignore[union-attr]
    except Exception:
        await callback.message.answer("\n".join(lines), reply_markup=kb)  # type: ignore[union-attr]
    await callback.answer()


# ── Admin: Chat detail ───────────────────────────────────────────────


@callbacks_router.callback_query(F.data.startswith("ch:"))
async def cb_chat_detail(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("Admin only.", show_alert=True)
        return

    try:
        chat_id = int((callback.data or "").split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("Invalid chat.", show_alert=True)
        return

    async with async_session() as session:
        chat = await ChatRepo(session).get_chat(chat_id)

    if chat is None:
        await callback.answer("Chat not found.", show_alert=True)
        return

    name = chat.title or chat.username or str(chat.chat_id)
    flags = []
    if chat.is_source:
        flags.append("Source")
    if chat.is_destination:
        flags.append("Destination")
    if chat.allow_self_send:
        flags.append("Self-send")

    text = (
        f"📝 <b>Chat Detail</b>\n\n"
        f"ID: <code>{chat.chat_id}</code>\n"
        f"Name: {name}\n"
        f"Type: {chat.chat_type}\n"
        f"Flags: {', '.join(flags) or 'none'}"
    )
    kb = build_chat_detail(chat.chat_id)
    try:
        await callback.message.edit_text(text, reply_markup=kb)  # type: ignore[union-attr]
    except Exception:
        await callback.message.answer(text, reply_markup=kb)  # type: ignore[union-attr]
    await callback.answer()


# ── Admin: Remove chat ───────────────────────────────────────────────


@callbacks_router.callback_query(F.data.regexp(r"^rm:\-?\d+$"))
async def cb_remove_prompt(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("Admin only.", show_alert=True)
        return

    chat_id = int((callback.data or "").split(":")[1])
    kb = build_remove_confirm(chat_id)
    try:
        await callback.message.edit_text(  # type: ignore[union-attr]
            f"Remove chat <code>{chat_id}</code>?\n"
            "This will deactivate it from all distribution.",
            reply_markup=kb,
        )
    except Exception:
        pass
    await callback.answer()


@callbacks_router.callback_query(F.data.regexp(r"^rmy:\-?\d+$"))
async def cb_remove_confirm(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("Admin only.", show_alert=True)
        return

    chat_id = int((callback.data or "").split(":")[1])
    async with async_session() as session:
        await ChatRepo(session).deactivate_chat(chat_id)

    try:
        await callback.message.edit_text(  # type: ignore[union-attr]
            f"✅ Chat <code>{chat_id}</code> removed.",
        )
    except Exception:
        pass
    await callback.answer("Removed.")


# ── Admin: Grant plan ────────────────────────────────────────────────


@callbacks_router.callback_query(F.data.regexp(r"^gr:\-?\d+$"))
async def cb_grant_menu(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("Admin only.", show_alert=True)
        return

    chat_id = int((callback.data or "").split(":")[1])
    kb = build_grant_plans(chat_id)
    try:
        await callback.message.edit_text(  # type: ignore[union-attr]
            f"🎁 Grant subscription to <code>{chat_id}</code>:",
            reply_markup=kb,
        )
    except Exception:
        pass
    await callback.answer()


@callbacks_router.callback_query(F.data.regexp(r"^gp:\w+:\-?\d+$"))
async def cb_grant_exec(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("Admin only.", show_alert=True)
        return

    parts = (callback.data or "").split(":")
    plan_key = parts[1]
    chat_id = int(parts[2])
    plan = PLANS.get(plan_key)
    if plan is None:
        await callback.answer("Unknown plan.", show_alert=True)
        return

    admin_id = callback.from_user.id

    async with async_session() as session:
        repo = SubscriptionRepo(session)
        sub = await repo.create_subscription(
            chat_id=chat_id,
            user_id=admin_id,
            plan=plan.key,
            stars_amount=0,
            days=plan.days,
            charge_id=f"admin_grant_{admin_id}",
        )

    redis = _get_redis()
    if redis:
        await invalidate_cache(redis, chat_id)

    expires_str = sub.expires_at.strftime("%d %b %Y")
    try:
        await callback.message.edit_text(  # type: ignore[union-attr]
            f"✅ Granted <b>{plan.label}</b> to chat <code>{chat_id}</code>.\n"
            f"Expires: <b>{expires_str}</b>",
        )
    except Exception:
        pass
    await callback.answer("Granted.")


# ── Admin: Revoke ────────────────────────────────────────────────────


@callbacks_router.callback_query(F.data.regexp(r"^rv:\-?\d+$"))
async def cb_revoke_prompt(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("Admin only.", show_alert=True)
        return

    chat_id = int((callback.data or "").split(":")[1])
    kb = build_revoke_confirm(chat_id)
    try:
        await callback.message.edit_text(  # type: ignore[union-attr]
            f"Revoke subscriptions for <code>{chat_id}</code>?",
            reply_markup=kb,
        )
    except Exception:
        pass
    await callback.answer()


@callbacks_router.callback_query(F.data.regexp(r"^rvy:\-?\d+$"))
async def cb_revoke_exec(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("Admin only.", show_alert=True)
        return

    chat_id = int((callback.data or "").split(":")[1])
    async with async_session() as session:
        revoked = await SubscriptionRepo(session).revoke_subscription(chat_id)

    if revoked:
        redis = _get_redis()
        if redis:
            await invalidate_cache(redis, chat_id)
        text = f"✅ Subscriptions revoked for <code>{chat_id}</code>."
    else:
        text = f"No active subscriptions for <code>{chat_id}</code>."

    try:
        await callback.message.edit_text(text)  # type: ignore[union-attr]
    except Exception:
        pass
    await callback.answer("Done." if revoked else "None found.")


# ── Admin: Mute presets ──────────────────────────────────────────────


@callbacks_router.callback_query(F.data.regexp(r"^md:\d+$"))
async def cb_mute_menu(callback: CallbackQuery) -> None:
    """Show mute duration presets for a user."""
    if not _is_admin(callback.from_user.id):
        await callback.answer("Admin only.", show_alert=True)
        return

    user_id = int((callback.data or "").split(":")[1])
    kb = build_mute_presets(user_id)
    try:
        await callback.message.edit_text(  # type: ignore[union-attr]
            f"🔇 Mute user <code>{user_id}</code>?\nSelect duration:",
            reply_markup=kb,
        )
    except Exception:
        pass
    await callback.answer()


@callbacks_router.callback_query(F.data.regexp(r"^mu:\d+:\w+$"))
async def cb_mute_exec(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("Admin only.", show_alert=True)
        return

    parts = (callback.data or "").split(":")
    user_id = int(parts[1])
    duration_str = parts[2]

    td = parse_duration(duration_str)
    if td is None:
        await callback.answer("Invalid duration.", show_alert=True)
        return

    expires = datetime.now(timezone.utc) + td
    admin_id = callback.from_user.id

    async with async_session() as session:
        await RestrictionRepo(session).create_restriction(
            user_id=user_id,
            restriction_type="mute",
            restricted_by=admin_id,
            expires_at=expires,
        )

    redis = _get_redis()
    if redis:
        await invalidate_restriction_cache(redis, user_id)

    try:
        await callback.message.edit_text(  # type: ignore[union-attr]
            f"🔇 User <code>{user_id}</code> muted for <b>{format_duration(td)}</b>.\n"
            f"Expires: <b>{expires.strftime('%d %b %Y %H:%M')} UTC</b>",
        )
    except Exception:
        pass
    await callback.answer("Muted.")


# ── Admin: Unmute ────────────────────────────────────────────────────


@callbacks_router.callback_query(F.data.regexp(r"^um:\d+$"))
async def cb_unmute(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("Admin only.", show_alert=True)
        return

    user_id = int((callback.data or "").split(":")[1])

    async with async_session() as session:
        removed = await RestrictionRepo(session).remove_restriction(user_id, "mute")

    if removed:
        redis = _get_redis()
        if redis:
            await invalidate_restriction_cache(redis, user_id)
        kb = build_unmute_undo(user_id)
        text = f"🔊 User <code>{user_id}</code> unmuted."
    else:
        kb = None
        text = f"User <code>{user_id}</code> is not muted."

    try:
        await callback.message.edit_text(text, reply_markup=kb)  # type: ignore[union-attr]
    except Exception:
        pass
    await callback.answer()


# ── Admin: Ban confirm ───────────────────────────────────────────────


@callbacks_router.callback_query(F.data.regexp(r"^bn:\d+$"))
async def cb_ban_prompt(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("Admin only.", show_alert=True)
        return

    user_id = int((callback.data or "").split(":")[1])
    kb = build_ban_confirm(user_id)
    try:
        await callback.message.edit_text(  # type: ignore[union-attr]
            f"⛔ Permanently ban user <code>{user_id}</code>?\n\n"
            "Choose whether to also delete their past messages:",
            reply_markup=kb,
        )
    except Exception:
        pass
    await callback.answer()


@callbacks_router.callback_query(F.data.regexp(r"^byd:\d+$"))
async def cb_ban_delete(callback: CallbackQuery) -> None:
    """Ban a user AND delete all their redistributed messages."""
    if not _is_admin(callback.from_user.id):
        await callback.answer("Admin only.", show_alert=True)
        return

    user_id = int((callback.data or "").split(":")[1])
    admin_id = callback.from_user.id

    async with async_session() as session:
        await RestrictionRepo(session).create_restriction(
            user_id=user_id,
            restriction_type="ban",
            restricted_by=admin_id,
            expires_at=None,
        )

    redis = _get_redis()
    if redis:
        await invalidate_restriction_cache(redis, user_id)

    # Fire ban cleanup (message deletion)
    from bot.services.distributor import get_distributor
    distributor = get_distributor()
    asyncio.create_task(_ban_cleanup_bg(distributor._bot, user_id))

    try:
        await callback.message.edit_text(  # type: ignore[union-attr]
            f"⛔ User <code>{user_id}</code> permanently banned.\n"
            "Their redistributed messages are being deleted.",
        )
    except Exception:
        pass
    await callback.answer("Banned.")


@callbacks_router.callback_query(F.data.regexp(r"^byn:\d+$"))
async def cb_ban_only(callback: CallbackQuery) -> None:
    """Ban a user without deleting their past messages."""
    if not _is_admin(callback.from_user.id):
        await callback.answer("Admin only.", show_alert=True)
        return

    user_id = int((callback.data or "").split(":")[1])
    admin_id = callback.from_user.id

    async with async_session() as session:
        await RestrictionRepo(session).create_restriction(
            user_id=user_id,
            restriction_type="ban",
            restricted_by=admin_id,
            expires_at=None,
        )

    redis = _get_redis()
    if redis:
        await invalidate_restriction_cache(redis, user_id)

    try:
        await callback.message.edit_text(  # type: ignore[union-attr]
            f"⛔ User <code>{user_id}</code> permanently banned.\n"
            "Their past messages were kept.",
        )
    except Exception:
        pass
    await callback.answer("Banned.")


async def _ban_cleanup_bg(bot, user_id: int) -> None:
    """Background task: delete redistributed messages from a banned user."""
    try:
        async with async_session() as session:
            messages = await SendLogRepo(session).get_dest_messages_by_user(user_id)
        deleted = 0
        for cid, mid in messages:
            try:
                await bot.delete_message(cid, mid)
                deleted += 1
            except Exception:
                pass
            await asyncio.sleep(0.05)
        logger.info("Ban cleanup (button): user %d, deleted %d/%d", user_id, deleted, len(messages))
    except Exception as e:
        logger.error("Ban cleanup error for user %d: %s", user_id, e)


# ── Admin: Unban ─────────────────────────────────────────────────────


@callbacks_router.callback_query(F.data.regexp(r"^ub:\d+$"))
async def cb_unban(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("Admin only.", show_alert=True)
        return

    user_id = int((callback.data or "").split(":")[1])

    async with async_session() as session:
        removed = await RestrictionRepo(session).remove_restriction(user_id, "ban")

    if removed:
        redis = _get_redis()
        if redis:
            await invalidate_restriction_cache(redis, user_id)
        kb = build_unban_undo(user_id)
        text = f"✅ User <code>{user_id}</code> unbanned."
    else:
        kb = None
        text = f"User <code>{user_id}</code> is not banned."

    try:
        await callback.message.edit_text(text, reply_markup=kb)  # type: ignore[union-attr]
    except Exception:
        pass
    await callback.answer()


# ══════════════════════════════════════════════════════════════════════
# HELP CALLBACKS
# ══════════════════════════════════════════════════════════════════════


_HELP_MAIN_TEXT = (
    "📖 <b>Help</b>\n\n"
    "I sync your messages across all your connected Telegram chats "
    "— they arrive as originals, never as forwards.\n\n"
    "<b>Commands</b>\n"
    "/start — Connect this chat\n"
    "/stop — Disconnect this chat\n"
    "/selfsend — Echo your own messages back\n"
    "/broadcast — Control what you send and receive\n"
    "/subscribe — Go Premium\n"
    "/plan — Check your current plan\n"
    "/stats — Your activity and stats\n"
    "/help — This guide"
)

_HELP_HOW_TEXT = (
    "💡 <b>How it works</b>\n\n"
    "Send any message here — text, photo, video, sticker, voice, "
    "document — and it appears in all your other connected chats "
    "as an original message, never a forward.\n\n"
    "• <b>Replies stay threaded</b> — reply to a synced message "
    "and the reply shows up in every chat as a proper Telegram reply\n"
    "• <b>Your tag appears on messages</b> — a readable name like "
    "<b>golden_arrow</b> links back to the bot on every message you send\n"
    "• <b>Works everywhere</b> — private chats, groups, supergroups, "
    "and channels\n"
    "• <b>Privacy first</b> — no forwarding tags, no metadata leaks"
)

_HELP_PREM_TEXT = (
    "⭐ <b>About Premium</b>\n\n"
    "You get <b>30 days of full access</b> from the moment you connect. "
    "After that, messages from other users to your chats require a "
    "Premium plan.\n\n"
    "• <b>Sync Control</b> — choose exactly what this chat sends "
    "and receives\n"
    "• <b>Full network access</b> — keep getting messages from everyone\n"
    "• <b>Plans start at ~1 star per hour</b>\n\n"
    "Tap /subscribe to see pricing, or /plan to check your status."
)

_HELP_ADMIN_TEXT = (
    "🛠 <b>Admin Guide</b>\n\n"
    "<b>Configuration</b>\n"
    "/status — Live dashboard with action buttons\n"
    "/pause · /resume — Control all distribution\n"
    "/edits — Toggle edit redistribution (off / resend)\n"
    "/signature · /signatureurl · /signatureoff — Message signature\n\n"
    "<b>Chat management</b>\n"
    "/list — Browse connected chats with pagination\n"
    "/remove — Disconnect a chat (by ID or reply)\n"
    "/grant — Give someone Premium (by ID or reply)\n"
    "/revoke — Remove someone's Premium (by ID or reply)\n\n"
    "<b>Moderation</b>\n"
    "/mute — Temporarily silence a user (preset buttons or custom duration)\n"
    "/unmute — Lift a mute\n"
    "/ban — Permanently block (with or without deleting their messages)\n"
    "/unban — Lift a ban\n"
    "/whois — Look up user by their alias name\n\n"
    "<i>Tip: all moderation commands work by replying to a message "
    "or passing a user ID.</i>"
)


@callbacks_router.callback_query(F.data == "help:how")
async def cb_help_how(callback: CallbackQuery) -> None:
    admin = _is_admin(callback.from_user.id)
    kb = build_help_back(admin)
    try:
        await callback.message.edit_text(_HELP_HOW_TEXT, reply_markup=kb)  # type: ignore[union-attr]
    except Exception:
        await callback.message.answer(_HELP_HOW_TEXT, reply_markup=kb)  # type: ignore[union-attr]
    await callback.answer()


@callbacks_router.callback_query(F.data == "help:prem")
async def cb_help_prem(callback: CallbackQuery) -> None:
    admin = _is_admin(callback.from_user.id)
    kb = build_help_back(admin)
    try:
        await callback.message.edit_text(_HELP_PREM_TEXT, reply_markup=kb)  # type: ignore[union-attr]
    except Exception:
        await callback.message.answer(_HELP_PREM_TEXT, reply_markup=kb)  # type: ignore[union-attr]
    await callback.answer()


@callbacks_router.callback_query(F.data == "help:admin")
async def cb_help_admin(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("Admin only.", show_alert=True)
        return

    kb = build_help_back(is_admin=True)
    try:
        await callback.message.edit_text(_HELP_ADMIN_TEXT, reply_markup=kb)  # type: ignore[union-attr]
    except Exception:
        await callback.message.answer(_HELP_ADMIN_TEXT, reply_markup=kb)  # type: ignore[union-attr]
    await callback.answer()


@callbacks_router.callback_query(F.data == "help:back")
async def cb_help_back(callback: CallbackQuery) -> None:
    admin = _is_admin(callback.from_user.id)
    kb = build_help_menu(admin)
    try:
        await callback.message.edit_text(_HELP_MAIN_TEXT, reply_markup=kb)  # type: ignore[union-attr]
    except Exception:
        await callback.message.answer(_HELP_MAIN_TEXT, reply_markup=kb)  # type: ignore[union-attr]
    await callback.answer()
