"""
Unit tests for Letta webhook handler content extraction.
Ported from TypeScript tests in matrix-identity-bridge.
"""
import pytest
from src.letta.webhook_handler import (
    extract_content_text,
    extract_user_content,
    extract_assistant_content,
    is_inter_agent_relay,
    verify_webhook_signature,
    LettaMessage,
)


class TestExtractContentText:
    """Test extract_content_text function"""
    
    def test_returns_none_for_none(self):
        assert extract_content_text(None) is None
    
    def test_returns_string_directly(self):
        assert extract_content_text("Hello world") == "Hello world"
    
    def test_returns_empty_string_as_is(self):
        assert extract_content_text("") == ""
    
    def test_extracts_text_from_array_single_part(self):
        content = [{"type": "text", "text": "Hello from array"}]
        assert extract_content_text(content) == "Hello from array"
    
    def test_extracts_and_joins_multiple_text_parts(self):
        content = [
            {"type": "text", "text": "Part 1"},
            {"type": "text", "text": "Part 2"},
            {"type": "text", "text": "Part 3"}
        ]
        assert extract_content_text(content) == "Part 1\nPart 2\nPart 3"
    
    def test_skips_non_text_type_parts(self):
        content = [
            {"type": "image", "url": "http://example.com/img.png"},
            {"type": "text", "text": "Actual text"}
        ]
        assert extract_content_text(content) == "Actual text"
    
    def test_returns_none_for_array_with_no_text_parts(self):
        content = [
            {"type": "image", "url": "http://example.com/img.png"},
            {"type": "audio", "url": "http://example.com/audio.mp3"}
        ]
        assert extract_content_text(content) is None
    
    def test_returns_none_for_empty_array(self):
        assert extract_content_text([]) is None
    
    def test_extracts_text_from_object_with_text_field(self):
        content = {"text": "Object text", "other": "data"}
        assert extract_content_text(content) == "Object text"
    
    def test_returns_json_for_object_without_text_field(self):
        content = {"data": "value", "number": 42}
        result = extract_content_text(content)
        assert '"data"' in result
        assert '"value"' in result
    
    def test_handles_nested_structure_gracefully(self):
        content = [{"type": "complex", "nested": {"deep": "value"}}]
        result = extract_content_text(content)
        assert result is None
    
    def test_handles_mixed_valid_invalid_parts(self):
        content = [
            {"type": "text", "text": "Valid 1"},
            {"type": "other"},
            {"type": "text", "text": "Valid 2"},
            {"not_a_type": "field"}
        ]
        assert extract_content_text(content) == "Valid 1\nValid 2"


class TestRealLettaPayloads:
    """Test with actual Letta webhook payload structures"""
    
    def test_v1_agent_simple_response(self):
        content = [{"type": "text", "text": "2 + 2 = 4"}]
        assert extract_content_text(content) == "2 + 2 = 4"
    
    def test_v1_agent_multiline_response(self):
        content = [
            {"type": "text", "text": "Here are the steps:\n1. First\n2. Second\n3. Third"}
        ]
        result = extract_content_text(content)
        assert "1. First" in result
        assert "2. Second" in result
    
    def test_v1_agent_empty_text(self):
        content = [{"type": "text", "text": ""}]
        assert extract_content_text(content) == ""


class TestExtractUserContent:
    """Test extract_user_content function"""
    
    def test_returns_none_for_empty_messages(self):
        assert extract_user_content([]) is None
        assert extract_user_content(None) is None
    
    def test_extracts_user_message_string_content(self):
        messages = [
            LettaMessage(message_type="user_message", content="What is 2+2?")
        ]
        assert extract_user_content(messages) == "What is 2+2?"
    
    def test_extracts_user_message_array_content(self):
        messages = [
            LettaMessage(
                message_type="user_message",
                content=[{"type": "text", "text": "Hello from array"}]
            )
        ]
        assert extract_user_content(messages) == "Hello from array"
    
    def test_ignores_non_user_messages(self):
        messages = [
            LettaMessage(message_type="assistant_message", content="I am the assistant"),
            LettaMessage(message_type="user_message", content="I am the user")
        ]
        assert extract_user_content(messages) == "I am the user"


class TestExtractAssistantContent:
    """Test extract_assistant_content function"""
    
    def test_returns_none_for_empty_messages(self):
        assert extract_assistant_content([]) is None
        assert extract_assistant_content(None) is None
    
    def test_extracts_assistant_message_string_content(self):
        messages = [
            LettaMessage(message_type="assistant_message", content="The answer is 4")
        ]
        assert extract_assistant_content(messages) == "The answer is 4"
    
    def test_extracts_assistant_message_array_content(self):
        messages = [
            LettaMessage(
                message_type="assistant_message",
                content=[{"type": "text", "text": "Response from array"}]
            )
        ]
        assert extract_assistant_content(messages) == "Response from array"
    
    def test_concatenates_multiple_assistant_messages(self):
        messages = [
            LettaMessage(message_type="assistant_message", content="Part 1"),
            LettaMessage(message_type="assistant_message", content="Part 2"),
            LettaMessage(message_type="assistant_message", content="Part 3")
        ]
        result = extract_assistant_content(messages)
        assert result == "Part 1Part 2Part 3"
    
    def test_uses_assistant_message_field_as_fallback(self):
        messages = [
            LettaMessage(
                message_type="assistant_message",
                content=None,
                assistant_message="Fallback content"
            )
        ]
        assert extract_assistant_content(messages) == "Fallback content"


class TestIsInterAgentRelay:
    """Test is_inter_agent_relay function"""
    
    def test_detects_inter_agent_message(self):
        content = "[INTER-AGENT MESSAGE from Meridian] Hello"
        assert is_inter_agent_relay(content) is True
    
    def test_detects_opencode_message(self):
        content = "[MESSAGE FROM OPENCODE USER] Some content"
        assert is_inter_agent_relay(content) is True
    
    def test_detects_forwarded_message(self):
        content = "[FORWARDED FROM Room] Message"
        assert is_inter_agent_relay(content) is True
    
    def test_returns_false_for_normal_message(self):
        content = "This is a normal response"
        assert is_inter_agent_relay(content) is False


class TestVerifyWebhookSignature:
    """Test verify_webhook_signature function"""
    
    def test_skips_verification_when_skip_flag_true(self):
        result = verify_webhook_signature(
            payload="test",
            signature=None,
            secret="secret",
            skip_verification=True
        )
        assert result is True
    
    def test_skips_when_no_secret_configured(self):
        result = verify_webhook_signature(
            payload="test",
            signature="t=123,v1=abc",
            secret=None,
            skip_verification=False
        )
        assert result is True
    
    def test_fails_when_no_signature_provided(self):
        result = verify_webhook_signature(
            payload="test",
            signature=None,
            secret="mysecret",
            skip_verification=False
        )
        assert result is False
    
    def test_fails_on_invalid_signature_format(self):
        result = verify_webhook_signature(
            payload="test",
            signature="invalid",
            secret="mysecret",
            skip_verification=False
        )
        assert result is False
    
    def test_valid_signature_verification(self):
        import hmac
        import hashlib
        
        secret = "test_secret"
        payload = '{"test": "data"}'
        timestamp = "1234567890"
        
        signed = f"{timestamp}.{payload}"
        sig = hmac.new(
            secret.encode(),
            signed.encode(),
            hashlib.sha256
        ).hexdigest()
        
        signature = f"t={timestamp},v1={sig}"
        
        result = verify_webhook_signature(
            payload=payload,
            signature=signature,
            secret=secret,
            skip_verification=False
        )
        assert result is True
    
    def test_invalid_signature_verification(self):
        result = verify_webhook_signature(
            payload='{"test": "data"}',
            signature="t=123,v1=wrongsig",
            secret="mysecret",
            skip_verification=False
        )
        assert result is False
