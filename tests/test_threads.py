"""Tests for the bot-rooted thread tracker (bot/services/threads.py)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from bot.services.threads import THREAD_TTL, is_in_bot_thread, mark_in_thread


def _patch_send_log_lookup(return_value):
    """Helper: patch SendLogRepo.reverse_lookup to return ``return_value``."""
    return patch(
        "bot.services.threads.SendLogRepo.reverse_lookup",
        AsyncMock(return_value=return_value),
    )


def _patch_session_no_op():
    """Patch async_session so the DB layer never actually runs."""
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=AsyncMock())
    cm.__aexit__ = AsyncMock(return_value=None)
    return patch("bot.services.threads.async_session", return_value=cm)


# ── mark_in_thread ──────────────────────────────────────────────────────────


class TestMarkInThread:
    @pytest.mark.asyncio
    async def test_adds_to_redis_set(self, fake_redis):
        # fake_redis doesn't ship with sadd/sismember; install minimal versions
        _install_set_ops(fake_redis)
        await mark_in_thread(fake_redis, 100, 42)
        assert fake_redis._store.get("thread:100") == {"42"}

    @pytest.mark.asyncio
    async def test_refreshes_ttl(self, fake_redis):
        _install_set_ops(fake_redis)
        with patch.object(fake_redis, "expire", AsyncMock(return_value=True)) as exp:
            await mark_in_thread(fake_redis, 100, 1)
            exp.assert_awaited()
            assert exp.call_args[0] == ("thread:100", THREAD_TTL)


# ── is_in_bot_thread ────────────────────────────────────────────────────────


class TestIsInBotThread:
    @pytest.mark.asyncio
    async def test_direct_reply_to_bot_message(self, fake_redis):
        """send_log reverse_lookup returning origin → True (root case)."""
        _install_set_ops(fake_redis)
        with _patch_session_no_op(), _patch_send_log_lookup((999, 5)):
            assert await is_in_bot_thread(fake_redis, 100, 7) is True

    @pytest.mark.asyncio
    async def test_known_thread_member_via_redis(self, fake_redis):
        """send_log misses but the target is in the thread set → True (chain case)."""
        _install_set_ops(fake_redis)
        await mark_in_thread(fake_redis, 100, 42)
        with _patch_session_no_op(), _patch_send_log_lookup(None):
            assert await is_in_bot_thread(fake_redis, 100, 42) is True

    @pytest.mark.asyncio
    async def test_not_in_thread(self, fake_redis):
        """Neither send_log nor Redis set knows the target → False (drop)."""
        _install_set_ops(fake_redis)
        with _patch_session_no_op(), _patch_send_log_lookup(None):
            assert await is_in_bot_thread(fake_redis, 100, 999) is False

    @pytest.mark.asyncio
    async def test_db_failure_falls_back_to_redis(self, fake_redis):
        """DB blip → still consult the Redis set rather than dropping outright."""
        _install_set_ops(fake_redis)
        await mark_in_thread(fake_redis, 100, 42)

        cm = AsyncMock()
        cm.__aenter__ = AsyncMock(side_effect=RuntimeError("db down"))
        cm.__aexit__ = AsyncMock(return_value=None)
        with patch("bot.services.threads.async_session", return_value=cm):
            assert await is_in_bot_thread(fake_redis, 100, 42) is True


# ── helpers ─────────────────────────────────────────────────────────────────


def _install_set_ops(fake_redis):
    """The fake_redis fixture in conftest doesn't model sadd/sismember; install
    minimal in-memory versions used by tests in this module."""

    async def _sadd(key, *members):
        if key not in fake_redis._store or not isinstance(fake_redis._store[key], set):
            fake_redis._store[key] = set()
        added = 0
        for m in members:
            if m not in fake_redis._store[key]:
                fake_redis._store[key].add(m)
                added += 1
        return added

    async def _sismember(key, member):
        if key not in fake_redis._store or not isinstance(fake_redis._store[key], set):
            return 0
        return 1 if member in fake_redis._store[key] else 0

    fake_redis.sadd = AsyncMock(side_effect=_sadd)
    fake_redis.sismember = AsyncMock(side_effect=_sismember)
