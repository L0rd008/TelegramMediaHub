"""Deduplication engine — multi-layered, source-chat-scoped.

Three independent guards live here, each addressing a different threat model:

1. ``is_duplicate_update`` — the **webhook retry guard**.  Telegram occasionally
   redelivers the same update after a network blip; the second delivery has the
   same ``(chat_id, message_id)`` pair.  We mark every incoming update so the
   retry is dropped silently.  Short TTL (60 s) — retries happen in seconds, not
   hours.

2. ``is_duplicate`` — the **content repost guard** for single (non-album)
   messages.  Keyed on ``(source_chat_id, fingerprint)``.  Within the same
   source chat, an identical text or identical media (by ``file_unique_id``)
   re-posted within the TTL window is dropped.  CRITICAL: the key is scoped by
   ``source_chat_id`` so distinct chats sending the same content (e.g. two users
   in different groups both saying "good morning") don't collide.  Without that
   scoping, the bot silently dropped ~95% of legitimate text traffic — every
   "ok", "thanks", or shared meme across the network was suppressed after the
   first sighting.

3. ``is_album_duplicate`` — the **album repost guard**.  Keyed on
   ``(source_chat_id, sorted_file_unique_ids_hash)``.  Called at flush time
   (see :mod:`bot.services.media_group`) so the whole album is judged as a
   unit.  Re-uploading the same set of files in any order is detected even
   though Telegram assigns a fresh ``media_group_id`` to each upload.

The previous implementation did per-item content dedup *before* buffering, with
a global key namespace.  That caused two failures:

- **Partial album bug** — if a single item of a new album shared its
  ``file_unique_id`` with any prior message anywhere on the bot, that one item
  was dropped and the album arrived at recipients with a hole.
- **Cross-chat collision** — text and media legitimately distinct across chats
  were silently suppressed.

Both are fixed by (a) scoping every key with ``source_chat_id`` and (b) moving
album content dedup from per-item-pre-buffer to whole-group-at-flush.
"""

from __future__ import annotations

import hashlib
import logging

import redis.asyncio as aioredis

from bot.services.normalizer import NormalizedMessage
from bot.utils.text import text_hash

logger = logging.getLogger(__name__)

# TTLs.  Tuned per threat model.
DEDUP_TTL = 86_400          # 24 h — content repost window for single media/text
DEDUP_ALBUM_TTL = 86_400    # 24 h — album repost window
DEDUP_UPDATE_TTL = 60       # 60 s — webhook retry guard; Telegram retries fast
DEDUP_MG_SEEN_TTL = 86_400  # 24 h — per-(chat, media_group_id) seen marker


# ── Layer 1: webhook retry guard ─────────────────────────────────────────────


async def is_duplicate_update(
    redis: aioredis.Redis,
    chat_id: int,
    message_id: int,
) -> bool:
    """Return True if (chat_id, message_id) was seen in the last ``DEDUP_UPDATE_TTL`` seconds.

    First call for a given pair returns False (and marks the pair as seen).
    Subsequent calls within the window return True.

    This is the cheapest and most precise dedup — it catches webhook
    redeliveries without any false positives, so it should run before the
    content-based guard.
    """
    key = f"dup:upd:{chat_id}:{message_id}"
    was_new = await redis.set(key, "1", ex=DEDUP_UPDATE_TTL, nx=True)
    return not was_new


# ── Layer 2: content repost guard for single messages ────────────────────────


def compute_fingerprint(msg: NormalizedMessage) -> str | None:
    """Compute the content portion of a dedup key.

    Returned string is namespace-prefixed (``media:`` or ``text:``) but does
    NOT include the source_chat_id.  Callers (or :func:`is_duplicate`) are
    responsible for prefixing the chat scope.

    Returns ``None`` for content the bot can't fingerprint (stickers, video
    notes etc. lack stable text and we don't dedup them — replays of the same
    sticker are usually intentional).
    """
    if msg.file_unique_id:
        return f"media:{msg.file_unique_id}"
    if msg.text:
        return f"text:{text_hash(msg.text)}"
    return None


def _content_dedup_key(source_chat_id: int, fingerprint: str) -> str:
    """Build the per-(chat, content) Redis key."""
    return f"dup:c:{source_chat_id}:{fingerprint}"


async def is_duplicate(
    redis: aioredis.Redis,
    msg: NormalizedMessage,
) -> bool:
    """Per-source-chat content dedup for single (non-album) messages.

    Returns True if the same text/file was already redistributed from this
    source chat within ``DEDUP_TTL``.  Returns False if the message has no
    fingerprintable content (e.g. sticker without text) — those go through.

    Scoping by ``source_chat_id`` is intentional and load-bearing; see the
    module docstring for the prior bug it eliminates.
    """
    fingerprint = compute_fingerprint(msg)
    if fingerprint is None:
        return False

    key = _content_dedup_key(msg.source_chat_id, fingerprint)
    was_new = await redis.set(key, "1", ex=DEDUP_TTL, nx=True)

    if not was_new:
        logger.debug(
            "Duplicate content in chat %d: %s",
            msg.source_chat_id,
            fingerprint,
        )
        return True
    return False


# ── Layer 3: album repost guard (whole-group fingerprint) ───────────────────


def compute_group_fingerprint(items: list[NormalizedMessage]) -> str | None:
    """Hash an album's items into a single stable fingerprint.

    The fingerprint is computed over the sorted set of ``file_unique_id``
    values, so two uploads of the same files produce the same hash regardless
    of:

    - the (volatile) ``media_group_id`` Telegram assigns,
    - the per-item ordering inside the album.

    Returns ``None`` if no items have a ``file_unique_id`` — extremely rare
    (would mean a pure-text or sticker-only album which Telegram doesn't
    actually support) and we let those through.
    """
    fuids = sorted(i.file_unique_id for i in items if i.file_unique_id)
    if not fuids:
        return None
    blob = "|".join(fuids).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:32]


async def is_album_duplicate(
    redis: aioredis.Redis,
    source_chat_id: int,
    items: list[NormalizedMessage],
) -> bool:
    """Atomic SET NX on the per-(chat, group-content-hash) key.

    Returns True if this album content was already redistributed from this
    source chat within ``DEDUP_ALBUM_TTL``.

    Called from :meth:`MediaGroupBuffer._flush_group` after items are
    assembled but before distribution.  Matching the dedup decision to the
    full assembled group avoids the partial-album problem the old per-item
    pre-buffer scheme caused.
    """
    fp = compute_group_fingerprint(items)
    if fp is None:
        return False

    key = f"dup:alb:{source_chat_id}:{fp}"
    was_new = await redis.set(key, "1", ex=DEDUP_ALBUM_TTL, nx=True)

    if not was_new:
        logger.debug(
            "Duplicate album in chat %d: %s (%d items)",
            source_chat_id,
            fp,
            len(items),
        )
        return True
    return False


# ── Helper: per-(chat, media_group_id) "seen" marker ────────────────────────


async def is_media_group_seen(
    redis: aioredis.Redis,
    source_chat_id: int,
    media_group_id: str,
) -> bool:
    """Mark this media_group_id as seen and report whether it was already known.

    Scoped by ``source_chat_id`` — ``media_group_id`` is a 64-bit integer
    chosen by Telegram's clients, and while collisions across chats are
    extremely unlikely they're not impossible.

    NOTE: this only catches duplicate physical-upload events (e.g. an item
    arriving twice from the same upload).  It cannot detect re-uploads of the
    same content with a fresh ``media_group_id`` — that's what
    :func:`is_album_duplicate` is for.
    """
    key = f"dup:mg:{source_chat_id}:{media_group_id}"
    was_new = await redis.set(key, "1", ex=DEDUP_MG_SEEN_TTL, nx=True)
    return not was_new
