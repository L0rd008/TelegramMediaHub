"""Message normalizer – extracts a uniform NormalizedMessage from any incoming Message."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from aiogram.types import Message, MessageEntity

from bot.utils.enums import MessageType

logger = logging.getLogger(__name__)


@dataclass
class NormalizedMessage:
    """Unified representation of any content message."""

    message_type: MessageType
    source_chat_id: int
    source_message_id: int
    media_group_id: str | None = None

    # Content
    text: str | None = None
    caption: str | None = None
    entities: list[dict[str, Any]] | None = None
    caption_entities: list[dict[str, Any]] | None = None

    # Media
    file_id: str | None = None
    file_unique_id: str | None = None

    # Media-specific metadata
    duration: int | None = None
    width: int | None = None
    height: int | None = None
    performer: str | None = None
    title: str | None = None
    file_name: str | None = None

    # Flags
    has_spoiler: bool = False
    show_caption_above_media: bool = False
    supports_streaming: bool | None = None

    # Media group items (populated only for MEDIA_GROUP type)
    group_items: list[NormalizedMessage] = field(default_factory=list)

    # Sender identity (None for channel posts without from_user)
    source_user_id: int | None = None

    # Reply threading – set by the message handler after send_log reverse lookup
    reply_source_chat_id: int | None = None
    reply_source_message_id: int | None = None


def _entities_to_dicts(entities: list[MessageEntity] | None) -> list[dict[str, Any]] | None:
    """Convert MessageEntity list to serializable dicts (for JSON storage in media group buffer)."""
    if not entities:
        return None
    result = []
    for e in entities:
        d: dict[str, Any] = {
            "type": e.type,
            "offset": e.offset,
            "length": e.length,
        }
        if e.url:
            d["url"] = e.url
        if e.user:
            d["user"] = {"id": e.user.id, "is_bot": e.user.is_bot, "first_name": e.user.first_name}
        if e.language:
            d["language"] = e.language
        if e.custom_emoji_id:
            d["custom_emoji_id"] = e.custom_emoji_id
        result.append(d)
    return result


def normalize(message: Message) -> NormalizedMessage | None:
    """Extract a NormalizedMessage from an incoming Message.

    Returns None if the message type is not supported (service messages, etc).
    """
    # ── Block paid media ──────────────────────────────────────────────
    if message.paid_media:
        logger.debug("Skipping paid media message %d", message.message_id)
        return None

    base = dict(
        source_chat_id=message.chat.id,
        source_message_id=message.message_id,
        source_user_id=message.from_user.id if message.from_user else None,
        media_group_id=message.media_group_id,
        show_caption_above_media=bool(getattr(message, "show_caption_above_media", False)),
    )

    # ── Text ──────────────────────────────────────────────────────────
    if message.text:
        return NormalizedMessage(
            message_type=MessageType.TEXT,
            text=message.text,
            entities=_entities_to_dicts(message.entities),
            **base,
        )

    # ── Photo ─────────────────────────────────────────────────────────
    if message.photo:
        # Use the largest photo size (last in the list by convention, or largest by file_size)
        largest = max(message.photo, key=lambda p: p.file_size or 0)
        return NormalizedMessage(
            message_type=MessageType.PHOTO,
            file_id=largest.file_id,
            file_unique_id=largest.file_unique_id,
            width=largest.width,
            height=largest.height,
            caption=message.caption,
            caption_entities=_entities_to_dicts(message.caption_entities),
            has_spoiler=bool(getattr(message, "has_media_spoiler", False)),
            **base,
        )

    # ── Video ─────────────────────────────────────────────────────────
    if message.video:
        v = message.video
        return NormalizedMessage(
            message_type=MessageType.VIDEO,
            file_id=v.file_id,
            file_unique_id=v.file_unique_id,
            duration=v.duration,
            width=v.width,
            height=v.height,
            caption=message.caption,
            caption_entities=_entities_to_dicts(message.caption_entities),
            has_spoiler=bool(getattr(message, "has_media_spoiler", False)),
            supports_streaming=True,  # Default to true when re-sending
            **base,
        )

    # ── Animation (GIF) ──────────────────────────────────────────────
    if message.animation:
        a = message.animation
        return NormalizedMessage(
            message_type=MessageType.ANIMATION,
            file_id=a.file_id,
            file_unique_id=a.file_unique_id,
            duration=a.duration,
            width=a.width,
            height=a.height,
            caption=message.caption,
            caption_entities=_entities_to_dicts(message.caption_entities),
            has_spoiler=bool(getattr(message, "has_media_spoiler", False)),
            **base,
        )

    # ── Audio ─────────────────────────────────────────────────────────
    if message.audio:
        au = message.audio
        return NormalizedMessage(
            message_type=MessageType.AUDIO,
            file_id=au.file_id,
            file_unique_id=au.file_unique_id,
            duration=au.duration,
            performer=au.performer,
            title=au.title,
            file_name=au.file_name,
            caption=message.caption,
            caption_entities=_entities_to_dicts(message.caption_entities),
            **base,
        )

    # ── Document ──────────────────────────────────────────────────────
    if message.document:
        doc = message.document
        return NormalizedMessage(
            message_type=MessageType.DOCUMENT,
            file_id=doc.file_id,
            file_unique_id=doc.file_unique_id,
            file_name=doc.file_name,
            caption=message.caption,
            caption_entities=_entities_to_dicts(message.caption_entities),
            **base,
        )

    # ── Voice ─────────────────────────────────────────────────────────
    if message.voice:
        vo = message.voice
        return NormalizedMessage(
            message_type=MessageType.VOICE,
            file_id=vo.file_id,
            file_unique_id=vo.file_unique_id,
            duration=vo.duration,
            caption=message.caption,
            caption_entities=_entities_to_dicts(message.caption_entities),
            **base,
        )

    # ── Video Note ────────────────────────────────────────────────────
    if message.video_note:
        vn = message.video_note
        return NormalizedMessage(
            message_type=MessageType.VIDEO_NOTE,
            file_id=vn.file_id,
            file_unique_id=vn.file_unique_id,
            duration=vn.duration,
            width=vn.length,  # diameter
            height=vn.length,
            **base,
        )

    # ── Sticker ───────────────────────────────────────────────────────
    if message.sticker:
        st = message.sticker
        return NormalizedMessage(
            message_type=MessageType.STICKER,
            file_id=st.file_id,
            file_unique_id=st.file_unique_id,
            **base,
        )

    # ── Unsupported ───────────────────────────────────────────────────
    logger.debug(
        "Skipping unsupported message %d in chat %d",
        message.message_id,
        message.chat.id,
    )
    return None
