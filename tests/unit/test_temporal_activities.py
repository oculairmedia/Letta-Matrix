import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from temporal_workflows import activities
from temporal_workflows.activities import (
    CleanupArtifactsInput,
    MatrixAPIError,
    MatrixStatusInput,
    NotifyAgentInput,
    notify_letta_agent,
    update_matrix_status,
    cleanup_file_artifacts,
)
from temporal_workflows.workflows.file_processing import FileProcessingWorkflow
from temporal_workflows.activities import DownloadResult


class _MockResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class _MockAsyncClient:
    def __init__(self, response: _MockResponse):
        self._response = response
        self.post_calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, json):
        self.post_calls.append((url, json))
        return self._response


class _MockGatewayWS:
    def __init__(self):
        self.sent = []
        self._events = [
            json.dumps({"type": "result", "success": True}),
        ]

    async def send(self, payload):
        self.sent.append(payload)

    async def recv(self):
        return json.dumps({"type": "session_init", "session_id": "ses-1"})

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._events:
            return self._events.pop(0)
        raise StopAsyncIteration

    async def close(self):
        return None


@pytest.mark.asyncio
async def test_update_matrix_status_uses_edit_as_agent_endpoint(monkeypatch):
    client = _MockAsyncClient(_MockResponse(200, {"event_id": "$edited"}))
    monkeypatch.setattr(activities.httpx, "AsyncClient", lambda timeout=30.0: client)

    result = await update_matrix_status(
        MatrixStatusInput(
            room_id="!room:matrix.test",
            message="Updated",
            agent_id="agent-123",
            event_id="$original",
        )
    )

    assert result.success is True
    assert result.event_id == "$edited"
    assert len(client.post_calls) == 1
    called_url, payload = client.post_calls[0]
    assert called_url.endswith("/api/v1/messages/edit-as-agent")
    assert payload["agent_id"] == "agent-123"
    assert payload["event_id"] == "$original"


@pytest.mark.asyncio
async def test_update_matrix_status_raises_on_logical_failure(monkeypatch):
    client = _MockAsyncClient(_MockResponse(200, {"success": False, "error": "edit failed"}))
    monkeypatch.setattr(activities.httpx, "AsyncClient", lambda timeout=30.0: client)

    with pytest.raises(MatrixAPIError):
        await update_matrix_status(
            MatrixStatusInput(
                room_id="!room:matrix.test",
                message="Updated",
                agent_id="agent-123",
                event_id="$original",
            )
        )


@pytest.mark.asyncio
async def test_notify_letta_agent_sends_room_source_and_unique_request_id(monkeypatch):
    ws = _MockGatewayWS()

    async def _connect(*args, **kwargs):
        return ws

    monkeypatch.setattr(activities.websockets, "connect", _connect)

    result = await notify_letta_agent(
        NotifyAgentInput(
            agent_id="agent-123",
            message="hello",
            room_id="!room:matrix.test",
            conversation_id="conv-1",
        )
    )

    assert result.success is True
    assert len(ws.sent) == 2

    session_start = json.loads(ws.sent[0])
    assert session_start["type"] == "session_start"
    assert session_start["agent_id"] == "agent-123"
    assert session_start["conversation_id"] == "conv-1"

    message_payload = json.loads(ws.sent[1])
    assert message_payload["type"] == "message"
    assert message_payload["source"] == {"channel": "matrix", "chatId": "!room:matrix.test"}
    assert str(message_payload["request_id"]).startswith("temporal-notify-")


def test_persist_file_concurrency_safe(monkeypatch, tmp_path):
    monkeypatch.setattr(activities, "PERSISTENT_DOCUMENTS_DIR", str(tmp_path))

    source_a = tmp_path / "a.txt"
    source_b = tmp_path / "b.txt"
    source_a.write_text("same-content", encoding="utf-8")
    source_b.write_text("same-content", encoding="utf-8")

    def _call(path, name, mxc):
        return activities._persist_file(str(path), name, mxc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        r1 = pool.submit(_call, source_a, "doc-a.txt", "mxc://server/id-a")
        r2 = pool.submit(_call, source_b, "doc-b.txt", "mxc://server/id-b")
        out1 = r1.result(timeout=5)
        out2 = r2.result(timeout=5)

    assert out1[1] == out2[1]
    assert (out1[2], out2[2]).count(True) == 1

    idx = tmp_path / ".hashes.json"
    data = json.loads(idx.read_text(encoding="utf-8"))
    assert len(data) == 1


@pytest.mark.asyncio
async def test_cleanup_file_artifacts_removes_hash_and_files(monkeypatch, tmp_path):
    monkeypatch.setattr(activities, "PERSISTENT_DOCUMENTS_DIR", str(tmp_path))

    media_dir = tmp_path / "media123"
    media_dir.mkdir(parents=True, exist_ok=True)
    persistent = media_dir / "doc.txt"
    persistent.write_text("hello", encoding="utf-8")

    temp_file = tmp_path / "temp.bin"
    temp_file.write_bytes(b"data")

    file_hash = "abc123"
    index = {
        file_hash: {
            "filename": "doc.txt",
            "mxc_url": "mxc://server/media123",
            "persistent_path": str(persistent),
            "ts": 1.0,
        }
    }
    (tmp_path / ".hashes.json").write_text(json.dumps(index), encoding="utf-8")

    result = await cleanup_file_artifacts(
        CleanupArtifactsInput(
            temp_file_path=str(temp_file),
            persistent_path=str(persistent),
            file_hash=file_hash,
            remove_persistent=True,
        )
    )

    assert result.temp_removed is True
    assert result.persistent_removed is True
    assert result.hash_entry_removed is True
    assert not temp_file.exists()
    assert not persistent.exists()
    idx = json.loads((tmp_path / ".hashes.json").read_text(encoding="utf-8"))
    assert file_hash not in idx


def test_should_remove_persistent_on_cancel():
    dr = DownloadResult(
        file_path="/tmp/file",
        file_size=10,
        duration_ms=1,
        persistent_path="/app/documents/x/file",
        file_hash="hash",
        is_duplicate=False,
    )
    assert FileProcessingWorkflow._should_remove_persistent_on_cancel(dr, ingest_completed=False) is True
    assert FileProcessingWorkflow._should_remove_persistent_on_cancel(dr, ingest_completed=True) is False

    dup = DownloadResult(
        file_path="/tmp/file",
        file_size=10,
        duration_ms=1,
        persistent_path="/app/documents/y/file",
        file_hash="hash2",
        is_duplicate=True,
    )
    assert FileProcessingWorkflow._should_remove_persistent_on_cancel(dup, ingest_completed=False) is False
