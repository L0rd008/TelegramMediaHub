"""Tests for the message normalizer service."""

from __future__ import annotations

from unittest.mock import MagicMock

from bot.services.normalizer import normalize
from bot.utils.enums import MessageType


class TestNormalizeText:
    def test_text_message(self, make_message):
        msg = make_message(text="Hello world", message_id=42, chat_id=100)
        result = normalize(msg)
        assert result is not None
        assert result.message_type == MessageType.TEXT
        assert result.text == "Hello world"
        assert result.source_chat_id == 100
        assert result.source_message_id == 42
        assert result.file_id is None

    def test_text_with_entities(self, make_message):
        entity = MagicMock()
        entity.type = "bold"
        entity.offset = 0
        entity.length = 5
        entity.url = None
        entity.user = None
        entity.language = None
        entity.custom_emoji_id = None
        msg = make_message(text="Hello world", entities=[entity])
        result = normalize(msg)
        assert result is not None
        assert result.entities is not None
        assert len(result.entities) == 1
        assert result.entities[0]["type"] == "bold"


class TestNormalizePhoto:
    def test_photo_uses_largest_size(self, make_message, make_photo_size):
        small = make_photo_size(file_id="small", file_unique_id="u_small", file_size=1000)
        large = make_photo_size(file_id="large", file_unique_id="u_large", file_size=50000)
        msg = make_message(photo=[small, large], caption="A photo")
        result = normalize(msg)
        assert result is not None
        assert result.message_type == MessageType.PHOTO
        assert result.file_id == "large"
        assert result.file_unique_id == "u_large"
        assert result.caption == "A photo"

    def test_photo_with_spoiler(self, make_message, make_photo_size):
        photo = make_photo_size()
        msg = make_message(photo=[photo])
        msg.has_media_spoiler = True
        result = normalize(msg)
        assert result is not None
        assert result.has_spoiler is True


class TestNormalizeVideo:
    def test_video_message(self, make_message):
        video = MagicMock()
        video.file_id = "vid_123"
        video.file_unique_id = "uniq_vid"
        video.duration = 30
        video.width = 1920
        video.height = 1080
        msg = make_message(video=video, caption="A video")
        result = normalize(msg)
        assert result is not None
        assert result.message_type == MessageType.VIDEO
        assert result.file_id == "vid_123"
        assert result.duration == 30
        assert result.width == 1920
        assert result.supports_streaming is True


class TestNormalizeAudio:
    def test_audio_message(self, make_message):
        audio = MagicMock()
        audio.file_id = "audio_123"
        audio.file_unique_id = "uniq_audio"
        audio.duration = 180
        audio.performer = "Artist"
        audio.title = "Song"
        audio.file_name = "song.mp3"
        msg = make_message(audio=audio)
        result = normalize(msg)
        assert result is not None
        assert result.message_type == MessageType.AUDIO
        assert result.performer == "Artist"
        assert result.title == "Song"
        assert result.file_name == "song.mp3"


class TestNormalizeDocument:
    def test_document_message(self, make_message):
        doc = MagicMock()
        doc.file_id = "doc_123"
        doc.file_unique_id = "uniq_doc"
        doc.file_name = "report.pdf"
        msg = make_message(document=doc, caption="Report")
        result = normalize(msg)
        assert result is not None
        assert result.message_type == MessageType.DOCUMENT
        assert result.file_name == "report.pdf"
        assert result.caption == "Report"


class TestNormalizeVoice:
    def test_voice_message(self, make_message):
        voice = MagicMock()
        voice.file_id = "voice_123"
        voice.file_unique_id = "uniq_voice"
        voice.duration = 5
        msg = make_message(voice=voice)
        result = normalize(msg)
        assert result is not None
        assert result.message_type == MessageType.VOICE
        assert result.duration == 5


class TestNormalizeVideoNote:
    def test_video_note(self, make_message):
        vn = MagicMock()
        vn.file_id = "vn_123"
        vn.file_unique_id = "uniq_vn"
        vn.duration = 10
        vn.length = 240  # diameter
        msg = make_message(video_note=vn)
        result = normalize(msg)
        assert result is not None
        assert result.message_type == MessageType.VIDEO_NOTE
        assert result.width == 240
        assert result.height == 240


class TestNormalizeSticker:
    def test_sticker(self, make_message):
        sticker = MagicMock()
        sticker.file_id = "sticker_123"
        sticker.file_unique_id = "uniq_sticker"
        msg = make_message(sticker=sticker)
        result = normalize(msg)
        assert result is not None
        assert result.message_type == MessageType.STICKER
        assert result.caption is None  # Stickers have no caption


class TestNormalizeAnimation:
    def test_animation(self, make_message):
        anim = MagicMock()
        anim.file_id = "anim_123"
        anim.file_unique_id = "uniq_anim"
        anim.duration = 3
        anim.width = 320
        anim.height = 240
        msg = make_message(animation=anim, caption="GIF")
        result = normalize(msg)
        assert result is not None
        assert result.message_type == MessageType.ANIMATION
        assert result.caption == "GIF"


class TestNormalizeEdgeCases:
    def test_paid_media_is_blocked(self, make_message):
        msg = make_message(text="paid", paid_media=MagicMock())
        result = normalize(msg)
        assert result is None

    def test_unsupported_message_returns_none(self, make_message):
        # A message with no text, photo, video, etc.
        msg = make_message()
        result = normalize(msg)
        assert result is None

    def test_media_group_id_is_preserved(self, make_message, make_photo_size):
        photo = make_photo_size()
        msg = make_message(photo=[photo], media_group_id="album_42")
        result = normalize(msg)
        assert result is not None
        assert result.media_group_id == "album_42"
