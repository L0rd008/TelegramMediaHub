"""Tests for the deduplication engine.

Three independent guards are exercised:

- ``is_duplicate_update`` — webhook retry guard, keyed on
  ``(chat_id, message_id)``, 60 s TTL.
- ``is_duplicate`` — content repost guard for single messages, keyed on
  ``(source_chat_id, fingerprint)``, 24 h TTL.  Critically scoped per chat so
  cross-chat collisions don't drop legitimate traffic.
- ``is_album_duplicate`` / ``compute_group_fingerprint`` — album repost guard
  using a hash over the sorted ``file_unique_id`` set so re-uploads (with a
  fresh ``media_group_id``) are caught.

The pre-2026-04-25 implementation deduped globally on content alone, which
silently dropped ~95% of legitimate text traffic across distinct chats.  These
tests guard against any regression to that behaviour.
"""

from __future__ import annotations

import pytest

from bot.services.dedup import (
    compute_fingerprint,
    compute_group_fingerprint,
    is_album_duplicate,
    is_duplicate,
    is_duplicate_update,
    is_media_group_seen,
)
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


# ── compute_fingerprint ──────────────────────────────────────────────────────


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


# ── is_duplicate (per-chat content dedup) ────────────────────────────────────


class TestIsDuplicate:
    @pytest.mark.asyncio
    async def test_first_message_is_not_duplicate(self, fake_redis):
        msg = _make_msg(text="unique text")
        assert await is_duplicate(fake_redis, msg) is False

    @pytest.mark.asyncio
    async def test_same_message_second_time_is_duplicate(self, fake_redis):
        msg = _make_msg(text="duplicate text")
        await is_duplicate(fake_redis, msg)
        assert await is_duplicate(fake_redis, msg) is True

    @pytest.mark.asyncio
    async def test_different_messages_not_duplicate(self, fake_redis):
        msg1 = _make_msg(text="first")
        msg2 = _make_msg(text="second")
        await is_duplicate(fake_redis, msg1)
        assert await is_duplicate(fake_redis, msg2) is False

    @pytest.mark.asyncio
    async def test_media_dedup_by_file_unique_id(self, fake_redis):
        msg = _make_msg(
            message_type=MessageType.PHOTO,
            file_unique_id="photo_uniq_1",
        )
        assert await is_duplicate(fake_redis, msg) is False
        assert await is_duplicate(fake_redis, msg) is True

    @pytest.mark.asyncio
    async def test_no_fingerprint_allows_through(self, fake_redis):
        msg = _make_msg(message_type=MessageType.STICKER)
        assert await is_duplicate(fake_redis, msg) is False

    @pytest.mark.asyncio
    async def test_same_text_different_chats_both_relayed(self, fake_redis):
        """Regression for the cross-chat collision bug.

        Two distinct chats each producing the same common phrase
        ("good morning") must NOT collide — both should pass dedup.
        """
        a = _make_msg(text="good morning", source_chat_id=111)
        b = _make_msg(text="good morning", source_chat_id=222)
        assert await is_duplicate(fake_redis, a) is False
        assert await is_duplicate(fake_redis, b) is False

    @pytest.mark.asyncio
    async def test_same_media_different_chats_both_relayed(self, fake_redis):
        """Same shared meme posted in two different source chats — both relayed."""
        a = _make_msg(
            message_type=MessageType.PHOTO,
            file_unique_id="meme_xyz",
            source_chat_id=111,
        )
        b = _make_msg(
            message_type=MessageType.PHOTO,
            file_unique_id="meme_xyz",
            source_chat_id=222,
        )
        assert await is_duplicate(fake_redis, a) is False
        assert await is_duplicate(fake_redis, b) is False

    @pytest.mark.asyncio
    async def test_redis_key_is_chat_scoped(self, fake_redis):
        """The actual Redis key MUST start with the per-chat prefix ``dup:c:{chat}:``."""
        msg = _make_msg(text="hello", source_chat_id=42)
        await is_duplicate(fake_redis, msg)
        assert any(k.startswith("dup:c:42:text:") for k in fake_redis._store)


# ── is_duplicate_update (webhook retry guard) ────────────────────────────────


class TestIsDuplicateUpdate:
    @pytest.mark.asyncio
    async def test_first_update_passes(self, fake_redis):
        assert await is_duplicate_update(fake_redis, 100, 1) is False

    @pytest.mark.asyncio
    async def test_redelivered_update_dropped(self, fake_redis):
        await is_duplicate_update(fake_redis, 100, 1)
        assert await is_duplicate_update(fake_redis, 100, 1) is True

    @pytest.mark.asyncio
    async def test_different_message_id_passes(self, fake_redis):
        await is_duplicate_update(fake_redis, 100, 1)
        assert await is_duplicate_update(fake_redis, 100, 2) is False

    @pytest.mark.asyncio
    async def test_different_chat_passes(self, fake_redis):
        await is_duplicate_update(fake_redis, 100, 1)
        assert await is_duplicate_update(fake_redis, 200, 1) is False


# ── compute_group_fingerprint / is_album_duplicate ──────────────────────────


class TestGroupFingerprint:
    def test_same_files_same_fingerprint(self):
        a = [_make_msg(file_unique_id="x"), _make_msg(file_unique_id="y")]
        b = [_make_msg(file_unique_id="x"), _make_msg(file_unique_id="y")]
        assert compute_group_fingerprint(a) == compute_group_fingerprint(b)

    def test_order_independent(self):
        """Re-uploading the same files in a different order yields the same fp."""
        a = [_make_msg(file_unique_id="x"), _make_msg(file_unique_id="y")]
        b = [_make_msg(file_unique_id="y"), _make_msg(file_unique_id="x")]
        assert compute_group_fingerprint(a) == compute_group_fingerprint(b)

    def test_different_files_different_fingerprint(self):
        a = [_make_msg(file_unique_id="x"), _make_msg(file_unique_id="y")]
        b = [_make_msg(file_unique_id="x"), _make_msg(file_unique_id="z")]
        assert compute_group_fingerprint(a) != compute_group_fingerprint(b)

    def test_no_file_unique_ids_returns_none(self):
        a = [_make_msg(message_type=MessageType.STICKER)]
        assert compute_group_fingerprint(a) is None


class TestIsAlbumDuplicate:
    @pytest.mark.asyncio
    async def test_first_album_passes(self, fake_redis):
        items = [_make_msg(file_unique_id="a"), _make_msg(file_unique_id="b")]
        assert await is_album_duplicate(fake_redis, 100, items) is False

    @pytest.mark.asyncio
    async def test_reupload_same_files_dropped(self, fake_redis):
        items = [_make_msg(file_unique_id="a"), _make_msg(file_unique_id="b")]
        await is_album_duplicate(fake_redis, 100, items)
        # Same files, fresh NormalizedMessage instances (simulating re-upload
        # with a new media_group_id), same source chat — must be detected.
        items2 = [_make_msg(file_unique_id="a"), _make_msg(file_unique_id="b")]
        assert await is_album_duplicate(fake_redis, 100, items2) is True

    @pytest.mark.asyncio
    async def test_reupload_in_different_chat_passes(self, fake_redis):
        """Same content uploaded to a *different* source chat is not a duplicate."""
        items = [_make_msg(file_unique_id="a"), _make_msg(file_unique_id="b")]
        await is_album_duplicate(fake_redis, 100, items)
        items2 = [_make_msg(file_unique_id="a"), _make_msg(file_unique_id="b")]
        assert await is_album_duplicate(fake_redis, 200, items2) is False

    @pytest.mark.asyncio
    async def test_partially_overlapping_albums_both_pass(self, fake_redis):
        """Albums that share some but not all files have different fps."""
        a = [_make_msg(file_unique_id="x"), _make_msg(file_unique_id="y")]
        b = [_make_msg(file_unique_id="x"), _make_msg(file_unique_id="z")]
        await is_album_duplicate(fake_redis, 100, a)
        assert await is_album_duplicate(fake_redis, 100, b) is False


# ── is_media_group_seen ─────────────────────────────────────────────────────


class TestIsMediaGroupSeen:
    @pytest.mark.asyncio
    async def test_first_call_marks_and_reports_unseen(self, fake_redis):
        assert await is_media_group_seen(fake_redis, 100, "group_1") is False

    @pytest.mark.asyncio
    async def test_second_call_reports_seen(self, fake_redis):
        await is_media_group_seen(fake_redis, 100, "group_2")
        assert await is_media_group_seen(fake_redis, 100, "group_2") is True

    @pytest.mark.asyncio
    async def test_different_groups_independent(self, fake_redis):
        await is_media_group_seen(fake_redis, 100, "group_a")
        assert await is_media_group_seen(fake_redis, 100, "group_b") is False

    @pytest.mark.asyncio
    async def test_same_group_id_different_chats_independent(self, fake_redis):
        """Defence in depth: media_group_id is supposed to be globally unique
        but we don't bet our dedup correctness on Telegram client behaviour."""
        await is_media_group_seen(fake_redis, 100, "g")
        assert await is_media_group_seen(fake_redis, 200, "g") is False
