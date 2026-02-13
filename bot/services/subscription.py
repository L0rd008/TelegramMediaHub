"""Subscription service – cached premium checks, nudge logic, trial reminders."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import redis.asyncio as aioredis
from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.config import settings
from bot.db.engine import async_session
from bot.db.repositories.subscription_repo import SubscriptionRepo

logger = logging.getLogger(__name__)

# ── Plan definitions ──────────────────────────────────────────────────

CACHE_TTL = 300  # 5 minutes
NUDGE_COOLDOWN = 86_400  # 24 hours


@dataclass(frozen=True)
class Plan:
    key: str
    label: str
    stars: int
    days: int
    badge: str  # extra marketing text, "" for none


PLANS: dict[str, Plan] = {
    "week": Plan(key="week", label="1 Week", stars=250, days=7, badge=""),
    "month": Plan(
        key="month", label="1 Month", stars=750, days=30, badge="Most popular"
    ),
    "year": Plan(key="year", label="1 Year", stars=10000, days=365, badge=""),
}


# ── Premium check (cached) ───────────────────────────────────────────


async def is_premium(
    redis: aioredis.Redis,
    chat_id: int,
    registered_at: datetime,
) -> bool:
    """Return True if the chat is in trial or has an active subscription.

    Uses a Redis cache (``sub:{chat_id}``) with a 5-min TTL to avoid
    hitting the database on every message.
    """
    cache_key = f"sub:{chat_id}"
    cached = await redis.get(cache_key)
    # Admins never need a subscription.
    if chat_id in settings.admin_ids:
        if cached != "1":
            await redis.set(cache_key, "1", ex=CACHE_TTL)
        return True
    if cached is not None:
        return cached == "1"

    # Check trial first
    now = datetime.now(timezone.utc)
    trial_end = registered_at.replace(tzinfo=timezone.utc) if registered_at.tzinfo is None else registered_at
    trial_end = trial_end + timedelta(days=settings.TRIAL_DAYS)
    if now < trial_end:
        await redis.set(cache_key, "1", ex=CACHE_TTL)
        return True

    # Check paid subscription
    async with async_session() as session:
        repo = SubscriptionRepo(session)
        sub = await repo.get_active_subscription(chat_id)

    if sub is not None:
        await redis.set(cache_key, "1", ex=CACHE_TTL)
        return True

    await redis.set(cache_key, "0", ex=CACHE_TTL)
    return False


async def invalidate_cache(redis: aioredis.Redis, chat_id: int) -> None:
    """Delete the subscription cache key after a purchase or grant."""
    await redis.delete(f"sub:{chat_id}")


# ── Missed-message counter ───────────────────────────────────────────


async def record_missed(redis: aioredis.Redis, chat_id: int) -> int:
    """Increment the daily missed-message counter and return the new count."""
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    key = f"missed:{chat_id}:{today}"
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, 86_400 * 2)  # Auto-cleanup after 2 days
    return count


async def get_missed_today(redis: aioredis.Redis, chat_id: int) -> int:
    """Return the number of messages missed so far today (for nudge copy)."""
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    val = await redis.get(f"missed:{chat_id}:{today}")
    return int(val) if val else 0


# ── Nudge rate-limiting ──────────────────────────────────────────────


async def should_nudge(redis: aioredis.Redis, chat_id: int) -> bool:
    """Return True if we haven't nudged this chat in the last 24 h.

    If True is returned the cooldown key is set, so the caller doesn't
    need to do anything extra.
    """
    key = f"nudge:{chat_id}"
    was_set = await redis.set(key, "1", ex=NUDGE_COOLDOWN, nx=True)
    return bool(was_set)


# ── Trial helper ─────────────────────────────────────────────────────


def get_trial_days_remaining(registered_at: datetime) -> int:
    """Return the number of full days left in the trial (0 if expired)."""
    if registered_at.tzinfo is None:
        registered_at = registered_at.replace(tzinfo=timezone.utc)
    trial_end = registered_at + timedelta(days=settings.TRIAL_DAYS)
    remaining = (trial_end - datetime.now(timezone.utc)).days
    return max(0, remaining)


# ── Marketing text builders ──────────────────────────────────────────


def build_pricing_text() -> str:
    """Return the beautifully-formatted pricing card text (HTML)."""
    week = PLANS["week"]
    month = PLANS["month"]
    year = PLANS["year"]

    weekly_daily = week.stars / week.days
    monthly_daily = month.stars / month.days
    saving_vs_weekly = round((1 - monthly_daily / weekly_daily) * 100)

    lines = [
        "<b>Go Premium</b>",
        "",
        "Get messages from every connected chat — not just your own.",
        "",
        f"  ⏱  <b>{week.label}</b> — {week.stars} ⭐",
        f"      <i>~{weekly_daily:.0f} stars/day</i>",
        "",
        f"  🔥 <b>{month.label}</b> — {month.stars} ⭐  ← <b>Most popular</b>",
        f"      <i>~{monthly_daily:.0f} stars/day · Best value</i>",
        "",
        f"  📅 <b>{year.label}</b> — {year.stars:,} ⭐",
        "      <i>Set it and forget it</i>",
        "",
        "Free members can still sync their own messages. "
        "Premium opens up your whole network.",
    ]
    return "\n".join(lines)


def build_pricing_keyboard(target_chat_id: int) -> InlineKeyboardMarkup:
    """Return the inline keyboard with plan buttons."""
    buttons = [
        [
            InlineKeyboardButton(
                text=f"⏱  {PLANS['week'].label} — {PLANS['week'].stars} ⭐",
                callback_data=f"sub:week:{target_chat_id}",
            )
        ],
        [
            InlineKeyboardButton(
                text=f"🔥 {PLANS['month'].label} — {PLANS['month'].stars} ⭐  Most popular",
                callback_data=f"sub:month:{target_chat_id}",
            )
        ],
        [
            InlineKeyboardButton(
                text=f"📅 {PLANS['year'].label} — {PLANS['year'].stars:,} ⭐",
                callback_data=f"sub:year:{target_chat_id}",
            )
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def build_subscribe_button() -> InlineKeyboardMarkup:
    """Single 'Subscribe' button for nudges and reminders."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⭐ View Plans", callback_data="sub:show")]
        ]
    )


# ── Trial expiry reminder background task ────────────────────────────

REMINDER_DAYS = (7, 3, 1)
REMINDER_INTERVAL = 86_400  # Run once per day


class TrialReminderTask:
    """Periodic background task that sends trial-expiry reminders."""

    def __init__(self, bot: Bot, redis: aioredis.Redis) -> None:
        self._bot = bot
        self._redis = redis
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="trial-reminder")
        logger.info("Trial reminder task started.")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Trial reminder task stopped.")

    async def _loop(self) -> None:
        # Small initial delay so the bot is fully ready
        await asyncio.sleep(60)
        while self._running:
            try:
                await self._send_reminders()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("Trial reminder error: %s", e)
            await asyncio.sleep(REMINDER_INTERVAL)

    async def _send_reminders(self) -> None:
        for days_before in REMINDER_DAYS:
            async with async_session() as session:
                repo = SubscriptionRepo(session)
                chats = await repo.get_expiring_trials(days_before)

            for chat in chats:
                if chat.chat_id in settings.admin_ids:
                    continue
                # Avoid sending duplicates using Redis
                dedup_key = f"trial_remind:{chat.chat_id}:{days_before}"
                if await self._redis.set(dedup_key, "1", ex=86_400 * 2, nx=True):
                    await self._send_single_reminder(chat.chat_id, days_before)

    async def _send_single_reminder(self, chat_id: int, days_left: int) -> None:
        if days_left == 1:
            text = (
                "<b>Last day of free access.</b>\n\n"
                "After today, messages from other chats will pause. "
                "Your own messages keep flowing.\n\n"
                "Keep everything connected — plans start at about "
                "<b>1 star per hour</b>."
            )
        elif days_left == 3:
            text = (
                "Your free access ends in <b>3 days</b>.\n\n"
                "After that, you'll still be able to sync your own messages. "
                "To keep your full network connected, Premium starts at "
                "<b>250 stars</b>."
            )
        else:
            text = (
                f"Just a heads up — your free access wraps up in "
                f"<b>{days_left} days</b>.\n\n"
                "Everything still works right now. If you'd like to keep "
                "getting messages from your full network after that, "
                "the monthly plan is about <b>1 star per hour</b>."
            )
        try:
            await self._bot.send_message(
                chat_id,
                text,
                reply_markup=build_subscribe_button(),
            )
            logger.info("Sent %d-day trial reminder to chat %d", days_left, chat_id)
        except Exception as e:
            logger.debug("Failed to send trial reminder to %d: %s", chat_id, e)
