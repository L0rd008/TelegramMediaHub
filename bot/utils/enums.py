"""Enums used across the bot."""

from __future__ import annotations

from enum import Enum


class MessageType(str, Enum):
    TEXT = "TEXT"
    PHOTO = "PHOTO"
    VIDEO = "VIDEO"
    ANIMATION = "ANIMATION"
    AUDIO = "AUDIO"
    DOCUMENT = "DOCUMENT"
    VOICE = "VOICE"
    VIDEO_NOTE = "VIDEO_NOTE"
    STICKER = "STICKER"
    MEDIA_GROUP = "MEDIA_GROUP"
