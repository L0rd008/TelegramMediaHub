"""Subscription repository – CRUD operations for the subscriptions table."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import settings
from bot.models.chat import Chat
from bot.models.subscription import Subscription


class SubscriptionRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get_active_subscription(self, chat_id: int) -> Subscription | None:
        """Return the latest subscription whose expires_at > now(), or None."""
        now = datetime.now(timezone.utc)
        result = await self._s.execute(
            select(Subscription)
            .where(
                Subscription.chat_id == chat_id,
                Subscription.expires_at > now,
            )
            .order_by(Subscription.expires_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def create_subscription(
        self,
        chat_id: int,
        user_id: int,
        plan: str,
        stars_amount: int,
        days: int,
        charge_id: str,
    ) -> Subscription:
        """Create a new subscription, stacking on top of any existing one.

        If the chat already has an active subscription, the new one starts
        from the current expiry date (so the durations stack).
        """
        now = datetime.now(timezone.utc)

        # Check for existing active subscription to stack
        existing = await self.get_active_subscription(chat_id)
        if existing and existing.expires_at > now:
            start = existing.expires_at
        else:
            start = now

        expires = start + timedelta(days=days)

        sub = Subscription(
            chat_id=chat_id,
            user_id=user_id,
            plan=plan,
            stars_amount=stars_amount,
            starts_at=start,
            expires_at=expires,
            telegram_payment_charge_id=charge_id,
        )
        self._s.add(sub)
        await self._s.commit()
        await self._s.refresh(sub)
        return sub

    async def get_expiring_trials(self, days_before: int) -> list[Chat]:
        """Return chats whose trial expires in exactly ``days_before`` days.

        A chat's trial expires at ``registered_at + TRIAL_DAYS``.
        We look for chats where that date falls on the target day.
        """
        now = datetime.now(timezone.utc)
        # Chat.registered_at is stored without tzinfo, so use naive UTC bounds.
        target_date = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=days_before)
        trial_offset = timedelta(days=settings.TRIAL_DAYS)

        # Trial expiry = registered_at + TRIAL_DAYS
        # We want:  target_date - 1 day < registered_at + TRIAL_DAYS <= target_date
        # Rearranged: target_date - TRIAL_DAYS - 1 day < registered_at <= target_date - TRIAL_DAYS
        lower_bound = target_date - trial_offset - timedelta(days=1)
        upper_bound = target_date - trial_offset

        # Exclude chats that already have a paid subscription
        has_sub = (
            select(Subscription.chat_id)
            .where(
                Subscription.chat_id == Chat.chat_id,
                Subscription.expires_at > now,
            )
            .correlate(Chat)
            .exists()
        )

        result = await self._s.execute(
            select(Chat).where(
                Chat.active == True,  # noqa: E712
                Chat.registered_at > lower_bound,
                Chat.registered_at <= upper_bound,
                ~has_sub,
            )
        )
        return list(result.scalars().all())

    async def count_premium_chats(self) -> int:
        """Count chats with an active paid subscription (for social proof)."""
        now = datetime.now(timezone.utc)
        result = await self._s.execute(
            select(func.count(func.distinct(Subscription.chat_id))).where(
                Subscription.expires_at > now
            )
        )
        return result.scalar_one()

    async def count_subscription_breakdown(self) -> dict[str, int]:
        """Count active subscriptions grouped by plan.

        Returns e.g. {"week": 3, "month": 15, "year": 2}.
        """
        now = datetime.now(timezone.utc)
        result = await self._s.execute(
            select(Subscription.plan, func.count())
            .where(Subscription.expires_at > now)
            .group_by(Subscription.plan)
        )
        return {row[0]: row[1] for row in result.all()}

    async def revoke_subscription(self, chat_id: int) -> bool:
        """Expire all active subscriptions for a chat immediately.

        Returns True if any rows were affected.
        """
        now = datetime.now(timezone.utc)
        from sqlalchemy import update

        result = await self._s.execute(
            update(Subscription)
            .where(
                Subscription.chat_id == chat_id,
                Subscription.expires_at > now,
            )
            .values(expires_at=now)
        )
        await self._s.commit()
        return result.rowcount > 0  # type: ignore[union-attr]
