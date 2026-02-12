"""Tests for the media group buffer service."""

from __future__ import annotations

import json

import pytest

from bot.services.media_group import MediaGroupBuffer
from bot.services.normalizer import NormalizedMessage
from bot.utils.enums import MessageType


def _make_item(
    message_type: MessageType = MessageType.PHOTO,
    source_message_id: int = 1,
    media_group_id: str = "album_1",
    file_id: str = "file_1",
    file_unique_id: str = "uniq_1",
) -> NormalizedMessage:
    return NormalizedMessage(
        message_type=message_type,
        source_chat_id=100,
        source_message_id=source_message_id,
        media_group_id=media_group_id,
        file_id=file_id,
        file_unique_id=file_unique_id,
    )


class TestMediaGroupBufferSerialization:
    def test_to_dict_excludes_group_items(self):
        item = _make_item()
        d = MediaGroupBuffer._to_dict(item)
        assert "group_items" not in d
        assert d["message_type"] == "PHOTO"

    def test_from_dict_round_trip(self):
        item = _make_item(file_id="f1", file_unique_id="u1", source_message_id=42)
        d = MediaGroupBuffer._to_dict(item)
        restored = MediaGroupBuffer._from_dict(d)
        assert restored.message_type == MessageType.PHOTO
        assert restored.file_id == "f1"
        assert restored.file_unique_id == "u1"
        assert restored.source_message_id == 42
        assert restored.source_chat_id == 100
        assert restored.group_items == []

    def test_to_dict_serializes_to_json(self):
        item = _make_item()
        d = MediaGroupBuffer._to_dict(item)
        # Should be JSON-serializable
        json_str = json.dumps(d)
        assert isinstance(json_str, str)
        parsed = json.loads(json_str)
        assert parsed["message_type"] == "PHOTO"


class TestMediaGroupBufferAdd:
    @pytest.mark.asyncio
    async def test_add_pushes_to_redis(self, fake_redis):
        buffer = MediaGroupBuffer(fake_redis, distributor=None)
        item = _make_item(media_group_id="grp_1")
        await buffer.add(item)

        # Should have called rpush on the buffer key
        fake_redis.rpush.assert_called_once()
        call_args = fake_redis.rpush.call_args
        assert call_args[0][0] == "mgbuf:grp_1"

        # Should have set the lock key
        fake_redis.set.assert_called_once()
        lock_call = fake_redis.set.call_args
        assert lock_call[0][0] == "mglock:grp_1"

    @pytest.mark.asyncio
    async def test_add_ignores_non_media_group(self, fake_redis):
        buffer = MediaGroupBuffer(fake_redis, distributor=None)
        item = _make_item(media_group_id=None)  # type: ignore[arg-type]
        item.media_group_id = None
        await buffer.add(item)
        fake_redis.rpush.assert_not_called()

    @pytest.mark.asyncio
    async def test_multiple_items_same_group(self, fake_redis):
        buffer = MediaGroupBuffer(fake_redis, distributor=None)
        for i in range(3):
            item = _make_item(
                media_group_id="grp_2",
                source_message_id=i + 1,
                file_id=f"f_{i}",
                file_unique_id=f"u_{i}",
            )
            await buffer.add(item)

        assert fake_redis.rpush.call_count == 3
        # All should target the same key
        for call in fake_redis.rpush.call_args_list:
            assert call[0][0] == "mgbuf:grp_2"


class TestMediaGroupBufferFlush:
    @pytest.mark.asyncio
    async def test_flush_creates_composite_message(self, fake_redis):
        """Test that _flush_group creates a MEDIA_GROUP composite and distributes it."""
        distributed = []

        class FakeDistributor:
            async def distribute(self, msg):
                distributed.append(msg)

        buffer = MediaGroupBuffer(fake_redis, distributor=FakeDistributor())

        # Manually populate Redis buffer
        items = []
        for i in range(3):
            item = _make_item(
                media_group_id="grp_flush",
                source_message_id=i + 1,
                file_id=f"f_{i}",
                file_unique_id=f"u_{i}",
            )
            items.append(json.dumps(MediaGroupBuffer._to_dict(item)))

        # Mock pipeline
        pipe_mock = fake_redis.pipeline.return_value
        pipe_mock.lrange.return_value = None
        pipe_mock.delete.return_value = None
        pipe_mock.execute = pytest.importorskip("unittest.mock").AsyncMock(
            return_value=[items, 1]
        )

        await buffer._flush_group("grp_flush")

        assert len(distributed) == 1
        composite = distributed[0]
        assert composite.message_type == MessageType.MEDIA_GROUP
        assert composite.media_group_id == "grp_flush"
        assert len(composite.group_items) == 3
        # Items should be sorted by source_message_id
        assert [item.source_message_id for item in composite.group_items] == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_flush_empty_group_is_noop(self, fake_redis):
        distributed = []

        class FakeDistributor:
            async def distribute(self, msg):
                distributed.append(msg)

        buffer = MediaGroupBuffer(fake_redis, distributor=FakeDistributor())

        pipe_mock = fake_redis.pipeline.return_value
        pipe_mock.lrange.return_value = None
        pipe_mock.delete.return_value = None
        pipe_mock.execute = pytest.importorskip("unittest.mock").AsyncMock(
            return_value=[[], 0]
        )

        await buffer._flush_group("empty_group")
        assert len(distributed) == 0
