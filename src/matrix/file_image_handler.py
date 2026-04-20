"""
Image upload handling — base64 encoding for multimodal Letta messages.
"""

import asyncio
import io
import logging
from typing import Optional, Tuple

from PIL import Image

from src.matrix.file_download import FileMetadata
from src.matrix.formatter import wrap_opencode_routing

logger = logging.getLogger("matrix_client.file_handler")

# Budget for the raw (pre-base64) image bytes. base64 inflates 4/3, and the
# full WS frame is wrapped in a JSON envelope (message text, input_content
# array, request_id, etc.) that must all fit under the gateway's 1 MiB
# maxPayload. 650 KiB raw → ~866 KiB base64 leaves safe headroom.
MAX_RAW_BYTES = 650 * 1024
# Claude Vision's native resolution — larger inputs are downscaled server-side
# anyway, so resizing here just saves bytes with no fidelity loss.
MAX_DIMENSION = 1568
JPEG_QUALITIES = (85, 75, 65, 55)


def _compress_to_budget(file_path: str) -> Tuple[bytes, str]:
    """Resize + re-encode the image so raw bytes ≤ MAX_RAW_BYTES.

    Returns (compressed_bytes, media_type). Prefers PNG when the source has
    transparency and fits; otherwise flattens to JPEG and steps quality down.
    Falls back to a halved, low-quality JPEG if nothing else fits.
    """
    with Image.open(file_path) as img:
        img.load()

    original_size = img.size
    has_alpha = img.mode in ("RGBA", "LA") or (
        img.mode == "P" and "transparency" in img.info
    )

    if max(img.size) > MAX_DIMENSION:
        ratio = MAX_DIMENSION / max(img.size)
        new_size = (int(img.size[0] * ratio), int(img.size[1] * ratio))
        img = img.resize(new_size, Image.Resampling.LANCZOS)
        logger.info(f"Resized image {original_size} → {new_size}")

    if has_alpha:
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        if buf.tell() <= MAX_RAW_BYTES:
            return buf.getvalue(), "image/png"
        # Flatten onto white before JPEG-encoding.
        bg = Image.new("RGB", img.size, (255, 255, 255))
        alpha = img.split()[-1] if img.mode == "RGBA" else None
        bg.paste(img.convert("RGBA"), mask=alpha)
        img = bg
    elif img.mode != "RGB":
        img = img.convert("RGB")

    for quality in JPEG_QUALITIES:
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        if buf.tell() <= MAX_RAW_BYTES:
            return buf.getvalue(), "image/jpeg"

    halved = img.resize((img.size[0] // 2, img.size[1] // 2), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    halved.save(buf, format="JPEG", quality=55, optimize=True)
    return buf.getvalue(), "image/jpeg"


class FileImageHandlerMixin:
    """Image upload methods mixed into LettaFileHandler."""

    async def _handle_image_upload(self, metadata: FileMetadata, room_id: str, agent_id: Optional[str] = None) -> Optional[list]:
        """Handle image upload by sending as multimodal message to agent."""
        import base64

        await self._notify(room_id, f"🖼️ Processing image: {metadata.file_name}")

        async with self._downloaded_file(metadata) as file_path:
            compressed_bytes, media_type = await asyncio.to_thread(
                _compress_to_budget, file_path
            )
            image_data = base64.standard_b64encode(compressed_bytes).decode("utf-8")

            logger.info(
                f"Encoded image {metadata.file_name} as {media_type} base64 "
                f"({len(compressed_bytes)} raw → {len(image_data)} chars)"
            )

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
                        "media_type": media_type,
                        "data": image_data,
                    }
                }
            ]
            logger.info(f"Built multimodal content for image {metadata.file_name}")
            return input_content
