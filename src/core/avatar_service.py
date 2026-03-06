#!/usr/bin/env python3
"""Avatar generation and upload service for Matrix agent users."""

import hashlib
import io
import logging
from typing import Any, Dict, Optional

import aiohttp

try:
    from PIL import Image, ImageDraw, ImageFont

    HAS_PIL = True
except ImportError:
    HAS_PIL = False
    Image = None  # type: ignore
    ImageDraw = None  # type: ignore
    ImageFont = None  # type: ignore


DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=10)


class AvatarService:

    def __init__(self, config, logger: logging.Logger):
        self.config = config
        self.logger = logger
        self.homeserver_url = config.homeserver_url
        self.user_manager: Optional[Any] = None
        self.mappings: Dict[str, Any] = {}

    async def set_default_avatar_for_agent(
        self,
        agent_name: str,
        matrix_user_id: str,
        admin_token: Optional[str] = None,
    ) -> bool:
        _ = admin_token

        if not HAS_PIL:
            self.logger.warning(
                f"Pillow not installed, skipping avatar generation for {agent_name}"
            )
            return False

        if self.user_manager is None:
            self.logger.warning(
                f"User manager unavailable, cannot set avatar for {agent_name}"
            )
            return False

        try:
            try:
                check_url = (
                    f"{self.homeserver_url}/_matrix/client/v3/profile/{matrix_user_id}/avatar_url"
                )
                async with aiohttp.ClientSession() as session:
                    async with session.get(check_url, timeout=DEFAULT_TIMEOUT) as response:
                        if response.status == 200:
                            data = await response.json()
                            if data.get("avatar_url"):
                                self.logger.debug(
                                    f"Agent {agent_name} already has avatar, skipping"
                                )
                                return True
            except Exception as e:
                self.logger.debug(
                    f"Could not check existing avatar for {agent_name}: {e}"
                )

            username = matrix_user_id.split(":")[0].replace("@", "")
            mapping = None
            for _, m in self.mappings.items():
                if m.matrix_user_id == matrix_user_id:
                    mapping = m
                    break

            if not mapping or not mapping.matrix_password:
                self.logger.warning(
                    f"No password found for agent {agent_name}, cannot set avatar"
                )
                return False

            login_url = f"{self.homeserver_url}/_matrix/client/r0/login"
            login_data = {
                "type": "m.login.password",
                "user": username,
                "password": mapping.matrix_password,
            }

            agent_token = None
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    login_url, json=login_data, timeout=DEFAULT_TIMEOUT
                ) as response:
                    if response.status == 200:
                        login_result = await response.json()
                        agent_token = login_result.get("access_token")
                    else:
                        self.logger.warning(
                            f"Failed to login as {agent_name} for avatar upload: {response.status}"
                        )
                        return False

            if not agent_token:
                self.logger.warning(f"No token obtained for {agent_name}")
                return False

            avatar_bytes = self._generate_avatar_image(agent_name, size=128)
            if not avatar_bytes:
                self.logger.warning(f"Failed to generate avatar for {agent_name}")
                return False

            filename = f"{username}_avatar.png"
            avatar_url = await self.user_manager.upload_avatar(
                avatar_bytes,
                filename,
                "image/png",
                agent_token,
            )

            if not avatar_url:
                self.logger.warning(f"Failed to upload avatar for {agent_name}")
                return False

            success = await self.user_manager.set_user_avatar(
                matrix_user_id,
                avatar_url,
                agent_token,
            )

            if success:
                self.logger.info(f"Successfully set avatar for agent {agent_name}")
            else:
                self.logger.warning(f"Failed to set avatar for agent {agent_name}")

            return success

        except Exception as e:
            self.logger.error(
                f"Error setting avatar for {agent_name}: {type(e).__name__}: {e}",
                exc_info=True,
            )
            return False

    def _generate_avatar_image(self, agent_name: str, size: int = 256) -> Optional[bytes]:
        if not HAS_PIL or Image is None or ImageDraw is None or ImageFont is None:
            return None

        try:
            letter = agent_name[0].upper() if agent_name else "A"

            hash_obj = hashlib.md5(agent_name.encode())
            hash_hex = hash_obj.hexdigest()
            r = int(hash_hex[0:2], 16)
            g = int(hash_hex[2:4], 16)
            b = int(hash_hex[4:6], 16)

            img = Image.new("RGB", (size, size), color=(r, g, b))  # type: ignore
            draw = ImageDraw.Draw(img)  # type: ignore

            margin = size // 8
            draw.ellipse(
                [(margin, margin), (size - margin, size - margin)],
                fill=(255, 255, 255),
            )

            font_size = size // 2
            try:
                font = ImageFont.truetype(  # type: ignore
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                    font_size,
                )
            except Exception:
                font = ImageFont.load_default()  # type: ignore

            bbox = draw.textbbox((0, 0), letter, font=font)
            text_width = bbox[2] - bbox[0]
            text_height = bbox[3] - bbox[1]
            x = (size - text_width) // 2
            y = (size - text_height) // 2

            draw.text((x, y), letter, fill=(50, 50, 50), font=font)

            img_bytes = io.BytesIO()
            img.save(img_bytes, format="PNG")
            img_bytes.seek(0)
            return img_bytes.getvalue()

        except Exception as e:
            self.logger.error(f"Error generating avatar image for {agent_name}: {e}")
            return None
