"""Text utilities – hashing and truncation."""

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
