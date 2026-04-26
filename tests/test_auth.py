"""Tests for bot/services/auth.py — admin gate for sensitive toggles."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.services.auth import caller_can_manage


def _msg(chat_type: str, user_id: int | None = 999, bot=None):
    m = MagicMock()
    m.chat = MagicMock()
    m.chat.id = 100
    m.chat.type = chat_type
    if user_id is None:
        m.from_user = None
    else:
        m.from_user = MagicMock()
        m.from_user.id = user_id
    m.bot = bot
    return m


def _bot_with_status(status: str):
    bot = MagicMock()
    member = MagicMock()
    member.status = status
    bot.get_chat_member = AsyncMock(return_value=member)
    return bot


class TestCallerCanManage:
    @pytest.mark.asyncio
    async def test_private_chat_always_passes(self):
        m = _msg("private", user_id=42)
        assert await caller_can_manage(m) is True

    @pytest.mark.asyncio
    async def test_group_admin_passes(self):
        bot = _bot_with_status("administrator")
        m = _msg("group", user_id=42, bot=bot)
        assert await caller_can_manage(m) is True

    @pytest.mark.asyncio
    async def test_group_creator_passes(self):
        bot = _bot_with_status("creator")
        m = _msg("group", user_id=42, bot=bot)
        assert await caller_can_manage(m) is True

    @pytest.mark.asyncio
    async def test_group_member_denied(self):
        bot = _bot_with_status("member")
        m = _msg("supergroup", user_id=42, bot=bot)
        assert await caller_can_manage(m) is False

    @pytest.mark.asyncio
    async def test_anonymous_admin_passes(self):
        # GroupAnonymousBot id — by definition only group admins can post anon
        m = _msg("group", user_id=1087968824)
        assert await caller_can_manage(m) is True

    @pytest.mark.asyncio
    async def test_channel_post_no_user_passes(self):
        # Channel posts often have from_user=None; only admins can post in channels
        m = _msg("channel", user_id=None)
        assert await caller_can_manage(m) is True

    @pytest.mark.asyncio
    async def test_api_failure_denies(self):
        bot = MagicMock()
        bot.get_chat_member = AsyncMock(side_effect=RuntimeError("api fail"))
        m = _msg("supergroup", user_id=42, bot=bot)
        assert await caller_can_manage(m) is False
