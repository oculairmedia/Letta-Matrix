import logging
import os
import inspect
from typing import Optional

import aiohttp
from openai import AsyncOpenAI


logger = logging.getLogger("matrix_client.voice")

_DEFAULT_TTS_PROVIDER = "elevenlabs"
_DEFAULT_ELEVENLABS_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"
_DEFAULT_ELEVENLABS_MODEL_ID = "eleven_monolingual_v1"
_DEFAULT_OPENAI_TTS_VOICE = "alloy"
_DEFAULT_OPENAI_TTS_MODEL = "tts-1"


def _get_tts_provider() -> str:
    return os.getenv("TTS_PROVIDER", _DEFAULT_TTS_PROVIDER).strip().lower()


def is_tts_configured() -> bool:
    provider = _get_tts_provider()
    if provider == "openai":
        return bool(os.getenv("OPENAI_API_KEY"))
    return bool(os.getenv("ELEVENLABS_API_KEY"))


async def synthesize_speech(text: str) -> Optional[bytes]:
    if not text.strip():
        logger.warning("TTS requested with empty text")
        return None

    provider = _get_tts_provider()
    if not is_tts_configured():
        logger.warning("TTS provider '%s' is not configured", provider)
        return None

    try:
        if provider == "openai":
            return await _synthesize_with_openai(text)
        if provider == "elevenlabs":
            return await _synthesize_with_elevenlabs(text)

        logger.warning("Unsupported TTS provider: %s", provider)
        return None
    except Exception:
        logger.warning("TTS synthesis failed for provider '%s'", provider, exc_info=True)
        return None


async def _synthesize_with_elevenlabs(text: str) -> Optional[bytes]:
    api_key = os.getenv("ELEVENLABS_API_KEY")
    voice_id = os.getenv("ELEVENLABS_VOICE_ID", _DEFAULT_ELEVENLABS_VOICE_ID)
    model_id = os.getenv("ELEVENLABS_MODEL_ID", _DEFAULT_ELEVENLABS_MODEL_ID)

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    payload = {
        "text": text,
        "model_id": model_id,
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=payload) as response:
            if response.status >= 400:
                error_text = await response.text()
                logger.warning(
                    "ElevenLabs TTS failed with status %s: %s",
                    response.status,
                    error_text,
                )
                return None

            audio_data = await response.read()
            if not audio_data:
                logger.warning("ElevenLabs TTS returned empty audio")
                return None
            return audio_data


async def _synthesize_with_openai(text: str) -> Optional[bytes]:
    api_key = os.getenv("OPENAI_API_KEY")
    voice = os.getenv("OPENAI_TTS_VOICE", _DEFAULT_OPENAI_TTS_VOICE)
    model = os.getenv("OPENAI_TTS_MODEL", _DEFAULT_OPENAI_TTS_MODEL)

    client = AsyncOpenAI(api_key=api_key)
    try:
        response = await client.audio.speech.create(
            model=model,
            voice=voice,
            input=text,
        )

        audio_data = await _extract_audio_bytes(response)
        if not audio_data:
            logger.warning("OpenAI TTS returned empty audio")
            return None

        return audio_data
    except Exception:
        logger.warning("OpenAI TTS failed", exc_info=True)
        return None
    finally:
        close_method = getattr(client, "close", None)
        if callable(close_method):
            close_result = close_method()
            if inspect.isawaitable(close_result):
                await close_result


async def _extract_audio_bytes(response: object) -> Optional[bytes]:
    aread_method = getattr(response, "aread", None)
    if callable(aread_method):
        aread_result = aread_method()
        if inspect.isawaitable(aread_result):
            data = await aread_result
            return data if isinstance(data, bytes) else None

    read_method = getattr(response, "read", None)
    if callable(read_method):
        result = read_method()
        if isinstance(result, bytes):
            return result
        if inspect.isawaitable(result):
            awaited_result = await result
            return awaited_result if isinstance(awaited_result, bytes) else None

    content = getattr(response, "content", None)
    if isinstance(content, bytes):
        return content

    return None
