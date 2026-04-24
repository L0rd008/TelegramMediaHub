"""Sender service – maps NormalizedMessage to the correct Bot API send* call.

Key principle: ALWAYS use file_id reuse, NEVER forwardMessage or copyMessage.
This ensures zero forwarding metadata on sent messages.
"""

from __future__ import annotations

import logging
from typing import Any

import redis.asyncio as aioredis
from aiogram import Bot
from aiogram.types import (
    InputMediaAudio,
    InputMediaDocument,
    InputMediaPhoto,
    InputMediaVideo,
    Message,
    MessageEntity,
    ReplyParameters,
)

from bot.services.normalizer import NormalizedMessage
from bot.services.signature import apply_signature
from bot.utils.enums import MessageType
from bot.utils.text import utf16_len as _utf16_len  # Bug 4: canonical UTF-16 helper

logger = logging.getLogger(__name__)

# Limits per Telegram Bot API (in UTF-16 code units)
TEXT_MAX_LEN = 4096
CAPTION_MAX_LEN = 1024

# Redis key and TTL for the cached bot username (C-1)
_BOT_USERNAME_REDIS_KEY = "bot:username"
_BOT_USERNAME_TTL = 3600  # 1 hour


async def _get_bot_username(bot: Bot, redis: aioredis.Redis) -> str:
    """Return the bot's username, using Redis as a process-safe cache.

    Falls back to a getMe() API call on cache miss, then stores the result
    in Redis with a 1-hour TTL so all processes share the same value and it
    is automatically invalidated after username changes.
    """
    cached = await redis.get(_BOT_USERNAME_REDIS_KEY)
    if cached:
        return cached.decode() if isinstance(cached, bytes) else cached

    info = await bot.get_me()
    username = info.username or ""
    if username:
        await redis.set(_BOT_USERNAME_REDIS_KEY, username, ex=_BOT_USERNAME_TTL)
    return username



def _rebuild_entities(raw: list[dict[str, Any]] | None) -> list[MessageEntity] | None:
    """Rebuild MessageEntity objects from serialized dicts."""
    if not raw:
        return None
    entities = []
    for d in raw:
        kwargs: dict[str, Any] = {
            "type": d["type"],
            "offset": d["offset"],
            "length": d["length"],
        }
        if "url" in d:
            kwargs["url"] = d["url"]
        if "language" in d:
            kwargs["language"] = d["language"]
        if "custom_emoji_id" in d:
            kwargs["custom_emoji_id"] = d["custom_emoji_id"]
        # Note: text_mention entities require a live User object which we cannot
        # reliably reconstruct from a stored dict, so they are intentionally dropped.
        entities.append(MessageEntity(**kwargs))
    return entities or None


def _clip_entities(
    entities: list[MessageEntity] | None, text: str
) -> list[MessageEntity] | None:
    """Remove entities that point outside the bounds of *text*.

    When apply_signature truncates content, any entity whose span extends
    beyond the new text length would cause a TelegramBadRequest.  Entities
    whose end offset exceeds the text length are dropped entirely; entities
    that fit within the surviving prefix are kept unchanged.
    """
    if not entities:
        return entities
    text_utf16 = _utf16_len(text)
    clipped = [
        e for e in entities
        if e.offset + e.length <= text_utf16
    ]
    return clipped or None


def _build_alias_entity(
    content: str, alias_plain: str, alias_url: str
) -> MessageEntity | None:
    """Find the alias in *content* and return a text_link MessageEntity for it.

    Offsets and lengths are expressed in UTF-16 code units as required by the
    Bot API.  Using Python string indices (len / rfind) gives wrong results
    when the content contains astral-plane characters (emoji, etc.) because
    each such character occupies 2 UTF-16 units but only 1 Python codepoint.
    """
    idx = content.rfind(alias_plain)
    if idx < 0:
        return None
    offset_utf16 = _utf16_len(content[:idx])
    length_utf16 = _utf16_len(alias_plain)
    return MessageEntity(
        type="text_link", offset=offset_utf16, length=length_utf16, url=alias_url
    )


async def send_single(
    bot: Bot,
    msg: NormalizedMessage,
    chat_id: int,
    signature: str | None,
    reply_to_message_id: int | None = None,
    sender_alias: str | None = None,
    redis: aioredis.Redis | None = None,
    allow_paid_broadcast: bool = False,
) -> Message | None:
    """Send a single NormalizedMessage to *chat_id*. Returns the sent Message."""

    # Resolve alias as plain text + URL (never HTML — avoids entity/parse_mode conflict)
    alias_plain = ""
    alias_url = ""
    if sender_alias and redis:
        bot_uname = await _get_bot_username(bot, redis)
        alias_plain = sender_alias
        alias_url = f"https://t.me/{bot_uname}" if bot_uname else ""
    elif sender_alias:
        # redis not available — alias text only, no link
        alias_plain = sender_alias

    if alias_plain:
        raw_text = f"{msg.text}\n\n{alias_plain}" if msg.text else msg.text
        raw_caption = f"{msg.caption}\n\n{alias_plain}" if msg.caption else alias_plain
    else:
        raw_text = msg.text
        raw_caption = msg.caption

    caption = apply_signature(raw_caption, signature, CAPTION_MAX_LEN)
    text = apply_signature(raw_text, signature, TEXT_MAX_LEN)
    entities = _rebuild_entities(msg.entities)
    caption_entities = _rebuild_entities(msg.caption_entities)

    # Bug 6c: if apply_signature truncated the content, drop entities that now
    # point outside the (shorter) text — they would cause TelegramBadRequest.
    if text is not None and text != raw_text:
        entities = _clip_entities(entities, text)
    if caption is not None and caption != raw_caption:
        caption_entities = _clip_entities(caption_entities, caption)

    # Append a text_link entity for the alias so it renders as a clickable link
    # regardless of whether the original message had entities.
    if alias_plain and alias_url:
        if text:
            ent = _build_alias_entity(text, alias_plain, alias_url)
            if ent:
                entities = (entities or []) + [ent]
        if caption:
            ent = _build_alias_entity(caption, alias_plain, alias_url)
            if ent:
                caption_entities = (caption_entities or []) + [ent]

    # Build reply_parameters if we have a reply target
    reply_params: ReplyParameters | None = None
    if reply_to_message_id:
        reply_params = ReplyParameters(
            message_id=reply_to_message_id,
            allow_sending_without_reply=True,
        )

    try:
        match msg.message_type:
            case MessageType.TEXT:
                return await bot.send_message(
                    chat_id=chat_id,
                    text=text or "",
                    entities=entities,
                    # C-2: parse_mode=None disables the global HTML default so that
                    # the entities array is honoured instead of being silently ignored.
                    parse_mode=None,
                    allow_paid_broadcast=allow_paid_broadcast,
                    reply_parameters=reply_params,
                )

            case MessageType.PHOTO:
                return await bot.send_photo(
                    chat_id=chat_id,
                    photo=msg.file_id,  # type: ignore[arg-type]
                    caption=caption,
                    caption_entities=caption_entities,
                    parse_mode=None,
                    has_spoiler=msg.has_spoiler,
                    show_caption_above_media=msg.show_caption_above_media or None,
                    allow_paid_broadcast=allow_paid_broadcast,
                    reply_parameters=reply_params,
                )

            case MessageType.VIDEO:
                return await bot.send_video(
                    chat_id=chat_id,
                    video=msg.file_id,  # type: ignore[arg-type]
                    caption=caption,
                    caption_entities=caption_entities,
                    parse_mode=None,
                    duration=msg.duration,
                    width=msg.width,
                    height=msg.height,
                    has_spoiler=msg.has_spoiler,
                    supports_streaming=msg.supports_streaming,
                    show_caption_above_media=msg.show_caption_above_media or None,
                    allow_paid_broadcast=allow_paid_broadcast,
                    reply_parameters=reply_params,
                )

            case MessageType.ANIMATION:
                return await bot.send_animation(
                    chat_id=chat_id,
                    animation=msg.file_id,  # type: ignore[arg-type]
                    caption=caption,
                    caption_entities=caption_entities,
                    parse_mode=None,
                    duration=msg.duration,
                    width=msg.width,
                    height=msg.height,
                    has_spoiler=msg.has_spoiler,
                    show_caption_above_media=msg.show_caption_above_media or None,
                    allow_paid_broadcast=allow_paid_broadcast,
                    reply_parameters=reply_params,
                )

            case MessageType.AUDIO:
                return await bot.send_audio(
                    chat_id=chat_id,
                    audio=msg.file_id,  # type: ignore[arg-type]
                    caption=caption,
                    caption_entities=caption_entities,
                    parse_mode=None,
                    duration=msg.duration,
                    performer=msg.performer,
                    title=msg.title,
                    allow_paid_broadcast=allow_paid_broadcast,
                    reply_parameters=reply_params,
                )

            case MessageType.DOCUMENT:
                return await bot.send_document(
                    chat_id=chat_id,
                    document=msg.file_id,  # type: ignore[arg-type]
                    caption=caption,
                    caption_entities=caption_entities,
                    parse_mode=None,
                    allow_paid_broadcast=allow_paid_broadcast,
                    reply_parameters=reply_params,
                )

            case MessageType.VOICE:
                return await bot.send_voice(
                    chat_id=chat_id,
                    voice=msg.file_id,  # type: ignore[arg-type]
                    caption=caption,
                    caption_entities=caption_entities,
                    parse_mode=None,
                    duration=msg.duration,
                    allow_paid_broadcast=allow_paid_broadcast,
                    reply_parameters=reply_params,
                )

            case MessageType.VIDEO_NOTE:
                return await bot.send_video_note(
                    chat_id=chat_id,
                    video_note=msg.file_id,  # type: ignore[arg-type]
                    duration=msg.duration,
                    length=msg.width,  # diameter
                    allow_paid_broadcast=allow_paid_broadcast,
                    reply_parameters=reply_params,
                )

            case MessageType.STICKER:
                return await bot.send_sticker(
                    chat_id=chat_id,
                    sticker=msg.file_id,  # type: ignore[arg-type]
                    allow_paid_broadcast=allow_paid_broadcast,
                    reply_parameters=reply_params,
                )

            case MessageType.MEDIA_GROUP:
                return await send_media_group(
                    bot, msg, chat_id, signature,
                    reply_to_message_id=reply_to_message_id,
                    sender_alias=sender_alias,
                    redis=redis,
                    allow_paid_broadcast=allow_paid_broadcast,
                )

            case _:
                logger.warning("Unknown message type: %s", msg.message_type)
                return None

    except Exception as e:
        logger.error(
            "Failed to send %s to chat %d: %s",
            msg.message_type.value,
            chat_id,
            e,
        )
        raise


async def send_media_group(
    bot: Bot,
    msg: NormalizedMessage,
    chat_id: int,
    signature: str | None,
    reply_to_message_id: int | None = None,
    sender_alias: str | None = None,
    redis: aioredis.Redis | None = None,
    allow_paid_broadcast: bool = False,
) -> list[Message]:
    """Send a media group (album) to *chat_id*.

    Handles type-compatibility splitting per Telegram API:
    - Photos and videos can be mixed freely
    - Audio can only be grouped with other audio
    - Documents can only be grouped with other documents
    """
    if not msg.group_items:
        logger.warning("Media group with no items, skipping")
        return []

    # Single item → send as individual message
    if len(msg.group_items) == 1:
        result = await send_single(
            bot, msg.group_items[0], chat_id, signature,
            reply_to_message_id=reply_to_message_id,
            sender_alias=sender_alias,
            redis=redis,
            allow_paid_broadcast=allow_paid_broadcast,
        )
        return [result] if result else []

    # Group items by compatible types
    groups = _split_by_compatibility(msg.group_items)

    # Resolve alias as plain text + URL (entity-based, never HTML)
    alias_plain = ""
    alias_url = ""
    if sender_alias and redis:
        bot_uname = await _get_bot_username(bot, redis)
        alias_plain = sender_alias
        alias_url = f"https://t.me/{bot_uname}" if bot_uname else ""
    elif sender_alias:
        alias_plain = sender_alias

    all_results: list[Message] = []
    for group in groups:
        input_media = []
        for i, item in enumerate(group):
            # C-3: Build caption/entities correctly for every item in the group.
            # Only the first item of the entire album receives the alias + signature;
            # all other items preserve their own original caption and entities.
            if i == 0 and alias_plain:
                raw_cap = f"{item.caption}\n\n{alias_plain}" if item.caption else alias_plain
            else:
                raw_cap = item.caption

            if i == 0:
                cap = apply_signature(raw_cap, signature, CAPTION_MAX_LEN)
            else:
                cap = item.caption  # preserve original caption verbatim

            # Rebuild entities for every item (not just the first)
            cap_entities = _rebuild_entities(item.caption_entities)

            # Add alias entity to the first item's caption
            if i == 0 and alias_plain and alias_url and cap:
                ent = _build_alias_entity(cap, alias_plain, alias_url)
                if ent:
                    cap_entities = (cap_entities or []) + [ent]

            match item.message_type:
                case MessageType.PHOTO:
                    input_media.append(
                        InputMediaPhoto(
                            media=item.file_id,  # type: ignore[arg-type]
                            caption=cap,
                            caption_entities=cap_entities,
                            # C-2: disable global parse_mode so entities are used
                            parse_mode=None,
                            has_spoiler=item.has_spoiler,
                            show_caption_above_media=item.show_caption_above_media or None,
                        )
                    )
                case MessageType.VIDEO:
                    input_media.append(
                        InputMediaVideo(
                            media=item.file_id,  # type: ignore[arg-type]
                            caption=cap,
                            caption_entities=cap_entities,
                            parse_mode=None,
                            has_spoiler=item.has_spoiler,
                            duration=item.duration,
                            width=item.width,
                            height=item.height,
                            supports_streaming=item.supports_streaming,
                            show_caption_above_media=item.show_caption_above_media or None,
                        )
                    )
                # Bug 1: ANIMATION is NOT accepted by sendMediaGroup (Bot API only
                # allows audio, document, photo, video).  Fall through to send_single.
                case MessageType.AUDIO:
                    input_media.append(
                        InputMediaAudio(
                            media=item.file_id,  # type: ignore[arg-type]
                            caption=cap,
                            caption_entities=cap_entities,
                            parse_mode=None,
                            duration=item.duration,
                            performer=item.performer,
                            title=item.title,
                        )
                    )
                case MessageType.DOCUMENT:
                    input_media.append(
                        InputMediaDocument(
                            media=item.file_id,  # type: ignore[arg-type]
                            caption=cap,
                            caption_entities=cap_entities,
                            parse_mode=None,
                        )
                    )
                case _:
                    # Fallback: send as individual message
                    await send_single(
                        bot, item, chat_id, signature if i == 0 else None,
                        sender_alias=sender_alias if i == 0 else None,
                        redis=redis,
                        allow_paid_broadcast=allow_paid_broadcast,
                    )
                    continue

        # Build reply_parameters for media groups
        mg_reply_params: ReplyParameters | None = None
        if reply_to_message_id:
            mg_reply_params = ReplyParameters(
                message_id=reply_to_message_id,
                allow_sending_without_reply=True,
            )

        if len(input_media) >= 2:
            # Send in chunks of 10 (Telegram limit).
            # M-6 (known limitation): only the first chunk carries reply_parameters;
            # subsequent chunks are sent without a reply reference to avoid floating
            # orphaned messages while keeping the first chunk anchored correctly.
            for chunk_start in range(0, len(input_media), 10):
                chunk = input_media[chunk_start : chunk_start + 10]
                chunk_results = await bot.send_media_group(
                    chat_id=chat_id,
                    media=chunk,
                    allow_paid_broadcast=allow_paid_broadcast,
                    reply_parameters=mg_reply_params,
                )
                # Only the first chunk gets the reply anchor
                mg_reply_params = None
                # Bug 5: collect ALL sent messages so the caller can log each one
                all_results.extend(chunk_results)
        elif len(input_media) == 1:
            # Single item after splitting – send individually
            single = await send_single(
                bot, group[0], chat_id, signature,
                sender_alias=sender_alias,
                redis=redis,
                allow_paid_broadcast=allow_paid_broadcast,
            )
            if single:
                all_results.append(single)

    return all_results


def _split_by_compatibility(
    items: list[NormalizedMessage],
) -> list[list[NormalizedMessage]]:
    """Split items into groups of compatible types for sendMediaGroup.

    Per Bot API docs, sendMediaGroup accepts only InputMediaPhoto, InputMediaVideo,
    InputMediaAudio, and InputMediaDocument.  InputMediaAnimation is NOT supported
    and will cause a 400 Bad Request.  ANIMATION items are sent individually.

    - PHOTO, VIDEO → can mix together (visual group)
    - AUDIO → audio only
    - DOCUMENT → documents only
    - ANIMATION and Others → individual sends
    """
    visual: list[NormalizedMessage] = []
    audio: list[NormalizedMessage] = []
    documents: list[NormalizedMessage] = []
    other: list[NormalizedMessage] = []

    for item in items:
        # Bug 1 fix: ANIMATION excluded from visual group — not valid in sendMediaGroup
        if item.message_type in (MessageType.PHOTO, MessageType.VIDEO):
            visual.append(item)
        elif item.message_type == MessageType.AUDIO:
            audio.append(item)
        elif item.message_type == MessageType.DOCUMENT:
            documents.append(item)
        else:
            # ANIMATION, STICKER, VIDEO_NOTE, etc. — all sent individually
            other.append(item)

    groups: list[list[NormalizedMessage]] = []
    if visual:
        groups.append(visual)
    if audio:
        groups.append(audio)
    if documents:
        groups.append(documents)
    # "other" types (including animation) are sent individually
    for item in other:
        groups.append([item])

    return groups


# ── Test helpers ──────────────────────────────────────────────────────
# L-1: Allow resetting the Redis-cached username between test runs.

async def _reset_bot_username_cache(redis: aioredis.Redis) -> None:
    """Delete the cached bot username from Redis. For use in tests only."""
    await redis.delete(_BOT_USERNAME_REDIS_KEY)
