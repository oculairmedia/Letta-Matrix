"""Tests for src/matrix/agent_media.py — upload/send audio, image, file, video."""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.matrix.agent_media import (
    upload_and_send_audio,
    fetch_and_send_image,
    fetch_and_send_file,
    fetch_and_send_video,
)


@pytest.fixture
def config():
    cfg = MagicMock()
    cfg.homeserver_url = "https://matrix.test"
    return cfg


@pytest.fixture
def test_logger():
    return logging.getLogger("test.agent_media")


def _mock_session_with_responses(*responses):
    """Build a mock session whose post/put calls return the given responses in order."""
    mock_session = MagicMock()
    call_index = {"i": 0}

    def make_cm(*args, **kwargs):
        idx = call_index["i"]
        call_index["i"] += 1
        resp = responses[idx] if idx < len(responses) else MagicMock(status=500)
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=resp)
        cm.__aexit__ = AsyncMock(return_value=None)
        return cm

    mock_session.post = MagicMock(side_effect=make_cm)
    mock_session.put = MagicMock(side_effect=make_cm)
    mock_session.get = MagicMock(side_effect=make_cm)
    return mock_session


# ---------------------------------------------------------------------------
# upload_and_send_audio
# ---------------------------------------------------------------------------


class TestUploadAndSendAudio:

    @pytest.mark.asyncio
    async def test_success(self, config, test_logger):
        upload_resp = MagicMock(status=200)
        upload_resp.json = AsyncMock(return_value={"content_uri": "mxc://test/audio1"})

        send_resp = MagicMock(status=200)
        send_resp.json = AsyncMock(return_value={"event_id": "$audio_evt"})

        session = _mock_session_with_responses(upload_resp, send_resp)

        with (
            patch("src.matrix.agent_media._media_session_scope") as mock_scope,
            patch("src.matrix.agent_media._get_agent_token", new_callable=AsyncMock, return_value="tok"),
        ):
            mock_scope.return_value.__aenter__ = AsyncMock(return_value=session)
            mock_scope.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await upload_and_send_audio(
                "!room:test", b"\x00" * 100, "voice.ogg", "audio/ogg", config, test_logger
            )

        assert result == "$audio_evt"

    @pytest.mark.asyncio
    async def test_no_agent_token(self, config, test_logger):
        session = MagicMock()

        with (
            patch("src.matrix.agent_media._media_session_scope") as mock_scope,
            patch("src.matrix.agent_media._get_agent_token", new_callable=AsyncMock, return_value=None),
        ):
            mock_scope.return_value.__aenter__ = AsyncMock(return_value=session)
            mock_scope.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await upload_and_send_audio(
                "!room:test", b"\x00" * 100, "voice.ogg", "audio/ogg", config, test_logger
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_upload_failure(self, config, test_logger):
        upload_resp = MagicMock(status=500)
        upload_resp.text = AsyncMock(return_value="Internal error")

        session = _mock_session_with_responses(upload_resp)

        with (
            patch("src.matrix.agent_media._media_session_scope") as mock_scope,
            patch("src.matrix.agent_media._get_agent_token", new_callable=AsyncMock, return_value="tok"),
        ):
            mock_scope.return_value.__aenter__ = AsyncMock(return_value=session)
            mock_scope.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await upload_and_send_audio(
                "!room:test", b"\x00" * 100, "voice.ogg", "audio/ogg", config, test_logger
            )

        assert result is None


# ---------------------------------------------------------------------------
# fetch_and_send_image
# ---------------------------------------------------------------------------


class TestFetchAndSendImage:

    @pytest.mark.asyncio
    async def test_ssrf_blocked(self, config, test_logger):
        from src.utils.ssrf_protection import SSRFError

        with patch(
            "src.matrix.agent_media.build_pinned_connector",
            side_effect=SSRFError("Private IP blocked"),
        ):
            result = await fetch_and_send_image(
                "!room:test", "http://192.168.1.1/evil.png", "alt", config, test_logger
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_image_too_small(self, config, test_logger):
        fetch_resp = MagicMock(status=200)
        fetch_resp.read = AsyncMock(return_value=b"\x89PNG")  # < 100 bytes
        fetch_resp.headers = {"Content-Type": "image/png"}

        fetch_session = MagicMock()
        fetch_cm = MagicMock()
        fetch_cm.__aenter__ = AsyncMock(return_value=fetch_resp)
        fetch_cm.__aexit__ = AsyncMock(return_value=None)
        fetch_session.get = MagicMock(return_value=fetch_cm)

        fetch_session_cm = MagicMock()
        fetch_session_cm.__aenter__ = AsyncMock(return_value=fetch_session)
        fetch_session_cm.__aexit__ = AsyncMock(return_value=None)

        media_session = MagicMock()
        media_scope_cm = MagicMock()
        media_scope_cm.__aenter__ = AsyncMock(return_value=media_session)
        media_scope_cm.__aexit__ = AsyncMock(return_value=None)

        mock_connector = MagicMock()

        with (
            patch("src.matrix.agent_media.build_pinned_connector", return_value=("http://safe.test/img.png", mock_connector)),
            patch("src.matrix.agent_media._media_session_scope", return_value=media_scope_cm),
            patch("src.matrix.agent_media.aiohttp.ClientSession", return_value=fetch_session_cm),
        ):
            result = await fetch_and_send_image(
                "!room:test", "http://safe.test/img.png", "alt", config, test_logger
            )

        assert result is None


# ---------------------------------------------------------------------------
# fetch_and_send_file
# ---------------------------------------------------------------------------


class TestFetchAndSendFile:

    @pytest.mark.asyncio
    async def test_ssrf_blocked(self, config, test_logger):
        from src.utils.ssrf_protection import SSRFError

        with patch(
            "src.matrix.agent_media.build_pinned_connector",
            side_effect=SSRFError("Private IP blocked"),
        ):
            result = await fetch_and_send_file(
                "!room:test", "http://10.0.0.1/secret.pdf", "secret.pdf", config, test_logger
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_file_too_small(self, config, test_logger):
        fetch_resp = MagicMock(status=200)
        fetch_resp.read = AsyncMock(return_value=b"tiny")  # < 10 bytes
        fetch_resp.headers = {"Content-Type": "application/pdf"}

        fetch_session = MagicMock()
        fetch_cm = MagicMock()
        fetch_cm.__aenter__ = AsyncMock(return_value=fetch_resp)
        fetch_cm.__aexit__ = AsyncMock(return_value=None)
        fetch_session.get = MagicMock(return_value=fetch_cm)

        fetch_session_cm = MagicMock()
        fetch_session_cm.__aenter__ = AsyncMock(return_value=fetch_session)
        fetch_session_cm.__aexit__ = AsyncMock(return_value=None)

        media_session = MagicMock()
        media_scope_cm = MagicMock()
        media_scope_cm.__aenter__ = AsyncMock(return_value=media_session)
        media_scope_cm.__aexit__ = AsyncMock(return_value=None)

        mock_connector = MagicMock()

        with (
            patch("src.matrix.agent_media.build_pinned_connector", return_value=("http://safe.test/f.pdf", mock_connector)),
            patch("src.matrix.agent_media._media_session_scope", return_value=media_scope_cm),
            patch("src.matrix.agent_media.aiohttp.ClientSession", return_value=fetch_session_cm),
        ):
            result = await fetch_and_send_file(
                "!room:test", "http://safe.test/f.pdf", "doc.pdf", config, test_logger
            )

        assert result is None


# ---------------------------------------------------------------------------
# fetch_and_send_video
# ---------------------------------------------------------------------------


class TestFetchAndSendVideo:

    @pytest.mark.asyncio
    async def test_ssrf_blocked(self, config, test_logger):
        from src.utils.ssrf_protection import SSRFError

        with patch(
            "src.matrix.agent_media.build_pinned_connector",
            side_effect=SSRFError("Private IP blocked"),
        ):
            result = await fetch_and_send_video(
                "!room:test", "http://172.16.0.1/vid.mp4", "alt", config, test_logger
            )

        assert result is None
