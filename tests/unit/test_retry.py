"""
Unit tests for src/core/retry.py

Tests the CONVERSATION_BUSY (409) retry logic with exponential backoff.
"""

import asyncio
import pytest
from unittest.mock import Mock, patch, MagicMock
from typing import Optional

from src.core.retry import (
    ConversationBusyError,
    is_conversation_busy_error,
    retry_on_conversation_busy,
    send_message_with_retry,
)


class TestConversationBusyError:
    def test_error_attributes(self):
        error = ConversationBusyError(
            conversation_id="conv-123",
            attempts=3,
            last_error=ValueError("original error")
        )
        
        assert error.conversation_id == "conv-123"
        assert error.attempts == 3
        assert isinstance(error.last_error, ValueError)
        assert "conv-123" in str(error)
        assert "3" in str(error)
    
    def test_error_without_last_error(self):
        error = ConversationBusyError(
            conversation_id="conv-456",
            attempts=5,
            last_error=None
        )
        
        assert error.last_error is None
        assert "conv-456" in str(error)


class TestIsConversationBusyError:
    def test_generic_exception_not_busy(self):
        error = ValueError("some error")
        assert is_conversation_busy_error(error) is False
    
    def test_error_with_409_and_conversation_in_message(self):
        error = Exception("Error 409: conversation busy")
        assert is_conversation_busy_error(error) is True
    
    def test_error_with_409_and_busy_in_message(self):
        error = Exception("HTTP 409 - resource busy")
        assert is_conversation_busy_error(error) is True
    
    def test_error_without_409(self):
        error = Exception("conversation busy but no status code")
        assert is_conversation_busy_error(error) is False
    
    @patch('src.core.retry.ConflictError', None)
    def test_fallback_when_sdk_not_available(self):
        error = Exception("Error code: 409 - conversation is busy")
        assert is_conversation_busy_error(error) is True


class MockConflictError(Exception):
    pass


class TestIsConversationBusyErrorWithConflictError:
    @patch('src.core.retry.ConflictError', MockConflictError)
    def test_conflict_error_with_busy_message(self):
        error = MockConflictError("CONVERSATION_BUSY: agent is processing")
        assert is_conversation_busy_error(error) is True
    
    @patch('src.core.retry.ConflictError', MockConflictError)
    def test_conflict_error_with_body_attribute(self):
        error = MockConflictError("Some error")
        error.body = {"error": "CONVERSATION_BUSY"}
        assert is_conversation_busy_error(error) is True
    
    @patch('src.core.retry.ConflictError', MockConflictError)
    def test_conflict_error_generic(self):
        error = MockConflictError("generic conflict")
        assert is_conversation_busy_error(error) is True


class TestRetryOnConversationBusy:
    @pytest.mark.asyncio
    async def test_success_on_first_attempt(self):
        call_count = 0
        
        def sync_func():
            nonlocal call_count
            call_count += 1
            return "success"
        
        result = await retry_on_conversation_busy(
            func=sync_func,
            conversation_id="conv-123",
            max_retries=3,
        )
        
        assert result == "success"
        assert call_count == 1
    
    @pytest.mark.asyncio
    async def test_success_on_second_attempt(self):
        call_count = 0
        
        def sync_func():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("Error 409: conversation busy")
            return "success after retry"
        
        with patch('src.core.retry.asyncio.sleep', return_value=None):
            result = await retry_on_conversation_busy(
                func=sync_func,
                conversation_id="conv-123",
                max_retries=3,
                base_delay=0.001,
            )
        
        assert result == "success after retry"
        assert call_count == 2
    
    @pytest.mark.asyncio
    async def test_exhausts_retries_and_raises(self):
        call_count = 0
        
        def sync_func():
            nonlocal call_count
            call_count += 1
            raise Exception("Error 409: conversation busy")
        
        with patch('src.core.retry.asyncio.sleep', return_value=None):
            with pytest.raises(ConversationBusyError) as exc_info:
                await retry_on_conversation_busy(
                    func=sync_func,
                    conversation_id="conv-123",
                    max_retries=2,
                    base_delay=0.001,
                )
        
        assert call_count == 3
        assert exc_info.value.conversation_id == "conv-123"
        assert exc_info.value.attempts == 3
    
    @pytest.mark.asyncio
    async def test_non_busy_error_raises_immediately(self):
        call_count = 0
        
        def sync_func():
            nonlocal call_count
            call_count += 1
            raise ValueError("not a busy error")
        
        with pytest.raises(ValueError):
            await retry_on_conversation_busy(
                func=sync_func,
                conversation_id="conv-123",
                max_retries=3,
            )
        
        assert call_count == 1
    
    @pytest.mark.asyncio
    async def test_async_function_support(self):
        call_count = 0
        
        async def async_func():
            nonlocal call_count
            call_count += 1
            return "async result"
        
        result = await retry_on_conversation_busy(
            func=async_func,
            conversation_id="conv-123",
            max_retries=3,
        )
        
        assert result == "async result"
        assert call_count == 1
    
    @pytest.mark.asyncio
    async def test_exponential_backoff_delays(self):
        call_count = 0
        sleep_calls = []
        
        async def mock_sleep(delay):
            sleep_calls.append(delay)
        
        def sync_func():
            nonlocal call_count
            call_count += 1
            if call_count < 4:
                raise Exception("Error 409: conversation busy")
            return "success"
        
        with patch('src.core.retry.asyncio.sleep', side_effect=mock_sleep):
            result = await retry_on_conversation_busy(
                func=sync_func,
                conversation_id="conv-123",
                max_retries=3,
                base_delay=1.0,
                max_delay=8.0,
            )
        
        assert result == "success"
        assert len(sleep_calls) == 3
        assert sleep_calls[0] == 1.0
        assert sleep_calls[1] == 2.0
        assert sleep_calls[2] == 4.0
    
    @pytest.mark.asyncio
    async def test_max_delay_cap(self):
        call_count = 0
        sleep_calls = []
        
        async def mock_sleep(delay):
            sleep_calls.append(delay)
        
        def sync_func():
            nonlocal call_count
            call_count += 1
            if call_count < 5:
                raise Exception("Error 409: conversation busy")
            return "success"
        
        with patch('src.core.retry.asyncio.sleep', side_effect=mock_sleep):
            result = await retry_on_conversation_busy(
                func=sync_func,
                conversation_id="conv-123",
                max_retries=4,
                base_delay=2.0,
                max_delay=5.0,
            )
        
        assert result == "success"
        assert sleep_calls[2] == 5.0
        assert sleep_calls[3] == 5.0


class TestSendMessageWithRetry:
    @pytest.mark.asyncio
    async def test_success_without_retry(self):
        mock_client = Mock()
        mock_stream = [Mock(content="response")]
        mock_client.conversations.messages.create.return_value = mock_stream
        
        result = await send_message_with_retry(
            letta_client=mock_client,
            conversation_id="conv-123",
            message="Hello",
            streaming=True,
        )
        
        assert result == mock_stream
        mock_client.conversations.messages.create.assert_called_once_with(
            conversation_id="conv-123",
            input="Hello",
            streaming=True,
        )
    
    @pytest.mark.asyncio
    async def test_retry_on_busy(self):
        mock_client = Mock()
        call_count = 0
        
        def create_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("Error 409: conversation busy")
            return [Mock(content="response")]
        
        mock_client.conversations.messages.create.side_effect = create_side_effect
        
        with patch('src.core.retry.asyncio.sleep', return_value=None):
            result = await send_message_with_retry(
                letta_client=mock_client,
                conversation_id="conv-123",
                message="Hello",
                streaming=True,
                max_retries=3,
            )
        
        assert call_count == 2
        assert len(result) == 1
    
    @pytest.mark.asyncio
    async def test_passes_additional_kwargs(self):
        mock_client = Mock()
        mock_stream = [Mock()]
        mock_client.conversations.messages.create.return_value = mock_stream
        
        await send_message_with_retry(
            letta_client=mock_client,
            conversation_id="conv-123",
            message="Hello",
            streaming=False,
            stream_tokens=True,
            include_pings=False,
        )
        
        mock_client.conversations.messages.create.assert_called_once_with(
            conversation_id="conv-123",
            input="Hello",
            streaming=False,
            stream_tokens=True,
            include_pings=False,
        )


class TestLogging:
    @pytest.mark.asyncio
    async def test_logs_retry_attempts(self):
        mock_logger = Mock()
        call_count = 0
        
        def sync_func():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise Exception("Error 409: conversation busy")
            return "success"
        
        with patch('src.core.retry.asyncio.sleep', return_value=None):
            await retry_on_conversation_busy(
                func=sync_func,
                conversation_id="conv-123",
                max_retries=3,
                logger_instance=mock_logger,
            )
        
        mock_logger.warning.assert_called()
        warning_msg = mock_logger.warning.call_args[0][0]
        assert "conv-123" in warning_msg
        assert "busy" in warning_msg.lower()
    
    @pytest.mark.asyncio
    async def test_logs_final_failure(self):
        mock_logger = Mock()
        
        def sync_func():
            raise Exception("Error 409: conversation busy")
        
        with patch('src.core.retry.asyncio.sleep', return_value=None):
            with pytest.raises(ConversationBusyError):
                await retry_on_conversation_busy(
                    func=sync_func,
                    conversation_id="conv-123",
                    max_retries=1,
                    logger_instance=mock_logger,
                )
        
        mock_logger.error.assert_called()
        error_msg = mock_logger.error.call_args[0][0]
        assert "conv-123" in error_msg
