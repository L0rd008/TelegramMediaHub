"""Tests for the deduplication engine."""

from __future__ import annotations

import pytest

from bot.services.dedup import compute_fingerprint, is_duplicate, is_media_group_seen
from bot.services.normalizer import NormalizedMessage
from bot.utils.enums import MessageType


def _make_msg(**kwargs) -> NormalizedMessage:
    defaults = dict(
        message_type=MessageType.TEXT,
        source_chat_id=100,
        source_message_id=1,
    )
    defaults.update(kwargs)
    return NormalizedMessage(**defaults)


class TestComputeFingerprint:
    def test_media_uses_file_unique_id(self):
        msg = _make_msg(message_type=MessageType.PHOTO, file_unique_id="abc123")
        fp = compute_fingerprint(msg)
        assert fp == "media:abc123"

    def test_text_uses_sha256(self):
        msg = _make_msg(text="Hello world")
        fp = compute_fingerprint(msg)
        assert fp is not None
        assert fp.startswith("text:")
        assert len(fp) == 5 + 32  # "text:" + 32 hex chars

    def test_same_text_same_fingerprint(self):
        msg1 = _make_msg(text="Hello")
        msg2 = _make_msg(text="Hello")
        assert compute_fingerprint(msg1) == compute_fingerprint(msg2)

    def test_different_text_different_fingerprint(self):
        msg1 = _make_msg(text="Hello")
        msg2 = _make_msg(text="World")
        assert compute_fingerprint(msg1) != compute_fingerprint(msg2)

    def test_whitespace_stripped_for_text(self):
        msg1 = _make_msg(text="  Hello  ")
        msg2 = _make_msg(text="Hello")
        assert compute_fingerprint(msg1) == compute_fingerprint(msg2)

    def test_no_file_no_text_returns_none(self):
        msg = _make_msg(message_type=MessageType.STICKER)
        fp = compute_fingerprint(msg)
        assert fp is None


class TestIsDuplicate:
    @pytest.mark.asyncio
    async def test_first_message_is_not_duplicate(self, fake_redis):
        msg = _make_msg(text="unique text")
        result = await is_duplicate(fake_redis, msg, bot_id=1)
        assert result is False

    @pytest.mark.asyncio
    async def test_same_message_second_time_is_duplicate(self, fake_redis):
        msg = _make_msg(text="duplicate text")
        await is_duplicate(fake_redis, msg, bot_id=1)
        result = await is_duplicate(fake_redis, msg, bot_id=1)
        assert result is True

    @pytest.mark.asyncio
    async def test_different_messages_not_duplicate(self, fake_redis):
        msg1 = _make_msg(text="first")
        msg2 = _make_msg(text="second")
        await is_duplicate(fake_redis, msg1, bot_id=1)
        result = await is_duplicate(fake_redis, msg2, bot_id=1)
        assert result is False

    @pytest.mark.asyncio
    async def test_media_dedup_by_file_unique_id(self, fake_redis):
        msg = _make_msg(
            message_type=MessageType.PHOTO,
            file_unique_id="photo_uniq_1",
        )
        assert await is_duplicate(fake_redis, msg, bot_id=1) is False
        assert await is_duplicate(fake_redis, msg, bot_id=1) is True

    @pytest.mark.asyncio
    async def test_no_fingerprint_allows_through(self, fake_redis):
        msg = _make_msg(message_type=MessageType.STICKER)
        result = await is_duplicate(fake_redis, msg, bot_id=1)
        assert result is False


class TestIsMediaGroupSeen:
    @pytest.mark.asyncio
    async def test_first_item_marks_group(self, fake_redis):
        result = await is_media_group_seen(fake_redis, "group_1")
        assert result is False  # First time → not previously seen

    @pytest.mark.asyncio
    async def test_second_item_is_seen(self, fake_redis):
        await is_media_group_seen(fake_redis, "group_2")
        result = await is_media_group_seen(fake_redis, "group_2")
        assert result is True  # Second time → already seen

    @pytest.mark.asyncio
    async def test_different_groups_independent(self, fake_redis):
        await is_media_group_seen(fake_redis, "group_a")
        result = await is_media_group_seen(fake_redis, "group_b")
        assert result is False  # Different group → not seen
