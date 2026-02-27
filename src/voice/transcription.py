import io
import logging
import os
from dataclasses import dataclass
from typing import Optional

import aiohttp
from openai import AsyncOpenAI


logger = logging.getLogger("matrix_client.voice")

_DEFAULT_OPENAI_MODEL = "whisper-1"
_DEFAULT_WHISPER_MODEL = "Systran/faster-whisper-medium"
_DEFAULT_WHISPER_URL = "http://whisper:8000/v1"
_DEFAULT_MISTRAL_MODEL = "voxtral-mini-latest"
_MISTRAL_TRANSCRIPTION_URL = "https://api.mistral.ai/v1/audio/transcriptions"


@dataclass
class TranscriptionResult:
    text: str
    error: Optional[str] = None


def _get_provider() -> str:
    return os.getenv("TRANSCRIPTION_PROVIDER", "whisper").strip().lower()


def is_transcription_configured() -> bool:
    provider = _get_provider()
    if provider == "mistral":
        return bool(os.getenv("MISTRAL_API_KEY"))
    if provider == "openai":
        return bool(os.getenv("OPENAI_API_KEY"))
    if provider == "whisper":
        # Local whisper needs no API key, just a reachable URL
        return True
    return False


async def transcribe_audio(audio_data: bytes, filename: str = "voice.ogg") -> TranscriptionResult:
    provider = _get_provider()

    if not audio_data:
        return TranscriptionResult(text="", error="No audio data provided")

    if not is_transcription_configured():
        return TranscriptionResult(
            text="",
            error=f"Transcription is not configured for provider '{provider}'",
        )

    try:
        if provider == "mistral":
            return await _transcribe_with_mistral(audio_data, filename)
        if provider == "openai":
            return await _transcribe_with_openai(audio_data, filename)
        if provider == "whisper":
            return await _transcribe_with_whisper(audio_data, filename)

        error = f"Unsupported transcription provider: {provider}"
        logger.error(error)
        return TranscriptionResult(text="", error=error)
    except Exception as exc:
        logger.error("Unexpected transcription failure", exc_info=True)
        return TranscriptionResult(text="", error=str(exc))


async def _transcribe_with_openai(audio_data: bytes, filename: str) -> TranscriptionResult:
    api_key = os.getenv("OPENAI_API_KEY")
    model = os.getenv("OPENAI_TRANSCRIPTION_MODEL", _DEFAULT_OPENAI_MODEL)
    client = AsyncOpenAI(api_key=api_key)

    file_buffer = io.BytesIO(audio_data)
    file_buffer.name = filename

    try:
        response = await client.audio.transcriptions.create(
            model=model,
            file=file_buffer,
        )
        text = getattr(response, "text", "") or ""
        if not text:
            logger.warning("OpenAI transcription returned empty text")
        return TranscriptionResult(text=text)
    except Exception as exc:
        logger.error("OpenAI transcription failed", exc_info=True)
        return TranscriptionResult(text="", error=str(exc))


async def _transcribe_with_whisper(audio_data: bytes, filename: str) -> TranscriptionResult:
    """Transcribe using local faster-whisper (Speaches) via OpenAI-compatible API."""
    base_url = os.getenv("WHISPER_BASE_URL", "").strip() or _DEFAULT_WHISPER_URL
    model = os.getenv("WHISPER_MODEL", "").strip() or _DEFAULT_WHISPER_MODEL
    client = AsyncOpenAI(base_url=base_url, api_key="not-needed")

    file_buffer = io.BytesIO(audio_data)
    file_buffer.name = filename

    try:
        response = await client.audio.transcriptions.create(
            model=model,
            file=file_buffer,
        )
        text = getattr(response, "text", "") or ""
        if not text:
            logger.warning("Whisper transcription returned empty text")
        return TranscriptionResult(text=text)
    except Exception as exc:
        logger.error("Whisper transcription failed", exc_info=True)
        return TranscriptionResult(text="", error=str(exc))


async def _transcribe_with_mistral(audio_data: bytes, filename: str) -> TranscriptionResult:
    api_key = os.getenv("MISTRAL_API_KEY")
    model = os.getenv("MISTRAL_TRANSCRIPTION_MODEL", _DEFAULT_MISTRAL_MODEL)

    headers = {
        "Authorization": f"Bearer {api_key}",
    }

    form = aiohttp.FormData()
    form.add_field("model", model)
    form.add_field(
        "file",
        audio_data,
        filename=filename,
        content_type="application/octet-stream",
    )

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(_MISTRAL_TRANSCRIPTION_URL, headers=headers, data=form) as response:
                if response.status >= 400:
                    error_text = await response.text()
                    logger.error(
                        "Mistral transcription failed with status %s: %s",
                        response.status,
                        error_text,
                    )
                    return TranscriptionResult(
                        text="",
                        error=f"Mistral API error ({response.status}): {error_text}",
                    )

                payload = await response.json()
                text = payload.get("text", "")
                if not text:
                    logger.warning("Mistral transcription returned empty text")
                return TranscriptionResult(text=text)
    except Exception as exc:
        logger.error("Mistral transcription request failed", exc_info=True)
        return TranscriptionResult(text="", error=str(exc))
