"""Start/stop/selfsend/broadcast/stats handlers for all users."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from aiogram.enums import ParseMode
from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from bot.config import settings
from bot.db.engine import async_session
from bot.db.repositories.chat_repo import ChatRepo
from bot.db.repositories.restriction_repo import RestrictionRepo
from bot.db.repositories.send_log_repo import SendLogRepo
from bot.db.repositories.subscription_repo import SubscriptionRepo
from bot.services.keyboards import (
    build_broadcast_panel,
    build_help_menu,
    build_main_menu,
    build_selfsend_result,
    build_stats_actions,
    build_stop_confirm,
)
from bot.services.auth import caller_can_manage
from bot.services.subscription import (
    build_subscribe_button,
    get_trial_days_remaining,
    is_premium,
)


async def _enforce_admin_or_reply(message: Message) -> bool:
    """For sensitive toggles in groups/channels, require admin status.

    Returns True if the caller is allowed to proceed; False (and answers
    a friendly denial) otherwise.  Private chats always pass.
    """
    if await caller_can_manage(message):
        return True
    await message.answer(
        "Only chat <b>admins</b> can change this for the group. "
        "Ask an admin to run the command, or use it in your private chat "
        "with me to change your own settings.",
        parse_mode=ParseMode.HTML,
    )
    return False

logger = logging.getLogger(__name__)

start_router = Router(name="start")


@start_router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    """Register this chat as source+destination and show the onboarding guide.

    The guide is tailored to where ``/start`` was run:

    - In a private chat with the bot → personal-network onboarding (selfsend
      backup recipe, multi-chat fan-out, premium-lite pitch).
    - In a group / supergroup → group operator onboarding (text vs media
      relay rules, admin-only toggles, premium notes).
    - In a channel → channel operator onboarding (media-only relay rule).
    """
    chat = message.chat

    async with async_session() as session:
        repo = ChatRepo(session)
        await repo.upsert_chat(
            chat_id=chat.id,
            chat_type=chat.type,
            title=chat.title,
            username=chat.username,
        )

    logger.info("Chat registered via /start: %d (type=%s)", chat.id, chat.type)

    # Resolve the user's alias for the welcome message
    alias_line = ""
    if message.from_user:
        redis = _get_redis()
        if redis:
            from bot.services.alias import get_alias
            alias = await get_alias(redis, message.from_user.id)
            alias_line = (
                f"\n\n<i>Your tag is</i> <b>{alias}</b> — it appears next to "
                "your messages on the other side of the network so people "
                "know who sent what."
            )

    # Bot username for command-mention examples (e.g. /selfsend@MediaHub_Bot)
    me = await message.bot.get_me() if message.bot else None
    bot_at = f"@{me.username}" if me and me.username else ""

    if chat.type == "private":
        body = _onboarding_private(bot_at)
    elif chat.type in ("group", "supergroup"):
        body = _onboarding_group(bot_at)
    elif chat.type == "channel":
        body = _onboarding_channel(bot_at)
    else:
        body = _onboarding_private(bot_at)

    await message.answer(
        body + alias_line,
        reply_markup=build_main_menu(),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


# ── Onboarding bodies ──────────────────────────────────────────────────


def _onboarding_private(bot_at: str) -> str:
    """Onboarding for the user's direct chat with the bot."""
    from bot.services.value_prop import access_blurb_for_onboarding

    sel = f"/selfsend{bot_at}"
    bro = f"/broadcast{bot_at}"
    return (
        "👋 <b>You're connected.</b>\n\n"
        "I'm a relay bot — content you send me lands in <i>every other chat</i> "
        "I'm connected to, and content from those chats lands here. "
        "Messages arrive as originals, never as forwards.\n\n"
        "<b>What gets relayed</b>\n"
        "• <b>Private chat with me</b> (this chat): everything — text, photos, "
        "videos, audio, files, voice, stickers — both ways.\n"
        "• <b>Groups I'm in</b>: all media, plus any text that's part "
        "of a reply thread to one of my messages. Casual group chatter is "
        "ignored.\n"
        "• <b>Channels I'm in as admin</b>: media only.\n\n"
        "<b>Try it — backup recipe</b>\n"
        "1. Make a private group with just yourself (no other members; leave "
        "  <i>“restrict saving content”</i> OFF).\n"
        "2. Add me to that group; admin role is enough — no extra permissions.\n"
        f"3. In that group, run {sel} and turn it ON. Now anything you send "
        "  here also lands there as a backup.\n\n"
        "<b>Try it — network recipe</b>\n"
        "Add me to two or more chats and use them as a multi-room conversation. "
        "Photo-share with friends across one group, archive in another.\n\n"
        f"{access_blurb_for_onboarding()}\n\n"
        f"Useful commands: {bro} (sync direction), /stats (your activity), "
        "/help (full guide). Tap below to explore."
    )


def _onboarding_group(bot_at: str) -> str:
    """Onboarding when /start is run inside a group/supergroup."""
    sel = f"/selfsend{bot_at}"
    bro = f"/broadcast{bot_at}"
    return (
        "👋 <b>This group is connected.</b>\n\n"
        "I relay content between this group and all the other chats my owner "
        "is connected to. Here's how I behave in groups specifically:\n\n"
        "<b>What I relay from here</b>\n"
        "• Every photo, video, file, audio, voice note, sticker, animation — "
        "  always, regardless of who sent it.\n"
        "• Text <b>only</b> when it's part of a reply thread that started "
        "  with one of my messages. Casual group chat stays in the group.\n\n"
        "<b>What I relay into here</b>\n"
        "• Everything that lands on my private chat with my owner (and "
        "  anything from other connected chats) shows up here automatically — "
        "  while this chat's free month is active. After that, "
        "  outbound continues; inbound is a Premium feature. /plan shows "
        "  the current state any time.\n\n"
        "<b>Admin controls</b> (chat admins only)\n"
        f"• {bro} — pause/resume sending or receiving for this group.\n"
        f"• {sel} — also echo this group's messages back to itself "
        "  (off by default).\n"
        "• /stop — disconnect this group from the network.\n\n"
        "Don't enable <i>“restrict saving content”</i> on the group settings — "
        "Telegram blocks the relay when that's on."
    )


def _onboarding_channel(bot_at: str) -> str:
    """Onboarding when /start is run inside a channel."""
    bro = f"/broadcast{bot_at}"
    return (
        "📣 <b>This channel is connected.</b>\n\n"
        "I'll relay every <b>media</b> post from this channel — photos, "
        "videos, files, audio, animations, voice notes, stickers — into "
        "all the other chats connected to my owner. Plain-text channel "
        "posts are not relayed.\n\n"
        f"Use {bro} to pause sync for this channel. /stop disconnects it.\n\n"
        "Channels run on the same plan model as other chats: full "
        "two-way relay during the first month, then outbound stays open "
        "and inbound becomes Premium. /plan tells you where this channel "
        "stands."
    )


@start_router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    """Show role-aware help with drill-down buttons."""
    from bot.services.value_prop import free_vs_premium_block

    user_id = message.from_user.id if message.from_user else None
    admin = _is_admin(user_id)

    lines = [
        "📖 <b>How I work</b>",
        "",
        "I'm a relay bot. Anything I see in one connected chat I forward to "
        "every other connected chat — as originals, never forwards.",
        "",
        "<b>Relay rules</b>",
        "• <i>Private chat with me</i>: everything you send (text, media, "
        "  files) goes to my network. Everything from the network arrives here.",
        "• <i>Groups I'm in</i>: I relay all media. I relay text <b>only</b> "
        "  when it's part of a reply thread to one of my messages.",
        "• <i>Channels I'm admin in</i>: media-only relay. Plain text posts "
        "  stay in the channel.",
        "",
        "<b>Attribution</b>",
        "Every message I relay gets a small sign showing the sender's alias tag. "
        "When the source is a group, I also append the group's own alias tag — "
        "so recipients see <i>who said what, in which group</i>.",
        "",
        "<b>Plans</b>",
        free_vs_premium_block(),
        "",
        "<b>Commands</b>",
        "/start — Connect this chat / show the guide",
        "/stop — Disconnect this chat",
        "/selfsend — Echo your own messages back to this chat (off by default)",
        "/broadcast — Pause/resume sync direction (chat admins only in groups)",
        "/subscribe — Go Premium · /plan — Check your plan",
        "/stats — Your activity in the network",
        "/help — This guide",
    ]
    if admin:
        # Admin commands are deliberately NOT pushed to BotFather (they don't
        # appear in the in-client "/" picker), so this is the only place an
        # operator can discover their full toolkit.  Keep this list in sync
        # with docs/botfather-setup.md → "Admin-only commands".
        lines.extend([
            "",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "🛠 <b>Admin commands</b> (you only — invisible to others)",
            "",
            "<b>Network ops</b>",
            "/status — Live dashboard",
            "/list — All connected chats",
            "/pause — Stop all syncing across the network",
            "/resume — Resume syncing",
            "/edits — Toggle edited-message redistribution",
            "",
            "<b>Signature</b>",
            "/signature &lt;text&gt; — Add a signature line to outgoing messages",
            "/signatureurl &lt;url&gt; — Make the signature a clickable link",
            "/signatureoff — Remove the signature",
            "",
            "<b>Chat lifecycle</b>",
            "/remove &lt;chat_id|reply&gt; — Disconnect a chat from the network",
            "/grant &lt;chat_id&gt; &lt;plan&gt; — Give a chat Premium access",
            "/revoke &lt;chat_id|reply&gt; — Remove a chat's Premium",
            "",
            "<b>User moderation</b>",
            "/mute &lt;user_id|reply&gt; &lt;duration&gt; — Silence a user for a while",
            "/unmute &lt;user_id|reply&gt; — Lift a mute",
            "/ban &lt;user_id|reply&gt; — Permanently block a user",
            "/unban &lt;user_id|reply&gt; — Unblock a user",
            "/whois &lt;name&gt; — Look up the user behind an alias",
            "",
            "<b>Chat moderation</b>",
            "/banchat &lt;chat_id|reply&gt; — Block all messages from a source chat",
            "/unbanchat &lt;chat_id|reply&gt; — Lift a chat-level ban",
            "/chatwhois &lt;name&gt; — Look up the chat behind an alias",
        ])

    kb = build_help_menu(admin)
    await message.answer("\n".join(lines), reply_markup=kb,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


@start_router.message(Command("stop"))
async def cmd_stop(message: Message) -> None:
    """Show confirmation before unregistering this chat."""
    await message.answer(
        "You're about to <b>disconnect</b> this chat.\n\n"
        "It will stop sending and receiving synced messages. "
        "You can always reconnect with /start.",
        reply_markup=build_stop_confirm(),
        parse_mode=ParseMode.HTML,
    )


@start_router.message(Command("selfsend"))
async def cmd_selfsend(message: Message, command: CommandObject) -> None:
    """Toggle self-send for this chat."""
    args = (command.args or "").strip().lower()

    # No args → show button panel (read-only view, anyone can see)
    if args not in ("on", "off"):
        async with async_session() as session:
            chat = await ChatRepo(session).get_chat(message.chat.id)
        if chat is None:
            await message.answer("Please /start first to register this chat.",
                parse_mode=ParseMode.HTML,
            )
            return
        status = "ON ✅" if chat.allow_self_send else "OFF"
        kb = build_selfsend_result(chat.allow_self_send)
        await message.answer(
            f"🔄 <b>Echo is currently {status}</b>\n\n"
            "When echo is on, messages you send here also come back "
            "to this chat from your other connected chats.",
            reply_markup=kb,
            parse_mode=ParseMode.HTML,
        )
        return

    # Mutation path — require admin in groups/channels
    if not await _enforce_admin_or_reply(message):
        return

    enabled = args == "on"

    async with async_session() as session:
        repo = ChatRepo(session)
        await repo.toggle_self_send(message.chat.id, enabled)

    status = "ON ✅" if enabled else "OFF"
    kb = build_selfsend_result(enabled)
    await message.answer(f"🔄 Echo is now <b>{status}</b>", reply_markup=kb,
        parse_mode=ParseMode.HTML,
    )


@start_router.message(Command("broadcast"))
async def cmd_broadcast(message: Message, command: CommandObject) -> None:
    """Control broadcast direction. Usage: /broadcast off|on in|out, or no args for panel."""
    raw_args = (command.args or "").strip().lower().split()

    # No args → show button panel
    if not raw_args or len(raw_args) != 2 or raw_args[0] not in ("off", "on") or raw_args[1] not in ("in", "out"):
        redis = _get_redis()
        if redis is None:
            await message.answer("Service temporarily unavailable.",
                parse_mode=ParseMode.HTML,
            )
            return

        async with async_session() as session:
            chat_obj = await ChatRepo(session).get_chat(message.chat.id)
        if chat_obj is None:
            await message.answer("Please /start first to register this chat.",
                parse_mode=ParseMode.HTML,
            )
            return

        if not await is_premium(redis, message.chat.id, chat_obj.registered_at):
            await message.answer(
                "<b>Sync Control</b> — Premium\n\n"
                "Lets you pause this chat's <i>sending</i> or <i>receiving</i> "
                "independently. Useful when one chat in your network is going "
                "through a noisy spell and you want a one-way mute, or when "
                "you want a chat to be receive-only.\n\n"
                "Free relay (everything in, everything out) keeps working "
                "exactly as it does now. Plans start at about "
                "<b>1 star / hour</b>.",
                reply_markup=build_subscribe_button(),
        parse_mode=ParseMode.HTML,
    )
            return

        out_status = "ON" if chat_obj.is_source else "PAUSED"
        in_status = "ON" if chat_obj.is_destination else "PAUSED"
        kb = build_broadcast_panel(chat_obj.is_source, chat_obj.is_destination)
        await message.answer(
            "<b>Sync Control</b>\n\n"
            f"Sending: <b>{out_status}</b> — content from here goes to your other chats\n"
            f"Receiving: <b>{in_status}</b> — content from other chats arrives here",
            reply_markup=kb,
            parse_mode=ParseMode.HTML,
        )
        return

    action, direction = raw_args[0], raw_args[1]
    enabled = action == "on"

    # Mutation path — require admin in groups/channels (before premium check)
    if not await _enforce_admin_or_reply(message):
        return

    # Premium gating
    redis = _get_redis()
    if redis is None:
        await message.answer("Service temporarily unavailable.",
            parse_mode=ParseMode.HTML,
        )
        return

    async with async_session() as session:
        chat_obj = await ChatRepo(session).get_chat(message.chat.id)
    if chat_obj is None:
        await message.answer("Please /start first to register this chat.",
            parse_mode=ParseMode.HTML,
        )
        return

    if not await is_premium(redis, message.chat.id, chat_obj.registered_at):
        await message.answer(
            "<b>Sync Control</b> is a Premium feature.\n\n"
            "Choose exactly what this chat sends and receives. "
            "Plans start at about <b>1 star per hour</b>.",
            reply_markup=build_subscribe_button(),
        parse_mode=ParseMode.HTML,
    )
        return

    async with async_session() as session:
        repo = ChatRepo(session)
        if direction == "out":
            await repo.toggle_source(message.chat.id, enabled)
        else:
            await repo.toggle_destination(message.chat.id, enabled)

    # Re-fetch and show panel with updated state
    async with async_session() as session:
        chat_obj = await ChatRepo(session).get_chat(message.chat.id)

    out_status = "ON" if chat_obj.is_source else "PAUSED"
    in_status = "ON" if chat_obj.is_destination else "PAUSED"
    kb = build_broadcast_panel(chat_obj.is_source, chat_obj.is_destination)
    await message.answer(
        "<b>Sync Control</b>\n\n"
        f"Sending: <b>{out_status}</b> — content from here goes to your other chats\n"
        f"Receiving: <b>{in_status}</b> — content from other chats arrives here",
        reply_markup=kb,
        parse_mode=ParseMode.HTML,
    )


# ── /stats ───────────────────────────────────────────────────────────


def _is_admin(user_id: int | None) -> bool:
    if user_id is None:
        return False
    return user_id in settings.admin_ids


@start_router.message(Command("stats"))
@start_router.channel_post(Command("stats"))
async def cmd_stats(message: Message) -> None:
    """Show per-chat stats for everyone; global stats appended for admins."""
    chat_id = message.chat.id
    user_id = message.from_user.id if message.from_user else None
    admin = _is_admin(user_id)

    try:
        async with async_session() as session:
            chat_repo = ChatRepo(session)
            log_repo = SendLogRepo(session)
            sub_repo = SubscriptionRepo(session)

            chat = await chat_repo.get_chat(chat_id)
            if chat is None:
                await message.answer("This chat is not registered. Use /start first.",
                    parse_mode=ParseMode.HTML,
                )
                return

            sent_count = await log_repo.count_messages_from_chat(chat_id)
            recv_count = await log_repo.count_messages_to_chat(chat_id)
            active_sub = await sub_repo.get_active_subscription(chat_id)
    except Exception as e:
        logger.exception("Stats error for chat %d: %s", chat_id, e)
        await message.answer("Stats are temporarily unavailable. Please try again in a bit.",
            parse_mode=ParseMode.HTML,
        )
        return

    # Chat name
    name = chat.title or chat.username or str(chat.chat_id)

    # Days since registration
    reg_date = chat.registered_at
    if reg_date.tzinfo is None:
        reg_date = reg_date.replace(tzinfo=timezone.utc)
    days_active = max(0, (datetime.now(timezone.utc) - reg_date).days)

    # Alias
    alias_text = ""
    if user_id:
        redis = _get_redis()
        if redis:
            try:
                from bot.services.alias import get_alias
                alias = await get_alias(redis, user_id)
                alias_text = f"\nYour ID tag: <code>[{alias}]</code>"
            except Exception as e:
                logger.debug("Alias lookup failed for %d: %s", user_id, e)

    # Broadcast state
    src = "ON" if chat.is_source else "Paused"
    dst = "ON" if chat.is_destination else "Paused"

    # Plan one-liner
    trial_left = get_trial_days_remaining(chat.registered_at)
    if active_sub:
        expires_at = active_sub.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        remaining = max(0, (expires_at - datetime.now(timezone.utc)).days)
        plan_line = f"Premium — {active_sub.plan.capitalize()} ({remaining}d left)"
    else:
        if trial_left > 0:
            plan_line = f"Full access ({trial_left}d left)"
        else:
            plan_line = "Free access ended"

    # Missed messages (only meaningful if trial expired and no sub)
    missed_line = ""
    if not active_sub and trial_left <= 0:
        redis = _get_redis()
        if redis:
            from bot.services.subscription import get_missed_today
            missed = await get_missed_today(redis, chat_id)
            if missed > 0:
                missed_line = (
                    f"\n\n{missed:,} new message{'s' if missed != 1 else ''} "
                    "waiting in your network."
                )

    lines = [
        "<b>Your Activity</b>",
        "",
        f"Chat: <b>{name}</b> ({chat.chat_type})",
        f"Connected since: {chat.registered_at.strftime('%d %b %Y')} ({days_active}d ago)"
        f"{alias_text}",
        "",
        "<b>Last 48 hours:</b>",
        f"  Sent out: <b>{sent_count:,}</b> messages",
        f"  Received: <b>{recv_count:,}</b> messages",
        "",
        f"Sync: Sending {src} · Receiving {dst}",
        f"Plan: {plan_line}{missed_line}",
    ]

    # —— Global stats (admin only) --------------------------------------------------
    if admin:
        try:
            async with async_session() as session:
                chat_repo = ChatRepo(session)
                sub_repo = SubscriptionRepo(session)
                log_repo = SendLogRepo(session)
                res_repo = RestrictionRepo(session)

                total_active = await chat_repo.count_active()
                type_counts = await chat_repo.count_by_type()
                source_count = await chat_repo.count_sources()
                dest_count = await chat_repo.count_destinations()
                premium_count = await sub_repo.count_premium_chats()
                sub_breakdown = await sub_repo.count_subscription_breakdown()
                total_dist = await log_repo.count_total_distributed()
                unique_senders = await log_repo.count_unique_senders()
                restrictions = await res_repo.count_active_restrictions()

            # Trial vs expired: active - premium = non-premium active chats
            non_premium = max(0, total_active - premium_count)

            # Type breakdown line
            type_parts = []
            for t in ("private", "group", "supergroup", "channel"):
                c = type_counts.get(t, 0)
                if c > 0:
                    type_parts.append(f"{t.capitalize()}: {c}")
            type_line = " | ".join(type_parts) if type_parts else "None"

            # Sub breakdown line
            sub_parts = []
            for p in ("week", "month", "year"):
                c = sub_breakdown.get(p, 0)
                if c > 0:
                    sub_parts.append(f"{p.capitalize()}: {c}")
            sub_line = " | ".join(sub_parts) if sub_parts else "None"

            muted = restrictions.get("mute", 0)
            banned = restrictions.get("ban", 0)

            try:
                from bot.services.distributor import get_distributor
                queue_size = get_distributor().queue_size
            except Exception as e:
                logger.debug("Queue size unavailable: %s", e)
                queue_size = "N/A"

            lines.extend([
                "",
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                "",
                "<b>Network Overview</b>",
                "",
                f"Connected chats: <b>{total_active}</b>",
                f"  {type_line}",
                f"  Sending: {source_count} | Receiving: {dest_count}",
                "",
                f"Premium members: <b>{premium_count}</b> | Free: {non_premium}",
                f"  ({sub_line})",
                "",
                "<b>Last 48 hours:</b>",
                f"  Messages synced: <b>{total_dist:,}</b>",
                f"  Active senders: <b>{unique_senders}</b>",
                "",
                f"Moderation: {muted} silenced · {banned} blocked",
                f"Queue: {queue_size}",
            ])
        except Exception as e:
            logger.exception("Stats error (admin global): %s", e)
            lines.extend([
                "",
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                "",
                "<b>Network Overview</b>",
                "",
                "Stats are temporarily unavailable.",
            ])

    kb = build_stats_actions(admin)
    await message.answer("\n".join(lines), reply_markup=kb,
        parse_mode=ParseMode.HTML,
    )


def _get_redis():
    """Get Redis instance from the running distributor (avoids circular imports)."""
    try:
        from bot.services.distributor import get_distributor
        return get_distributor()._redis
    except RuntimeError:
        return None
