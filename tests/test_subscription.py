"""Tests for subscription service, cache, nudge, and trial logic."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.services.subscription import (
    PLANS,
    build_pricing_keyboard,
    build_pricing_text,
    build_subscribe_button,
    get_missed_today,
    get_trial_days_remaining,
    invalidate_cache,
    is_premium,
    record_missed,
    should_nudge,
)


# ── PLANS constant ────────────────────────────────────────────────────


def test_plans_have_correct_keys():
    assert set(PLANS.keys()) == {"week", "month", "year"}


def test_week_plan():
    assert PLANS["week"].stars == 250
    assert PLANS["week"].days == 7


def test_month_plan_is_best_value():
    assert PLANS["month"].stars == 750
    assert PLANS["month"].days == 30
    assert "BEST VALUE" in PLANS["month"].badge


def test_year_plan():
    assert PLANS["year"].stars == 10000
    assert PLANS["year"].days == 365


# ── get_trial_days_remaining ──────────────────────────────────────────


def test_trial_days_remaining_full():
    """A chat registered right now should have ~30 days left."""
    now = datetime.now(timezone.utc)
    remaining = get_trial_days_remaining(now)
    assert remaining == 30 or remaining == 29  # Boundary


def test_trial_days_remaining_expired():
    """A chat registered 60 days ago should have 0 days left."""
    old = datetime.now(timezone.utc) - timedelta(days=60)
    assert get_trial_days_remaining(old) == 0


def test_trial_days_remaining_partial():
    """A chat registered 20 days ago should have ~10 days left."""
    reg = datetime.now(timezone.utc) - timedelta(days=20)
    remaining = get_trial_days_remaining(reg)
    assert 9 <= remaining <= 10


def test_trial_days_remaining_naive_datetime():
    """Naive datetimes should be treated as UTC."""
    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    remaining = get_trial_days_remaining(now_naive)
    assert remaining >= 29


# ── is_premium (cached) ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_is_premium_in_trial(fake_redis):
    """A chat still in trial should be premium."""
    registered = datetime.now(timezone.utc) - timedelta(days=5)
    result = await is_premium(fake_redis, 100, registered)
    assert result is True

    # Should have cached the result
    cached = await fake_redis.get("sub:100")
    assert cached == "1"


@pytest.mark.asyncio
async def test_is_premium_trial_expired_no_sub(fake_redis):
    """A chat past trial with no subscription should NOT be premium."""
    registered = datetime.now(timezone.utc) - timedelta(days=60)

    with patch("bot.services.subscription.async_session") as mock_session_maker:
        mock_session = AsyncMock()
        mock_session_maker.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_maker.return_value.__aexit__ = AsyncMock(return_value=False)

        # Mock SubscriptionRepo.get_active_subscription to return None
        with patch(
            "bot.services.subscription.SubscriptionRepo"
        ) as MockRepo:
            MockRepo.return_value.get_active_subscription = AsyncMock(
                return_value=None
            )
            result = await is_premium(fake_redis, 200, registered)

    assert result is False
    cached = await fake_redis.get("sub:200")
    assert cached == "0"


@pytest.mark.asyncio
async def test_is_premium_cached_hit(fake_redis):
    """If the cache says '1', don't hit DB."""
    await fake_redis.set("sub:300", "1")
    registered = datetime.now(timezone.utc) - timedelta(days=60)
    result = await is_premium(fake_redis, 300, registered)
    assert result is True


@pytest.mark.asyncio
async def test_is_premium_cached_miss(fake_redis):
    """If the cache says '0', return False without hitting DB."""
    await fake_redis.set("sub:400", "0")
    registered = datetime.now(timezone.utc) - timedelta(days=60)
    result = await is_premium(fake_redis, 400, registered)
    assert result is False


# ── invalidate_cache ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_invalidate_cache(fake_redis):
    await fake_redis.set("sub:500", "1")
    await invalidate_cache(fake_redis, 500)
    assert await fake_redis.get("sub:500") is None


# ── record_missed / get_missed_today ──────────────────────────────────


@pytest.mark.asyncio
async def test_record_missed_increments(fake_redis):
    count1 = await record_missed(fake_redis, 600)
    assert count1 == 1

    count2 = await record_missed(fake_redis, 600)
    assert count2 == 2


@pytest.mark.asyncio
async def test_get_missed_today_zero(fake_redis):
    count = await get_missed_today(fake_redis, 700)
    assert count == 0


@pytest.mark.asyncio
async def test_get_missed_today_after_records(fake_redis):
    await record_missed(fake_redis, 800)
    await record_missed(fake_redis, 800)
    await record_missed(fake_redis, 800)
    count = await get_missed_today(fake_redis, 800)
    assert count == 3


# ── should_nudge ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_should_nudge_first_time(fake_redis):
    result = await should_nudge(fake_redis, 900)
    assert result is True


@pytest.mark.asyncio
async def test_should_nudge_second_time_blocked(fake_redis):
    await should_nudge(fake_redis, 1000)  # First time – sets key
    result = await should_nudge(fake_redis, 1000)  # Second time – blocked
    assert result is False


# ── Marketing text builders ──────────────────────────────────────────


def test_build_pricing_text_contains_plans():
    text = build_pricing_text()
    assert "250" in text
    assert "750" in text
    assert "10,000" in text
    assert "BEST VALUE" in text
    assert "Save" in text


def test_build_pricing_text_contains_features():
    """Pricing text should list the new premium features."""
    text = build_pricing_text()
    assert "Reply threading" in text
    assert "/broadcast" in text
    assert "Broadcast control" in text
    assert "Sender aliases" in text


def test_build_pricing_text_yearly_no_per_day():
    """Yearly plan should say 'No renewals for a full year' instead of per-day cost."""
    text = build_pricing_text()
    assert "No renewals for a full year" in text


def test_build_pricing_keyboard_has_three_buttons():
    kb = build_pricing_keyboard(123)
    assert len(kb.inline_keyboard) == 3
    # Check callback data includes chat_id
    for row in kb.inline_keyboard:
        assert "123" in row[0].callback_data


def test_build_subscribe_button():
    kb = build_subscribe_button()
    assert len(kb.inline_keyboard) == 1
    assert kb.inline_keyboard[0][0].callback_data == "sub:show"
