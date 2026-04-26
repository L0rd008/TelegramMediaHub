"""Chat-alias service — cached lookup with the same Redis cache shape as
:mod:`bot.services.alias` for users.

The cache namespace ``chat_alias:{chat_id}`` is distinct from ``alias:{user_id}``
so a user and a chat with the same numeric id (which can happen across the
positive/negative split Telegram uses) never collide.
"""

from __future__ import annotations

import logging

import redis.asyncio as aioredis

from bot.db.engine import async_session
from bot.db.repositories.chat_alias_repo import ChatAliasRepo

logger = logging.getLogger(__name__)

CHAT_ALIAS_CACHE_TTL = 3600  # 1 hour, mirrors user alias cache


async def get_chat_alias(redis: aioredis.Redis, chat_id: int) -> str:
    """Return the chat's alias, creating one on first call.

    Uses Redis cache ``chat_alias:{chat_id}`` with a 1-hour TTL.
    """
    cache_key = f"chat_alias:{chat_id}"
    cached = await redis.get(cache_key)
    if cached is not None:
        return cached if isinstance(cached, str) else cached.decode()

    async with async_session() as session:
        repo = ChatAliasRepo(session)
        alias = await repo.get_or_create(chat_id)

    await redis.set(cache_key, alias, ex=CHAT_ALIAS_CACHE_TTL)
    return alias


def format_group_attribution(user_alias: str, chat_alias: str) -> str:
    """Combine a user alias and a chat alias into the visible attribution label.

    Format: ``user_alias @ chat_alias`` (e.g. ``golden_arrow @ misty_grove``).
    The ``@`` separator is unambiguous (no @-prefix means it's not a Telegram
    username) and reads naturally as "this user, in this group".
    """
    return f"{user_alias} @ {chat_alias}"
