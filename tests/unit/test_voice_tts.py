"""Tests for src/voice/tts.py — text-to-speech synthesis."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.voice.tts import is_tts_configured, synthesize_speech


class TestIsTtsConfigured:

    def test_elevenlabs_needs_key(self, monkeypatch):
        monkeypatch.setenv("TTS_PROVIDER", "elevenlabs")
        monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
        assert is_tts_configured() is False

    def test_elevenlabs_with_key(self, monkeypatch):
        monkeypatch.setenv("TTS_PROVIDER", "elevenlabs")
        monkeypatch.setenv("ELEVENLABS_API_KEY", "el-test")
        assert is_tts_configured() is True

    def test_openai_needs_key(self, monkeypatch):
        monkeypatch.setenv("TTS_PROVIDER", "openai")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        assert is_tts_configured() is False

    def test_openai_with_key(self, monkeypatch):
        monkeypatch.setenv("TTS_PROVIDER", "openai")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        assert is_tts_configured() is True

    def test_default_provider_is_elevenlabs(self, monkeypatch):
        monkeypatch.delenv("TTS_PROVIDER", raising=False)
        monkeypatch.setenv("ELEVENLABS_API_KEY", "el-test")
        assert is_tts_configured() is True


class TestSynthesizeSpeech:

    @pytest.mark.asyncio
    async def test_empty_text_returns_none(self):
        result = await synthesize_speech("")
        assert result is None

    @pytest.mark.asyncio
    async def test_whitespace_only_returns_none(self):
        result = await synthesize_speech("   ")
        assert result is None

    @pytest.mark.asyncio
    async def test_unconfigured_returns_none(self, monkeypatch):
        monkeypatch.setenv("TTS_PROVIDER", "elevenlabs")
        monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
        result = await synthesize_speech("Hello")
        assert result is None

    @pytest.mark.asyncio
    async def test_elevenlabs_happy_path(self, monkeypatch):
        monkeypatch.setenv("TTS_PROVIDER", "elevenlabs")
        monkeypatch.setenv("ELEVENLABS_API_KEY", "el-test")

        audio_bytes = b"\xff\xfb\x90\x00" * 100  # fake MP3

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read = AsyncMock(return_value=audio_bytes)

        mock_session = MagicMock()
        mock_cm = MagicMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_response)
        mock_cm.__aexit__ = AsyncMock(return_value=None)
        mock_session.post = MagicMock(return_value=mock_cm)

        with patch(
            "src.voice.tts._get_tts_session",
            new_callable=AsyncMock,
            return_value=mock_session,
        ):
            result = await synthesize_speech("Hello world")

        assert result == audio_bytes

    @pytest.mark.asyncio
    async def test_elevenlabs_api_error_returns_none(self, monkeypatch):
        monkeypatch.setenv("TTS_PROVIDER", "elevenlabs")
        monkeypatch.setenv("ELEVENLABS_API_KEY", "el-test")

        mock_response = MagicMock()
        mock_response.status = 500
        mock_response.text = AsyncMock(return_value="Server Error")

        mock_session = MagicMock()
        mock_cm = MagicMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_response)
        mock_cm.__aexit__ = AsyncMock(return_value=None)
        mock_session.post = MagicMock(return_value=mock_cm)

        with patch(
            "src.voice.tts._get_tts_session",
            new_callable=AsyncMock,
            return_value=mock_session,
        ):
            result = await synthesize_speech("Hello")

        assert result is None

    @pytest.mark.asyncio
    async def test_openai_happy_path(self, monkeypatch):
        monkeypatch.setenv("TTS_PROVIDER", "openai")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

        audio_bytes = b"\x00\x01\x02" * 100

        mock_response = MagicMock()
        mock_response.aread = AsyncMock(return_value=audio_bytes)

        mock_client = MagicMock()
        mock_client.audio.speech.create = AsyncMock(return_value=mock_response)
        mock_client.close = MagicMock(return_value=None)

        with patch("src.voice.tts.AsyncOpenAI", return_value=mock_client):
            result = await synthesize_speech("Hello from OpenAI")

        assert result == audio_bytes

    @pytest.mark.asyncio
    async def test_openai_exception_returns_none(self, monkeypatch):
        monkeypatch.setenv("TTS_PROVIDER", "openai")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

        mock_client = MagicMock()
        mock_client.audio.speech.create = AsyncMock(side_effect=RuntimeError("API error"))
        mock_client.close = MagicMock(return_value=None)

        with patch("src.voice.tts.AsyncOpenAI", return_value=mock_client):
            result = await synthesize_speech("Will fail")

        assert result is None

    @pytest.mark.asyncio
    async def test_unsupported_provider_returns_none(self, monkeypatch):
        monkeypatch.setenv("TTS_PROVIDER", "google")
        monkeypatch.setenv("ELEVENLABS_API_KEY", "")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        result = await synthesize_speech("Hello")
        assert result is None
