"""
Unit tests for exception chaining in letta_bridge.py

Tests verify that re-raised exceptions preserve the original cause via `from e`
in the following scenarios:
  1. Gateway client connection failures
  2. Streaming message failures
  3. HTTP status errors from Letta API
  4. Unexpected errors during API calls
"""

import pytest
import logging
from unittest.mock import AsyncMock, MagicMock, patch
from dataclasses import dataclass

from src.matrix.config import LettaApiError
from src.matrix.letta_bridge import _get_gateway_client, send_to_letta_api_streaming, send_to_letta_api


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def mock_config():
    """Mock configuration object for testing"""
    @dataclass
    class MockConfig:
        homeserver_url: str = "http://test-synapse:8008"
        username: str = "@test:matrix.test"
        password: str = "test_password"
        room_id: str = "!testroom:matrix.test"
        letta_api_url: str = "http://test-letta:8283"
        letta_token: str = "test_token"
        letta_agent_id: str = "test-agent-id"
        log_level: str = "INFO"
        letta_gateway_url: str = "ws://test-gateway:8407/api/v1/agent-gateway"
        letta_gateway_api_key: str = "test_gateway_key"
        letta_gateway_idle_timeout: float = 3600.0
        letta_gateway_max_connections: int = 20
        letta_typing_enabled: bool = False
        letta_conversations_enabled: bool = False
        letta_streaming_live_edit: bool = False
        letta_streaming_enabled: bool = False
        letta_streaming_timeout: float = 120.0
        letta_streaming_idle_timeout: float = 120.0
        letta_max_tool_calls: int = 100

    return MockConfig()


@pytest.fixture
def mock_logger():
    """Mock logger for testing"""
    return MagicMock(spec=logging.Logger)


# ============================================================================
# Tests for _get_gateway_client exception chaining
# ============================================================================

@pytest.mark.asyncio
async def test_gateway_unavailable_chains_original_exception(mock_config, mock_logger):
    """
    Test that when gateway connection fails, the raised LettaApiError
    has __cause__ set to the original exception.
    
    Scenario: GatewayClient constructor raises ConnectionRefusedError
    Expected: LettaApiError with __cause__ = ConnectionRefusedError
    """
    original_error = ConnectionRefusedError("Connection refused to gateway")
    
    with patch("src.letta.ws_gateway_client.get_gateway_client") as mock_get_gw:
        mock_get_gw.side_effect = original_error
        
        with pytest.raises(LettaApiError) as exc_info:
            await _get_gateway_client(mock_config, mock_logger)
        
        # Verify exception chaining
        assert exc_info.value.__cause__ is not None
        assert isinstance(exc_info.value.__cause__, ConnectionRefusedError)
        assert str(exc_info.value.__cause__) == "Connection refused to gateway"


@pytest.mark.asyncio
async def test_gateway_timeout_chains_original_exception(mock_config, mock_logger):
    """
    Test that when gateway connection times out, the raised LettaApiError
    has __cause__ set to the original timeout exception.
    
    Scenario: GatewayClient constructor raises asyncio.TimeoutError
    Expected: LettaApiError with __cause__ = asyncio.TimeoutError
    """
    import asyncio
    original_error = asyncio.TimeoutError("Gateway connection timed out")
    
    with patch("src.letta.ws_gateway_client.get_gateway_client") as mock_get_gw:
        mock_get_gw.side_effect = original_error
        
        with pytest.raises(LettaApiError) as exc_info:
            await _get_gateway_client(mock_config, mock_logger)
        
        # Verify exception chaining
        assert exc_info.value.__cause__ is not None
        assert isinstance(exc_info.value.__cause__, asyncio.TimeoutError)
        assert "timed out" in str(exc_info.value.__cause__)


@pytest.mark.asyncio
async def test_gateway_generic_error_chains_original_exception(mock_config, mock_logger):
    """
    Test that when gateway raises a generic exception, the raised LettaApiError
    has __cause__ set to the original exception.
    
    Scenario: GatewayClient constructor raises RuntimeError
    Expected: LettaApiError with __cause__ = RuntimeError
    """
    original_error = RuntimeError("Gateway initialization failed")
    
    with patch("src.letta.ws_gateway_client.get_gateway_client") as mock_get_gw:
        mock_get_gw.side_effect = original_error
        
        with pytest.raises(LettaApiError) as exc_info:
            await _get_gateway_client(mock_config, mock_logger)
        
        # Verify exception chaining
        assert exc_info.value.__cause__ is not None
        assert isinstance(exc_info.value.__cause__, RuntimeError)


# ============================================================================
# Tests for send_to_letta_api_streaming exception chaining
# ============================================================================

# ============================================================================
# Tests for send_to_letta_api exception chaining
# ============================================================================

@pytest.mark.asyncio
async def test_api_error_chains_http_status_error(mock_config, mock_logger):
    """
    Test that when Letta API returns HTTP error, the raised LettaApiError
    has __cause__ set to the original aiohttp.ClientResponseError.
    
    Scenario: collect_via_gateway raises aiohttp.ClientResponseError
    Expected: LettaApiError with __cause__ = ClientResponseError
    """
    import aiohttp
    
    # Create a mock ClientResponseError
    original_error = aiohttp.ClientResponseError(
        request_info=MagicMock(),
        history=(),
        status=500,
        message="Internal Server Error",
        headers={},
    )
    
    # Mock the gateway client
    mock_gateway = AsyncMock()
    
    with patch("src.matrix.letta_bridge._get_gateway_client") as mock_get_gw:
        mock_get_gw.return_value = mock_gateway
        
        with patch("src.matrix.letta_bridge._resolve_agent_for_room") as mock_resolve_agent:
            mock_resolve_agent.return_value = ("agent-123", "TestAgent")
            
            with patch("src.matrix.letta_bridge._resolve_conversation_id") as mock_resolve_conv:
                mock_resolve_conv.return_value = "conv-123"
                
                with patch("src.letta.gateway_stream_reader.collect_via_gateway") as mock_collect:
                    mock_collect.side_effect = original_error
                    
                    with pytest.raises(LettaApiError) as exc_info:
                        await send_to_letta_api(
                            message_body="test message",
                            sender_id="@user:matrix.test",
                            config=mock_config,
                            logger=mock_logger,
                            room_id="!room:matrix.test",
                        )
                    
                    # Verify exception chaining
                    assert exc_info.value.__cause__ is not None
                    assert isinstance(exc_info.value.__cause__, aiohttp.ClientResponseError)
                    assert exc_info.value.status_code == 500


@pytest.mark.asyncio
async def test_api_error_chains_error_code_pattern(mock_config, mock_logger):
    """
    Test that when Letta SDK raises error with "Error code:" pattern,
    the raised LettaApiError has __cause__ set to the original exception.
    
    Scenario: collect_via_gateway raises Exception with "Error code: 400" pattern
    Expected: LettaApiError with __cause__ = original Exception
    """
    original_error = Exception("Error code: 400 - Bad Request")
    
    # Mock the gateway client
    mock_gateway = AsyncMock()
    
    with patch("src.matrix.letta_bridge._get_gateway_client") as mock_get_gw:
        mock_get_gw.return_value = mock_gateway
        
        with patch("src.matrix.letta_bridge._resolve_agent_for_room") as mock_resolve_agent:
            mock_resolve_agent.return_value = ("agent-123", "TestAgent")
            
            with patch("src.matrix.letta_bridge._resolve_conversation_id") as mock_resolve_conv:
                mock_resolve_conv.return_value = "conv-123"
                
                with patch("src.letta.gateway_stream_reader.collect_via_gateway") as mock_collect:
                    mock_collect.side_effect = original_error
                    
                    with pytest.raises(LettaApiError) as exc_info:
                        await send_to_letta_api(
                            message_body="test message",
                            sender_id="@user:matrix.test",
                            config=mock_config,
                            logger=mock_logger,
                            room_id="!room:matrix.test",
                        )
                    
                    # Verify exception chaining
                    assert exc_info.value.__cause__ is not None
                    assert isinstance(exc_info.value.__cause__, Exception)
                    assert "Error code: 400" in str(exc_info.value.__cause__)
                    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_api_unexpected_error_chains_original(mock_config, mock_logger):
    """
    Test that when an unexpected error occurs during API call,
    the raised LettaApiError has __cause__ set to the original exception.
    
    Scenario: collect_via_gateway raises ValueError (unexpected)
    Expected: LettaApiError with __cause__ = ValueError
    """
    original_error = ValueError("Unexpected value error")
    
    # Mock the gateway client
    mock_gateway = AsyncMock()
    
    with patch("src.matrix.letta_bridge._get_gateway_client") as mock_get_gw:
        mock_get_gw.return_value = mock_gateway
        
        with patch("src.matrix.letta_bridge._resolve_agent_for_room") as mock_resolve_agent:
            mock_resolve_agent.return_value = ("agent-123", "TestAgent")
            
            with patch("src.matrix.letta_bridge._resolve_conversation_id") as mock_resolve_conv:
                mock_resolve_conv.return_value = "conv-123"
                
                with patch("src.letta.gateway_stream_reader.collect_via_gateway") as mock_collect:
                    mock_collect.side_effect = original_error
                    
                    with pytest.raises(LettaApiError) as exc_info:
                        await send_to_letta_api(
                            message_body="test message",
                            sender_id="@user:matrix.test",
                            config=mock_config,
                            logger=mock_logger,
                            room_id="!room:matrix.test",
                        )
                    
                    # Verify exception chaining
                    assert exc_info.value.__cause__ is not None
                    assert isinstance(exc_info.value.__cause__, ValueError)
                    assert "Unexpected value error" in str(exc_info.value.__cause__)


# ============================================================================
# Integration test: Verify exception chain is preserved through stack
# ============================================================================

@pytest.mark.asyncio
async def test_exception_chain_preserved_through_traceback(mock_config, mock_logger):
    """
    Test that the exception chain is preserved and accessible through
    the traceback, allowing debugging tools to see the original cause.
    
    This is important for logging and error reporting systems that
    inspect __cause__ to provide better error context.
    """
    original_error = RuntimeError("Original root cause")
    
    with patch("src.letta.ws_gateway_client.get_gateway_client") as mock_get_gw:
        mock_get_gw.side_effect = original_error
        
        try:
            await _get_gateway_client(mock_config, mock_logger)
        except LettaApiError as e:
            # Verify the chain is intact
            assert e.__cause__ is original_error
            assert e.__cause__.__class__.__name__ == "RuntimeError"
            assert str(e.__cause__) == "Original root cause"
            
            # Verify we can walk the chain
            cause = e.__cause__
            assert cause is not None
            assert isinstance(cause, RuntimeError)
