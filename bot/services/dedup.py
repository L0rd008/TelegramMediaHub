"""Deduplication engine – fingerprint computation and Redis-backed seen cache."""

from __future__ import annotations

import logging

import redis.asyncio as aioredis

from bot.services.normalizer import NormalizedMessage
from bot.utils.text import text_hash

logger = logging.getLogger(__name__)

DEDUP_TTL = 86400  # 24 hours


def compute_fingerprint(msg: NormalizedMessage) -> str | None:
    """Compute a content fingerprint for deduplication.

    - Media: use file_unique_id (stable across bots, unique per file)
    - Text: SHA-256 of normalized text
    - Otherwise: cannot dedup
    """
    if msg.file_unique_id:
        return f"media:{msg.file_unique_id}"
    if msg.text:
        return f"text:{text_hash(msg.text)}"
    return None


async def is_duplicate(
    redis: aioredis.Redis,
    msg: NormalizedMessage,
    bot_id: int,
) -> bool:
    """Check if a message is a duplicate.

    Returns True if the message should be dropped (duplicate or self-sent).
    """
    # ── Self-message detection (loop prevention) ──────────────────────
    # This is checked at the middleware level too, but double-check here
    # for media groups that bypass the middleware check.

    fingerprint = compute_fingerprint(msg)
    if fingerprint is None:
        # Cannot compute fingerprint – allow through
        return False

    # SET NX (only set if not exists) + EX (expire after TTL)
    # Returns True if key was set (i.e., NOT a duplicate)
    was_new = await redis.set(f"dedup:{fingerprint}", "1", ex=DEDUP_TTL, nx=True)

    if not was_new:
        logger.debug("Duplicate detected: %s", fingerprint)
        return True

    return False


async def is_media_group_seen(
    redis: aioredis.Redis,
    media_group_id: str,
) -> bool:
    """Check if we've already started processing this media group.

    The first item marks it as seen; subsequent items are allowed through
    to be buffered (not dedup-rejected).
    """
    was_new = await redis.set(f"dedup:mg:{media_group_id}", "1", ex=DEDUP_TTL, nx=True)
    return not was_new  # True = already seen = first item already processed
