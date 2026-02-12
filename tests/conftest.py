"""Shared fixtures for TelegramMediaHub tests."""

from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock

# Ensure BOT_TOKEN is set before any bot module triggers Settings validation
os.environ.setdefault("BOT_TOKEN", "0:TEST_TOKEN")

import pytest


@pytest.fixture(scope="session")
def event_loop():
    """Provide a single event loop for the whole test session."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def fake_redis():
    """In-memory mock that behaves like redis.asyncio.Redis for the subset we use."""

    store: dict[str, str] = {}

    redis = AsyncMock()

    async def _set(key, value, ex=None, nx=False):
        if nx and key in store:
            return None  # Key already exists
        store[key] = value
        return True

    async def _get(key):
        return store.get(key)

    async def _exists(key):
        return 1 if key in store else 0

    async def _rpush(key, *values):
        if key not in store:
            store[key] = []  # type: ignore[assignment]
        for v in values:
            store[key].append(v)  # type: ignore[union-attr]
        return len(store[key])  # type: ignore[arg-type]

    async def _expire(key, ttl):
        return True

    async def _zadd(key, mapping):
        if key not in store:
            store[key] = {}  # type: ignore[assignment]
        store[key].update(mapping)  # type: ignore[union-attr]
        return len(mapping)

    async def _zremrangebyscore(key, min_score, max_score):
        if key not in store or not isinstance(store[key], dict):
            return 0
        to_remove = [
            k for k, v in store[key].items()  # type: ignore[union-attr]
            if float(v) >= float(min_score) and float(v) <= float(max_score)
        ]
        for k in to_remove:
            del store[key][k]  # type: ignore[attr-defined]
        return len(to_remove)

    async def _zcard(key):
        if key not in store or not isinstance(store[key], dict):
            return 0
        return len(store[key])  # type: ignore[arg-type]

    async def _zrange(key, start, end, withscores=False):
        if key not in store or not isinstance(store[key], dict):
            return []
        items = sorted(store[key].items(), key=lambda x: float(x[1]))  # type: ignore[union-attr]
        sliced = items[start : end + 1 if end >= 0 else None]
        if withscores:
            return [(k, float(v)) for k, v in sliced]
        return [k for k, _ in sliced]

    async def _incr(key):
        current = store.get(key, "0")
        new_val = int(current) + 1
        store[key] = str(new_val)
        return new_val

    async def _delete(*keys):
        count = 0
        for k in keys:
            if k in store:
                del store[k]
                count += 1
        return count

    redis.set = AsyncMock(side_effect=_set)
    redis.get = AsyncMock(side_effect=_get)
    redis.exists = AsyncMock(side_effect=_exists)
    redis.rpush = AsyncMock(side_effect=_rpush)
    redis.expire = AsyncMock(side_effect=_expire)
    redis.incr = AsyncMock(side_effect=_incr)
    redis.delete = AsyncMock(side_effect=_delete)
    redis.zadd = AsyncMock(side_effect=_zadd)
    redis.zremrangebyscore = AsyncMock(side_effect=_zremrangebyscore)
    redis.zcard = AsyncMock(side_effect=_zcard)
    redis.zrange = AsyncMock(side_effect=_zrange)

    # pipeline() must be a regular (non-async) method that returns a mock
    # supporting chained sync calls (.lrange, .delete) + async .execute()
    pipe = MagicMock()
    pipe.execute = AsyncMock(return_value=[])
    redis.pipeline = MagicMock(return_value=pipe)

    redis._store = store  # Expose for assertions
    return redis


@pytest.fixture
def make_message():
    """Factory to create a mock aiogram Message with desired attributes."""

    def _make(
        message_id: int = 1,
        chat_id: int = 100,
        chat_type: str = "private",
        text: str | None = None,
        photo: list | None = None,
        video=None,
        animation=None,
        audio=None,
        document=None,
        voice=None,
        video_note=None,
        sticker=None,
        caption: str | None = None,
        entities=None,
        caption_entities=None,
        media_group_id: str | None = None,
        paid_media=None,
        from_user_id: int = 999,
        is_bot: bool = False,
    ):
        msg = MagicMock()
        msg.message_id = message_id
        msg.chat = MagicMock()
        msg.chat.id = chat_id
        msg.chat.type = chat_type
        msg.text = text
        msg.photo = photo
        msg.video = video
        msg.animation = animation
        msg.audio = audio
        msg.document = document
        msg.voice = voice
        msg.video_note = video_note
        msg.sticker = sticker
        msg.caption = caption
        msg.entities = entities
        msg.caption_entities = caption_entities
        msg.media_group_id = media_group_id
        msg.paid_media = paid_media
        msg.from_user = MagicMock()
        msg.from_user.id = from_user_id
        msg.from_user.is_bot = is_bot

        # Attributes accessed via getattr
        msg.has_media_spoiler = False
        msg.show_caption_above_media = False

        return msg

    return _make


@pytest.fixture
def make_photo_size():
    """Factory to create a mock PhotoSize."""

    def _make(file_id="photo_123", file_unique_id="uniq_photo", width=800, height=600, file_size=50000):
        ps = MagicMock()
        ps.file_id = file_id
        ps.file_unique_id = file_unique_id
        ps.width = width
        ps.height = height
        ps.file_size = file_size
        return ps

    return _make
