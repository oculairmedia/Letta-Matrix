"""
Unit tests for the WebSocket gateway client.

Tests cover:
- Per-event timeout behavior and connection eviction
- in_use flag management (set during streaming, cleared on success/timeout/exception)
- Abort frame sending on timeout
- Successful streaming event flow
- Error recovery and retry logic
"""

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call
from typing import Dict, Any

from src.letta.ws_gateway_client import (
    GatewayClient,
    GatewayUnavailableError,
    GatewaySessionError,
    _PoolEntry,
)


@pytest.fixture
def mock_ws():
    """Create a mock WebSocket connection."""
    ws = AsyncMock()
    ws.send = AsyncMock()
    ws.recv = AsyncMock()
    ws.close = AsyncMock()
    ws.ping = AsyncMock()
    return ws


@pytest.fixture
def gateway_client():
    """Create a GatewayClient with short timeouts for testing."""
    client = GatewayClient(
        gateway_url="ws://localhost:8000",
        idle_timeout=3600.0,
        max_connections=20,
        connect_timeout=10.0,
        event_timeout=0.1,  # 100ms for fast tests
    )
    return client


@pytest.mark.asyncio
async def test_send_message_streaming_timeout_evicts_connection(gateway_client, mock_ws):
    """
    Test that when entry.ws.recv() hangs beyond _event_timeout,
    GatewayUnavailableError is raised after retry, and the entry is evicted.
    """
    agent_id = "test-agent-1"
    
    # Mock _get_or_create to return our mock entry
    entry = _PoolEntry(
        ws=mock_ws,
        agent_id=agent_id,
        session_id="session-123",
        in_use=True,
    )
    
    # First call to _get_or_create returns entry, second call (after evict) raises
    with patch.object(gateway_client, "_get_or_create", side_effect=[entry, GatewayUnavailableError("No connection")]):
        with patch.object(gateway_client, "_evict", new_callable=AsyncMock) as mock_evict:
            # Make recv hang forever (will timeout)
            mock_ws.recv.side_effect = asyncio.sleep(999)
            
            # Should raise GatewayUnavailableError after timeout and retry
            with pytest.raises(GatewayUnavailableError):
                async for _ in gateway_client.send_message_streaming(
                    agent_id=agent_id,
                    message="test message",
                ):
                    pass
            
            # Verify evict was called (at least once for timeout)
            assert mock_evict.called


@pytest.mark.asyncio
async def test_in_use_flag_set_during_streaming(gateway_client, mock_ws):
    """
    Test that during message streaming, entry.in_use is set to True.
    """
    agent_id = "test-agent-2"
    
    entry = _PoolEntry(
        ws=mock_ws,
        agent_id=agent_id,
        session_id="session-123",
        in_use=True,
    )
    
    # Track in_use state during recv
    in_use_during_recv = None
    
    async def recv_with_state_check():
        nonlocal in_use_during_recv
        in_use_during_recv = entry.in_use
        return json.dumps({"type": "result", "content": "done"})
    
    mock_ws.recv.side_effect = recv_with_state_check
    
    with patch.object(gateway_client, "_get_or_create", return_value=entry):
        async for event in gateway_client.send_message_streaming(
            agent_id=agent_id,
            message="test message",
        ):
            pass
    
    # Verify in_use was True during recv
    assert in_use_during_recv is True


@pytest.mark.asyncio
async def test_in_use_flag_cleared_on_success(gateway_client, mock_ws):
    """
    Test that after successful streaming completes (result event),
    in_use is set to False.
    """
    agent_id = "test-agent-3"
    
    entry = _PoolEntry(
        ws=mock_ws,
        agent_id=agent_id,
        session_id="session-123",
        in_use=True,
    )
    
    # Return result event
    mock_ws.recv.return_value = json.dumps({"type": "result", "content": "done"})
    
    with patch.object(gateway_client, "_get_or_create", return_value=entry):
        async for _ in gateway_client.send_message_streaming(
            agent_id=agent_id,
            message="test message",
        ):
            pass
    
    # Verify in_use is False after completion
    assert entry.in_use is False


@pytest.mark.asyncio
async def test_in_use_flag_cleared_on_timeout(gateway_client, mock_ws):
    """
    Test that after timeout, in_use is set to False (try/finally guarantee).
    """
    agent_id = "test-agent-4"
    
    entry = _PoolEntry(
        ws=mock_ws,
        agent_id=agent_id,
        session_id="session-123",
        in_use=True,
    )
    
    # Make recv hang (will timeout)
    mock_ws.recv.side_effect = asyncio.sleep(999)
    
    with patch.object(gateway_client, "_get_or_create", side_effect=[entry, GatewayUnavailableError("No connection")]):
        with patch.object(gateway_client, "_evict", new_callable=AsyncMock):
            try:
                async for _ in gateway_client.send_message_streaming(
                    agent_id=agent_id,
                    message="test message",
                ):
                    pass
            except GatewayUnavailableError:
                pass
    
    # Verify in_use is False after timeout (try/finally ensures this)
    assert entry.in_use is False


@pytest.mark.asyncio
async def test_in_use_flag_cleared_on_exception(gateway_client, mock_ws):
    """
    Test that after any exception, in_use is set to False.
    """
    agent_id = "test-agent-5"
    
    entry = _PoolEntry(
        ws=mock_ws,
        agent_id=agent_id,
        session_id="session-123",
        in_use=True,
    )
    
    # Make recv raise an exception
    mock_ws.recv.side_effect = RuntimeError("Connection error")
    
    with patch.object(gateway_client, "_get_or_create", side_effect=[entry, GatewayUnavailableError("No connection")]):
        with patch.object(gateway_client, "_evict", new_callable=AsyncMock):
            try:
                async for _ in gateway_client.send_message_streaming(
                    agent_id=agent_id,
                    message="test message",
                ):
                    pass
            except GatewayUnavailableError:
                pass
    
    # Verify in_use is False after exception (try/finally ensures this)
    assert entry.in_use is False


@pytest.mark.asyncio
async def test_timeout_sends_abort_frame(gateway_client, mock_ws):
    """
    Test that on timeout, an abort frame {"type": "abort"} is sent before eviction.
    """
    agent_id = "test-agent-6"
    
    entry = _PoolEntry(
        ws=mock_ws,
        agent_id=agent_id,
        session_id="session-123",
        in_use=True,
    )
    
    # Simulate timeout by raising asyncio.TimeoutError
    mock_ws.recv.side_effect = asyncio.TimeoutError()
    
    with patch.object(gateway_client, "_get_or_create", side_effect=[entry, GatewayUnavailableError("No connection")]):
        with patch.object(gateway_client, "_evict", new_callable=AsyncMock):
            try:
                async for _ in gateway_client.send_message_streaming(
                    agent_id=agent_id,
                    message="test message",
                ):
                    pass
            except GatewayUnavailableError:
                pass
    
    # Verify abort frame was sent
    abort_calls = [
        c for c in mock_ws.send.call_args_list
        if c[0][0] == json.dumps({"type": "abort"})
    ]
    assert len(abort_calls) > 0, "Abort frame should be sent on timeout"


@pytest.mark.asyncio
async def test_successful_streaming_yields_events(gateway_client, mock_ws):
    """
    Test normal flow: stream events yield correctly, result event terminates.
    """
    agent_id = "test-agent-7"
    
    entry = _PoolEntry(
        ws=mock_ws,
        agent_id=agent_id,
        session_id="session-123",
        in_use=True,
    )
    
    # Return stream events then result
    events = [
        json.dumps({"type": "stream", "content": "hello"}),
        json.dumps({"type": "stream", "content": "world"}),
        json.dumps({"type": "result", "content": "done"}),
    ]
    mock_ws.recv.side_effect = events
    
    with patch.object(gateway_client, "_get_or_create", return_value=entry):
        collected_events = []
        async for event in gateway_client.send_message_streaming(
            agent_id=agent_id,
            message="test message",
        ):
            collected_events.append(event)
    
    # Verify we got all events
    assert len(collected_events) == 3
    assert collected_events[0]["type"] == "stream"
    assert collected_events[1]["type"] == "stream"
    assert collected_events[2]["type"] == "result"
    
    # Verify in_use is False after completion
    assert entry.in_use is False


@pytest.mark.asyncio
async def test_session_init_event_yielded(gateway_client, mock_ws):
    """
    Test that session_init events are yielded during streaming.
    """
    agent_id = "test-agent-8"
    
    entry = _PoolEntry(
        ws=mock_ws,
        agent_id=agent_id,
        session_id="session-123",
        in_use=True,
    )
    
    # Return session_init then result
    events = [
        json.dumps({"type": "session_init", "session_id": "session-123"}),
        json.dumps({"type": "result", "content": "done"}),
    ]
    mock_ws.recv.side_effect = events
    
    with patch.object(gateway_client, "_get_or_create", return_value=entry):
        collected_events = []
        async for event in gateway_client.send_message_streaming(
            agent_id=agent_id,
            message="test message",
        ):
            collected_events.append(event)
    
    # Verify session_init was yielded
    assert len(collected_events) == 2
    assert collected_events[0]["type"] == "session_init"
    assert collected_events[1]["type"] == "result"


@pytest.mark.asyncio
async def test_error_event_raises_gateway_session_error(gateway_client, mock_ws):
    """
    Test that error events from gateway raise GatewaySessionError.
    """
    agent_id = "test-agent-9"
    
    entry = _PoolEntry(
        ws=mock_ws,
        agent_id=agent_id,
        session_id="session-123",
        in_use=True,
    )
    
    # Return error event
    mock_ws.recv.return_value = json.dumps({
        "type": "error",
        "code": "INVALID_MESSAGE",
        "message": "Invalid message format",
    })
    
    with patch.object(gateway_client, "_get_or_create", return_value=entry):
        with pytest.raises(GatewaySessionError) as exc_info:
            async for _ in gateway_client.send_message_streaming(
                agent_id=agent_id,
                message="test message",
            ):
                pass
    
    assert exc_info.value.code == "INVALID_MESSAGE"
    assert "Invalid message format" in str(exc_info.value)


@pytest.mark.asyncio
async def test_non_json_frame_skipped(gateway_client, mock_ws):
    """
    Test that non-JSON frames are skipped and streaming continues.
    """
    agent_id = "test-agent-10"
    
    entry = _PoolEntry(
        ws=mock_ws,
        agent_id=agent_id,
        session_id="session-123",
        in_use=True,
    )
    
    # Return non-JSON, then valid events
    events = [
        "not json",
        json.dumps({"type": "stream", "content": "hello"}),
        json.dumps({"type": "result", "content": "done"}),
    ]
    mock_ws.recv.side_effect = events
    
    with patch.object(gateway_client, "_get_or_create", return_value=entry):
        collected_events = []
        async for event in gateway_client.send_message_streaming(
            agent_id=agent_id,
            message="test message",
        ):
            collected_events.append(event)
    
    # Verify non-JSON was skipped, valid events collected
    assert len(collected_events) == 2
    assert collected_events[0]["type"] == "stream"
    assert collected_events[1]["type"] == "result"


@pytest.mark.asyncio
async def test_connection_closed_triggers_retry(gateway_client, mock_ws):
    """
    Test that ConnectionClosed exception triggers retry with fresh connection.
    """
    import websockets
    
    agent_id = "test-agent-11"
    
    entry1 = _PoolEntry(
        ws=mock_ws,
        agent_id=agent_id,
        session_id="session-123",
        in_use=True,
    )
    
    entry2_ws = AsyncMock()
    entry2_ws.recv = AsyncMock(return_value=json.dumps({"type": "result", "content": "done"}))
    entry2 = _PoolEntry(
        ws=entry2_ws,
        agent_id=agent_id,
        session_id="session-456",
        in_use=True,
    )
    
    # First connection closes, second succeeds
    mock_ws.recv.side_effect = websockets.ConnectionClosed(None, None)
    entry2_ws.recv = AsyncMock(return_value=json.dumps({"type": "result", "content": "done"}))
    
    with patch.object(gateway_client, "_get_or_create", side_effect=[entry1, entry2]):
        with patch.object(gateway_client, "_evict", new_callable=AsyncMock):
            collected_events = []
            async for event in gateway_client.send_message_streaming(
                agent_id=agent_id,
                message="test message",
            ):
                collected_events.append(event)
    
    # Verify we got result from second connection
    assert len(collected_events) == 1
    assert collected_events[0]["type"] == "result"


@pytest.mark.asyncio
async def test_pool_entry_in_use_state_isolation(gateway_client, mock_ws):
    """
    Test that in_use flag is properly isolated per pool entry.
    """
    agent_id_1 = "agent-1"
    agent_id_2 = "agent-2"
    
    entry1_ws = AsyncMock()
    entry1_ws.recv = AsyncMock(return_value=json.dumps({"type": "result", "content": "done1"}))
    entry1 = _PoolEntry(
        ws=entry1_ws,
        agent_id=agent_id_1,
        session_id="session-1",
        in_use=True,
    )
    
    entry2_ws = AsyncMock()
    entry2_ws.recv = AsyncMock(return_value=json.dumps({"type": "result", "content": "done2"}))
    entry2 = _PoolEntry(
        ws=entry2_ws,
        agent_id=agent_id_2,
        session_id="session-2",
        in_use=True,
    )
    
    with patch.object(gateway_client, "_get_or_create", side_effect=[entry1, entry2]):
        # Stream from first agent
        async for _ in gateway_client.send_message_streaming(
            agent_id=agent_id_1,
            message="message 1",
        ):
            pass
        
        # Stream from second agent
        async for _ in gateway_client.send_message_streaming(
            agent_id=agent_id_2,
            message="message 2",
        ):
            pass
    
    # Both should have in_use cleared
    assert entry1.in_use is False
    assert entry2.in_use is False


@pytest.mark.asyncio
async def test_get_or_create_returns_entry_with_in_use_set(gateway_client):
    """
    Test that _get_or_create returns entries with in_use=True.
    """
    agent_id = "test-agent-get-or-create"
    
    # Create a healthy entry with in_use=False
    mock_ws = AsyncMock()
    mock_ws.ping = AsyncMock(return_value=None)
    entry = _PoolEntry(
        ws=mock_ws,
        agent_id=agent_id,
        session_id="session-123",
        in_use=False,
    )
    
    # Manually add to pool
    gateway_client._pool[agent_id] = entry
    
    # Call _get_or_create
    result = await gateway_client._get_or_create(agent_id)
    
    # Verify returned entry has in_use=True
    assert result is entry
    assert result.in_use is True


@pytest.mark.asyncio
async def test_get_or_create_waits_for_busy_entry(gateway_client):
    """
    Test that when an entry is in_use=True, _get_or_create waits until it becomes available.
    """
    agent_id = "test-agent-busy"
    
    # Create a healthy entry with in_use=True
    mock_ws = AsyncMock()
    mock_ws.ping = AsyncMock(return_value=None)
    entry = _PoolEntry(
        ws=mock_ws,
        agent_id=agent_id,
        session_id="session-123",
        in_use=True,
    )
    
    # Manually add to pool
    gateway_client._pool[agent_id] = entry
    
    # Start _get_or_create as a task
    task = asyncio.create_task(gateway_client._get_or_create(agent_id))
    
    # Give it time to start waiting
    await asyncio.sleep(0.05)
    
    # Set in_use to False to unblock
    entry.in_use = False
    
    # Task should complete and return the same entry (now reserved with in_use=True)
    result = await asyncio.wait_for(task, timeout=1.0)
    assert result is entry
    assert result.in_use is True


@pytest.mark.asyncio
async def test_get_or_create_timeout_on_permanently_busy_entry(gateway_client):
    """
    Test that when entry stays in_use=True beyond connect_timeout, GatewayUnavailableError is raised.
    """
    agent_id = "test-agent-timeout"
    
    # Create a healthy entry with in_use=True
    mock_ws = AsyncMock()
    mock_ws.ping = AsyncMock(return_value=None)
    entry = _PoolEntry(
        ws=mock_ws,
        agent_id=agent_id,
        session_id="session-123",
        in_use=True,
    )
    
    # Manually add to pool
    gateway_client._pool[agent_id] = entry
    gateway_client._connect_timeout = 0.1
    
    # Call _get_or_create with short timeout
    with pytest.raises(GatewayUnavailableError) as exc_info:
        await gateway_client._get_or_create(agent_id)
    
    assert "Timed out" in str(exc_info.value)


@pytest.mark.asyncio
async def test_evict_oldest_unlocked_skips_in_use(gateway_client):
    """
    Test that _evict_oldest_unlocked skips entries that are in_use.
    """
    agent_id_old = "agent-old"
    agent_id_new = "agent-new"
    
    # Create two entries
    mock_ws_old = AsyncMock()
    mock_ws_new = AsyncMock()
    
    entry_old = _PoolEntry(
        ws=mock_ws_old,
        agent_id=agent_id_old,
        session_id="session-old",
        in_use=True,
        last_used=0,  # Very old
    )
    
    entry_new = _PoolEntry(
        ws=mock_ws_new,
        agent_id=agent_id_new,
        session_id="session-new",
        in_use=False,
        last_used=100,  # Recent
    )
    
    # Add to pool
    gateway_client._pool[agent_id_old] = entry_old
    gateway_client._pool[agent_id_new] = entry_new
    
    # Call _evict_oldest_unlocked
    await gateway_client._evict_oldest_unlocked()
    
    # Verify agent-old is still in pool (skipped because in_use)
    assert agent_id_old in gateway_client._pool
    # Verify agent-new was evicted (oldest among non-in_use)
    assert agent_id_new not in gateway_client._pool


@pytest.mark.asyncio
async def test_evict_oldest_unlocked_noop_when_all_in_use(gateway_client):
    """
    Test that _evict_oldest_unlocked does nothing when all entries are in_use.
    """
    agent_id_1 = "agent-1"
    agent_id_2 = "agent-2"
    
    # Create two entries, both in_use
    mock_ws_1 = AsyncMock()
    mock_ws_2 = AsyncMock()
    
    entry_1 = _PoolEntry(
        ws=mock_ws_1,
        agent_id=agent_id_1,
        session_id="session-1",
        in_use=True,
    )
    
    entry_2 = _PoolEntry(
        ws=mock_ws_2,
        agent_id=agent_id_2,
        session_id="session-2",
        in_use=True,
    )
    
    # Add to pool
    gateway_client._pool[agent_id_1] = entry_1
    gateway_client._pool[agent_id_2] = entry_2
    
    # Call _evict_oldest_unlocked
    await gateway_client._evict_oldest_unlocked()
    
    # Verify both are still in pool
    assert agent_id_1 in gateway_client._pool
    assert agent_id_2 in gateway_client._pool
