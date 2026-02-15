"""Media group buffer – accumulate album parts, emit complete albums.

Telegram sends album items as separate updates with the same media_group_id.
We buffer them in Redis and flush after 1 second of inactivity.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict

import redis.asyncio as aioredis

from bot.services.normalizer import NormalizedMessage
from bot.utils.enums import MessageType

logger = logging.getLogger(__name__)

BUFFER_TTL = 2  # seconds
FLUSH_INTERVAL = 0.5  # seconds – how often we check for ready groups


class MediaGroupBuffer:
    """Redis-backed media group accumulator."""

    def __init__(
        self,
        redis: aioredis.Redis,
        distributor,  # Distributor – avoid circular import
    ) -> None:
        self._redis = redis
        self._distributor = distributor
        self._flusher_task: asyncio.Task | None = None
        self._running = False

    async def add(self, msg: NormalizedMessage) -> None:
        """Add a message to the media group buffer."""
        if not msg.media_group_id:
            return

        key = f"mgbuf:{msg.media_group_id}"
        lock_key = f"mglock:{msg.media_group_id}"

        # Serialize the message
        data = json.dumps(self._to_dict(msg))
        await self._redis.rpush(key, data)
        await self._redis.expire(key, BUFFER_TTL)

        # Set/refresh the flush lock (1 second TTL)
        # When this expires, no new items have arrived for 1s → time to flush
        await self._redis.set(lock_key, "1", ex=1)

    async def start_flusher(self) -> None:
        """Start the background flusher task."""
        self._running = True
        self._flusher_task = asyncio.create_task(
            self._flusher_loop(), name="media-group-flusher"
        )
        logger.info("Media group flusher started.")

    async def stop_flusher(self) -> None:
        """Stop the flusher task."""
        self._running = False
        if self._flusher_task:
            self._flusher_task.cancel()
            try:
                await self._flusher_task
            except asyncio.CancelledError:
                pass
        logger.info("Media group flusher stopped.")

    async def _flusher_loop(self) -> None:
        """Periodically check for media groups ready to flush."""
        while self._running:
            try:
                await self._check_and_flush()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("Flusher error: %s", e)
            await asyncio.sleep(FLUSH_INTERVAL)

    async def _check_and_flush(self) -> None:
        """Check all pending media groups and flush those whose lock has expired."""
        # Scan for mgbuf:* keys
        cursor = 0
        while True:
            cursor, keys = await self._redis.scan(cursor, match="mgbuf:*", count=100)
            for key in keys:
                # Extract media_group_id from key
                mg_id = key.replace("mgbuf:", "", 1) if isinstance(key, str) else key.decode().replace("mgbuf:", "", 1)
                lock_key = f"mglock:{mg_id}"

                # Check if lock has expired (no new items for 1s)
                lock_exists = await self._redis.exists(lock_key)
                if not lock_exists:
                    await self._flush_group(mg_id)

            if cursor == 0:
                break

    async def _flush_group(self, media_group_id: str) -> None:
        """Flush a complete media group to the distributor."""
        key = f"mgbuf:{media_group_id}"

        # Pop all items atomically
        pipe = self._redis.pipeline()
        pipe.lrange(key, 0, -1)
        pipe.delete(key)
        results = await pipe.execute()
        raw_items = results[0]

        if not raw_items:
            return

        # Deserialize and sort by source_message_id
        items: list[NormalizedMessage] = []
        for raw in raw_items:
            data = json.loads(raw)
            items.append(self._from_dict(data))

        items.sort(key=lambda m: m.source_message_id)

        logger.info(
            "Flushing media group %s with %d items",
            media_group_id,
            len(items),
        )

        # Create a composite NormalizedMessage of type MEDIA_GROUP
        composite = NormalizedMessage(
            message_type=MessageType.MEDIA_GROUP,
            source_chat_id=items[0].source_chat_id,
            source_message_id=items[0].source_message_id,
            source_user_id=items[0].source_user_id,
            media_group_id=media_group_id,
            group_items=items,
        )

        # Distribute
        await self._distributor.distribute(composite)

    @staticmethod
    def _to_dict(msg: NormalizedMessage) -> dict:
        """Serialize NormalizedMessage to dict (without nested group_items)."""
        d = asdict(msg)
        d.pop("group_items", None)  # Don't serialise nested groups
        d["message_type"] = msg.message_type.value
        return d

    @staticmethod
    def _from_dict(data: dict) -> NormalizedMessage:
        """Deserialize dict to NormalizedMessage."""
        data["message_type"] = MessageType(data["message_type"])
        data.pop("group_items", None)
        return NormalizedMessage(**data)


# ── Singleton accessor ────────────────────────────────────────────────

_buffer: MediaGroupBuffer | None = None


def get_media_group_buffer(
    redis: aioredis.Redis | None = None,
    distributor=None,
) -> MediaGroupBuffer:
    global _buffer
    if _buffer is None:
        if redis is None or distributor is None:
            raise RuntimeError("MediaGroupBuffer not initialized")
        _buffer = MediaGroupBuffer(redis, distributor)
    return _buffer
