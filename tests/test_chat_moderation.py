"""Tests for the chat-level ban check (bot/services/moderation.py)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.services.moderation import (
    invalidate_chat_restriction_cache,
    is_chat_restricted,
)


def _patch_repo_returns(restriction):
    """Patch ChatRestrictionRepo.get_active_restriction to return ``restriction``."""
    return patch(
        "bot.services.moderation.ChatRestrictionRepo.get_active_restriction"
        if False  # placeholder for IDE; real path below
        else "bot.db.repositories.chat_restriction_repo.ChatRestrictionRepo.get_active_restriction",
        AsyncMock(return_value=restriction),
    )


def _patch_session_no_op():
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=AsyncMock())
    cm.__aexit__ = AsyncMock(return_value=None)
    # The moderation module imports async_session lazily inside the function,
    # so patch where it's actually resolved at call time.
    return patch("bot.db.engine.async_session", return_value=cm)


class TestIsChatRestricted:
    @pytest.mark.asyncio
    async def test_uncached_no_restriction(self, fake_redis):
        with _patch_session_no_op(), _patch_repo_returns(None):
            assert await is_chat_restricted(fake_redis, 100) is None
        # Cached as "none"
        assert fake_redis._store.get("chat_restrict:100") == "none"

    @pytest.mark.asyncio
    async def test_uncached_active_ban(self, fake_redis):
        restriction = MagicMock()
        restriction.restriction_type = "ban"
        with _patch_session_no_op(), _patch_repo_returns(restriction):
            assert await is_chat_restricted(fake_redis, 100) == "banned"
        assert fake_redis._store.get("chat_restrict:100") == "banned"

    @pytest.mark.asyncio
    async def test_cached_hit_skips_db(self, fake_redis):
        # Pre-seed cache
        fake_redis._store["chat_restrict:100"] = "banned"
        # Repo should NOT be consulted; assert via patch.assert_not_called
        with patch(
            "bot.db.repositories.chat_restriction_repo.ChatRestrictionRepo.get_active_restriction",
            AsyncMock(),
        ) as repo_call:
            result = await is_chat_restricted(fake_redis, 100)
            repo_call.assert_not_called()
        assert result == "banned"

    @pytest.mark.asyncio
    async def test_cached_none_returns_none(self, fake_redis):
        fake_redis._store["chat_restrict:100"] = "none"
        with patch(
            "bot.db.repositories.chat_restriction_repo.ChatRestrictionRepo.get_active_restriction",
            AsyncMock(),
        ) as repo_call:
            assert await is_chat_restricted(fake_redis, 100) is None
            repo_call.assert_not_called()


class TestInvalidateChatRestrictionCache:
    @pytest.mark.asyncio
    async def test_deletes_key(self, fake_redis):
        fake_redis._store["chat_restrict:100"] = "banned"
        await invalidate_chat_restriction_cache(fake_redis, 100)
        assert "chat_restrict:100" not in fake_redis._store
