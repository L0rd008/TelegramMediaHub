"""Admin command handler – restricted to ADMIN_USER_IDS."""

from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from bot.config import settings
from bot.db.engine import async_session
from bot.db.repositories.chat_repo import ChatRepo
from bot.db.repositories.config_repo import ConfigRepo
from bot.db.repositories.subscription_repo import SubscriptionRepo
from bot.services.distributor import get_distributor
from bot.services.subscription import PLANS, invalidate_cache

logger = logging.getLogger(__name__)

admin_router = Router(name="admin")


def _is_admin(user_id: int | None) -> bool:
    """Check if user_id is in the admin list."""
    if user_id is None:
        return False
    return user_id in settings.admin_ids


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
        "",
        "<b>Signature:</b>",
        f"  Enabled: {sig_enabled}",
        f"  Text: {sig_text or '(none)'}",
        f"  URL: {sig_url or '(none)'}",
    ]

    await message.answer("\n".join(lines))


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

    lines = [f"📋 <b>Active Chats</b> (page {page + 1}, {total} total)\n"]
    for c in chats:
        name = c.title or c.username or str(c.chat_id)
        flags = []
        if c.is_source:
            flags.append("📤")
        if c.is_destination:
            flags.append("📥")
        if c.allow_self_send:
            flags.append("🔄")
        lines.append(f"• <code>{c.chat_id}</code> {name} ({c.chat_type}) {''.join(flags)}")

    await message.answer("\n".join(lines))


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

    await message.answer("⏸️ Distribution paused. Use /resume to continue.")


# ── /resume ───────────────────────────────────────────────────────────


@admin_router.message(Command("resume"))
async def cmd_resume(message: Message) -> None:
    """Resume content distribution."""
    if not _is_admin(message.from_user and message.from_user.id):
        return

    async with async_session() as session:
        repo = ConfigRepo(session)
        await repo.set_value("paused", "false")

    await message.answer("▶️ Distribution resumed.")


# ── /edits ────────────────────────────────────────────────────────────


@admin_router.message(Command("edits"))
async def cmd_edits(message: Message, command: CommandObject) -> None:
    """Set edit redistribution mode. Usage: /edits off|resend"""
    if not _is_admin(message.from_user and message.from_user.id):
        return

    mode = (command.args or "").strip().lower()
    if mode not in ("off", "resend"):
        await message.answer("Usage: /edits off|resend")
        return

    async with async_session() as session:
        repo = ConfigRepo(session)
        await repo.set_value("edit_redistribution", mode)

    await message.answer(f"✅ Edit redistribution: <b>{mode}</b>")


# ── /remove ───────────────────────────────────────────────────────────


@admin_router.message(Command("remove"))
async def cmd_remove(message: Message, command: CommandObject) -> None:
    """Remove a chat by ID. Usage: /remove <chat_id>"""
    if not _is_admin(message.from_user and message.from_user.id):
        return

    chat_id_str = (command.args or "").strip()
    if not chat_id_str:
        await message.answer("Usage: /remove <chat_id>")
        return

    try:
        chat_id = int(chat_id_str)
    except ValueError:
        await message.answer("Invalid chat ID. Must be a number.")
        return

    async with async_session() as session:
        repo = ChatRepo(session)
        await repo.deactivate_chat(chat_id)

    await message.answer(f"✅ Chat <code>{chat_id}</code> removed.")


# ── /grant ───────────────────────────────────────────────────────────


@admin_router.message(Command("grant"))
async def cmd_grant(message: Message, command: CommandObject) -> None:
    """Grant a subscription to a chat. Usage: /grant <chat_id> <plan>"""
    if not _is_admin(message.from_user and message.from_user.id):
        return

    args = (command.args or "").strip().split()
    if len(args) != 2:
        plans_list = ", ".join(PLANS.keys())
        await message.answer(
            f"Usage: /grant &lt;chat_id&gt; &lt;plan&gt;\n"
            f"Plans: {plans_list}"
        )
        return

    try:
        chat_id = int(args[0])
    except ValueError:
        await message.answer("Invalid chat ID. Must be a number.")
        return

    plan_key = args[1].lower()
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
    """Revoke active subscriptions for a chat. Usage: /revoke <chat_id>"""
    if not _is_admin(message.from_user and message.from_user.id):
        return

    chat_id_str = (command.args or "").strip()
    if not chat_id_str:
        await message.answer("Usage: /revoke &lt;chat_id&gt;")
        return

    try:
        chat_id = int(chat_id_str)
    except ValueError:
        await message.answer("Invalid chat ID. Must be a number.")
        return

    async with async_session() as session:
        repo = SubscriptionRepo(session)
        revoked = await repo.revoke_subscription(chat_id)

    if revoked:
        # Invalidate cache
        distributor = get_distributor()
        await invalidate_cache(distributor._redis, chat_id)
        await message.answer(
            f"✅ Subscriptions revoked for chat <code>{chat_id}</code>."
        )
    else:
        await message.answer(
            f"No active subscriptions found for chat <code>{chat_id}</code>."
        )
