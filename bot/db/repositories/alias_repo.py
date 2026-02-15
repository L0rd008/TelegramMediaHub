"""Alias repository – CRUD for user_aliases table."""

from __future__ import annotations

import secrets

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models.user_alias import UserAlias
from bot.services.alias_words import ADJECTIVES, NOUNS

_MAX_RETRIES = 10


def _generate_alias() -> str:
    """Generate a readable two-word alias like ``golden_arrow``."""
    adj = secrets.choice(ADJECTIVES)
    noun = secrets.choice(NOUNS)
    return f"{adj}_{noun}"


class AliasRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get_or_create(self, user_id: int) -> str:
        """Return the alias for *user_id*, creating one if it doesn't exist."""
        result = await self._s.execute(
            select(UserAlias.alias).where(UserAlias.user_id == user_id)
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            return existing

        # Generate a unique alias (retry on collision)
        for _ in range(_MAX_RETRIES):
            alias = _generate_alias()
            collision = await self._s.execute(
                select(UserAlias.user_id).where(UserAlias.alias == alias)
            )
            if collision.scalar_one_or_none() is None:
                row = UserAlias(user_id=user_id, alias=alias)
                self._s.add(row)
                await self._s.commit()
                return alias

        # Extremely unlikely fallback — use adj + user_id suffix
        fallback = f"{secrets.choice(ADJECTIVES)}_{user_id % 9999}"
        row = UserAlias(user_id=user_id, alias=fallback)
        self._s.add(row)
        await self._s.commit()
        return fallback

    async def lookup_by_alias(self, alias: str) -> int | None:
        """Return the user_id behind an alias, or None if not found."""
        result = await self._s.execute(
            select(UserAlias.user_id).where(UserAlias.alias == alias)
        )
        return result.scalar_one_or_none()
