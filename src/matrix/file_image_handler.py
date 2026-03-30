"""
Image upload handling — base64 encoding for multimodal Letta messages.
"""

import logging
from typing import Optional

from src.matrix.file_download import FileMetadata
from src.matrix.formatter import wrap_opencode_routing

logger = logging.getLogger("matrix_client.file_handler")


class FileImageHandlerMixin:
    """Image upload methods mixed into LettaFileHandler."""

    async def _handle_image_upload(self, metadata: FileMetadata, room_id: str, agent_id: Optional[str] = None) -> Optional[list]:
        """Handle image upload by sending as multimodal message to agent."""
        import base64

        await self._notify(room_id, f"🖼️ Processing image: {metadata.file_name}")

        async with self._downloaded_file(metadata) as file_path:
            with open(file_path, 'rb') as f:
                image_data = base64.standard_b64encode(f.read()).decode('utf-8')

            logger.info(f"Encoded image {metadata.file_name} as base64 ({len(image_data)} chars)")

            if metadata.caption:
                message_text = (
                    f"[Image Upload: {metadata.file_name}]\n\n"
                    f"The user shared an image and asked: \"{metadata.caption}\"\n\n"
                    f"Please analyze the image and respond to the user's question."
                )
                logger.info(f"Including user caption in image message: {metadata.caption[:50]}...")
            else:
                message_text = (
                    f"[Image Upload: {metadata.file_name}]\n\n"
                    f"The user has shared an image with you. Please analyze the image and describe what you see."
                )

            if metadata.sender and metadata.sender.startswith("@oc_"):
                message_text = wrap_opencode_routing(message_text, metadata.sender)
                logger.info("[OPENCODE-IMAGE] Injected @mention instruction for image upload")

            input_content = [
                {
                    "type": "text",
                    "text": message_text
                },
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": metadata.file_type,
                        "data": image_data,
                    }
                }
            ]
            logger.info(f"Built multimodal content for image {metadata.file_name}")
            return input_content
