"""Text utilities – hashing, truncation, and encoding helpers."""

from __future__ import annotations

import hashlib


def text_hash(text: str) -> str:
    """Return first 32 chars of SHA-256 hex digest of normalized text."""
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:32]


def truncate(text: str, max_len: int, suffix: str = "...") -> str:
    """Truncate *text* to *max_len* characters, appending *suffix* if cut."""
    if len(text) <= max_len:
        return text
    return text[: max_len - len(suffix)] + suffix


def utf16_len(s: str) -> int:
    """Return the number of UTF-16 code units in *s*.

    The Telegram Bot API measures message length and entity offsets in UTF-16
    code units (surrogates).  BMP characters are 1 unit; supplementary /
    astral characters (most emoji, e.g. 🔥💬🎯) are 2 units.  Using Python's
    built-in len() (which counts Unicode codepoints, 1 per character) gives
    wrong results for strings that contain any emoji before the measured span,
    leading to entity misalignment and silent TelegramBadRequest errors when
    messages exceed the effective limit.

    This is the single canonical implementation shared by sender.py and
    signature.py.  Do not duplicate it.
    """
    return len(s.encode("utf-16-le")) // 2
