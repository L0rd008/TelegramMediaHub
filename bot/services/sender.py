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
    source_chat_label: str | None = None,
    source_chat_url: str | None = None,
) -> Message | None:
    """Send a single NormalizedMessage to *chat_id*. Returns the sent Message.

    Attribution label priority:
      1. sender_alias  – user pseudonym (e.g. "golden_arrow") → links to bot
      2. source_chat_label – channel/anon-admin chat name    → links to chat URL
      3. Nothing (sticker, video note, or no registration)

    UI 1+2: attribution is combined with the signature onto a single line:
      "↗ golden_arrow · — via @MediaHubDistBot"
    This replaces the previous two-paragraph format.
    """

    # ── Determine attribution label and URL ──────────────────────────────────
    # Bug 3 fix: fall back to source chat identity when no user alias exists
    # (channel posts, anonymous admin posts).
    attr_label = ""
    attr_url = ""

    if sender_alias:
        attr_label = sender_alias
        if redis:
            bot_uname = await _get_bot_username(bot, redis)
            attr_url = f"https://t.me/{bot_uname}" if bot_uname else ""
        # attr_url stays "" when redis unavailable; alias still shown as plain text
    elif source_chat_label:
        attr_label = source_chat_label
        attr_url = source_chat_url or ""

    # ── Build combined attribution line (UI 1+2) ─────────────────────────────
    # Format: "↗ {label} · {signature}"  (one line, not two paragraphs)
    # The ↗ prefix visually marks attribution; · separates label from sig.
    if attr_label and signature:
        attribution = f"↗ {attr_label} · {signature}"
    elif attr_label:
        attribution = f"↗ {attr_label}"
    else:
        attribution = signature  # no label — fall back to plain signature

    # ── Apply attribution as the full "signature" argument ───────────────────
    # apply_signature handles: length limits (UTF-16 aware), content truncation,
    # None-safety, and the \n\n separator.
    raw_text = msg.text
    raw_caption = msg.caption

    text = apply_signature(raw_text, attribution, TEXT_MAX_LEN)
    caption = apply_signature(raw_caption, attribution, CAPTION_MAX_LEN)

    entities = _rebuild_entities(msg.entities)
    caption_entities = _rebuild_entities(msg.caption_entities)

    # Drop entities that now point outside the (possibly truncated) text
    if text is not None and text != raw_text:
        entities = _clip_entities(entities, text)
    if caption is not None and caption != raw_caption:
        caption_entities = _clip_entities(caption_entities, caption)

    # Add a text_link entity for the attribution label so it is clickable.
    # _build_alias_entity uses rfind so it targets the attribution-line
    # occurrence (last in text) even if the label also appears in the body.
    if attr_label and attr_url:
        if text:
            ent = _build_alias_entity(text, attr_label, attr_url)
            if ent:
                entities = (entities or []) + [ent]
        if caption:
            ent = _build_alias_entity(caption, attr_label, attr_url)
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
                    source_chat_label=source_chat_label,
                    source_chat_url=source_chat_url,
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
    source_chat_label: str | None = None,
    source_chat_url: str | None = None,
) -> list[Message]:
    """Send a media group (album) to *chat_id*.

    Handles type-compatibility splitting per Telegram API:
    - Photos and videos can be mixed freely
    - Audio can only be grouped with other audio
    - Documents can only be grouped with other documents
    - Animations, stickers, video notes → sent individually via send_single
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
            source_chat_label=source_chat_label,
            source_chat_url=source_chat_url,
        )
        return [result] if result else []

    # Group items by compatible types
    groups = _split_by_compatibility(msg.group_items)

    # ── Attribution setup (mirrors send_single logic) ─────────────────────────
    # Bug 3 fix: use source_chat_label when sender_alias is absent.
    attr_label = ""
    attr_url = ""
    if sender_alias:
        attr_label = sender_alias
        if redis:
            bot_uname = await _get_bot_username(bot, redis)
            attr_url = f"https://t.me/{bot_uname}" if bot_uname else ""
    elif source_chat_label:
        attr_label = source_chat_label
        attr_url = source_chat_url or ""

    # Build combined attribution line (UI 1+2, same format as send_single)
    if attr_label and signature:
        attribution = f"↗ {attr_label} · {signature}"
    elif attr_label:
        attribution = f"↗ {attr_label}"
    else:
        attribution = signature  # plain signature, no label prefix

    all_results: list[Message] = []
    for group in groups:
        input_media = []
        for i, item in enumerate(group):
            if i == 0:
                # First item of the album: apply attribution (label + signature).
                cap = apply_signature(item.caption, attribution, CAPTION_MAX_LEN)
                cap_entities = _rebuild_entities(item.caption_entities)

                # Clip entities that now fall outside the (possibly truncated) caption
                if cap is not None and cap != item.caption:
                    cap_entities = _clip_entities(cap_entities, cap)

                # Add text_link entity for the attribution label
                if attr_label and attr_url and cap:
                    ent = _build_alias_entity(cap, attr_label, attr_url)
                    if ent:
                        cap_entities = (cap_entities or []) + [ent]
            else:
                # Subsequent items: preserve original caption and entities verbatim.
                cap = item.caption
                cap_entities = _rebuild_entities(item.caption_entities)

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
                    # ANIMATION, STICKER, VIDEO_NOTE etc. are not valid in
                    # sendMediaGroup.  Send individually and collect the result.
                    # Bug 2 fix: was `await send_single(...)` with discarded result.
                    # The missing append meant these messages were never logged to
                    # send_log, breaking reply threading and ban-cleanup for them.
                    fallback_result = await send_single(
                        bot, item, chat_id,
                        signature if i == 0 else None,
                        sender_alias=sender_alias if i == 0 else None,
                        redis=redis,
                        allow_paid_broadcast=allow_paid_broadcast,
                        source_chat_label=source_chat_label if i == 0 else None,
                        source_chat_url=source_chat_url if i == 0 else None,
                    )
                    if fallback_result:
                        all_results.append(fallback_result)  # Bug 2 fix
                    continue

        # Build reply_parameters for the media group batch
        mg_reply_params: ReplyParameters | None = None
        if reply_to_message_id:
            mg_reply_params = ReplyParameters(
                message_id=reply_to_message_id,
                allow_sending_without_reply=True,
            )

        if len(input_media) >= 2:
            # Send in chunks of 10 (Telegram limit).
            # Only the first chunk carries reply_parameters; subsequent chunks
            # are sent without a reply anchor to avoid floating orphaned messages.
            for chunk_start in range(0, len(input_media), 10):
                chunk = input_media[chunk_start : chunk_start + 10]
                chunk_results = await bot.send_media_group(
                    chat_id=chat_id,
                    media=chunk,
                    allow_paid_broadcast=allow_paid_broadcast,
                    reply_parameters=mg_reply_params,
                )
                mg_reply_params = None  # Only first chunk is anchored
                all_results.extend(chunk_results)
        elif len(input_media) == 1:
            # Single item after compatibility split – send individually
            single = await send_single(
                bot, group[0], chat_id, signature,
                sender_alias=sender_alias,
                redis=redis,
                allow_paid_broadcast=allow_paid_broadcast,
                source_chat_label=source_chat_label,
                source_chat_url=source_chat_url,
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
