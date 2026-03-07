import logging
import uuid
from typing import Any, Dict, Optional

import aiohttp

from src.matrix.config import Config
from src.matrix.agent_auth import get_agent_token as _get_agent_token
from src.utils.ssrf_protection import SSRFError, validate_url


_AGENT_UPLOAD_TIMEOUT = aiohttp.ClientTimeout(total=30)
_AGENT_SEND_TIMEOUT = aiohttp.ClientTimeout(total=15)


async def upload_and_send_audio(
    room_id: str,
    audio_data: bytes,
    filename: str,
    mimetype: str,
    config: Config,
    logger: logging.Logger,
    duration_ms: Optional[int] = None,
) -> Optional[str]:
    try:
        async with aiohttp.ClientSession() as session:
            agent_token = await _get_agent_token(
                room_id, config, logger, session, caller="VOICE"
            )
            if not agent_token:
                return None

            upload_url = f"{config.homeserver_url}/_matrix/media/v3/upload"
            upload_headers = {
                "Authorization": f"Bearer {agent_token}",
                "Content-Type": mimetype,
            }

            async with session.post(
                upload_url,
                headers=upload_headers,
                params={"filename": filename},
                data=audio_data,
                timeout=_AGENT_UPLOAD_TIMEOUT,
            ) as upload_response:
                if upload_response.status != 200:
                    upload_error = await upload_response.text()
                    logger.error(
                        "[VOICE] Audio upload failed: %s - %s",
                        upload_response.status,
                        upload_error,
                    )
                    return None
                upload_data = await upload_response.json()
                content_uri = upload_data.get("content_uri")
                if not content_uri:
                    logger.error("[VOICE] Upload response missing content_uri")
                    return None

            txn_id = str(uuid.uuid4())
            message_url = f"{config.homeserver_url}/_matrix/client/r0/rooms/{room_id}/send/m.room.message/{txn_id}"
            message_headers = {
                "Authorization": f"Bearer {agent_token}",
                "Content-Type": "application/json",
            }
            info = {
                "mimetype": mimetype,
                "size": len(audio_data),
                "duration": duration_ms,
            }
            message_data = {
                "msgtype": "m.audio",
                "url": content_uri,
                "body": filename,
                "info": info,
                "org.matrix.msc1767.audio": {},
                "org.matrix.msc3245.voice": {},
            }

            async with session.put(
                message_url,
                headers=message_headers,
                json=message_data,
                timeout=_AGENT_SEND_TIMEOUT,
            ) as send_response:
                if send_response.status != 200:
                    send_error = await send_response.text()
                    logger.error(
                        "[VOICE] Audio send failed: %s - %s",
                        send_response.status,
                        send_error,
                    )
                    return None
                send_result = await send_response.json()
                event_id = send_result.get("event_id")
                logger.debug("[VOICE] Sent audio event_id: %s", event_id)
                return event_id

    except Exception as e:
        logger.error(
            f"[VOICE] Exception while uploading/sending audio: {e}", exc_info=True
        )
        return None


async def fetch_and_send_image(
    room_id: str,
    image_url: str,
    alt: str,
    config: Config,
    logger: logging.Logger,
) -> Optional[str]:
    try:
        try:
            validate_url(image_url)
        except SSRFError as e:
            logger.warning("[IMAGE] SSRF blocked: %s — %s", image_url, e)
            return None
        fetch_headers = {"User-Agent": "MatrixBridge/1.0"}
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(
                    image_url,
                    headers=fetch_headers,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as img_response:
                    if img_response.status != 200:
                        logger.error(
                            "[IMAGE] Failed to fetch image from %s: %s",
                            image_url,
                            img_response.status,
                        )
                        return None
                    image_data = await img_response.read()
                    content_type = img_response.headers.get("Content-Type", "image/png")
                    mimetype = content_type.split(";")[0].strip()
                    if not mimetype.startswith("image/"):
                        mimetype = "image/png"
            except Exception as fetch_err:
                logger.error(
                    "[IMAGE] Exception fetching image from %s: %s",
                    image_url,
                    fetch_err,
                )
                return None

            if not image_data or len(image_data) < 100:
                logger.warning(
                    "[IMAGE] Fetched image too small (%d bytes) from %s",
                    len(image_data) if image_data else 0,
                    image_url,
                )
                return None

            from urllib.parse import urlparse

            url_path = urlparse(image_url).path
            filename = url_path.split("/")[-1] if "/" in url_path else "image.png"
            if not filename or "." not in filename:
                ext = mimetype.split("/")[-1].replace("jpeg", "jpg")
                filename = f"image.{ext}"

            logger.info(
                "[IMAGE] Fetched %s (%d bytes, %s)",
                filename,
                len(image_data),
                mimetype,
            )

            agent_token = await _get_agent_token(
                room_id, config, logger, session, caller="IMAGE"
            )
            if not agent_token:
                return None

            upload_url = f"{config.homeserver_url}/_matrix/media/v3/upload"
            upload_headers = {
                "Authorization": f"Bearer {agent_token}",
                "Content-Type": mimetype,
            }

            async with session.post(
                upload_url,
                headers=upload_headers,
                params={"filename": filename},
                data=image_data,
                timeout=_AGENT_UPLOAD_TIMEOUT,
            ) as upload_response:
                if upload_response.status != 200:
                    upload_error = await upload_response.text()
                    logger.error(
                        "[IMAGE] Upload failed: %s - %s",
                        upload_response.status,
                        upload_error,
                    )
                    return None
                upload_result = await upload_response.json()
                content_uri = upload_result.get("content_uri")
                if not content_uri:
                    logger.error("[IMAGE] Upload response missing content_uri")
                    return None

            thumbnail_uri = None
            thumbnail_info = None
            orig_w, orig_h = None, None
            try:
                from PIL import Image as PILImage
                import io

                img = PILImage.open(io.BytesIO(image_data))
                orig_w, orig_h = img.size
                thumb_size = (320, 320)
                img.thumbnail(thumb_size, PILImage.Resampling.LANCZOS)
                thumb_w, thumb_h = img.size
                thumb_buf = io.BytesIO()
                img.save(thumb_buf, format="PNG")
                thumb_data = thumb_buf.getvalue()

                async with session.post(
                    upload_url,
                    headers={**upload_headers, "Content-Type": "image/png"},
                    params={"filename": "thumbnail.png"},
                    data=thumb_data,
                    timeout=_AGENT_UPLOAD_TIMEOUT,
                ) as thumb_resp:
                    if thumb_resp.status == 200:
                        thumb_result = await thumb_resp.json()
                        thumbnail_uri = thumb_result.get("content_uri")
                        thumbnail_info = {
                            "w": thumb_w,
                            "h": thumb_h,
                            "mimetype": "image/png",
                            "size": len(thumb_data),
                        }
                        logger.debug(
                            "[IMAGE] Uploaded thumbnail %dx%d (%d bytes)",
                            thumb_w,
                            thumb_h,
                            len(thumb_data),
                        )
            except ImportError:
                logger.debug("[IMAGE] Pillow not available, skipping thumbnail")
            except Exception as thumb_err:
                logger.debug(
                    "[IMAGE] Thumbnail generation failed: %s", thumb_err
                )

            txn_id = str(uuid.uuid4())
            message_url = f"{config.homeserver_url}/_matrix/client/r0/rooms/{room_id}/send/m.room.message/{txn_id}"
            message_headers = {
                "Authorization": f"Bearer {agent_token}",
                "Content-Type": "application/json",
            }

            image_info: Dict[str, Any] = {
                "mimetype": mimetype,
                "size": len(image_data),
            }
            if orig_w and orig_h:
                image_info["w"] = orig_w
                image_info["h"] = orig_h
            if thumbnail_uri and thumbnail_info:
                image_info["thumbnail_url"] = thumbnail_uri
                image_info["thumbnail_info"] = thumbnail_info

            message_data = {
                "msgtype": "m.image",
                "url": content_uri,
                "body": alt or filename,
                "info": image_info,
            }

            async with session.put(
                message_url,
                headers=message_headers,
                json=message_data,
                timeout=_AGENT_SEND_TIMEOUT,
            ) as send_response:
                if send_response.status != 200:
                    send_error = await send_response.text()
                    logger.error(
                        "[IMAGE] Send failed: %s - %s",
                        send_response.status,
                        send_error,
                    )
                    return None
                send_result = await send_response.json()
                event_id = send_result.get("event_id")
                logger.info("[IMAGE] Sent image event_id: %s", event_id)
                return event_id

    except Exception as e:
        logger.error(
            f"[IMAGE] Exception while fetching/sending image: {e}", exc_info=True
        )
        return None


async def fetch_and_send_file(
    room_id: str,
    file_url: str,
    filename: str,
    config: Config,
    logger: logging.Logger,
) -> Optional[str]:
    try:
        try:
            validate_url(file_url)
        except SSRFError as e:
            logger.warning("[FILE] SSRF blocked: %s — %s", file_url, e)
            return None
        fetch_headers = {"User-Agent": "MatrixBridge/1.0"}
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(
                    file_url,
                    headers=fetch_headers,
                    timeout=aiohttp.ClientTimeout(total=60),
                ) as resp:
                    if resp.status != 200:
                        logger.error(
                            "[FILE] Failed to fetch file from %s: %s",
                            file_url,
                            resp.status,
                        )
                        return None
                    file_data = await resp.read()
                    content_type = resp.headers.get(
                        "Content-Type", "application/octet-stream"
                    )
                    mimetype = content_type.split(";")[0].strip()
            except Exception as fetch_err:
                logger.error(
                    "[FILE] Exception fetching file from %s: %s",
                    file_url,
                    fetch_err,
                )
                return None

            if not file_data or len(file_data) < 10:
                logger.warning(
                    "[FILE] Fetched file too small (%d bytes)",
                    len(file_data) if file_data else 0,
                )
                return None

            if not filename:
                from urllib.parse import urlparse

                url_path = urlparse(file_url).path
                filename = url_path.split("/")[-1] if "/" in url_path else "file"
                if not filename or "." not in filename:
                    filename = "file.bin"

            logger.info(
                "[FILE] Fetched %s (%d bytes, %s)", filename, len(file_data), mimetype
            )

            agent_token = await _get_agent_token(
                room_id, config, logger, session, caller="FILE"
            )
            if not agent_token:
                return None

            upload_url = f"{config.homeserver_url}/_matrix/media/v3/upload"
            upload_headers = {
                "Authorization": f"Bearer {agent_token}",
                "Content-Type": mimetype,
            }
            async with session.post(
                upload_url,
                headers=upload_headers,
                params={"filename": filename},
                data=file_data,
                timeout=_AGENT_UPLOAD_TIMEOUT,
            ) as upload_resp:
                if upload_resp.status != 200:
                    logger.error("[FILE] Upload failed: %s", upload_resp.status)
                    return None
                content_uri = (await upload_resp.json()).get("content_uri")
                if not content_uri:
                    return None

            txn_id = str(uuid.uuid4())
            msg_url = f"{config.homeserver_url}/_matrix/client/r0/rooms/{room_id}/send/m.room.message/{txn_id}"
            msg_headers = {
                "Authorization": f"Bearer {agent_token}",
                "Content-Type": "application/json",
            }
            msg_data = {
                "msgtype": "m.file",
                "url": content_uri,
                "body": filename,
                "filename": filename,
                "info": {"mimetype": mimetype, "size": len(file_data)},
            }
            async with session.put(
                msg_url,
                headers=msg_headers,
                json=msg_data,
                timeout=_AGENT_SEND_TIMEOUT,
            ) as send_resp:
                if send_resp.status != 200:
                    logger.error("[FILE] Send failed: %s", send_resp.status)
                    return None
                event_id = (await send_resp.json()).get("event_id")
                logger.info("[FILE] Sent file event_id: %s", event_id)
                return event_id

    except Exception as e:
        logger.error(f"[FILE] Exception: {e}", exc_info=True)
        return None


async def fetch_and_send_video(
    room_id: str,
    video_url: str,
    alt: str,
    config: Config,
    logger: logging.Logger,
) -> Optional[str]:
    try:
        try:
            validate_url(video_url)
        except SSRFError as e:
            logger.warning("[VIDEO] SSRF blocked: %s — %s", video_url, e)
            return None
        fetch_headers = {"User-Agent": "MatrixBridge/1.0"}
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(
                    video_url,
                    headers=fetch_headers,
                    timeout=aiohttp.ClientTimeout(total=120),
                ) as resp:
                    if resp.status != 200:
                        logger.error(
                            "[VIDEO] Failed to fetch from %s: %s",
                            video_url,
                            resp.status,
                        )
                        return None
                    video_data = await resp.read()
                    content_type = resp.headers.get("Content-Type", "video/mp4")
                    mimetype = content_type.split(";")[0].strip()
                    if not mimetype.startswith("video/"):
                        mimetype = "video/mp4"
            except Exception as fetch_err:
                logger.error(
                    "[VIDEO] Exception fetching from %s: %s", video_url, fetch_err
                )
                return None

            if not video_data or len(video_data) < 100:
                logger.warning(
                    "[VIDEO] Fetched video too small (%d bytes)",
                    len(video_data) if video_data else 0,
                )
                return None

            from urllib.parse import urlparse

            url_path = urlparse(video_url).path
            filename = url_path.split("/")[-1] if "/" in url_path else "video.mp4"
            if not filename or "." not in filename:
                ext = mimetype.split("/")[-1]
                filename = f"video.{ext}"

            logger.info(
                "[VIDEO] Fetched %s (%d bytes, %s)",
                filename,
                len(video_data),
                mimetype,
            )

            agent_token = await _get_agent_token(
                room_id, config, logger, session, caller="VIDEO"
            )
            if not agent_token:
                return None

            upload_url = f"{config.homeserver_url}/_matrix/media/v3/upload"
            upload_headers = {
                "Authorization": f"Bearer {agent_token}",
                "Content-Type": mimetype,
            }
            async with session.post(
                upload_url,
                headers=upload_headers,
                params={"filename": filename},
                data=video_data,
                timeout=_AGENT_UPLOAD_TIMEOUT,
            ) as upload_resp:
                if upload_resp.status != 200:
                    logger.error("[VIDEO] Upload failed: %s", upload_resp.status)
                    return None
                content_uri = (await upload_resp.json()).get("content_uri")
                if not content_uri:
                    return None

            txn_id = str(uuid.uuid4())
            msg_url = f"{config.homeserver_url}/_matrix/client/r0/rooms/{room_id}/send/m.room.message/{txn_id}"
            msg_headers = {
                "Authorization": f"Bearer {agent_token}",
                "Content-Type": "application/json",
            }
            msg_data = {
                "msgtype": "m.video",
                "url": content_uri,
                "body": alt or filename,
                "info": {"mimetype": mimetype, "size": len(video_data)},
            }
            async with session.put(
                msg_url,
                headers=msg_headers,
                json=msg_data,
                timeout=_AGENT_SEND_TIMEOUT,
            ) as send_resp:
                if send_resp.status != 200:
                    logger.error("[VIDEO] Send failed: %s", send_resp.status)
                    return None
                event_id = (await send_resp.json()).get("event_id")
                logger.info("[VIDEO] Sent video event_id: %s", event_id)
                return event_id

    except Exception as e:
        logger.error(f"[VIDEO] Exception: {e}", exc_info=True)
        return None
