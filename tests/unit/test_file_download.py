import logging
import os
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

import pytest
from nio import Event

from src.matrix.file_download import FileDownloadService, FileMetadata, MAX_FILE_SIZE


def _build_event(content):
    return cast(Event, SimpleNamespace(
        source={"content": content},
        sender="@user:matrix.oculair.ca",
        server_timestamp=1700000000000,
        event_id="$event123",
    ))


def test_file_metadata_dataclass_creation():
    metadata = FileMetadata(
        file_url="mxc://matrix.oculair.ca/media123",
        file_name="test.pdf",
        file_type="application/pdf",
        file_size=1234,
        room_id="!room:matrix.oculair.ca",
        sender="@user:matrix.oculair.ca",
        timestamp=1700000000000,
        event_id="$event123",
        caption="please summarize",
    )

    assert metadata.file_name == "test.pdf"
    assert metadata.file_type == "application/pdf"
    assert metadata.caption == "please summarize"


def test_extract_file_metadata_valid_file_event():
    service = FileDownloadService("http://tuwunel:6167", "token", logging.getLogger("test"))
    event = _build_event(
        {
            "msgtype": "m.file",
            "url": "mxc://matrix.oculair.ca/abc123",
            "body": "document.pdf",
            "info": {"mimetype": "application/pdf", "size": 2048},
        }
    )

    metadata = service.extract_file_metadata(event, "!room:matrix.oculair.ca")

    assert metadata is not None
    assert metadata.file_url == "mxc://matrix.oculair.ca/abc123"
    assert metadata.file_name == "document.pdf"
    assert metadata.file_type == "application/pdf"


def test_extract_file_metadata_missing_url_returns_none():
    service = FileDownloadService("http://tuwunel:6167", "token", logging.getLogger("test"))
    event = _build_event(
        {
            "msgtype": "m.file",
            "body": "document.pdf",
            "info": {"mimetype": "application/pdf", "size": 2048},
        }
    )

    metadata = service.extract_file_metadata(event, "!room:matrix.oculair.ca")

    assert metadata is None


def test_validate_file_oversized_returns_error():
    service = FileDownloadService("http://tuwunel:6167", "token", logging.getLogger("test"))
    metadata = FileMetadata(
        file_url="mxc://matrix.oculair.ca/media123",
        file_name="large.pdf",
        file_type="application/pdf",
        file_size=MAX_FILE_SIZE + 1,
        room_id="!room:matrix.oculair.ca",
        sender="@user:matrix.oculair.ca",
        timestamp=1700000000000,
        event_id="$event123",
    )

    error = service.validate_file(metadata)

    assert error is not None
    assert "too large" in error


def test_validate_file_octet_stream_resolves_from_extension():
    service = FileDownloadService("http://tuwunel:6167", "token", logging.getLogger("test"))
    metadata = FileMetadata(
        file_url="mxc://matrix.oculair.ca/media123",
        file_name="notes.md",
        file_type="application/octet-stream",
        file_size=1024,
        room_id="!room:matrix.oculair.ca",
        sender="@user:matrix.oculair.ca",
        timestamp=1700000000000,
        event_id="$event123",
    )

    error = service.validate_file(metadata)

    assert error is None
    assert metadata.file_type == "text/markdown"


@pytest.mark.asyncio
async def test_download_file_success_with_mocked_aiohttp():
    service = FileDownloadService("http://tuwunel:6167", "matrix-token", logging.getLogger("test"))
    metadata = FileMetadata(
        file_url="mxc://matrix.oculair.ca/media123",
        file_name="document.pdf",
        file_type="application/pdf",
        file_size=1024,
        room_id="!room:matrix.oculair.ca",
        sender="@user:matrix.oculair.ca",
        timestamp=1700000000000,
        event_id="$event123",
    )

    class FakeContent:
        async def iter_chunked(self, _size):
            for chunk in [b"hello ", b"world"]:
                yield chunk

    class FakeResponse:
        status = 200
        content = FakeContent()

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def text(self):
            return ""

    class FakeSession:
        def __init__(self):
            self.request = None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def get(self, url, headers=None):
            self.request = (url, headers)
            return FakeResponse()

    fake_session = FakeSession()

    with patch("src.matrix.file_download.aiohttp.ClientSession", return_value=fake_session):
        downloaded_path = await service.download_file(metadata)

    try:
        assert os.path.exists(downloaded_path)
        with open(downloaded_path, "rb") as f:
            assert f.read() == b"hello world"
        assert fake_session.request is not None
        assert fake_session.request[1] == {"Authorization": "Bearer matrix-token"}
    finally:
        if os.path.exists(downloaded_path):
            os.unlink(downloaded_path)
