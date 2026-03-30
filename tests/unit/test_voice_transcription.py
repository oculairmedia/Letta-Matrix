"""Tests for src/voice/transcription.py — audio transcription providers."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.voice.transcription import (
    TranscriptionResult,
    is_transcription_configured,
    transcribe_audio,
)


class TestIsTranscriptionConfigured:

    def test_whisper_always_configured(self, monkeypatch):
        monkeypatch.setenv("TRANSCRIPTION_PROVIDER", "whisper")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        assert is_transcription_configured() is True

    def test_openai_needs_key(self, monkeypatch):
        monkeypatch.setenv("TRANSCRIPTION_PROVIDER", "openai")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        assert is_transcription_configured() is False

    def test_openai_with_key(self, monkeypatch):
        monkeypatch.setenv("TRANSCRIPTION_PROVIDER", "openai")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        assert is_transcription_configured() is True

    def test_mistral_needs_key(self, monkeypatch):
        monkeypatch.setenv("TRANSCRIPTION_PROVIDER", "mistral")
        monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
        assert is_transcription_configured() is False

    def test_mistral_with_key(self, monkeypatch):
        monkeypatch.setenv("TRANSCRIPTION_PROVIDER", "mistral")
        monkeypatch.setenv("MISTRAL_API_KEY", "mk-test")
        assert is_transcription_configured() is True

    def test_unknown_provider(self, monkeypatch):
        monkeypatch.setenv("TRANSCRIPTION_PROVIDER", "unknown")
        assert is_transcription_configured() is False

    def test_default_provider_is_whisper(self, monkeypatch):
        monkeypatch.delenv("TRANSCRIPTION_PROVIDER", raising=False)
        assert is_transcription_configured() is True


class TestTranscribeAudio:

    @pytest.mark.asyncio
    async def test_empty_audio_returns_error(self, monkeypatch):
        monkeypatch.setenv("TRANSCRIPTION_PROVIDER", "whisper")
        result = await transcribe_audio(b"")
        assert result.text == ""
        assert result.error == "No audio data provided"

    @pytest.mark.asyncio
    async def test_unconfigured_provider_returns_error(self, monkeypatch):
        monkeypatch.setenv("TRANSCRIPTION_PROVIDER", "openai")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        result = await transcribe_audio(b"\x00\x01\x02")
        assert result.text == ""
        assert "not configured" in result.error

    @pytest.mark.asyncio
    async def test_openai_provider_happy_path(self, monkeypatch):
        monkeypatch.setenv("TRANSCRIPTION_PROVIDER", "openai")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

        mock_response = MagicMock()
        mock_response.text = "Hello from audio"

        mock_client = MagicMock()
        mock_client.audio.transcriptions.create = AsyncMock(return_value=mock_response)

        with patch("src.voice.transcription.AsyncOpenAI", return_value=mock_client):
            result = await transcribe_audio(b"\x00\x01\x02", "test.ogg")

        assert result.text == "Hello from audio"
        assert result.error is None

    @pytest.mark.asyncio
    async def test_openai_provider_exception(self, monkeypatch):
        monkeypatch.setenv("TRANSCRIPTION_PROVIDER", "openai")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

        mock_client = MagicMock()
        mock_client.audio.transcriptions.create = AsyncMock(
            side_effect=RuntimeError("API down")
        )

        with patch("src.voice.transcription.AsyncOpenAI", return_value=mock_client):
            result = await transcribe_audio(b"\x00\x01\x02")

        assert result.text == ""
        assert "API down" in result.error

    @pytest.mark.asyncio
    async def test_whisper_provider_happy_path(self, monkeypatch):
        monkeypatch.setenv("TRANSCRIPTION_PROVIDER", "whisper")

        mock_response = MagicMock()
        mock_response.text = "Whisper result"

        mock_client = MagicMock()
        mock_client.audio.transcriptions.create = AsyncMock(return_value=mock_response)

        with patch("src.voice.transcription.AsyncOpenAI", return_value=mock_client):
            result = await transcribe_audio(b"\x00\x01\x02", "voice.ogg")

        assert result.text == "Whisper result"
        assert result.error is None

    @pytest.mark.asyncio
    async def test_mistral_provider_happy_path(self, monkeypatch):
        monkeypatch.setenv("TRANSCRIPTION_PROVIDER", "mistral")
        monkeypatch.setenv("MISTRAL_API_KEY", "mk-test")

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"text": "Mistral result"})

        mock_session = MagicMock()
        mock_cm = MagicMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_response)
        mock_cm.__aexit__ = AsyncMock(return_value=None)
        mock_session.post = MagicMock(return_value=mock_cm)

        with patch(
            "src.voice.transcription._get_transcription_session",
            new_callable=AsyncMock,
            return_value=mock_session,
        ):
            result = await transcribe_audio(b"\x00\x01\x02", "voice.ogg")

        assert result.text == "Mistral result"
        assert result.error is None

    @pytest.mark.asyncio
    async def test_mistral_provider_api_error(self, monkeypatch):
        monkeypatch.setenv("TRANSCRIPTION_PROVIDER", "mistral")
        monkeypatch.setenv("MISTRAL_API_KEY", "mk-test")

        mock_response = MagicMock()
        mock_response.status = 500
        mock_response.text = AsyncMock(return_value="Internal Server Error")

        mock_session = MagicMock()
        mock_cm = MagicMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_response)
        mock_cm.__aexit__ = AsyncMock(return_value=None)
        mock_session.post = MagicMock(return_value=mock_cm)

        with patch(
            "src.voice.transcription._get_transcription_session",
            new_callable=AsyncMock,
            return_value=mock_session,
        ):
            result = await transcribe_audio(b"\x00\x01\x02")

        assert result.text == ""
        assert "500" in result.error

    @pytest.mark.asyncio
    async def test_unsupported_provider(self, monkeypatch):
        monkeypatch.setenv("TRANSCRIPTION_PROVIDER", "deepgram")
        # deepgram returns False from is_transcription_configured
        result = await transcribe_audio(b"\x00\x01\x02")
        assert result.text == ""
        assert "not configured" in result.error
