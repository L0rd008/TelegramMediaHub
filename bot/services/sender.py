"""Sender service – maps NormalizedMessage to the correct Bot API send* call.

Key principle: ALWAYS use file_id reuse, NEVER forwardMessage or copyMessage.
This ensures zero forwarding metadata on sent messages.
"""

from __future__ import annotations

import logging
from typing import Any

import redis.asyncio as aioredis
from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.types import (
    InputMediaAnimation,
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

logger = logging.getLogger(__name__)

# Limits per Telegram Bot API
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
        # Note: user entities are simplified so we skip them for re-sending
        entities.append(MessageEntity(**kwargs))
    return entities or None


def _build_alias_entity(
    content: str, alias_plain: str, alias_url: str
) -> MessageEntity | None:
    """Find the alias in *content* and return a text_link MessageEntity for it."""
    idx = content.rfind(alias_plain)
    if idx < 0:
        return None
    return MessageEntity(
        type="text_link", offset=idx, length=len(alias_plain), url=alias_url
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
) -> Message | None:
    """Send a media group (album) to *chat_id*.

    Handles type-compatibility splitting per Telegram API:
    - Photos and videos can be mixed freely
    - Audio can only be grouped with other audio
    - Documents can only be grouped with other documents
    """
    if not msg.group_items:
        logger.warning("Media group with no items, skipping")
        return None

    # Single item → send as individual message
    if len(msg.group_items) == 1:
        return await send_single(
            bot, msg.group_items[0], chat_id, signature,
            reply_to_message_id=reply_to_message_id,
            sender_alias=sender_alias,
            redis=redis,
            allow_paid_broadcast=allow_paid_broadcast,
        )

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

    first_result = None
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
                case MessageType.ANIMATION:
                    input_media.append(
                        InputMediaAnimation(
                            media=item.file_id,  # type: ignore[arg-type]
                            caption=cap,
                            caption_entities=cap_entities,
                            parse_mode=None,
                            has_spoiler=item.has_spoiler,
                            duration=item.duration,
                            width=item.width,
                            height=item.height,
                            show_caption_above_media=item.show_caption_above_media or None,
                        )
                    )
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
                result = await bot.send_media_group(
                    chat_id=chat_id,
                    media=chunk,
                    allow_paid_broadcast=allow_paid_broadcast,
                    reply_parameters=mg_reply_params,
                )
                # Only the first chunk gets the reply anchor
                mg_reply_params = None
                if first_result is None and result:
                    first_result = result[0]
        elif len(input_media) == 1:
            # Single item after splitting – send individually
            result = await send_single(
                bot, group[0], chat_id, signature,
                sender_alias=sender_alias,
                redis=redis,
                allow_paid_broadcast=allow_paid_broadcast,
            )
            if first_result is None:
                first_result = result

    return first_result


def _split_by_compatibility(
    items: list[NormalizedMessage],
) -> list[list[NormalizedMessage]]:
    """Split items into groups of compatible types for sendMediaGroup.

    - PHOTO, VIDEO, ANIMATION → can mix together
    - AUDIO → audio only
    - DOCUMENT → documents only
    - Others → individual sends
    """
    visual: list[NormalizedMessage] = []
    audio: list[NormalizedMessage] = []
    documents: list[NormalizedMessage] = []
    other: list[NormalizedMessage] = []

    for item in items:
        if item.message_type in (MessageType.PHOTO, MessageType.VIDEO, MessageType.ANIMATION):
            visual.append(item)
        elif item.message_type == MessageType.AUDIO:
            audio.append(item)
        elif item.message_type == MessageType.DOCUMENT:
            documents.append(item)
        else:
            other.append(item)

    groups: list[list[NormalizedMessage]] = []
    if visual:
        groups.append(visual)
    if audio:
        groups.append(audio)
    if documents:
        groups.append(documents)
    # "other" types are sent individually (wrapped in single-item lists)
    for item in other:
        groups.append([item])

    return groups


# ── Test helpers ──────────────────────────────────────────────────────
# L-1: Allow resetting the Redis-cached username between test runs.

async def _reset_bot_username_cache(redis: aioredis.Redis) -> None:
    """Delete the cached bot username from Redis. For use in tests only."""
    await redis.delete(_BOT_USERNAME_REDIS_KEY)
