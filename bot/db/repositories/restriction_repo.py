"""Restriction repository – CRUD for user_restrictions table."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models.user_restriction import UserRestriction


class RestrictionRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get_active_restriction(
        self, user_id: int
    ) -> UserRestriction | None:
        """Return the most severe active restriction for a user (ban > mute).

        Expired mutes are ignored.
        """
        now = datetime.now(timezone.utc)
        result = await self._s.execute(
            select(UserRestriction)
            .where(
                UserRestriction.user_id == user_id,
                UserRestriction.active == True,  # noqa: E712
            )
            .order_by(
                # ban first, then mute
                UserRestriction.restriction_type.asc()
            )
        )
        for row in result.scalars():
            # Skip expired mutes
            if row.expires_at is not None:
                exp = row.expires_at.replace(tzinfo=timezone.utc) if row.expires_at.tzinfo is None else row.expires_at
                if exp <= now:
                    continue
            return row
        return None

    async def create_restriction(
        self,
        user_id: int,
        restriction_type: str,
        restricted_by: int,
        expires_at: datetime | None = None,
    ) -> UserRestriction:
        """Create a new restriction, deactivating any existing one of the same type."""
        # Deactivate previous restrictions of this type
        await self._s.execute(
            update(UserRestriction)
            .where(
                UserRestriction.user_id == user_id,
                UserRestriction.restriction_type == restriction_type,
                UserRestriction.active == True,  # noqa: E712
            )
            .values(active=False)
        )

        restriction = UserRestriction(
            user_id=user_id,
            restriction_type=restriction_type,
            restricted_by=restricted_by,
            expires_at=expires_at,
            active=True,
        )
        self._s.add(restriction)
        await self._s.commit()
        await self._s.refresh(restriction)
        return restriction

    async def remove_restriction(
        self, user_id: int, restriction_type: str
    ) -> bool:
        """Deactivate all active restrictions of a given type for a user.

        Returns True if any restriction was deactivated.
        """
        result = await self._s.execute(
            update(UserRestriction)
            .where(
                UserRestriction.user_id == user_id,
                UserRestriction.restriction_type == restriction_type,
                UserRestriction.active == True,  # noqa: E712
            )
            .values(active=False)
        )
        await self._s.commit()
        return (result.rowcount or 0) > 0

    async def count_active_restrictions(self) -> dict[str, int]:
        """Count active restrictions grouped by type.

        Returns e.g. {"mute": 2, "ban": 5}. Expired mutes are excluded.
        """
        from sqlalchemy import or_

        now = datetime.now(timezone.utc)
        result = await self._s.execute(
            select(UserRestriction.restriction_type, func.count())
            .where(
                UserRestriction.active == True,  # noqa: E712
                # Exclude expired mutes: keep bans (expires_at IS NULL) and
                # restrictions whose expiry is in the future.
                or_(
                    UserRestriction.expires_at.is_(None),
                    UserRestriction.expires_at > now,
                ),
            )
            .group_by(UserRestriction.restriction_type)
        )
        counts: dict[str, int] = {}
        for rtype, cnt in result.all():
            counts[rtype] = cnt
        return counts
