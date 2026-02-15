"""Centralized inline keyboard builders for every command interaction."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


# ── Helpers ──────────────────────────────────────────────────────────

def _btn(text: str, data: str) -> InlineKeyboardButton:
    """Shortcut to create a callback button."""
    return InlineKeyboardButton(text=text, callback_data=data)


# ── User: Main menu (shown after /start) ────────────────────────────

def build_main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn("⚙️ Settings", "settings"), _btn("📋 My Plan", "myplan"), _btn("⭐ Subscribe", "sub:show")],
    ])


# ── User: Settings panel ────────────────────────────────────────────

def build_settings_panel(
    is_self_send: bool,
    is_source: bool,
    is_destination: bool,
) -> InlineKeyboardMarkup:
    ss_label = "🔄 Self-send: ON" if is_self_send else "🔄 Self-send: OFF"
    ss_data = "ss:0" if is_self_send else "ss:1"

    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn(ss_label, ss_data)],
        [_btn("📡 Broadcast Control", "bc:panel")],
        [_btn("📋 My Plan", "myplan"), _btn("⭐ Subscribe", "sub:show")],
    ])


# ── User: Stop confirmation ─────────────────────────────────────────

def build_stop_confirm() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn("🛑 Yes, unregister", "stop:y"), _btn("Cancel", "noop")],
    ])


# ── User: Self-send toggle result ───────────────────────────────────

def build_selfsend_result(is_self_send: bool) -> InlineKeyboardMarkup:
    label = "Turn Off" if is_self_send else "Turn On"
    data = "ss:0" if is_self_send else "ss:1"
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn(label, data), _btn("⬅️ Settings", "settings")],
    ])


# ── User: Broadcast control panel ───────────────────────────────────

def build_broadcast_panel(is_source: bool, is_destination: bool) -> InlineKeyboardMarkup:
    out_label = "⏸ Pause Outgoing" if is_source else "▶️ Resume Outgoing"
    out_data = "bc:0o" if is_source else "bc:1o"
    in_label = "⏸ Pause Incoming" if is_destination else "▶️ Resume Incoming"
    in_data = "bc:0i" if is_destination else "bc:1i"

    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn(out_label, out_data)],
        [_btn(in_label, in_data)],
        [_btn("⬅️ Settings", "settings")],
    ])


# ── Admin: Status action buttons ────────────────────────────────────

def build_status_actions(
    is_paused: bool,
    edit_mode: str,
    sig_enabled: bool,
) -> InlineKeyboardMarkup:
    pause_btn = _btn("▶️ Resume", "ap:resume") if is_paused else _btn("⏸ Pause", "ap:pause")
    edit_label = f"📝 Edits: {edit_mode.upper()}"
    edit_data = "ap:e:res" if edit_mode == "off" else "ap:e:off"
    sig_btn = _btn("✏️ Sig: OFF", "ap:soff") if sig_enabled else _btn("✏️ Sig: (disabled)", "noop")

    return InlineKeyboardMarkup(inline_keyboard=[
        [pause_btn, _btn(edit_label, edit_data)],
        [sig_btn, _btn("📋 Chat List", "ls:1")],
    ])


# ── Admin: Chat list pagination ─────────────────────────────────────

def build_chat_list_nav(page: int, total_pages: int) -> InlineKeyboardMarkup:
    buttons: list[InlineKeyboardButton] = []
    if page > 1:
        buttons.append(_btn("« Prev", f"ls:{page - 1}"))
    buttons.append(_btn(f"Page {page}/{total_pages}", "noop"))
    if page < total_pages:
        buttons.append(_btn("Next »", f"ls:{page + 1}"))

    return InlineKeyboardMarkup(inline_keyboard=[
        buttons,
        [_btn("📊 Status", "ap:status")],
    ])


# ── Admin: Chat detail / actions ────────────────────────────────────

def build_chat_detail(chat_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            _btn("🗑 Remove", f"rm:{chat_id}"),
            _btn("🎁 Grant Sub", f"gr:{chat_id}"),
            _btn("🚫 Revoke Sub", f"rv:{chat_id}"),
        ],
        [_btn("⬅️ Back to List", "ls:1")],
    ])


# ── Admin: Remove confirmation ──────────────────────────────────────

def build_remove_confirm(chat_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn("✅ Confirm Remove", f"rmy:{chat_id}"), _btn("Cancel", "noop")],
    ])


# ── Admin: Grant plan picker ────────────────────────────────────────

def build_grant_plans(chat_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            _btn("⏱ 1 Week", f"gp:week:{chat_id}"),
            _btn("🔥 1 Month", f"gp:month:{chat_id}"),
            _btn("📅 1 Year", f"gp:year:{chat_id}"),
        ],
        [_btn("Cancel", "noop")],
    ])


# ── Admin: Revoke confirmation ──────────────────────────────────────

def build_revoke_confirm(chat_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn("✅ Confirm Revoke", f"rvy:{chat_id}"), _btn("Cancel", "noop")],
    ])


# ── Admin: Pause / Resume feedback ──────────────────────────────────

def build_pause_feedback() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn("▶️ Resume", "ap:resume"), _btn("📊 Status", "ap:status")],
    ])


def build_resume_feedback() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn("⏸ Pause", "ap:pause"), _btn("📊 Status", "ap:status")],
    ])


# ── Admin: Edits toggle panel ───────────────────────────────────────

def build_edits_panel(current_mode: str) -> InlineKeyboardMarkup:
    if current_mode == "off":
        return InlineKeyboardMarkup(inline_keyboard=[
            [_btn("Switch to Resend", "ap:e:res")],
            [_btn("📊 Status", "ap:status")],
        ])
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn("Switch to Off", "ap:e:off")],
        [_btn("📊 Status", "ap:status")],
    ])


# ── Admin: Mute duration presets ────────────────────────────────────

def build_mute_presets(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            _btn("30 min", f"mu:{user_id}:30m"),
            _btn("2 hours", f"mu:{user_id}:2h"),
        ],
        [
            _btn("1 day", f"mu:{user_id}:1d"),
            _btn("7 days", f"mu:{user_id}:7d"),
        ],
        [_btn("Cancel", "noop")],
    ])


# ── Admin: Ban confirmation ─────────────────────────────────────────

def build_ban_confirm(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn("⛔ Ban + Delete Messages", f"byd:{user_id}")],
        [_btn("⛔ Ban Only", f"byn:{user_id}")],
        [_btn("Cancel", "noop")],
    ])


# ── Admin: Moderation actions (after /whois) ────────────────────────

def build_moderation_actions(user_id: int, has_restriction: bool) -> InlineKeyboardMarkup:
    if has_restriction:
        return InlineKeyboardMarkup(inline_keyboard=[
            [_btn("🔊 Unmute", f"um:{user_id}"), _btn("✅ Unban", f"ub:{user_id}")],
        ])
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn("🔇 Mute", f"md:{user_id}"), _btn("⛔ Ban", f"bn:{user_id}")],
    ])


# ── Admin: Post-unmute undo ─────────────────────────────────────────

def build_unmute_undo(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn("Re-mute (1h)", f"mu:{user_id}:1h"), _btn("Re-mute (1d)", f"mu:{user_id}:1d")],
    ])


# ── Admin: Post-unban undo ──────────────────────────────────────────

def build_unban_undo(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn("Re-ban", f"bn:{user_id}")],
    ])


# ── Admin: Admin panel ──────────────────────────────────────────────

def build_admin_panel() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn("📊 Status", "ap:status"), _btn("📋 Chat List", "ls:1")],
    ])


# ── User: Plan contextual buttons ───────────────────────────────────

def build_plan_active_actions() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn("📡 Broadcast Control", "bc:panel"), _btn("⚙️ Settings", "settings")],
    ])


def build_plan_trial_actions() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn("📡 Broadcast Control", "bc:panel"), _btn("⚙️ Settings", "settings")],
        [_btn("⭐ View Plans", "sub:show")],
    ])


# ── User/Admin: Help menu ─────────────────────────────────────────────

def build_help_menu(is_admin: bool) -> InlineKeyboardMarkup:
    rows = [
        [_btn("💡 How it works", "help:how"), _btn("⭐ About Premium", "help:prem")],
    ]
    if is_admin:
        rows.append([_btn("🛠 Admin Guide", "help:admin")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_help_back(is_admin: bool) -> InlineKeyboardMarkup:
    rows = [[_btn("⬅️ Back to Help", "help:back")]]
    if is_admin:
        rows[0].append(_btn("🛠 Admin Guide", "help:admin"))
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ── User/Admin: Stats contextual buttons ────────────────────────────

def build_stats_actions(is_admin: bool) -> InlineKeyboardMarkup:
    row = [_btn("⚙️ Settings", "settings"), _btn("📋 My Plan", "myplan")]
    rows = [row]
    if is_admin:
        rows.append([_btn("📊 Status Dashboard", "ap:status")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
