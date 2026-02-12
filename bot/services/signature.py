"""Signature service – append configurable promotional signature."""

from __future__ import annotations


def apply_signature(
    content: str | None,
    signature: str | None,
    max_len: int,
) -> str | None:
    """Append *signature* to *content*, respecting *max_len*.

    Rules:
    - If neither content nor signature, return None
    - Signature is never truncated; content is truncated if needed
    - Separator is two newlines
    """
    if not content and not signature:
        return None
    if not signature:
        return content
    if not content:
        return signature

    separator = "\n\n"
    full = f"{content}{separator}{signature}"

    if len(full) <= max_len:
        return full

    # Truncate content to fit signature (signature is never truncated)
    ellipsis = "..."
    available = max_len - len(separator) - len(signature) - len(ellipsis)
    if available <= 0:
        # Signature alone exceeds max_len – just return signature truncated
        return signature[:max_len]
    return f"{content[:available]}{ellipsis}{separator}{signature}"
