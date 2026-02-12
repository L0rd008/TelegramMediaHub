"""Tests for the rate limiter service."""

from __future__ import annotations

import time

import pytest

from bot.services.rate_limiter import RateLimiter


class TestCooldownValues:
    def test_private_chat_cooldown(self):
        rl = RateLimiter.__new__(RateLimiter)
        assert rl._get_cooldown("private") == 1.0

    def test_channel_cooldown(self):
        rl = RateLimiter.__new__(RateLimiter)
        assert rl._get_cooldown("channel") == 1.0

    def test_group_cooldown(self):
        rl = RateLimiter.__new__(RateLimiter)
        assert rl._get_cooldown("group") == 3.0

    def test_supergroup_cooldown(self):
        rl = RateLimiter.__new__(RateLimiter)
        assert rl._get_cooldown("supergroup") == 3.0


class TestCircuitBreaker:
    def test_success_resets_error_count(self, fake_redis):
        rl = RateLimiter(fake_redis, global_limit=25)
        rl._chat_errors[100] = 2
        rl.report_success(100)
        assert 100 not in rl._chat_errors

    def test_three_errors_pause_chat(self, fake_redis):
        rl = RateLimiter(fake_redis, global_limit=25)
        rl.report_error(100)
        rl.report_error(100)
        assert 100 not in rl._chat_paused_until
        rl.report_error(100)
        # After 3 errors, chat should be paused
        assert 100 in rl._chat_paused_until
        assert rl._chat_paused_until[100] > time.time()
        # Error count should reset after triggering pause
        assert rl._chat_errors.get(100, 0) == 0

    def test_five_429s_trigger_global_pause(self, fake_redis):
        rl = RateLimiter(fake_redis, global_limit=25)
        for _ in range(4):
            rl.report_429(5.0)
        assert rl._global_paused_until == 0
        rl.report_429(5.0)
        # After 5× 429 in <60s, global pause should activate
        assert rl._global_paused_until > time.time()

    def test_old_429s_pruned(self, fake_redis):
        rl = RateLimiter(fake_redis, global_limit=25)
        # Add old timestamps (>60s ago)
        old_time = time.time() - 120
        rl._global_429_timestamps = [old_time] * 4
        rl.report_429(5.0)
        # Old ones pruned, so only 1 recent → no global pause
        assert rl._global_paused_until == 0
        assert len(rl._global_429_timestamps) == 1

    def test_success_on_unknown_chat_is_noop(self, fake_redis):
        rl = RateLimiter(fake_redis, global_limit=25)
        rl.report_success(999)  # Should not raise
        assert 999 not in rl._chat_errors


class TestGlobalTokenBucket:
    @pytest.mark.asyncio
    async def test_acquire_global_token_succeeds(self, fake_redis):
        rl = RateLimiter(fake_redis, global_limit=25)
        # Pipeline returns [removed_count, current_count] – bucket is empty
        pipe = fake_redis.pipeline.return_value
        pipe.execute = pytest.importorskip("unittest.mock").AsyncMock(
            return_value=[0, 0]
        )
        await rl._acquire_global_token()
        # Verify a token was added to the sorted set
        assert fake_redis.zadd.called

    @pytest.mark.asyncio
    async def test_per_chat_cooldown_first_call_passes(self, fake_redis):
        rl = RateLimiter(fake_redis, global_limit=25)
        # First call for a chat → no previous send → passes immediately
        await rl._acquire_chat_cooldown(100, 1.0)
        assert fake_redis.set.called
