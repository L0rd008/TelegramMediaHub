"""Tests for the signature service."""

from __future__ import annotations

from bot.services.signature import apply_signature


class TestApplySignature:
    def test_no_content_no_signature(self):
        assert apply_signature(None, None, 4096) is None

    def test_no_signature_returns_content(self):
        assert apply_signature("Hello", None, 4096) == "Hello"

    def test_no_content_returns_signature(self):
        assert apply_signature(None, "— via @Bot", 4096) == "— via @Bot"

    def test_appends_with_separator(self):
        result = apply_signature("Hello", "— via @Bot", 4096)
        assert result == "Hello\n\n— via @Bot"

    def test_respects_max_len(self):
        # Content + separator + signature must exceed max_len to trigger truncation
        signature = "— via @Bot"
        # Separator is "\n\n" (2 chars). Need content + 2 + len(sig) > 1024
        content = "A" * 1020  # 1020 + 2 + ~10 = 1032 > 1024
        result = apply_signature(content, signature, 1024)
        assert result is not None
        assert len(result) <= 1024
        assert result.endswith(signature)
        assert "..." in result

    def test_signature_never_truncated(self):
        content = "A" * 100
        signature = "— very long signature text here"
        result = apply_signature(content, signature, 50)
        assert result is not None
        # Content may be truncated, but signature should be intact
        assert result.endswith(signature) or result == signature[:50]

    def test_exact_max_len(self):
        content = "Hello"
        signature = "Sig"
        full = f"{content}\n\n{signature}"
        result = apply_signature(content, signature, len(full))
        assert result == full

    def test_one_char_over_truncates(self):
        content = "Hello"
        signature = "Sig"
        full = f"{content}\n\n{signature}"
        result = apply_signature(content, signature, len(full) - 1)
        assert result is not None
        assert len(result) <= len(full) - 1
        assert "..." in result

    def test_caption_limit(self):
        content = "B" * 900
        signature = "— via @Bot"
        result = apply_signature(content, signature, 1024)
        assert result is not None
        assert len(result) <= 1024

    def test_empty_string_content(self):
        result = apply_signature("", "sig", 100)
        assert result == "sig"
