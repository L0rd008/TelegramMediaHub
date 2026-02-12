"""Rate limiter – global token bucket + per-chat cooldown + 429 backoff + circuit breaker."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)


class RateLimiter:
    """Dual-layer rate limiter backed by Redis."""

    def __init__(self, redis: aioredis.Redis, global_limit: int = 25) -> None:
        self._redis = redis
        self._global_limit = global_limit

        # Circuit breaker state (in-memory for simplicity)
        self._chat_errors: dict[int, int] = {}  # chat_id → consecutive error count
        self._chat_paused_until: dict[int, float] = {}  # chat_id → unix timestamp
        self._global_429_timestamps: list[float] = []
        self._global_paused_until: float = 0

    async def acquire(self, chat_id: int, chat_type: str) -> None:
        """Block until it is safe to send a message to *chat_id*.

        Raises ``RateLimitPaused`` if circuit breaker is open.
        """
        # ── Circuit breaker checks ────────────────────────────────────
        now = time.time()

        # Global pause
        if now < self._global_paused_until:
            wait = self._global_paused_until - now
            logger.warning("Global rate pause active, waiting %.1fs", wait)
            await asyncio.sleep(wait)

        # Per-chat pause
        paused_until = self._chat_paused_until.get(chat_id, 0)
        if now < paused_until:
            wait = paused_until - now
            logger.warning("Chat %d paused, waiting %.1fs", chat_id, wait)
            await asyncio.sleep(wait)

        # ── Layer 1: Global token bucket ──────────────────────────────
        await self._acquire_global_token()

        # ── Layer 2: Per-chat cooldown ────────────────────────────────
        cooldown = self._get_cooldown(chat_type)
        await self._acquire_chat_cooldown(chat_id, cooldown)

    async def _acquire_global_token(self) -> None:
        """Acquire a token from the global token bucket."""
        key = "rate:global"
        while True:
            now = time.time()
            pipe = self._redis.pipeline()
            # Remove tokens older than 1 second
            pipe.zremrangebyscore(key, 0, now - 1.0)
            # Count current tokens
            pipe.zcard(key)
            results = await pipe.execute()
            count = results[1]

            if count < self._global_limit:
                # Add a new token
                token_id = str(uuid.uuid4())
                await self._redis.zadd(key, {token_id: now})
                await self._redis.expire(key, 2)  # Cleanup TTL
                return

            # Bucket full – wait for the oldest token to expire
            oldest = await self._redis.zrange(key, 0, 0, withscores=True)
            if oldest:
                wait = max(0.05, 1.0 - (now - oldest[0][1]))
            else:
                wait = 0.05
            await asyncio.sleep(wait)

    async def _acquire_chat_cooldown(self, chat_id: int, cooldown: float) -> None:
        """Enforce per-chat send cooldown."""
        key = f"rate:chat:{chat_id}"
        while True:
            last_send = await self._redis.get(key)
            if last_send is None:
                break
            elapsed = time.time() - float(last_send)
            if elapsed >= cooldown:
                break
            await asyncio.sleep(cooldown - elapsed)

        # Mark this send
        await self._redis.set(key, str(time.time()), ex=int(cooldown) + 2)

    def _get_cooldown(self, chat_type: str) -> float:
        """Return cooldown in seconds based on chat type."""
        if chat_type in ("group", "supergroup"):
            return 3.0
        return 1.0  # private, channel

    def report_success(self, chat_id: int) -> None:
        """Reset error counter on successful send."""
        self._chat_errors.pop(chat_id, None)

    def report_error(self, chat_id: int) -> None:
        """Track consecutive errors for circuit breaker."""
        self._chat_errors[chat_id] = self._chat_errors.get(chat_id, 0) + 1
        if self._chat_errors[chat_id] >= 3:
            self._chat_paused_until[chat_id] = time.time() + 300  # 5 min pause
            self._chat_errors[chat_id] = 0
            logger.warning("Circuit breaker: chat %d paused for 5 minutes", chat_id)

    def report_429(self, retry_after: float) -> None:
        """Track global 429 responses."""
        now = time.time()
        self._global_429_timestamps.append(now)
        # Prune old entries
        self._global_429_timestamps = [
            t for t in self._global_429_timestamps if now - t < 60
        ]
        if len(self._global_429_timestamps) >= 5:
            self._global_paused_until = now + 30
            self._global_429_timestamps.clear()
            logger.warning("Circuit breaker: global pause for 30 seconds (5× 429 in 60s)")
