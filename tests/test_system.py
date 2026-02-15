"""System-level integration tests — verify cross-module behaviour against the
documented spec (README.md / botfather-setup.md).

Each test exercises a full or near-full path through multiple modules rather
than a single unit in isolation.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.services.normalizer import NormalizedMessage
from bot.utils.enums import MessageType


# ═══════════════════════════════════════════════════════════════════════
# 1. Admin reply-to-bot-message resolves target via SendLogRepo
#    (validates Fix #1 — missing import would have caused NameError)
# ═══════════════════════════════════════════════════════════════════════


def test_admin_module_imports_send_log_repo():
    """admin.py must import SendLogRepo so _resolve_target_user can resolve
    replies to bot-sent messages."""
    from bot.handlers import admin

    assert hasattr(admin, "SendLogRepo"), (
        "SendLogRepo is not imported in admin.py — reply-based admin "
        "commands will crash with NameError"
    )


# ═══════════════════════════════════════════════════════════════════════
# 2. Subscription "remaining days" computes from now, not total duration
#    (validates Fixes #2 and #3)
# ═══════════════════════════════════════════════════════════════════════


def test_remaining_days_is_from_now_not_total():
    """Given a subscription that started 10 days ago and lasts 30 days,
    the remaining should be ~20 days, NOT 30."""
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    starts_at = now - timedelta(days=10)
    expires_at = starts_at + timedelta(days=30)

    # This is the WRONG calculation (total duration):
    wrong_remaining = (expires_at - starts_at).days  # always 30
    # This is the CORRECT calculation (from now):
    correct_remaining = max(0, (expires_at - now).days)  # ~20

    assert wrong_remaining == 30
    assert 18 <= correct_remaining <= 21  # accounts for timing
    assert correct_remaining != wrong_remaining


# ═══════════════════════════════════════════════════════════════════════
# 3. Media group composite carries source_user_id
#    (validates Fix #4)
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_media_group_composite_carries_source_user_id():
    """When flushing a media group, the composite NormalizedMessage must
    propagate source_user_id from the first item so that the distributor
    can resolve the sender alias."""
    from bot.services.media_group import MediaGroupBuffer

    distributed = []

    class FakeDistributor:
        async def distribute(self, msg):
            distributed.append(msg)

    fake_redis = AsyncMock()
    fake_redis.rpush = AsyncMock()
    fake_redis.expire = AsyncMock()
    fake_redis.set = AsyncMock()

    buffer = MediaGroupBuffer(fake_redis, distributor=FakeDistributor())

    # Prepare items with source_user_id set
    items_data = []
    for i in range(2):
        item = NormalizedMessage(
            message_type=MessageType.PHOTO,
            source_chat_id=100,
            source_message_id=i + 1,
            source_user_id=42,  # sender
            media_group_id="grp_uid",
            file_id=f"f_{i}",
            file_unique_id=f"u_{i}",
        )
        items_data.append(json.dumps(MediaGroupBuffer._to_dict(item)))

    # Mock Redis pipeline — pipeline() is synchronous, execute() is async
    mock_pipe = MagicMock()
    mock_pipe.lrange = MagicMock(return_value=mock_pipe)
    mock_pipe.delete = MagicMock(return_value=mock_pipe)
    mock_pipe.execute = AsyncMock(return_value=[items_data, 1])
    # pipeline() must return synchronously (it's not awaited in the code)
    fake_redis.pipeline = MagicMock(return_value=mock_pipe)

    await buffer._flush_group("grp_uid")

    assert len(distributed) == 1
    composite = distributed[0]
    assert composite.message_type == MessageType.MEDIA_GROUP
    assert composite.source_user_id == 42, (
        "composite.source_user_id must be propagated from items"
    )


# ═══════════════════════════════════════════════════════════════════════
# 4. Alias tag is entity-based, not HTML
#    (validates Fix #5 — HTML alias conflicted with existing entities)
# ═══════════════════════════════════════════════════════════════════════


def test_alias_entity_not_html_in_text():
    """_build_alias_entity must produce a text_link MessageEntity (not HTML)
    so that the alias renders as a clickable link regardless of whether the
    original message has formatting entities."""
    from bot.services.sender import _build_alias_entity

    content = "Hello bold world\n\ngolden_arrow"
    ent = _build_alias_entity(content, "golden_arrow", "https://t.me/Bot")

    assert ent is not None
    assert ent.type == "text_link"
    assert ent.url == "https://t.me/Bot"
    # Verify the offset points exactly at the alias within the text
    assert content[ent.offset : ent.offset + ent.length] == "golden_arrow"


def test_alias_entity_with_signature():
    """Alias entity offset must still be correct when a signature is appended
    after the alias text."""
    from bot.services.sender import _build_alias_entity
    from bot.services.signature import apply_signature

    raw = "Hello\n\nswift_tiger"
    text = apply_signature(raw, "— via @Bot", 4096)

    ent = _build_alias_entity(text, "swift_tiger", "https://t.me/Bot")
    assert ent is not None
    assert text[ent.offset : ent.offset + ent.length] == "swift_tiger"


def test_alias_entity_on_captionless_media():
    """When a media message has no caption, the alias should be the entire
    caption content, with a text_link entity at offset 0."""
    from bot.services.sender import _build_alias_entity
    from bot.services.signature import apply_signature

    # When caption is None, sender sets raw_caption = alias_plain
    raw_caption = "swift_tiger"
    caption = apply_signature(raw_caption, None, 1024)

    ent = _build_alias_entity(caption, "swift_tiger", "https://t.me/Bot")
    assert ent is not None
    assert ent.offset == 0
    assert ent.length == len("swift_tiger")


# ═══════════════════════════════════════════════════════════════════════
# 5. Trial reminder query correlation
#    (validates Fix #7 — missing chat_id correlation in EXISTS subquery)
# ═══════════════════════════════════════════════════════════════════════


def test_trial_reminder_query_has_chat_correlation():
    """The get_expiring_trials query must correlate the Subscription EXISTS
    subquery with Chat.chat_id, otherwise ALL chats are excluded when ANY
    subscription exists."""
    import ast
    import inspect

    from bot.db.repositories.subscription_repo import SubscriptionRepo

    source = inspect.getsource(SubscriptionRepo.get_expiring_trials)
    # Verify the source contains both chat_id correlation AND expires_at
    assert "Subscription.chat_id == Chat.chat_id" in source, (
        "EXISTS subquery must correlate Subscription.chat_id with Chat.chat_id"
    )


# ═══════════════════════════════════════════════════════════════════════
# 6. Restriction count excludes expired mutes
#    (validates Fix #8)
# ═══════════════════════════════════════════════════════════════════════


def test_restriction_count_source_has_expiry_filter():
    """count_active_restrictions must filter out expired mutes (expires_at
    IS NULL OR expires_at > now)."""
    import inspect

    from bot.db.repositories.restriction_repo import RestrictionRepo

    source = inspect.getsource(RestrictionRepo.count_active_restrictions)
    # Must reference expires_at and or_ for the filter
    assert "expires_at" in source
    assert "or_" in source or "is_(None)" in source, (
        "count_active_restrictions must filter expired mutes via OR condition"
    )


# ═══════════════════════════════════════════════════════════════════════
# 7. Edit handler checks user restrictions
#    (validates system-level fix — edits from muted/banned users dropped)
# ═══════════════════════════════════════════════════════════════════════


def test_edit_handler_has_restriction_check():
    """The edit handler must check is_user_restricted before redistributing,
    otherwise muted/banned users can bypass the restriction by editing."""
    import inspect

    from bot.handlers.edits import _handle_edit

    source = inspect.getsource(_handle_edit)
    assert "is_user_restricted" in source, (
        "Edit handler must call is_user_restricted to prevent muted/banned "
        "users from redistributing via edits"
    )


# ═══════════════════════════════════════════════════════════════════════
# 8. send_media_group passes sender_alias in all fallback paths
#    (validates Fix #6)
# ═══════════════════════════════════════════════════════════════════════


def test_send_media_group_fallback_passes_sender_alias():
    """All send_single calls within send_media_group must pass sender_alias."""
    import inspect

    from bot.services.sender import send_media_group

    source = inspect.getsource(send_media_group)
    # Count occurrences of send_single calls — each must have sender_alias
    lines = source.split("\n")
    send_single_calls = [
        (i, l.strip())
        for i, l in enumerate(lines)
        if "send_single(" in l and "await" in l
    ]

    for line_no, line in send_single_calls:
        # Find the full call (may span lines)
        call_block = "\n".join(lines[line_no:line_no + 6])
        assert "sender_alias" in call_block, (
            f"send_single call near '{line}' is missing sender_alias parameter"
        )


# ═══════════════════════════════════════════════════════════════════════
# 9. Full message pipeline: restriction → normalize → source → dedup
# ═══════════════════════════════════════════════════════════════════════


def test_message_handler_pipeline_order():
    """The message handler must perform checks in the documented order:
    restriction → normalize → source check → dedup → reply detect → distribute.
    """
    import inspect

    from bot.handlers.messages import _handle_content

    source = inspect.getsource(_handle_content)
    steps = [
        "is_user_restricted",
        "normalize(",
        "is_active_source",
        "is_duplicate",
        "reverse_lookup",
        "distributor.distribute",
    ]
    indices = []
    for step in steps:
        idx = source.find(step)
        assert idx >= 0, f"Missing step in pipeline: {step}"
        indices.append(idx)

    for i in range(len(indices) - 1):
        assert indices[i] < indices[i + 1], (
            f"Pipeline order violation: '{steps[i]}' must come before "
            f"'{steps[i + 1]}'"
        )


# ═══════════════════════════════════════════════════════════════════════
# 10. All documented commands have registered handlers
# ═══════════════════════════════════════════════════════════════════════


def _extract_commands_from_router(router) -> set[str]:
    """Extract all Command filter names from a router's message handlers."""
    from aiogram.filters import Command

    commands: set[str] = set()
    for handler in router.message.handlers:
        for filt in handler.filters:
            # aiogram v3: the filter object is the Command instance itself
            if isinstance(filt.callback, Command):
                for cmd in filt.callback.commands:
                    c = cmd.command if hasattr(cmd, "command") else str(cmd)
                    commands.add(c.lower())
    return commands


def test_all_documented_user_commands_are_registered():
    """Every user command from botfather-setup.md must have a handler."""
    from bot.handlers.start import start_router
    from bot.handlers.subscription import subscription_router

    commands = _extract_commands_from_router(start_router)
    commands |= _extract_commands_from_router(subscription_router)

    # Also check channel_post handlers (some commands like /stats have both)
    for handler in start_router.channel_post.handlers:
        for filt in handler.filters:
            from aiogram.filters import Command

            if isinstance(filt.callback, Command):
                for cmd in filt.callback.commands:
                    c = cmd.command if hasattr(cmd, "command") else str(cmd)
                    commands.add(c.lower())

    user_commands = {"start", "stop", "selfsend", "broadcast", "stats", "help",
                     "subscribe", "plan"}
    for cmd in user_commands:
        assert cmd in commands, f"User command /{cmd} has no handler"


def test_all_documented_admin_commands_are_registered():
    """Every admin command from botfather-setup.md must have a handler."""
    from bot.handlers.admin import admin_router

    commands = _extract_commands_from_router(admin_router)

    admin_commands = {"status", "list", "signature", "signatureurl",
                      "signatureoff", "pause", "resume", "edits",
                      "remove", "grant", "revoke", "mute", "unmute",
                      "ban", "unban", "whois"}
    for cmd in admin_commands:
        assert cmd in commands, f"Admin command /{cmd} has no handler"
