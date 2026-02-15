"""Tests for moderation service, alias system, and admin moderation commands."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.services.moderation import (
    format_duration,
    invalidate_restriction_cache,
    is_user_restricted,
    parse_duration,
)


# ── parse_duration ────────────────────────────────────────────────────


def test_parse_duration_minutes():
    assert parse_duration("30m") == timedelta(minutes=30)


def test_parse_duration_hours():
    assert parse_duration("2h") == timedelta(hours=2)


def test_parse_duration_days():
    assert parse_duration("7d") == timedelta(days=7)


def test_parse_duration_combined_dh():
    assert parse_duration("1d12h") == timedelta(days=1, hours=12)


def test_parse_duration_combined_hm():
    assert parse_duration("24h30m") == timedelta(hours=24, minutes=30)


def test_parse_duration_combined_dhm():
    assert parse_duration("1d6h30m") == timedelta(days=1, hours=6, minutes=30)


def test_parse_duration_empty():
    assert parse_duration("") is None


def test_parse_duration_invalid():
    assert parse_duration("abc") is None


def test_parse_duration_zero():
    assert parse_duration("0d0h0m") is None


def test_parse_duration_case_insensitive():
    assert parse_duration("2H") == timedelta(hours=2)


# ── format_duration ──────────────────────────────────────────────────


def test_format_duration_minutes():
    assert format_duration(timedelta(minutes=30)) == "30m"


def test_format_duration_hours():
    assert format_duration(timedelta(hours=2)) == "2h"


def test_format_duration_days():
    assert format_duration(timedelta(days=7)) == "7d"


def test_format_duration_combined():
    assert format_duration(timedelta(days=1, hours=6, minutes=30)) == "1d 6h 30m"


def test_format_duration_zero():
    assert format_duration(timedelta(seconds=0)) == "0m"


# ── is_user_restricted (cached) ──────────────────────────────────────


@pytest.mark.asyncio
async def test_is_user_restricted_cached_muted(fake_redis):
    """Should return cached 'muted' status."""
    await fake_redis.set("restrict:123", "muted")
    result = await is_user_restricted(fake_redis, 123)
    assert result == "muted"


@pytest.mark.asyncio
async def test_is_user_restricted_cached_banned(fake_redis):
    """Should return cached 'banned' status."""
    await fake_redis.set("restrict:456", "banned")
    result = await is_user_restricted(fake_redis, 456)
    assert result == "banned"


@pytest.mark.asyncio
async def test_is_user_restricted_cached_none(fake_redis):
    """Should return None when cached as 'none'."""
    await fake_redis.set("restrict:789", "none")
    result = await is_user_restricted(fake_redis, 789)
    assert result is None


@pytest.mark.asyncio
async def test_is_user_restricted_db_miss(fake_redis):
    """Should query DB and cache 'none' when no restriction found."""
    with patch("bot.services.moderation.async_session") as mock_session_maker:
        mock_session = AsyncMock()
        mock_session_maker.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_maker.return_value.__aexit__ = AsyncMock(return_value=False)

        with patch("bot.services.moderation.RestrictionRepo") as MockRepo:
            MockRepo.return_value.get_active_restriction = AsyncMock(return_value=None)
            result = await is_user_restricted(fake_redis, 999)

    assert result is None
    cached = await fake_redis.get("restrict:999")
    assert cached == "none"


@pytest.mark.asyncio
async def test_is_user_restricted_db_mute(fake_redis):
    """Should query DB and cache 'muted' when a mute restriction is found."""
    mock_restriction = MagicMock()
    mock_restriction.restriction_type = "mute"

    with patch("bot.services.moderation.async_session") as mock_session_maker:
        mock_session = AsyncMock()
        mock_session_maker.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_maker.return_value.__aexit__ = AsyncMock(return_value=False)

        with patch("bot.services.moderation.RestrictionRepo") as MockRepo:
            MockRepo.return_value.get_active_restriction = AsyncMock(
                return_value=mock_restriction
            )
            result = await is_user_restricted(fake_redis, 1001)

    assert result == "muted"
    cached = await fake_redis.get("restrict:1001")
    assert cached == "muted"


@pytest.mark.asyncio
async def test_is_user_restricted_db_ban(fake_redis):
    """Should query DB and cache 'banned' when a ban restriction is found."""
    mock_restriction = MagicMock()
    mock_restriction.restriction_type = "ban"

    with patch("bot.services.moderation.async_session") as mock_session_maker:
        mock_session = AsyncMock()
        mock_session_maker.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_maker.return_value.__aexit__ = AsyncMock(return_value=False)

        with patch("bot.services.moderation.RestrictionRepo") as MockRepo:
            MockRepo.return_value.get_active_restriction = AsyncMock(
                return_value=mock_restriction
            )
            result = await is_user_restricted(fake_redis, 1002)

    assert result == "banned"
    cached = await fake_redis.get("restrict:1002")
    assert cached == "banned"


# ── invalidate_restriction_cache ─────────────────────────────────────


@pytest.mark.asyncio
async def test_invalidate_restriction_cache(fake_redis):
    """Should delete the restriction cache key."""
    await fake_redis.set("restrict:500", "muted")
    await invalidate_restriction_cache(fake_redis, 500)
    assert await fake_redis.get("restrict:500") is None


# ── Alias generation ─────────────────────────────────────────────────


def test_alias_format():
    """Aliases should be two words joined by underscore."""
    from bot.db.repositories.alias_repo import _generate_alias

    alias = _generate_alias()
    parts = alias.split("_")
    assert len(parts) == 2
    assert all(p.isalpha() for p in parts)


def test_alias_format_tag_with_bot_username():
    """format_alias_tag with bot_username should return a clickable link."""
    from bot.services.alias import format_alias_tag

    result = format_alias_tag("golden_arrow", "MediaHubBot")
    assert result == '<a href="https://t.me/MediaHubBot">golden_arrow</a>'


def test_alias_format_tag_no_bot_username():
    """format_alias_tag without bot_username should return plain code tag."""
    from bot.services.alias import format_alias_tag

    result = format_alias_tag("golden_arrow")
    assert result == "<code>golden_arrow</code>"


# ── NormalizedMessage source_user_id ─────────────────────────────────


def test_normalized_message_has_source_user_id():
    """NormalizedMessage should have source_user_id field."""
    from bot.services.normalizer import NormalizedMessage
    from bot.utils.enums import MessageType

    msg = NormalizedMessage(
        message_type=MessageType.TEXT,
        source_chat_id=100,
        source_message_id=1,
        source_user_id=42,
        text="hello",
    )
    assert msg.source_user_id == 42


def test_normalized_message_source_user_id_default_none():
    """source_user_id should default to None."""
    from bot.services.normalizer import NormalizedMessage
    from bot.utils.enums import MessageType

    msg = NormalizedMessage(
        message_type=MessageType.TEXT,
        source_chat_id=100,
        source_message_id=1,
        text="hello",
    )
    assert msg.source_user_id is None


# ── SendLogRepo new methods ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_source_user_id():
    """get_source_user_id should return the user_id from send_log."""
    from bot.db.repositories.send_log_repo import SendLogRepo

    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = 42
    mock_session.execute = AsyncMock(return_value=mock_result)

    repo = SendLogRepo(mock_session)
    result = await repo.get_source_user_id(dest_chat_id=200, dest_message_id=55)

    assert result == 42


@pytest.mark.asyncio
async def test_get_source_user_id_not_found():
    """get_source_user_id should return None when not found."""
    from bot.db.repositories.send_log_repo import SendLogRepo

    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_session.execute = AsyncMock(return_value=mock_result)

    repo = SendLogRepo(mock_session)
    result = await repo.get_source_user_id(dest_chat_id=999, dest_message_id=999)

    assert result is None


@pytest.mark.asyncio
async def test_get_dest_messages_by_user():
    """get_dest_messages_by_user should return list of (chat_id, msg_id) tuples."""
    from bot.db.repositories.send_log_repo import SendLogRepo

    mock_session = AsyncMock()
    mock_row1 = MagicMock()
    mock_row1.dest_chat_id = 200
    mock_row1.dest_message_id = 55
    mock_row2 = MagicMock()
    mock_row2.dest_chat_id = 300
    mock_row2.dest_message_id = 66
    mock_result = MagicMock()
    mock_result.all.return_value = [mock_row1, mock_row2]
    mock_session.execute = AsyncMock(return_value=mock_result)

    repo = SendLogRepo(mock_session)
    result = await repo.get_dest_messages_by_user(user_id=42)

    assert result == [(200, 55), (300, 66)]


@pytest.mark.asyncio
async def test_get_dest_messages_by_user_empty():
    """get_dest_messages_by_user should return empty list when no messages found."""
    from bot.db.repositories.send_log_repo import SendLogRepo

    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.all.return_value = []
    mock_session.execute = AsyncMock(return_value=mock_result)

    repo = SendLogRepo(mock_session)
    result = await repo.get_dest_messages_by_user(user_id=999)

    assert result == []


# ── Sender alias integration ─────────────────────────────────────────


def test_build_alias_entity_found():
    """_build_alias_entity should return a text_link entity when alias is in content."""
    from bot.services.sender import _build_alias_entity

    ent = _build_alias_entity("Hello world\n\ngolden_arrow", "golden_arrow", "https://t.me/Bot")
    assert ent is not None
    assert ent.type == "text_link"
    assert ent.url == "https://t.me/Bot"
    assert ent.offset == len("Hello world\n\n")
    assert ent.length == len("golden_arrow")


def test_build_alias_entity_not_found():
    """_build_alias_entity should return None when alias is not in content."""
    from bot.services.sender import _build_alias_entity

    ent = _build_alias_entity("Hello world", "golden_arrow", "https://t.me/Bot")
    assert ent is None


# ── RestrictionRepo ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_restriction_repo_create():
    """create_restriction should add a record and commit."""
    from bot.db.repositories.restriction_repo import RestrictionRepo

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock()
    mock_session.commit = AsyncMock()
    mock_session.add = MagicMock()
    mock_session.refresh = AsyncMock()

    repo = RestrictionRepo(mock_session)
    result = await repo.create_restriction(
        user_id=100,
        restriction_type="mute",
        restricted_by=1,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=2),
    )

    mock_session.add.assert_called_once()
    assert mock_session.commit.call_count >= 1
    assert result is not None


@pytest.mark.asyncio
async def test_restriction_repo_remove():
    """remove_restriction should deactivate and commit."""
    from bot.db.repositories.restriction_repo import RestrictionRepo

    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.rowcount = 1
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.commit = AsyncMock()

    repo = RestrictionRepo(mock_session)
    removed = await repo.remove_restriction(user_id=100, restriction_type="mute")

    assert removed is True
    mock_session.commit.assert_called_once()


@pytest.mark.asyncio
async def test_restriction_repo_remove_none():
    """remove_restriction should return False when nothing to remove."""
    from bot.db.repositories.restriction_repo import RestrictionRepo

    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.rowcount = 0
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.commit = AsyncMock()

    repo = RestrictionRepo(mock_session)
    removed = await repo.remove_restriction(user_id=100, restriction_type="mute")

    assert removed is False
