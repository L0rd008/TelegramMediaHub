"""Moderation service – restriction checks with caching, duration parsing."""

from __future__ import annotations

import logging
import re
from datetime import timedelta

import redis.asyncio as aioredis

from bot.db.engine import async_session
from bot.db.repositories.restriction_repo import RestrictionRepo

logger = logging.getLogger(__name__)

RESTRICT_CACHE_TTL = 300  # 5 minutes

# Pattern for duration strings like "30m", "2h", "7d", "1d12h", "24h30m"
_DURATION_RE = re.compile(
    r"(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?$", re.IGNORECASE
)


async def is_user_restricted(
    redis_client: aioredis.Redis, user_id: int
) -> str | None:
    """Return ``"muted"``, ``"banned"``, or ``None``.

    Uses a Redis cache (``restrict:{user_id}``) with a 5-min TTL.
    """
    cache_key = f"restrict:{user_id}"
    cached = await redis_client.get(cache_key)
    if cached is not None:
        val = cached if isinstance(cached, str) else cached.decode()
        return val if val != "none" else None

    async with async_session() as session:
        repo = RestrictionRepo(session)
        restriction = await repo.get_active_restriction(user_id)

    if restriction is not None:
        value = restriction.restriction_type  # "mute" or "ban"
        # Store as "muted" / "banned" for clarity
        label = "muted" if value == "mute" else "banned"
        await redis_client.set(cache_key, label, ex=RESTRICT_CACHE_TTL)
        return label

    await redis_client.set(cache_key, "none", ex=RESTRICT_CACHE_TTL)
    return None


async def invalidate_restriction_cache(
    redis_client: aioredis.Redis, user_id: int
) -> None:
    """Delete the restriction cache key after a moderation action."""
    await redis_client.delete(f"restrict:{user_id}")


def parse_duration(text: str) -> timedelta | None:
    """Parse a human-friendly duration string.

    Supported formats: ``30m``, ``2h``, ``7d``, ``1d12h``, ``24h30m``, ``1d6h30m``.
    Returns ``None`` on invalid input.
    """
    text = text.strip().lower()
    if not text:
        return None

    match = _DURATION_RE.match(text)
    if match is None:
        return None

    days = int(match.group(1) or 0)
    hours = int(match.group(2) or 0)
    minutes = int(match.group(3) or 0)

    if days == 0 and hours == 0 and minutes == 0:
        return None

    return timedelta(days=days, hours=hours, minutes=minutes)


def format_duration(td: timedelta) -> str:
    """Format a timedelta into a human-readable string like '2d 6h 30m'."""
    total_seconds = int(td.total_seconds())
    if total_seconds <= 0:
        return "0m"

    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes = remainder // 60

    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")

    return " ".join(parts) if parts else "0m"
