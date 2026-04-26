"""Chat restriction repository — CRUD for the chat_restrictions table.

Mirrors :class:`bot.db.repositories.restriction_repo.RestrictionRepo` but
keys on ``chat_id``.  The ban-vs-mute hierarchy is preserved (ban is more
severe and sorts first), even though only ``ban`` is exposed in the UI today.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models.chat_restriction import ChatRestriction


def _naive_utc_now() -> datetime:
    """Return a naive UTC datetime for comparisons against legacy naive columns."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class ChatRestrictionRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get_active_restriction(
        self, chat_id: int
    ) -> ChatRestriction | None:
        """Return the most severe active restriction for ``chat_id`` (ban > mute).

        Expired entries are skipped.  Returns ``None`` if the chat is in good
        standing.
        """
        now = datetime.now(timezone.utc)
        result = await self._s.execute(
            select(ChatRestriction)
            .where(
                ChatRestriction.chat_id == chat_id,
                ChatRestriction.active == True,  # noqa: E712
            )
            .order_by(ChatRestriction.restriction_type.asc())
        )
        for row in result.scalars():
            if row.expires_at is not None:
                exp = row.expires_at.replace(tzinfo=timezone.utc) if row.expires_at.tzinfo is None else row.expires_at
                if exp <= now:
                    continue
            return row
        return None

    async def create_restriction(
        self,
        chat_id: int,
        restriction_type: str,
        restricted_by: int,
        expires_at: datetime | None = None,
    ) -> ChatRestriction:
        """Create a new restriction, deactivating any prior one of the same type."""
        if expires_at is not None and expires_at.tzinfo is not None:
            expires_at = expires_at.astimezone(timezone.utc).replace(tzinfo=None)

        await self._s.execute(
            update(ChatRestriction)
            .where(
                ChatRestriction.chat_id == chat_id,
                ChatRestriction.restriction_type == restriction_type,
                ChatRestriction.active == True,  # noqa: E712
            )
            .values(active=False)
        )

        restriction = ChatRestriction(
            chat_id=chat_id,
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
        self, chat_id: int, restriction_type: str
    ) -> bool:
        """Deactivate every active restriction of the given type for ``chat_id``."""
        result = await self._s.execute(
            update(ChatRestriction)
            .where(
                ChatRestriction.chat_id == chat_id,
                ChatRestriction.restriction_type == restriction_type,
                ChatRestriction.active == True,  # noqa: E712
            )
            .values(active=False)
        )
        await self._s.commit()
        return (result.rowcount or 0) > 0

    async def count_active_restrictions(self) -> dict[str, int]:
        """Count active chat restrictions grouped by type, excluding expired entries."""
        now = _naive_utc_now()
        result = await self._s.execute(
            select(ChatRestriction.restriction_type, func.count())
            .where(
                ChatRestriction.active == True,  # noqa: E712
                or_(
                    ChatRestriction.expires_at.is_(None),
                    ChatRestriction.expires_at > now,
                ),
            )
            .group_by(ChatRestriction.restriction_type)
        )
        return {rtype: cnt for rtype, cnt in result.all()}

    async def list_active_chat_ids(self, restriction_type: str = "ban") -> list[int]:
        """Return chat_ids with an active restriction of the given type."""
        now = _naive_utc_now()
        result = await self._s.execute(
            select(ChatRestriction.chat_id)
            .where(
                ChatRestriction.active == True,  # noqa: E712
                ChatRestriction.restriction_type == restriction_type,
                or_(
                    ChatRestriction.expires_at.is_(None),
                    ChatRestriction.expires_at > now,
                ),
            )
        )
        return [row[0] for row in result.all()]
