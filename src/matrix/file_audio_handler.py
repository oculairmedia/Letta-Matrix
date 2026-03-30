"""
Audio upload handling — voice transcription via Whisper.
"""

import logging

from src.matrix.file_download import FileMetadata
from src.voice.transcription import transcribe_audio

logger = logging.getLogger("matrix_client.file_handler")


class FileAudioHandlerMixin:
    """Audio upload methods mixed into LettaFileHandler."""

    async def _handle_audio_upload(self, metadata: FileMetadata) -> str:
        async with self._downloaded_file(metadata) as file_path:
            with open(file_path, 'rb') as audio_file:
                audio_data = audio_file.read()

            result = await transcribe_audio(audio_data, filename=metadata.file_name or "voice.ogg")
            if result.error:
                logger.warning(f"Voice transcription failed for {metadata.file_name}: {result.error}")
                return f"[Voice message - transcription failed: {result.error}]"

            transcribed_text = (result.text or "").strip()
            if not transcribed_text:
                transcribed_text = "(no speech detected)"

            logger.info(f"Voice transcription succeeded for {metadata.file_name}")
            return f"[Voice message]: {transcribed_text}"
