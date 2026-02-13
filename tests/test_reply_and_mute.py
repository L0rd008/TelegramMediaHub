"""Tests for reply threading (SendLogRepo) and mute/unmute command gating."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.db.repositories.send_log_repo import SendLogRepo
from bot.services.normalizer import NormalizedMessage
from bot.utils.enums import MessageType


# ── SendLogRepo tests ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reverse_lookup_returns_source():
    """reverse_lookup should return (source_chat_id, source_message_id) when a row exists."""
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_row = MagicMock()
    mock_row.source_chat_id = 100
    mock_row.source_message_id = 42
    mock_result.one_or_none.return_value = mock_row
    mock_session.execute = AsyncMock(return_value=mock_result)

    repo = SendLogRepo(mock_session)
    result = await repo.reverse_lookup(dest_chat_id=200, dest_message_id=55)

    assert result == (100, 42)
    mock_session.execute.assert_called_once()


@pytest.mark.asyncio
async def test_reverse_lookup_returns_none_when_missing():
    """reverse_lookup should return None when no matching row exists."""
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.one_or_none.return_value = None
    mock_session.execute = AsyncMock(return_value=mock_result)

    repo = SendLogRepo(mock_session)
    result = await repo.reverse_lookup(dest_chat_id=999, dest_message_id=999)

    assert result is None


@pytest.mark.asyncio
async def test_get_dest_message_id_returns_id():
    """get_dest_message_id should return the dest message id when found."""
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = 77
    mock_session.execute = AsyncMock(return_value=mock_result)

    repo = SendLogRepo(mock_session)
    result = await repo.get_dest_message_id(
        source_chat_id=100, source_message_id=42, dest_chat_id=300
    )

    assert result == 77


@pytest.mark.asyncio
async def test_get_dest_message_id_returns_none():
    """get_dest_message_id should return None when no matching row exists."""
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_session.execute = AsyncMock(return_value=mock_result)

    repo = SendLogRepo(mock_session)
    result = await repo.get_dest_message_id(
        source_chat_id=100, source_message_id=42, dest_chat_id=999
    )

    assert result is None


# ── NormalizedMessage reply fields ───────────────────────────────────


def test_normalized_message_has_reply_fields():
    """NormalizedMessage should have reply_source_chat_id and reply_source_message_id."""
    msg = NormalizedMessage(
        message_type=MessageType.TEXT,
        source_chat_id=100,
        source_message_id=1,
        text="hello",
    )
    assert msg.reply_source_chat_id is None
    assert msg.reply_source_message_id is None


def test_normalized_message_reply_fields_set():
    """Reply fields should be settable after construction."""
    msg = NormalizedMessage(
        message_type=MessageType.TEXT,
        source_chat_id=100,
        source_message_id=1,
        text="hello",
    )
    msg.reply_source_chat_id = 200
    msg.reply_source_message_id = 42
    assert msg.reply_source_chat_id == 200
    assert msg.reply_source_message_id == 42


# ── SendTask reply_to_message_id ─────────────────────────────────────


def test_send_task_default_reply_is_none():
    """SendTask.reply_to_message_id should default to None."""
    from bot.services.distributor import SendTask

    msg = NormalizedMessage(
        message_type=MessageType.TEXT,
        source_chat_id=100,
        source_message_id=1,
        text="hello",
    )
    task = SendTask(message=msg, dest_chat_id=200)
    assert task.reply_to_message_id is None


def test_send_task_with_reply():
    """SendTask should accept reply_to_message_id."""
    from bot.services.distributor import SendTask

    msg = NormalizedMessage(
        message_type=MessageType.TEXT,
        source_chat_id=100,
        source_message_id=1,
        text="hello",
    )
    task = SendTask(message=msg, dest_chat_id=200, reply_to_message_id=55)
    assert task.reply_to_message_id == 55


# ── ChatRepo toggle methods ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_chat_repo_toggle_source():
    """toggle_source should issue an UPDATE and commit."""
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock()
    mock_session.commit = AsyncMock()

    from bot.db.repositories.chat_repo import ChatRepo

    repo = ChatRepo(mock_session)
    await repo.toggle_source(chat_id=100, enabled=False)

    mock_session.execute.assert_called_once()
    mock_session.commit.assert_called_once()


@pytest.mark.asyncio
async def test_chat_repo_toggle_destination():
    """toggle_destination should issue an UPDATE and commit."""
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock()
    mock_session.commit = AsyncMock()

    from bot.db.repositories.chat_repo import ChatRepo

    repo = ChatRepo(mock_session)
    await repo.toggle_destination(chat_id=100, enabled=True)

    mock_session.execute.assert_called_once()
    mock_session.commit.assert_called_once()


# ── Mute/unmute premium gating ───────────────────────────────────────


@pytest.mark.asyncio
async def test_mute_requires_premium_when_expired(fake_redis):
    """Mute should be blocked when the user's trial has expired and they have no subscription."""
    from bot.services.subscription import is_premium

    registered = datetime.now(timezone.utc) - timedelta(days=60)

    with patch("bot.services.subscription.async_session") as mock_session_maker:
        mock_session = AsyncMock()
        mock_session_maker.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_maker.return_value.__aexit__ = AsyncMock(return_value=False)

        with patch("bot.services.subscription.SubscriptionRepo") as MockRepo:
            MockRepo.return_value.get_active_subscription = AsyncMock(return_value=None)
            result = await is_premium(fake_redis, 5000, registered)

    assert result is False


@pytest.mark.asyncio
async def test_mute_allowed_during_trial(fake_redis):
    """Mute should be allowed when the user is in their trial period."""
    from bot.services.subscription import is_premium

    registered = datetime.now(timezone.utc) - timedelta(days=5)
    result = await is_premium(fake_redis, 5001, registered)
    assert result is True


@pytest.mark.asyncio
async def test_mute_allowed_for_admin(fake_redis):
    """Mute should always be allowed for admin users."""
    from bot.services.subscription import is_premium

    registered = datetime.now(timezone.utc) - timedelta(days=60)

    with patch("bot.services.subscription.settings") as mock_settings:
        mock_settings.admin_ids = {6000}
        mock_settings.TRIAL_DAYS = 30
        result = await is_premium(fake_redis, 6000, registered)

    assert result is True
