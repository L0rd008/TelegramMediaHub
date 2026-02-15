"""Admin command handler – restricted to ADMIN_USER_IDS."""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from bot.config import settings
from bot.db.engine import async_session
from bot.db.repositories.alias_repo import AliasRepo
from bot.db.repositories.chat_repo import ChatRepo
from bot.db.repositories.config_repo import ConfigRepo
from bot.db.repositories.restriction_repo import RestrictionRepo
from bot.db.repositories.send_log_repo import SendLogRepo
from bot.db.repositories.subscription_repo import SubscriptionRepo
from bot.services.distributor import get_distributor
from bot.services.keyboards import (
    build_ban_confirm,
    build_chat_list_nav,
    build_edits_panel,
    build_moderation_actions,
    build_mute_presets,
    build_pause_feedback,
    build_resume_feedback,
    build_status_actions,
    build_unban_undo,
    build_unmute_undo,
)
from bot.services.moderation import (
    format_duration,
    invalidate_restriction_cache,
    parse_duration,
)
from bot.services.subscription import PLANS, invalidate_cache

logger = logging.getLogger(__name__)

admin_router = Router(name="admin")


def _is_admin(user_id: int | None) -> bool:
    """Check if user_id is in the admin list."""
    if user_id is None:
        return False
    return user_id in settings.admin_ids


async def _resolve_target_user(
    message: Message, args: str | None, bot_id: int
) -> int | None:
    """Resolve a user ID from either a reply or command arguments.

    Priority:
    1. If reply to non-bot message → reply.from_user.id
    2. If reply to bot message → reverse lookup send_log for source_user_id
    3. If args provided → parse first token as int
    """
    reply = message.reply_to_message
    if reply:
        if reply.from_user and reply.from_user.id != bot_id:
            return reply.from_user.id
        if reply.from_user and reply.from_user.id == bot_id:
            async with async_session() as session:
                repo = SendLogRepo(session)
                user_id = await repo.get_source_user_id(
                    message.chat.id, reply.message_id
                )
                return user_id

    if args:
        first_token = args.strip().split()[0]
        try:
            return int(first_token)
        except ValueError:
            return None

    return None


# ── /status ───────────────────────────────────────────────────────────


@admin_router.message(Command("status"))
async def cmd_status(message: Message) -> None:
    """Show bot status and statistics."""
    if not _is_admin(message.from_user and message.from_user.id):
        return

    async with async_session() as session:
        chat_repo = ChatRepo(session)
        config_repo = ConfigRepo(session)
        sub_repo = SubscriptionRepo(session)

        active_count = await chat_repo.count_active()
        premium_count = await sub_repo.count_premium_chats()
        config = await config_repo.get_all()

    paused = config.get("paused", "false") == "true"
    sig_enabled = config.get("signature_enabled", "true") == "true"
    sig_text = config.get("signature_text", "")
    sig_url = config.get("signature_url", "")
    edit_mode = config.get("edit_redistribution", "off")

    distributor = get_distributor()

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
    await message.answer("\n".join(lines), reply_markup=kb)


# ── /list ─────────────────────────────────────────────────────────────


@admin_router.message(Command("list"))
async def cmd_list(message: Message, command: CommandObject) -> None:
    """List all active chats with pagination."""
    if not _is_admin(message.from_user and message.from_user.id):
        return

    page = 0
    if command.args:
        try:
            page = max(0, int(command.args.strip()) - 1)
        except ValueError:
            pass

    async with async_session() as session:
        repo = ChatRepo(session)
        chats = await repo.list_all_active(offset=page * 20, limit=20)
        total = await repo.count_active()

    if not chats:
        await message.answer("No active chats.")
        return

    total_pages = max(1, math.ceil(total / 20))
    display_page = page + 1

    lines = [f"📋 <b>Active Chats</b> (page {display_page}/{total_pages}, {total} total)\n"]
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

    kb = build_chat_list_nav(display_page, total_pages)
    await message.answer("\n".join(lines), reply_markup=kb)


# ── /signature ────────────────────────────────────────────────────────


@admin_router.message(Command("signature"))
async def cmd_signature(message: Message, command: CommandObject) -> None:
    """Set the signature text. Usage: /signature Your text here"""
    if not _is_admin(message.from_user and message.from_user.id):
        return

    text = (command.args or "").strip()
    if not text:
        await message.answer("Usage: /signature <text>\nExample: /signature — via @MyChannel")
        return

    async with async_session() as session:
        repo = ConfigRepo(session)
        await repo.set_value("signature_text", text)
        await repo.set_value("signature_url", "")
        await repo.set_value("signature_enabled", "true")

    await message.answer(f"✅ Signature set: <code>{text}</code>")


# ── /signatureurl ────────────────────────────────────────────────────


@admin_router.message(Command("signatureurl"))
async def cmd_signatureurl(message: Message, command: CommandObject) -> None:
    """Set the signature URL. Usage: /signatureurl https://example.com"""
    if not _is_admin(message.from_user and message.from_user.id):
        return

    url = (command.args or "").strip()
    if not url:
        await message.answer("Usage: /signatureurl <url>")
        return

    async with async_session() as session:
        repo = ConfigRepo(session)
        await repo.set_value("signature_url", url)
        await repo.set_value("signature_text", "")
        await repo.set_value("signature_enabled", "true")

    await message.answer(f"✅ Signature URL set: <code>{url}</code>")


# ── /signatureoff ────────────────────────────────────────────────────


@admin_router.message(Command("signatureoff"))
async def cmd_signatureoff(message: Message) -> None:
    """Disable the signature."""
    if not _is_admin(message.from_user and message.from_user.id):
        return

    async with async_session() as session:
        repo = ConfigRepo(session)
        await repo.set_value("signature_enabled", "false")

    await message.answer("✅ Signature disabled.")


# ── /pause ────────────────────────────────────────────────────────────


@admin_router.message(Command("pause"))
async def cmd_pause(message: Message) -> None:
    """Pause all content distribution."""
    if not _is_admin(message.from_user and message.from_user.id):
        return

    async with async_session() as session:
        repo = ConfigRepo(session)
        await repo.set_value("paused", "true")

    await message.answer(
        "⏸️ <b>Distribution paused.</b>",
        reply_markup=build_pause_feedback(),
    )


# ── /resume ───────────────────────────────────────────────────────────


@admin_router.message(Command("resume"))
async def cmd_resume(message: Message) -> None:
    """Resume content distribution."""
    if not _is_admin(message.from_user and message.from_user.id):
        return

    async with async_session() as session:
        repo = ConfigRepo(session)
        await repo.set_value("paused", "false")

    await message.answer(
        "▶️ <b>Distribution resumed.</b>",
        reply_markup=build_resume_feedback(),
    )


# ── /edits ────────────────────────────────────────────────────────────


@admin_router.message(Command("edits"))
async def cmd_edits(message: Message, command: CommandObject) -> None:
    """Set edit redistribution mode. Usage: /edits off|resend, or no args for panel."""
    if not _is_admin(message.from_user and message.from_user.id):
        return

    mode = (command.args or "").strip().lower()

    # No args → show toggle panel
    if mode not in ("off", "resend"):
        async with async_session() as session:
            current = await ConfigRepo(session).get_value("edit_redistribution") or "off"
        kb = build_edits_panel(current)
        await message.answer(
            f"📝 <b>Edit redistribution: {current.upper()}</b>",
            reply_markup=kb,
        )
        return

    async with async_session() as session:
        repo = ConfigRepo(session)
        await repo.set_value("edit_redistribution", mode)

    kb = build_edits_panel(mode)
    await message.answer(f"✅ Edit redistribution: <b>{mode}</b>", reply_markup=kb)


# ── /remove ───────────────────────────────────────────────────────────


@admin_router.message(Command("remove"))
async def cmd_remove(message: Message, command: CommandObject) -> None:
    """Remove a chat by ID or reply. Usage: /remove <chat_id> or reply to a message."""
    if not _is_admin(message.from_user and message.from_user.id):
        return

    bot_info = await message.bot.get_me()
    target = await _resolve_target_user(message, command.args, bot_info.id)

    if target is None:
        await message.answer("Usage: /remove &lt;chat_id&gt; or reply to a user's message.")
        return

    async with async_session() as session:
        repo = ChatRepo(session)
        await repo.deactivate_chat(target)

    await message.answer(f"✅ Chat <code>{target}</code> removed.")


# ── /grant ───────────────────────────────────────────────────────────


@admin_router.message(Command("grant"))
async def cmd_grant(message: Message, command: CommandObject) -> None:
    """Grant a subscription. Usage: /grant <chat_id> <plan>, /grant <plan> (reply), or reply + /grant <plan>."""
    if not _is_admin(message.from_user and message.from_user.id):
        return

    args_raw = (command.args or "").strip().split()
    bot_info = await message.bot.get_me()

    # Determine chat_id and plan_key based on reply or args
    chat_id: int | None = None
    plan_key: str | None = None

    if message.reply_to_message:
        # Reply mode: /grant <plan>
        target = await _resolve_target_user(message, None, bot_info.id)
        chat_id = target
        if args_raw:
            plan_key = args_raw[0].lower()
    elif len(args_raw) == 2:
        # Standard mode: /grant <chat_id> <plan>
        try:
            chat_id = int(args_raw[0])
        except ValueError:
            await message.answer("Invalid chat ID. Must be a number.")
            return
        plan_key = args_raw[1].lower()

    if chat_id is None or plan_key is None:
        plans_list = ", ".join(PLANS.keys())
        await message.answer(
            f"Usage: /grant &lt;chat_id&gt; &lt;plan&gt; or reply + /grant &lt;plan&gt;\n"
            f"Plans: {plans_list}"
        )
        return

    plan = PLANS.get(plan_key)
    if plan is None:
        await message.answer(
            f"Unknown plan '<code>{plan_key}</code>'. "
            f"Available: {', '.join(PLANS.keys())}"
        )
        return

    admin_id = message.from_user.id if message.from_user else 0

    async with async_session() as session:
        repo = SubscriptionRepo(session)
        sub = await repo.create_subscription(
            chat_id=chat_id,
            user_id=admin_id,
            plan=plan.key,
            stars_amount=0,  # Granted free by admin
            days=plan.days,
            charge_id=f"admin_grant_{admin_id}",
        )

    # Invalidate cache
    distributor = get_distributor()
    await invalidate_cache(distributor._redis, chat_id)

    expires_str = sub.expires_at.strftime("%d %b %Y")
    await message.answer(
        f"✅ Granted <b>{plan.label}</b> to chat <code>{chat_id}</code>.\n"
        f"Expires: <b>{expires_str}</b>"
    )


# ── /revoke ──────────────────────────────────────────────────────────


@admin_router.message(Command("revoke"))
async def cmd_revoke(message: Message, command: CommandObject) -> None:
    """Revoke active subscriptions. Usage: /revoke <chat_id> or reply to a message."""
    if not _is_admin(message.from_user and message.from_user.id):
        return

    bot_info = await message.bot.get_me()
    target = await _resolve_target_user(message, command.args, bot_info.id)

    if target is None:
        await message.answer("Usage: /revoke &lt;chat_id&gt; or reply to a user's message.")
        return

    async with async_session() as session:
        repo = SubscriptionRepo(session)
        revoked = await repo.revoke_subscription(target)

    if revoked:
        distributor = get_distributor()
        await invalidate_cache(distributor._redis, target)
        await message.answer(
            f"✅ Subscriptions revoked for chat <code>{target}</code>."
        )
    else:
        await message.answer(
            f"No active subscriptions found for chat <code>{target}</code>."
        )


# ── /mute (admin moderation) ────────────────────────────────────────


@admin_router.message(Command("mute"))
async def cmd_mute(message: Message, command: CommandObject) -> None:
    """Mute a user temporarily. Usage: /mute <user_id> <duration> or reply + /mute <duration>.

    Duration: 30m, 2h, 7d, 1d12h, etc.
    """
    if not _is_admin(message.from_user and message.from_user.id):
        return

    bot_info = await message.bot.get_me()
    args_raw = (command.args or "").strip().split()

    # Parse target and duration
    target: int | None = None
    duration_str: str | None = None

    if message.reply_to_message:
        target = await _resolve_target_user(message, None, bot_info.id)
        duration_str = args_raw[0] if args_raw else None
    elif len(args_raw) >= 2:
        try:
            target = int(args_raw[0])
        except ValueError:
            await message.answer("Invalid user ID.")
            return
        duration_str = args_raw[1]

    if target is None:
        await message.answer(
            "Usage: /mute &lt;user_id&gt; &lt;duration&gt;\n"
            "Or reply to a message + /mute [duration]\n"
            "Duration: 30m, 2h, 7d, 1d12h"
        )
        return

    # Target known but no duration → show preset buttons
    if duration_str is None:
        kb = build_mute_presets(target)
        await message.answer(
            f"🔇 Mute user <code>{target}</code>?\nSelect duration:",
            reply_markup=kb,
        )
        return

    td = parse_duration(duration_str)
    if td is None:
        await message.answer("Invalid duration. Examples: 30m, 2h, 7d, 1d12h")
        return

    expires = datetime.now(timezone.utc) + td
    admin_id = message.from_user.id if message.from_user else 0

    async with async_session() as session:
        repo = RestrictionRepo(session)
        await repo.create_restriction(
            user_id=target,
            restriction_type="mute",
            restricted_by=admin_id,
            expires_at=expires,
        )

    distributor = get_distributor()
    await invalidate_restriction_cache(distributor._redis, target)

    await message.answer(
        f"🔇 User <code>{target}</code> muted for <b>{format_duration(td)}</b>.\n"
        f"Expires: <b>{expires.strftime('%d %b %Y %H:%M')} UTC</b>"
    )


# ── /unmute ──────────────────────────────────────────────────────────


@admin_router.message(Command("unmute"))
async def cmd_unmute(message: Message, command: CommandObject) -> None:
    """Unmute a user. Usage: /unmute <user_id> or reply to a message."""
    if not _is_admin(message.from_user and message.from_user.id):
        return

    bot_info = await message.bot.get_me()
    target = await _resolve_target_user(message, command.args, bot_info.id)

    if target is None:
        await message.answer("Usage: /unmute &lt;user_id&gt; or reply to a user's message.")
        return

    async with async_session() as session:
        repo = RestrictionRepo(session)
        removed = await repo.remove_restriction(target, "mute")

    if removed:
        distributor = get_distributor()
        await invalidate_restriction_cache(distributor._redis, target)
        kb = build_unmute_undo(target)
        await message.answer(f"🔊 User <code>{target}</code> unmuted.", reply_markup=kb)
    else:
        await message.answer(f"User <code>{target}</code> is not muted.")


# ── /ban ─────────────────────────────────────────────────────────────


@admin_router.message(Command("ban"))
async def cmd_ban(message: Message, command: CommandObject) -> None:
    """Permanently ban a user. Usage: /ban <user_id> or reply to a message."""
    if not _is_admin(message.from_user and message.from_user.id):
        return

    bot_info = await message.bot.get_me()
    target = await _resolve_target_user(message, command.args, bot_info.id)

    if target is None:
        await message.answer("Usage: /ban &lt;user_id&gt; or reply to a user's message.")
        return

    # Show confirmation prompt with ban options
    kb = build_ban_confirm(target)
    await message.answer(
        f"⛔ Permanently ban user <code>{target}</code>?\n\n"
        "Choose whether to also delete their past messages:",
        reply_markup=kb,
    )


# ── /unban ───────────────────────────────────────────────────────────


@admin_router.message(Command("unban"))
async def cmd_unban(message: Message, command: CommandObject) -> None:
    """Unban a user. Usage: /unban <user_id> or reply to a message."""
    if not _is_admin(message.from_user and message.from_user.id):
        return

    bot_info = await message.bot.get_me()
    target = await _resolve_target_user(message, command.args, bot_info.id)

    if target is None:
        await message.answer("Usage: /unban &lt;user_id&gt; or reply to a user's message.")
        return

    async with async_session() as session:
        repo = RestrictionRepo(session)
        removed = await repo.remove_restriction(target, "ban")

    if removed:
        distributor = get_distributor()
        await invalidate_restriction_cache(distributor._redis, target)
        kb = build_unban_undo(target)
        await message.answer(f"✅ User <code>{target}</code> unbanned.", reply_markup=kb)
    else:
        await message.answer(f"User <code>{target}</code> is not banned.")


# ── /whois ───────────────────────────────────────────────────────────


@admin_router.message(Command("whois"))
async def cmd_whois(message: Message, command: CommandObject) -> None:
    """Look up a user by their alias. Usage: /whois <name> (e.g. /whois golden_arrow)"""
    if not _is_admin(message.from_user and message.from_user.id):
        return

    raw = (command.args or "").strip().lower()
    if not raw:
        await message.answer(
            "Usage: /whois &lt;name&gt;  (e.g. /whois golden_arrow)\n"
            "Spaces and underscores are interchangeable."
        )
        return

    # Flexible matching: strip brackets, normalise spaces→underscores
    alias = raw.strip("[]").replace(" ", "_")

    async with async_session() as session:
        alias_repo = AliasRepo(session)
        user_id = await alias_repo.lookup_by_alias(alias)

    if user_id is None:
        await message.answer(f"No user found for <b>{alias}</b>.")
        return

    # Check restrictions
    async with async_session() as session:
        res_repo = RestrictionRepo(session)
        restriction = await res_repo.get_active_restriction(user_id)

    status = "None"
    if restriction:
        rtype = restriction.restriction_type.capitalize()
        if restriction.expires_at:
            exp = restriction.expires_at.strftime("%d %b %Y %H:%M UTC")
            status = f"{rtype} (until {exp})"
        else:
            status = f"{rtype} (permanent)"

    lines = [
        f"🔍 <b>Alias Lookup: {alias}</b>",
        "",
        f"User ID: <code>{user_id}</code>",
        f"Restriction: {status}",
    ]
    kb = build_moderation_actions(user_id, restriction is not None)
    await message.answer("\n".join(lines), reply_markup=kb)
