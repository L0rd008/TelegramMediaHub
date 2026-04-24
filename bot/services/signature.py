"""Signature service – append configurable promotional signature."""

from __future__ import annotations

from bot.utils.text import utf16_len

_SEPARATOR = "\n\n"
_ELLIPSIS = "..."


def apply_signature(
    content: str | None,
    signature: str | None,
    max_len: int,
) -> str | None:
    """Append *signature* to *content*, respecting *max_len* in UTF-16 units.

    Rules:
    - If neither content nor signature → return None.
    - Signature is NEVER truncated; content is trimmed to make room.
    - Separator is two newlines.
    - Length is measured in UTF-16 code units (Telegram's unit), NOT Python
      codepoints.  This prevents TelegramBadRequest for emoji-heavy messages
      where each emoji occupies 2 UTF-16 units but only 1 Python codepoint.
    """
    if not content and not signature:
        return None
    if not signature:
        return content
    if not content:
        return signature

    full = f"{content}{_SEPARATOR}{signature}"

    if utf16_len(full) <= max_len:
        return full

    # Content is too long — trim it character-by-character (UTF-16 aware)
    # to the largest prefix that still leaves room for separator + signature + ellipsis.
    reserved = (
        utf16_len(_SEPARATOR)
        + utf16_len(signature)
        + utf16_len(_ELLIPSIS)
    )
    target = max_len - reserved
    if target <= 0:
        # Signature alone fills max_len — return just the signature, hard-capped.
        # Walk rune-by-rune to avoid splitting surrogate pairs.
        trimmed_sig: list[str] = []
        units = 0
        for ch in signature:
            ch_units = utf16_len(ch)
            if units + ch_units > max_len:
                break
            trimmed_sig.append(ch)
            units += ch_units
        return "".join(trimmed_sig)

    # Trim content to fit
    trimmed: list[str] = []
    units = 0
    for ch in content:
        ch_units = utf16_len(ch)
        if units + ch_units > target:
            break
        trimmed.append(ch)
        units += ch_units

    return f"{''.join(trimmed)}{_ELLIPSIS}{_SEPARATOR}{signature}"
