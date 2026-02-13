"""Tests for /stats repository counting methods and stats keyboard builder."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.db.repositories.chat_repo import ChatRepo
from bot.db.repositories.restriction_repo import RestrictionRepo
from bot.db.repositories.send_log_repo import SendLogRepo
from bot.db.repositories.subscription_repo import SubscriptionRepo
from bot.services.keyboards import build_stats_actions


# ── Helpers ──────────────────────────────────────────────────────────


def _all_callback_data(kb):
    return [btn.callback_data for row in kb.inline_keyboard for btn in row if btn.callback_data]


# ── SendLogRepo counting ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_count_messages_from_chat():
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one.return_value = 42
    mock_session.execute = AsyncMock(return_value=mock_result)

    repo = SendLogRepo(mock_session)
    count = await repo.count_messages_from_chat(100)

    assert count == 42
    mock_session.execute.assert_called_once()


@pytest.mark.asyncio
async def test_count_messages_to_chat():
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one.return_value = 187
    mock_session.execute = AsyncMock(return_value=mock_result)

    repo = SendLogRepo(mock_session)
    count = await repo.count_messages_to_chat(200)

    assert count == 187


@pytest.mark.asyncio
async def test_count_total_distributed():
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one.return_value = 3241
    mock_session.execute = AsyncMock(return_value=mock_result)

    repo = SendLogRepo(mock_session)
    count = await repo.count_total_distributed()

    assert count == 3241


@pytest.mark.asyncio
async def test_count_unique_senders():
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one.return_value = 34
    mock_session.execute = AsyncMock(return_value=mock_result)

    repo = SendLogRepo(mock_session)
    count = await repo.count_unique_senders()

    assert count == 34


# ── ChatRepo counting ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_count_by_type():
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.all.return_value = [
        ("private", 12),
        ("group", 5),
        ("supergroup", 20),
        ("channel", 3),
    ]
    mock_session.execute = AsyncMock(return_value=mock_result)

    repo = ChatRepo(mock_session)
    result = await repo.count_by_type()

    assert result == {"private": 12, "group": 5, "supergroup": 20, "channel": 3}


@pytest.mark.asyncio
async def test_count_by_type_empty():
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.all.return_value = []
    mock_session.execute = AsyncMock(return_value=mock_result)

    repo = ChatRepo(mock_session)
    result = await repo.count_by_type()

    assert result == {}


@pytest.mark.asyncio
async def test_count_sources():
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one.return_value = 40
    mock_session.execute = AsyncMock(return_value=mock_result)

    repo = ChatRepo(mock_session)
    count = await repo.count_sources()

    assert count == 40


@pytest.mark.asyncio
async def test_count_destinations():
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one.return_value = 45
    mock_session.execute = AsyncMock(return_value=mock_result)

    repo = ChatRepo(mock_session)
    count = await repo.count_destinations()

    assert count == 45


# ── SubscriptionRepo counting ───────────────────────────────────────


@pytest.mark.asyncio
async def test_count_subscription_breakdown():
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.all.return_value = [
        ("week", 3),
        ("month", 15),
        ("year", 2),
    ]
    mock_session.execute = AsyncMock(return_value=mock_result)

    repo = SubscriptionRepo(mock_session)
    result = await repo.count_subscription_breakdown()

    assert result == {"week": 3, "month": 15, "year": 2}


@pytest.mark.asyncio
async def test_count_subscription_breakdown_empty():
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.all.return_value = []
    mock_session.execute = AsyncMock(return_value=mock_result)

    repo = SubscriptionRepo(mock_session)
    result = await repo.count_subscription_breakdown()

    assert result == {}


# ── RestrictionRepo counting ────────────────────────────────────────


@pytest.mark.asyncio
async def test_count_active_restrictions():
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.all.return_value = [
        ("mute", 2),
        ("ban", 5),
    ]
    mock_session.execute = AsyncMock(return_value=mock_result)

    repo = RestrictionRepo(mock_session)
    result = await repo.count_active_restrictions()

    assert result == {"mute": 2, "ban": 5}


@pytest.mark.asyncio
async def test_count_active_restrictions_none():
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.all.return_value = []
    mock_session.execute = AsyncMock(return_value=mock_result)

    repo = RestrictionRepo(mock_session)
    result = await repo.count_active_restrictions()

    assert result == {}


# ── Stats keyboard builder ──────────────────────────────────────────


def test_stats_actions_regular_user():
    kb = build_stats_actions(is_admin=False)
    data = _all_callback_data(kb)
    assert "settings" in data
    assert "myplan" in data
    # No admin status button
    assert "ap:status" not in data


def test_stats_actions_admin():
    kb = build_stats_actions(is_admin=True)
    data = _all_callback_data(kb)
    assert "settings" in data
    assert "myplan" in data
    assert "ap:status" in data


def test_stats_actions_admin_has_extra_row():
    kb_user = build_stats_actions(is_admin=False)
    kb_admin = build_stats_actions(is_admin=True)
    assert len(kb_admin.inline_keyboard) == len(kb_user.inline_keyboard) + 1
