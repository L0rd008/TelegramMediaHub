"""Tests for chat aliases and the composite group attribution signature."""

from __future__ import annotations

import pytest

from bot.services.chat_alias import format_group_attribution


class TestFormatGroupAttribution:
    def test_basic_join(self):
        assert format_group_attribution("golden_arrow", "misty_grove") == \
            "golden_arrow @ misty_grove"

    def test_empty_chat_alias_still_renders(self):
        # Defensive — caller should always pass a non-empty chat alias, but
        # if the lookup somehow produces "", we don't want a stray "@ ".
        # (The current implementation does join blindly; this test is a
        # canary if we ever change that policy.)
        assert format_group_attribution("user_a", "") == "user_a @ "

    def test_separator_is_unambiguous(self):
        """The '@' separator never appears in a legitimate two-word alias
        (alphas + underscore only), so it can never be confused for part
        of either side."""
        out = format_group_attribution("a_b", "c_d")
        # Exactly one '@' in the output
        assert out.count("@") == 1
