"""Single source of truth for the free-vs-premium value proposition.

Why this module exists
======================

The bot has *six* surfaces where the free/premium distinction shows up:

1. ``/start`` onboarding (private, group, channel — three flavours).
2. ``/help`` (when run by a regular user).
3. ``/plan`` (active premium / on trial / expired).
4. ``/subscribe`` pricing card (``build_pricing_text``).
5. Trial-expiry reminders at T-7, T-3, T-1 days, and T+0 (the day it ends).
6. The daily paywall nudge that fires when a free chat would have received
   a cross-chat message.

If those surfaces drift apart even slightly, users get inconsistent (and
therefore mistrust-inducing) signals about what they're paying for.  Every
piece of copy on those surfaces should compose from this module, never
re-state the delta inline.

The actual feature delta
========================

Both free AND premium:

- Outbound relay — messages you post in a chat reach every *premium* chat in
  the network, in real time, as originals (no forward tag).
- ``/selfsend`` — echo your own messages back to the same chat. Backups,
  archives, multi-device personal sync — all keep working.
- Reply threading, dedup, the bot-rooted thread gate, attribution aliases,
  rate limits, all the moderation tooling — unaffected by plan.

Premium adds *receiving from other chats*:

- Cross-chat inbound — messages other people post in *their* connected
  chats land in your chat in real time. This is the network experience.
- Sync Control — pause sending or receiving direction independently
  (the ``/broadcast off in`` and ``/broadcast off out`` knobs).

That's it.  No artificial caps, no feature flags hidden behind a paywall,
no "premium quality" of relay vs free relay.  The delta is a single,
honest line: *premium opens the inbound side of the network*.

Tone guidelines
===============

- Lead with what they get (continuity, network value), not what they pay.
- Be specific.  Vague pitches ("unlock everything") feel like a money grab.
  Naming the exact behaviour ("messages from other chats land here") feels
  like respect.
- Acknowledge what *stays* free.  A user who knows ``/selfsend`` keeps
  working trusts the bot more than one who feels held hostage.
- Soft CTAs.  ``/subscribe`` is a link, not a demand.
- Never use scarcity, urgency, fake counters, social-proof guilt, or
  "limited time".  This bot is a tool, not a funnel.
"""

from __future__ import annotations

# ── Atomic copy snippets ─────────────────────────────────────────────────────
#
# Keep these short, reusable, and free of HTML when possible — callers can
# wrap them in <b>/<i> tags as needed for the surface they're rendering on.

FREE_INCLUDES = (
    "Send out — your messages reach every Premium chat in the network",
    "/selfsend — echo your own messages back for backup or multi-device sync",
    "All settings, aliases, reply threading, moderation tools",
)

PREMIUM_ADDS = (
    "Receive — messages other people post in their chats land in yours",
    "Sync Control — pause sending or receiving direction independently",
)


# ── HTML-formatted blocks for chat replies ──────────────────────────────────


def free_vs_premium_block() -> str:
    """A neutral, factual side-by-side of free and Premium, in HTML.

    Used in ``/help``, ``/plan`` (expired branch), and the trial reminders.
    Returns a multi-line string ready to paste into ``message.answer``.
    """
    free_lines = "\n".join(f"  ✓ {item}" for item in FREE_INCLUDES)
    prem_lines = "\n".join(f"  ✓ {item}" for item in PREMIUM_ADDS)
    return (
        "<b>What's free</b>\n"
        f"{free_lines}\n\n"
        "<b>What Premium adds</b>\n"
        f"{prem_lines}"
    )


def access_blurb_for_onboarding() -> str:
    """Two-sentence summary used at the bottom of ``/start`` onboarding.

    Tells a brand-new user what to expect *during* and *after* their trial,
    without being pushy.  Premium is mentioned but not pitched — the goal at
    onboarding is comprehension, not conversion.
    """
    return (
        "<b>About your access</b>\n"
        "You're on the full version for the first month — including messages "
        "from every other chat in your network. After that, sending out and "
        "/selfsend backups keep working; receiving from other chats is what "
        "Premium keeps open. /plan tells you where you stand any time."
    )


# ── Reminder copy ───────────────────────────────────────────────────────────
#
# The trial reminder task delegates entirely to these so every change to
# wording is one-edit, here.


def reminder_t_minus_7() -> str:
    return (
        "Heads up — your full access wraps up in <b>7 days</b>.\n\n"
        "Right now you receive messages from every chat I'm connected to. "
        "After your first month, that inbound flow pauses. /selfsend backups "
        "and sending out from this chat keep running.\n\n"
        "If the network view is what you'd want to keep, /subscribe puts it "
        "back. The monthly plan works out to about <b>1 ⭐ / hour</b>."
    )


def reminder_t_minus_3() -> str:
    return (
        "<b>3 days left</b> of full access.\n\n"
        "Quick recap of what changes after that:\n\n"
        "<b>Stays</b>\n"
        "  ✓ Sending from this chat to your network\n"
        "  ✓ /selfsend backups\n"
        "  ✓ Every setting and alias as-is\n\n"
        "<b>Pauses</b>\n"
        "  ✗ New messages from other chats arriving here\n\n"
        "That last line is the only thing /subscribe brings back."
    )


def reminder_t_minus_1() -> str:
    return (
        "<b>Last day of full access.</b>\n\n"
        "After tonight the bot stays useful — outbound relay and /selfsend "
        "backups keep flowing. New messages from your network just won't "
        "land here anymore, until you /subscribe."
    )


def reminder_t_zero() -> str:
    """Sent the day the trial actually ends.

    First time the user sees the bot in its free-tier state, so we name the
    exact behaviour change and reassure them about what still works.
    """
    return (
        "Your free month is up. You're now on free access.\n\n"
        "<b>What still works</b>\n"
        "  ✓ /selfsend — backups to your group / chat\n"
        "  ✓ Sending out — your messages reach Premium chats in the network\n"
        "  ✓ All your existing settings, aliases, and moderation\n\n"
        "<b>What's paused</b>\n"
        "  ✗ Cross-chat inbound — messages other people send don't land here\n\n"
        "/subscribe brings the inbound side back the moment it goes through. "
        "Plans start at the weekly; the monthly is about <b>1 ⭐ / hour</b>."
    )


# ── Daily missed-message nudge ──────────────────────────────────────────────


def daily_nudge(missed: int) -> str:
    """Copy for the once-per-day nudge fired when a free chat would have
    received a cross-chat message.

    Specific (names the count), short, and ends with a non-imperative CTA.
    """
    plural = "messages" if missed != 1 else "message"
    return (
        f"<b>{missed:,}</b> {plural} from your network waited today, but "
        "since this chat is on free access they didn't land here.\n\n"
        "Premium opens the inbound side back up — about <b>1 ⭐ / hour</b> "
        "on the monthly plan. /plan shows your status, /subscribe shows the "
        "options."
    )
