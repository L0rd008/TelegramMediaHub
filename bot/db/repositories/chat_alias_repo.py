"""Chat alias repository — CRUD for the chat_aliases table.

Mirrors :mod:`bot.db.repositories.alias_repo` but for chat aliases.  Word
lists are shared with the user alias generator (~62,500 combinations), and
chat aliases are checked against *both* alias tables on insert so a chat
can never end up with the same readable name as a user.

If we ever exhaust the keyspace the fallback is ``adjective_chat-id-suffix``,
matching the user-alias fallback shape.
"""

from __future__ import annotations

import secrets

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models.chat_alias import ChatAlias
from bot.models.user_alias import UserAlias
from bot.services.alias_words import ADJECTIVES, NOUNS

_MAX_RETRIES = 10


def _generate_alias() -> str:
    """Generate a readable two-word alias like ``misty_grove``."""
    return f"{secrets.choice(ADJECTIVES)}_{secrets.choice(NOUNS)}"


class ChatAliasRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get_or_create(self, chat_id: int) -> str:
        """Return the alias for ``chat_id``, creating one on first call.

        Aliases are unique across BOTH ``chat_aliases`` AND ``user_aliases`` so
        ``/whois`` results are unambiguous.
        """
        result = await self._s.execute(
            select(ChatAlias.alias).where(ChatAlias.chat_id == chat_id)
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            return existing

        for _ in range(_MAX_RETRIES):
            alias = _generate_alias()
            chat_collision = await self._s.execute(
                select(ChatAlias.chat_id).where(ChatAlias.alias == alias)
            )
            if chat_collision.scalar_one_or_none() is not None:
                continue
            user_collision = await self._s.execute(
                select(UserAlias.user_id).where(UserAlias.alias == alias)
            )
            if user_collision.scalar_one_or_none() is not None:
                continue

            row = ChatAlias(chat_id=chat_id, alias=alias)
            self._s.add(row)
            await self._s.commit()
            return alias

        # Extremely unlikely fallback — embed the chat-id suffix.
        # Telegram chat ids can be negative; mod handles that without sign issues.
        suffix = abs(chat_id) % 9999
        fallback = f"{secrets.choice(ADJECTIVES)}_{suffix}"
        row = ChatAlias(chat_id=chat_id, alias=fallback)
        self._s.add(row)
        await self._s.commit()
        return fallback

    async def lookup_by_alias(self, alias: str) -> int | None:
        """Return the chat_id behind an alias, or None if not found."""
        result = await self._s.execute(
            select(ChatAlias.chat_id).where(ChatAlias.alias == alias)
        )
        return result.scalar_one_or_none()
