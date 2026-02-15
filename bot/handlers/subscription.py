"""Subscription handlers – /subscribe, /plan, payment callbacks."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    CallbackQuery,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)

from bot.db.engine import async_session
from bot.db.repositories.chat_repo import ChatRepo
from bot.db.repositories.subscription_repo import SubscriptionRepo
from bot.services.keyboards import build_plan_active_actions, build_plan_trial_actions
from bot.services.subscription import (
    PLANS,
    build_pricing_keyboard,
    build_pricing_text,
    build_subscribe_button,
    get_trial_days_remaining,
    invalidate_cache,
)

logger = logging.getLogger(__name__)

subscription_router = Router(name="subscription")


# ── /subscribe ────────────────────────────────────────────────────────


@subscription_router.message(Command("subscribe"))
async def cmd_subscribe(message: Message, command: CommandObject) -> None:
    """Show the pricing card with plan selection buttons."""
    # Determine target chat (this chat, or specified chat_id for channels)
    target_chat_id = message.chat.id
    if command.args:
        try:
            target_chat_id = int(command.args.strip())
        except ValueError:
            await message.answer("Usage: /subscribe [chat_id]")
            return

    text = build_pricing_text()
    keyboard = build_pricing_keyboard(target_chat_id)
    await message.answer(text, reply_markup=keyboard)


# ── /plan ─────────────────────────────────────────────────────────────


@subscription_router.message(Command("plan"))
async def cmd_plan(message: Message) -> None:
    """Show current subscription / trial status for this chat."""
    chat_id = message.chat.id

    async with async_session() as session:
        chat_repo = ChatRepo(session)
        chat = await chat_repo.get_chat(chat_id)
        sub_repo = SubscriptionRepo(session)
        active_sub = await sub_repo.get_active_subscription(chat_id)

    if chat is None:
        await message.answer(
            "This chat is not registered yet. Use /start first."
        )
        return

    if active_sub:
        expires_at = active_sub.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        remaining = max(0, (expires_at - datetime.now(timezone.utc)).days)
        src = "ON" if chat.is_source else "Paused"
        dst = "ON" if chat.is_destination else "Paused"
        lines = [
            "<b>You're a Premium member</b>",
            "",
            f"Plan: <b>{active_sub.plan.capitalize()}</b>",
            f"Active until: <b>{active_sub.expires_at.strftime('%d %b %Y')}</b> ({remaining} days)",
            "",
            f"Sync: Sending {src} · Receiving {dst}",
            "",
            "Everything is flowing. Enjoy.",
        ]
        await message.answer("\n".join(lines), reply_markup=build_plan_active_actions())
        return

    # Check trial
    trial_left = get_trial_days_remaining(chat.registered_at)
    if trial_left > 0:
        lines = [
            "<b>You have full access right now</b>",
            "",
            f"<b>{trial_left}</b> days left to explore everything — messages "
            "from all your connected chats, reply threading, sync control, and more.",
            "",
            "No payment needed yet.",
        ]
        await message.answer(
            "\n".join(lines), reply_markup=build_plan_trial_actions()
        )
        return

    # Expired
    lines = [
        "<b>Your free access has ended</b>",
        "",
        "Messages between your own chats still work. To get messages "
        "from your whole network again, go Premium — it's about "
        "<b>1 star per hour</b>.",
    ]
    await message.answer("\n".join(lines), reply_markup=build_subscribe_button())


# ── Callback: plan selection ──────────────────────────────────────────


@subscription_router.callback_query(F.data == "sub:show")
async def cb_show_plans(callback: CallbackQuery) -> None:
    """Re-show the pricing card (from nudge / reminder buttons)."""
    target_chat_id = callback.message.chat.id if callback.message else 0
    if not target_chat_id:
        await callback.answer("Something went wrong.", show_alert=True)
        return

    text = build_pricing_text()
    keyboard = build_pricing_keyboard(target_chat_id)
    await callback.message.answer(text, reply_markup=keyboard)  # type: ignore[union-attr]
    await callback.answer()


@subscription_router.callback_query(F.data.startswith("sub:"))
async def cb_select_plan(callback: CallbackQuery, bot: Bot) -> None:
    """User tapped a plan button – send them the Stars invoice."""
    data = callback.data or ""
    parts = data.split(":")
    # Expected format: sub:{plan}:{chat_id}
    if len(parts) != 3:
        await callback.answer("Invalid selection.", show_alert=True)
        return

    _, plan_key, chat_id_str = parts
    plan = PLANS.get(plan_key)
    if plan is None:
        await callback.answer("Unknown plan.", show_alert=True)
        return

    try:
        target_chat_id = int(chat_id_str)
    except ValueError:
        await callback.answer("Invalid chat.", show_alert=True)
        return

    # Send the invoice to the user who clicked
    user_chat_id = callback.from_user.id
    payload = f"sub:{plan_key}:{target_chat_id}"

    await bot.send_invoice(
        chat_id=user_chat_id,
        title=f"Premium — {plan.label}",
        description=(
            f"Unlock all content for {plan.label.lower()}. "
            f"Receive messages from every registered chat."
        ),
        payload=payload,
        currency="XTR",
        prices=[LabeledPrice(label=f"Premium {plan.label}", amount=plan.stars)],
    )
    await callback.answer()


# ── Pre-checkout validation ───────────────────────────────────────────


@subscription_router.pre_checkout_query()
async def on_pre_checkout(query: PreCheckoutQuery) -> None:
    """Validate the invoice payload and approve the checkout."""
    payload = query.invoice_payload or ""
    parts = payload.split(":")
    if len(parts) != 3 or parts[0] != "sub":
        await query.answer(ok=False, error_message="Invalid invoice.")
        return

    plan_key = parts[1]
    if plan_key not in PLANS:
        await query.answer(ok=False, error_message="Unknown plan.")
        return

    # All good — let the payment proceed
    await query.answer(ok=True)


# ── Successful payment ────────────────────────────────────────────────


@subscription_router.message(F.successful_payment)
async def on_successful_payment(message: Message) -> None:
    """Process a completed Stars payment – create subscription + confirm."""
    payment = message.successful_payment
    if payment is None:
        return

    payload = payment.invoice_payload or ""
    parts = payload.split(":")
    if len(parts) != 3 or parts[0] != "sub":
        return  # Not our invoice

    plan_key = parts[1]
    plan = PLANS.get(plan_key)
    if plan is None:
        return

    try:
        target_chat_id = int(parts[2])
    except ValueError:
        return

    user_id = message.from_user.id if message.from_user else 0
    charge_id = payment.telegram_payment_charge_id

    # Create subscription record
    async with async_session() as session:
        repo = SubscriptionRepo(session)
        sub = await repo.create_subscription(
            chat_id=target_chat_id,
            user_id=user_id,
            plan=plan.key,
            stars_amount=plan.stars,
            days=plan.days,
            charge_id=charge_id,
        )

    # Invalidate cache so the paywall check picks it up immediately
    from bot.services.distributor import get_distributor

    distributor = get_distributor()
    await invalidate_cache(distributor._redis, target_chat_id)

    # Send confirmation to the payer
    expires_str = sub.expires_at.strftime("%d %b %Y")
    target_label = (
        "this chat"
        if target_chat_id == message.chat.id
        else f"chat <code>{target_chat_id}</code>"
    )

    lines = [
        "<b>You're in. Welcome to Premium.</b>",
        "",
        f"Plan: <b>{plan.label}</b>",
        f"For: {target_label}",
        f"Active until: <b>{expires_str}</b>",
        "",
        "Messages from your entire network will now flow into this chat. "
        "Thank you for supporting the bot.",
    ]
    await message.answer("\n".join(lines))

    # If the subscription target is a different chat, notify that chat too
    if target_chat_id != message.chat.id:
        try:
            bot = message.bot
            if bot:
                notify_lines = [
                    f"This chat just got upgraded to <b>Premium</b> ({plan.label}).",
                    "",
                    "Messages from all connected chats will now arrive here. "
                    f"Active until <b>{expires_str}</b>.",
                ]
                await bot.send_message(target_chat_id, "\n".join(notify_lines))
        except Exception as e:
            logger.debug("Could not notify target chat %d: %s", target_chat_id, e)
