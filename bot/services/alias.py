"""Alias service – cached pseudonym lookup and formatting."""

from __future__ import annotations

import logging

import redis.asyncio as aioredis

from bot.db.engine import async_session
from bot.db.repositories.alias_repo import AliasRepo

logger = logging.getLogger(__name__)

ALIAS_CACHE_TTL = 3600  # 1 hour


async def get_alias(redis: aioredis.Redis, user_id: int) -> str:
    """Return the user's alias, creating one on first call.

    Uses a Redis cache (``alias:{user_id}``) with a 1-hour TTL.
    """
    cache_key = f"alias:{user_id}"
    cached = await redis.get(cache_key)
    if cached is not None:
        return cached if isinstance(cached, str) else cached.decode()

    async with async_session() as session:
        repo = AliasRepo(session)
        alias = await repo.get_or_create(user_id)

    await redis.set(cache_key, alias, ex=ALIAS_CACHE_TTL)
    return alias


def format_alias_tag(alias: str) -> str:
    """Return the HTML tag appended to messages: ``<code>[u-xxxxx]</code>``."""
    return f"<code>[{alias}]</code>"
