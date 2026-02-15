"""Tests for keyboard builders and callback data conventions."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.services.keyboards import (
    build_admin_panel,
    build_ban_confirm,
    build_broadcast_panel,
    build_chat_detail,
    build_chat_list_nav,
    build_edits_panel,
    build_grant_plans,
    build_help_back,
    build_help_menu,
    build_main_menu,
    build_moderation_actions,
    build_mute_presets,
    build_pause_feedback,
    build_plan_active_actions,
    build_plan_trial_actions,
    build_remove_confirm,
    build_resume_feedback,
    build_revoke_confirm,
    build_selfsend_result,
    build_settings_panel,
    build_status_actions,
    build_stop_confirm,
    build_unban_undo,
    build_unmute_undo,
)


# ── Helpers ──────────────────────────────────────────────────────────


def _all_buttons(kb):
    """Flatten all buttons from an InlineKeyboardMarkup."""
    return [btn for row in kb.inline_keyboard for btn in row]


def _all_callback_data(kb):
    """Get all callback_data values from a keyboard."""
    return [btn.callback_data for btn in _all_buttons(kb) if btn.callback_data]


# ── Main menu ────────────────────────────────────────────────────────


def test_main_menu_has_three_buttons():
    kb = build_main_menu()
    buttons = _all_buttons(kb)
    assert len(buttons) == 3


def test_main_menu_callbacks():
    kb = build_main_menu()
    data = _all_callback_data(kb)
    assert "settings" in data
    assert "myplan" in data
    assert "sub:show" in data


# ── Settings panel ───────────────────────────────────────────────────


def test_settings_panel_selfsend_on():
    kb = build_settings_panel(is_self_send=True, is_source=True, is_destination=True)
    data = _all_callback_data(kb)
    assert "ss:0" in data  # toggle to off


def test_settings_panel_selfsend_off():
    kb = build_settings_panel(is_self_send=False, is_source=True, is_destination=True)
    data = _all_callback_data(kb)
    assert "ss:1" in data  # toggle to on


# ── Stop confirm ─────────────────────────────────────────────────────


def test_stop_confirm_has_two_buttons():
    kb = build_stop_confirm()
    buttons = _all_buttons(kb)
    assert len(buttons) == 2
    data = _all_callback_data(kb)
    assert "stop:y" in data
    assert "noop" in data


# ── Self-send result ─────────────────────────────────────────────────


def test_selfsend_result_when_enabled():
    kb = build_selfsend_result(is_self_send=True)
    data = _all_callback_data(kb)
    assert "ss:0" in data  # offer to turn off
    assert "settings" in data


def test_selfsend_result_when_disabled():
    kb = build_selfsend_result(is_self_send=False)
    data = _all_callback_data(kb)
    assert "ss:1" in data  # offer to turn on


# ── Broadcast panel ──────────────────────────────────────────────────


def test_broadcast_panel_both_on():
    kb = build_broadcast_panel(is_source=True, is_destination=True)
    data = _all_callback_data(kb)
    assert "bc:0o" in data  # pause outgoing
    assert "bc:0i" in data  # pause incoming


def test_broadcast_panel_both_paused():
    kb = build_broadcast_panel(is_source=False, is_destination=False)
    data = _all_callback_data(kb)
    assert "bc:1o" in data  # resume outgoing
    assert "bc:1i" in data  # resume incoming


# ── Status actions ───────────────────────────────────────────────────


def test_status_actions_when_running():
    kb = build_status_actions(is_paused=False, edit_mode="off", sig_enabled=True)
    data = _all_callback_data(kb)
    assert "ap:pause" in data
    assert "ls:1" in data


def test_status_actions_when_paused():
    kb = build_status_actions(is_paused=True, edit_mode="resend", sig_enabled=True)
    data = _all_callback_data(kb)
    assert "ap:resume" in data


# ── Chat list navigation ────────────────────────────────────────────


def test_chat_list_nav_first_page():
    kb = build_chat_list_nav(page=1, total_pages=3)
    data = _all_callback_data(kb)
    assert "ls:2" in data  # next
    # No prev on first page
    assert "ls:0" not in data


def test_chat_list_nav_middle_page():
    kb = build_chat_list_nav(page=2, total_pages=3)
    data = _all_callback_data(kb)
    assert "ls:1" in data  # prev
    assert "ls:3" in data  # next


def test_chat_list_nav_last_page():
    kb = build_chat_list_nav(page=3, total_pages=3)
    data = _all_callback_data(kb)
    assert "ls:2" in data  # prev
    assert "ls:4" not in data  # no next


def test_chat_list_nav_single_page():
    kb = build_chat_list_nav(page=1, total_pages=1)
    data = _all_callback_data(kb)
    # Only noop (page label) and status button
    assert "noop" in data
    assert "ap:status" in data


# ── Chat detail ──────────────────────────────────────────────────────


def test_chat_detail_has_actions():
    kb = build_chat_detail(chat_id=12345)
    data = _all_callback_data(kb)
    assert "rm:12345" in data
    assert "gr:12345" in data
    assert "rv:12345" in data
    assert "ls:1" in data  # back


# ── Remove confirm ───────────────────────────────────────────────────


def test_remove_confirm():
    kb = build_remove_confirm(chat_id=99999)
    data = _all_callback_data(kb)
    assert "rmy:99999" in data
    assert "noop" in data


# ── Grant plans ──────────────────────────────────────────────────────


def test_grant_plans_has_three_plans():
    kb = build_grant_plans(chat_id=12345)
    data = _all_callback_data(kb)
    assert "gp:week:12345" in data
    assert "gp:month:12345" in data
    assert "gp:year:12345" in data


# ── Revoke confirm ───────────────────────────────────────────────────


def test_revoke_confirm():
    kb = build_revoke_confirm(chat_id=12345)
    data = _all_callback_data(kb)
    assert "rvy:12345" in data
    assert "noop" in data


# ── Mute presets ─────────────────────────────────────────────────────


def test_mute_presets_has_four_durations():
    kb = build_mute_presets(user_id=42)
    data = _all_callback_data(kb)
    assert "mu:42:30m" in data
    assert "mu:42:2h" in data
    assert "mu:42:1d" in data
    assert "mu:42:7d" in data


# ── Ban confirm ──────────────────────────────────────────────────────


def test_ban_confirm():
    kb = build_ban_confirm(user_id=42)
    data = _all_callback_data(kb)
    assert "byd:42" in data  # ban + delete
    assert "byn:42" in data  # ban only
    assert "noop" in data


# ── Moderation actions ───────────────────────────────────────────────


def test_moderation_actions_no_restriction():
    kb = build_moderation_actions(user_id=42, has_restriction=False)
    data = _all_callback_data(kb)
    assert "md:42" in data  # mute menu
    assert "bn:42" in data  # ban prompt


def test_moderation_actions_with_restriction():
    kb = build_moderation_actions(user_id=42, has_restriction=True)
    data = _all_callback_data(kb)
    assert "um:42" in data  # unmute
    assert "ub:42" in data  # unban


# ── Unmute/unban undo ────────────────────────────────────────────────


def test_unmute_undo():
    kb = build_unmute_undo(user_id=42)
    data = _all_callback_data(kb)
    assert "mu:42:1h" in data
    assert "mu:42:1d" in data


def test_unban_undo():
    kb = build_unban_undo(user_id=42)
    data = _all_callback_data(kb)
    assert "bn:42" in data


# ── Edits panel ──────────────────────────────────────────────────────


def test_edits_panel_off():
    kb = build_edits_panel("off")
    data = _all_callback_data(kb)
    assert "ap:e:res" in data  # switch to resend


def test_edits_panel_resend():
    kb = build_edits_panel("resend")
    data = _all_callback_data(kb)
    assert "ap:e:off" in data  # switch to off


# ── Pause/resume feedback ───────────────────────────────────────────


def test_pause_feedback():
    kb = build_pause_feedback()
    data = _all_callback_data(kb)
    assert "ap:resume" in data
    assert "ap:status" in data


def test_resume_feedback():
    kb = build_resume_feedback()
    data = _all_callback_data(kb)
    assert "ap:pause" in data
    assert "ap:status" in data


# ── Admin panel ──────────────────────────────────────────────────────


def test_admin_panel():
    kb = build_admin_panel()
    data = _all_callback_data(kb)
    assert "ap:status" in data
    assert "ls:1" in data


# ── Plan actions ─────────────────────────────────────────────────────


def test_plan_active_actions():
    kb = build_plan_active_actions()
    data = _all_callback_data(kb)
    assert "bc:panel" in data
    assert "settings" in data


def test_plan_trial_actions():
    kb = build_plan_trial_actions()
    data = _all_callback_data(kb)
    assert "bc:panel" in data
    assert "sub:show" in data


# ── Help menu ────────────────────────────────────────────────────────


def test_help_menu_regular_user():
    kb = build_help_menu(is_admin=False)
    data = _all_callback_data(kb)
    assert "help:how" in data
    assert "help:prem" in data
    assert "help:admin" not in data


def test_help_menu_admin():
    kb = build_help_menu(is_admin=True)
    data = _all_callback_data(kb)
    assert "help:how" in data
    assert "help:prem" in data
    assert "help:admin" in data


def test_help_back_regular_user():
    kb = build_help_back(is_admin=False)
    data = _all_callback_data(kb)
    assert "help:back" in data
    assert "help:admin" not in data


def test_help_back_admin():
    kb = build_help_back(is_admin=True)
    data = _all_callback_data(kb)
    assert "help:back" in data
    assert "help:admin" in data


# ── Callback data length ────────────────────────────────────────────


def test_all_callback_data_within_64_bytes():
    """Telegram limits callback_data to 64 bytes. Verify all our keyboards comply."""
    keyboards = [
        build_main_menu(),
        build_settings_panel(True, True, True),
        build_stop_confirm(),
        build_selfsend_result(True),
        build_broadcast_panel(True, True),
        build_status_actions(False, "off", True),
        build_chat_list_nav(1, 5),
        build_chat_detail(9999999999999),  # large chat ID
        build_remove_confirm(9999999999999),
        build_grant_plans(9999999999999),
        build_revoke_confirm(9999999999999),
        build_mute_presets(9999999999999),
        build_ban_confirm(9999999999999),
        build_moderation_actions(9999999999999, False),
        build_unmute_undo(9999999999999),
        build_unban_undo(9999999999999),
        build_edits_panel("off"),
        build_pause_feedback(),
        build_resume_feedback(),
        build_admin_panel(),
        build_plan_active_actions(),
        build_plan_trial_actions(),
        build_help_menu(True),
        build_help_menu(False),
        build_help_back(True),
        build_help_back(False),
    ]
    for kb in keyboards:
        for data in _all_callback_data(kb):
            assert len(data.encode("utf-8")) <= 64, f"Too long ({len(data.encode())}B): {data}"
