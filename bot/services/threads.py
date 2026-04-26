"""Bot-rooted reply thread tracker.

In groups (and supergroups), regular member chatter MUST NOT be relayed —
otherwise the network turns into a noisy mirror of every group conversation.
The relay rule for text in groups is: a text message gets relayed only if it
belongs to a *reply chain rooted in a bot-relayed message*.

Concretely:

- bot relays message ``M`` into group ``G`` → ``M`` is the root of a thread.
- A user in ``G`` replies to ``M`` with ``T1`` → ``T1`` is in-thread, gets
  relayed, and we mark ``T1`` as a thread member too.
- Another user replies to ``T1`` with ``T2`` → ``T2`` is in-thread (target
  ``T1`` is a known member), gets relayed, marked.
- A reply to a *non-bot, non-thread* message → not in-thread, dropped.

Telegram's Bot API only exposes the *immediate* reply target, not the full
chain.  We compensate by remembering every message we've already classified
as in-thread in a per-chat Redis Set, then doing a one-hop lookup on each
incoming reply.  Combined with the send_log reverse lookup that detects the
bot-message root, this gives us correct chain-walk behaviour with O(1) work
per arriving message.

Storage:

- Key:    ``thread:{chat_id}``
- Type:   Redis Set
- Members: stringified ``message_id`` values
- TTL:    24 h, refreshed on each membership add (matches send_log retention)

Memory cost is bounded by chat activity in the last 24 h; the set is auto-
expired by Redis, so no manual cleanup is required.
"""

from __future__ import annotations

import logging

import redis.asyncio as aioredis

from bot.db.engine import async_session
from bot.db.repositories.send_log_repo import SendLogRepo

logger = logging.getLogger(__name__)

THREAD_TTL = 86_400  # 24 h, mirrors send_log cleaner retention


def _key(chat_id: int) -> str:
    return f"thread:{chat_id}"


async def is_in_bot_thread(
    redis: aioredis.Redis,
    chat_id: int,
    reply_target_message_id: int,
) -> bool:
    """Return True if ``reply_target_message_id`` is the root or a known member
    of a thread rooted in a bot-relayed message in ``chat_id``.

    Two-step lookup:

    1. ``send_log`` reverse lookup — was the reply target a message the bot
       relayed into this chat?  If yes, this is a direct reply to the root.
    2. Redis Set membership — was the reply target itself classified
       in-thread on a previous call?  If yes, the chain is preserved.

    Both checks are O(1).  If either succeeds, the caller should add the
    *current* message's ``message_id`` to the set via :func:`mark_in_thread`
    so future replies that target *it* also relay.
    """
    # 1. Direct-to-bot check via send_log
    try:
        async with async_session() as session:
            sl_repo = SendLogRepo(session)
            origin = await sl_repo.reverse_lookup(chat_id, reply_target_message_id)
        if origin is not None:
            return True
    except Exception as e:
        # Don't let a transient DB hiccup block legitimate text relay outright
        # — fall through to the Redis-set check, which is the cheaper guard.
        logger.debug(
            "Thread root lookup failed for chat %d msg %d: %s",
            chat_id, reply_target_message_id, e,
        )

    # 2. Known thread member via Redis Set
    try:
        is_member = await redis.sismember(_key(chat_id), str(reply_target_message_id))
        return bool(is_member)
    except Exception as e:
        logger.debug(
            "Thread member lookup failed for chat %d msg %d: %s",
            chat_id, reply_target_message_id, e,
        )
        return False


async def mark_in_thread(
    redis: aioredis.Redis,
    chat_id: int,
    message_id: int,
) -> None:
    """Mark ``message_id`` as a member of an active bot-rooted thread in
    ``chat_id`` so that future replies to it are recognised as in-thread.

    Refreshes the per-chat Set's TTL on every call.  Failures are logged but
    swallowed — a missed mark only costs the next reply in the chain (it'll
    be evaluated against the bot-message root via send_log instead, and if
    that also misses the chain breaks).
    """
    key = _key(chat_id)
    try:
        await redis.sadd(key, str(message_id))
        await redis.expire(key, THREAD_TTL)
    except Exception as e:
        logger.debug(
            "Thread mark failed for chat %d msg %d: %s",
            chat_id, message_id, e,
        )
