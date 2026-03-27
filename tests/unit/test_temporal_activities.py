import json
import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest

from temporal_workflows import activities
from temporal_workflows.activities import deliver as deliver_activities
from temporal_workflows.activities import (
    CleanupArtifactsInput,
    DeliveryAckInput,
    DeliverToLettaInput,
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
        self._recv_queue = [
            json.dumps({"type": "session_init", "session_id": "ses-1"}),
            json.dumps({"type": "result", "success": True}),
        ]

    async def send(self, payload):
        self.sent.append(payload)

    async def recv(self):
        if not self._recv_queue:
            raise AssertionError("No more queued websocket events")
        return self._recv_queue.pop(0)

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


@pytest.mark.asyncio
async def test_notify_letta_agent_fire_and_forget_skips_result_wait(monkeypatch):
    """Fire-and-forget mode sends message but does NOT wait for result events."""
    ws = _MockGatewayWS()

    async def _connect(*args, **kwargs):
        return ws

    monkeypatch.setattr(activities.websockets, "connect", _connect)

    result = await notify_letta_agent(
        NotifyAgentInput(
            agent_id="agent-123",
            message="heads up",
            room_id="!room:matrix.test",
            wait_for_result=False,
        )
    )

    assert result.success is True
    assert result.response_text is None
    assert len(ws._recv_queue) == 1
    assert len(ws.sent) == 2


@pytest.mark.asyncio
async def test_notify_letta_agent_wait_for_result_default_collects_response(monkeypatch):
    """Default mode (wait_for_result=True) collects the full agent response."""
    ws = _MockGatewayWS()
    # Add a stream event before the result
    ws._recv_queue = [
        json.dumps({"type": "session_init", "session_id": "ses-1"}),
        json.dumps({"type": "stream", "event": "assistant", "content": "Hello "}),
        json.dumps({"type": "stream", "event": "assistant", "content": "world"}),
        json.dumps({"type": "result", "success": True}),
    ]

    async def _connect(*args, **kwargs):
        return ws

    monkeypatch.setattr(activities.websockets, "connect", _connect)

    result = await notify_letta_agent(
        NotifyAgentInput(
            agent_id="agent-123",
            message="document ready",
            room_id="!room:matrix.test",
        )
    )

    assert result.success is True
    assert result.response_text == "Hello world"
    assert result.duration_ms >= 0


@pytest.mark.asyncio
async def test_notify_letta_agent_raises_on_receive_timeout(monkeypatch):
    ws = _MockGatewayWS()
    ws._recv_queue = [json.dumps({"type": "session_init", "session_id": "ses-1"})]

    async def _connect(*args, **kwargs):
        return ws

    monkeypatch.setattr(activities.websockets, "connect", _connect)

    real_wait_for = asyncio.wait_for
    wait_calls = {"count": 0}

    async def _fake_wait_for(awaitable, timeout):
        if wait_calls["count"] == 0:
            wait_calls["count"] += 1
            return await real_wait_for(awaitable, timeout)
        raise asyncio.TimeoutError

    monkeypatch.setattr("temporal_workflows.activities.notify.asyncio.wait_for", _fake_wait_for)

    with pytest.raises(activities.NotifyError, match="Receive timeout"):
        await notify_letta_agent(
            NotifyAgentInput(
                agent_id="agent-123",
                message="hello",
                room_id="!room:matrix.test",
            )
        )


class _DeliverWS:
    def __init__(self, events):
        self._events = list(events)
        self.sent = []

    async def send(self, payload):
        self.sent.append(payload)

    async def recv(self):
        if not self._events:
            raise AssertionError("No queued websocket events")
        return self._events.pop(0)

    async def close(self):
        return None


class _HTTPResponse:
    def __init__(self, status_code=200, text="ok"):
        self.status_code = status_code
        self.text = text


class _HTTPClient:
    def __init__(self, response):
        self._response = response
        self.post_calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        return self._response


@pytest.mark.asyncio
async def test_deliver_to_letta_collects_stream_and_result(monkeypatch):
    ws = _DeliverWS(
        [
            json.dumps({"type": "session_init", "session_id": "sess-1"}),
            json.dumps({"type": "stream", "event": "assistant", "content": "Hello "}),
            json.dumps({"type": "stream", "event": "assistant", "content": "world"}),
            json.dumps({"type": "result", "success": True}),
        ]
    )

    async def _connect(*args, **kwargs):
        return ws

    monkeypatch.setattr(deliver_activities.websockets, "connect", _connect)

    result = await deliver_activities.deliver_to_letta(
        DeliverToLettaInput(
            agent_id="agent-123",
            message_body="hi",
            room_id="!room:matrix.test",
        )
    )

    assert result.success is True
    assert result.response_text == "Hello world"
    assert len(ws.sent) == 2


@pytest.mark.asyncio
async def test_deliver_to_letta_agent_not_found_is_non_retryable(monkeypatch):
    ws = _DeliverWS(
        [json.dumps({"type": "error", "message": "Agent does not exist"})]
    )

    async def _connect(*args, **kwargs):
        return ws

    monkeypatch.setattr(deliver_activities.websockets, "connect", _connect)

    with pytest.raises(deliver_activities.AgentNotFoundError):
        await deliver_activities.deliver_to_letta(
            DeliverToLettaInput(agent_id="missing-agent", message_body="hello")
        )


@pytest.mark.asyncio
async def test_send_delivery_ack_handles_5xx_gracefully(monkeypatch):
    client = _HTTPClient(_HTTPResponse(status_code=500, text="boom"))
    monkeypatch.setattr(deliver_activities.httpx, "AsyncClient", lambda timeout=10.0: client)

    result = await deliver_activities.send_delivery_ack(
        DeliveryAckInput(
            room_id="!room:matrix.test",
            event_id="$evt",
            agent_id="agent-123",
        )
    )

    assert result.success is False
    assert len(client.post_calls) == 1


@pytest.mark.asyncio
async def test_dead_letter_message_writes_db_and_alerts(monkeypatch):
    execute_calls = []

    class _Conn:
        async def execute(self, query, *args):
            execute_calls.append((query, args))

        async def close(self):
            return None

    async def _connect(_db_url):
        return _Conn()

    client = _HTTPClient(_HTTPResponse(status_code=200, text="ok"))
    monkeypatch.setattr(deliver_activities.httpx, "AsyncClient", lambda timeout=10.0: client)

    import types
    fake_asyncpg = types.SimpleNamespace(connect=_connect)
    import sys
    monkeypatch.setitem(sys.modules, "asyncpg", fake_asyncpg)

    result = await deliver_activities.dead_letter_message(
        deliver_activities.DeadLetterInput(
            event_id="$evt",
            room_id="!room:matrix.test",
            agent_id="agent-123",
            message_body="payload",
            sender="@user:matrix.test",
            error="failed",
            attempts=3,
        )
    )

    assert result.success is True
    assert len(execute_calls) == 1
    assert len(client.post_calls) == 1
