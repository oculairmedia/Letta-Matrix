import io
from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.core.avatar_service import AvatarService, HAS_PIL


@pytest.fixture
def avatar_service(mock_config):
    service = AvatarService(mock_config, Mock())
    service.user_manager = Mock()
    service.user_manager.upload_avatar = AsyncMock(return_value="mxc://matrix.test/avatar")
    service.user_manager.set_user_avatar = AsyncMock(return_value=True)
    service.mappings = {
        "agent-1": Mock(
            matrix_user_id="@agent_1:matrix.test",
            matrix_password="password123",
        )
    }
    return service


@pytest.mark.unit
def test_generate_avatar_image_returns_png_bytes(mock_config):
    if not HAS_PIL:
        pytest.skip("Pillow not installed")

    from PIL import Image

    service = AvatarService(mock_config, Mock())
    avatar_bytes = service._generate_avatar_image("Meridian", size=128)

    assert avatar_bytes is not None
    assert avatar_bytes.startswith(b"\x89PNG\r\n\x1a\n")
    image = Image.open(io.BytesIO(avatar_bytes))
    assert image.size == (128, 128)


@pytest.mark.unit
def test_generate_avatar_color_is_deterministic_for_same_name(mock_config):
    if not HAS_PIL:
        pytest.skip("Pillow not installed")

    from PIL import Image

    service = AvatarService(mock_config, Mock())
    avatar_one = service._generate_avatar_image("Meridian", size=128)
    avatar_two = service._generate_avatar_image("Meridian", size=128)

    assert avatar_one is not None
    assert avatar_two is not None

    image_one = Image.open(io.BytesIO(avatar_one))
    image_two = Image.open(io.BytesIO(avatar_two))

    assert image_one.getpixel((0, 0)) == image_two.getpixel((0, 0))


@pytest.mark.unit
@pytest.mark.asyncio
async def test_set_default_avatar_for_agent_calls_upload_api(avatar_service):
    check_response = AsyncMock()
    check_response.status = 200
    check_response.json = AsyncMock(return_value={})
    check_response.__aenter__ = AsyncMock(return_value=check_response)
    check_response.__aexit__ = AsyncMock(return_value=None)

    login_response = AsyncMock()
    login_response.status = 200
    login_response.json = AsyncMock(return_value={"access_token": "agent-token"})
    login_response.__aenter__ = AsyncMock(return_value=login_response)
    login_response.__aexit__ = AsyncMock(return_value=None)

    session = AsyncMock()
    session.get = Mock(return_value=check_response)
    session.post = Mock(return_value=login_response)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)

    with patch("src.core.avatar_service.aiohttp.ClientSession", return_value=session):
        success = await avatar_service.set_default_avatar_for_agent(
            "Agent One", "@agent_1:matrix.test"
        )

    assert success is True
    avatar_service.user_manager.upload_avatar.assert_awaited_once()
    avatar_service.user_manager.set_user_avatar.assert_awaited_once_with(
        "@agent_1:matrix.test",
        "mxc://matrix.test/avatar",
        "agent-token",
    )
