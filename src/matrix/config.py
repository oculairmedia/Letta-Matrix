"""
Configuration, exceptions, and logging setup for the Matrix bridge.

Extracted from client.py as a standalone module.
Re-exported by client.py for backward compatibility.
"""
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


# ── Exceptions ───────────────────────────────────────────────────────

class LettaApiError(Exception):
    """Raised when Letta API calls fail."""

    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        response_body: Optional[str] = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


class MatrixClientError(Exception):
    """Raised when Matrix client operations fail."""
    pass


class ConfigurationError(Exception):
    """Raised when configuration is invalid."""
    pass


class LettaCodeApiError(Exception):
    """Raised when Letta Code API calls fail."""

    def __init__(
        self,
        status_code: int,
        message: str,
        details: Optional[Any] = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.details = details


# ── Config Dataclass ─────────────────────────────────────────────────

@dataclass
class Config:
    homeserver_url: str
    username: str
    password: str
    room_id: str
    letta_api_url: str
    letta_token: str
    letta_agent_id: str
    log_level: str = "INFO"
    letta_code_api_url: str = os.getenv("LETTA_CODE_API_URL", "http://192.168.50.90:3099")
    letta_code_enabled: bool = os.getenv("LETTA_CODE_ENABLED", "true").lower() == "true"

    # Embedding configuration for Letta file uploads
    embedding_model: str = "letta/letta-free"
    embedding_endpoint: str = ""
    embedding_endpoint_type: str = "openai"
    embedding_dim: int = 1536
    embedding_chunk_size: int = 300
    # Matrix access token (set after login)
    matrix_token: Optional[str] = None
    # Streaming configuration
    letta_streaming_enabled: bool = False
    letta_streaming_timeout: float = 120.0
    letta_streaming_idle_timeout: float = 120.0
    letta_streaming_live_edit: bool = False
    letta_max_tool_calls: int = 100
    # Typing indicators
    letta_typing_enabled: bool = False
    # Conversations API configuration
    letta_conversations_enabled: bool = False
    # Gateway configuration
    letta_gateway_enabled: bool = False
    letta_gateway_url: str = "ws://192.168.50.90:8407/api/v1/agent-gateway"
    letta_gateway_api_key: str = ""
    letta_gateway_idle_timeout: float = 3600.0
    letta_gateway_max_connections: int = 20
    # Document parsing configuration (MarkItDown)
    document_parsing_enabled: bool = True
    document_parsing_max_file_size_mb: int = 50
    document_parsing_timeout: float = 120.0
    document_parsing_ocr_enabled: bool = True
    document_parsing_ocr_dpi: int = 200
    document_parsing_max_text_length: int = 50000
    # Group gating configuration (per-room response modes)
    matrix_groups: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> "Config":
        """Load configuration from environment variables."""
        try:
            config = cls(
                homeserver_url=os.getenv("MATRIX_HOMESERVER_URL", "http://localhost:8008"),
                username=os.getenv("MATRIX_USERNAME", "@letta:matrix.oculair.ca"),
                password=os.getenv("MATRIX_PASSWORD", "letta"),
                room_id=os.getenv("MATRIX_ROOM_ID", "") or "",
                letta_api_url=os.getenv("LETTA_API_URL", "http://192.168.50.90:8289"),
                letta_token=os.getenv("LETTA_TOKEN", "lettaSecurePass123"),
                letta_agent_id=os.getenv(
                    "LETTA_AGENT_ID", "agent-0e99d1a5-d9ca-43b0-9df9-c09761d01444"
                ),
                log_level=os.getenv("LOG_LEVEL", "INFO"),
                embedding_model=os.getenv("LETTA_EMBEDDING_MODEL", "letta/letta-free"),
                embedding_endpoint=os.getenv("LETTA_EMBEDDING_ENDPOINT", ""),
                embedding_endpoint_type=os.getenv("LETTA_EMBEDDING_ENDPOINT_TYPE", "openai"),
                embedding_dim=int(os.getenv("LETTA_EMBEDDING_DIM", "1536")),
                embedding_chunk_size=int(os.getenv("LETTA_EMBEDDING_CHUNK_SIZE", "300")),
                letta_streaming_enabled=os.getenv("LETTA_STREAMING_ENABLED", "false").lower() == "true",
                letta_streaming_timeout=float(os.getenv("LETTA_STREAMING_TIMEOUT", "120.0")),
                letta_streaming_idle_timeout=float(os.getenv("LETTA_STREAMING_IDLE_TIMEOUT", "120.0")),
                letta_streaming_live_edit=os.getenv("LETTA_STREAMING_LIVE_EDIT", "false").lower() == "true",
                letta_code_api_url=os.getenv("LETTA_CODE_API_URL", "http://192.168.50.90:3099"),
                letta_code_enabled=os.getenv("LETTA_CODE_ENABLED", "true").lower() == "true",
                letta_max_tool_calls=int(os.getenv("LETTA_MAX_TOOL_CALLS", "100")),
                letta_conversations_enabled=os.getenv("LETTA_CONVERSATIONS_ENABLED", "false").lower() == "true",
                letta_typing_enabled=os.getenv("LETTA_TYPING_ENABLED", "false").lower() == "true",
                letta_gateway_enabled=os.getenv("LETTA_GATEWAY_ENABLED", "false").lower() == "true",
                letta_gateway_url=os.getenv(
                    "LETTA_GATEWAY_URL", "ws://192.168.50.90:8407/api/v1/agent-gateway"
                ),
                letta_gateway_api_key=os.getenv("LETTA_GATEWAY_API_KEY", ""),
                letta_gateway_idle_timeout=float(os.getenv("LETTA_GATEWAY_IDLE_TIMEOUT", "3600.0")),
                letta_gateway_max_connections=int(os.getenv("LETTA_GATEWAY_MAX_CONNECTIONS", "20")),
                document_parsing_enabled=os.getenv("DOCUMENT_PARSING_ENABLED", "true").lower() == "true",
                document_parsing_max_file_size_mb=int(os.getenv("DOCUMENT_PARSING_MAX_FILE_SIZE_MB", "50")),
                document_parsing_timeout=float(os.getenv("DOCUMENT_PARSING_TIMEOUT_SECONDS", "120.0")),
                document_parsing_ocr_enabled=os.getenv("DOCUMENT_PARSING_OCR_ENABLED", "true").lower() == "true",
                document_parsing_ocr_dpi=int(os.getenv("DOCUMENT_PARSING_OCR_DPI", "200")),
                document_parsing_max_text_length=int(os.getenv("DOCUMENT_PARSING_MAX_TEXT_LENGTH", "50000")),
            )
            # Load group gating configuration
            groups_json = os.getenv("MATRIX_GROUPS_JSON", "").strip()
            if groups_json:
                from src.matrix.group_config import load_groups_config
                config.matrix_groups = load_groups_config(groups_json)
            return config
        except Exception as e:
            raise ConfigurationError(f"Failed to load configuration: {e}")


# ── Logging Setup ────────────────────────────────────────────────────

class _JSONFormatter(logging.Formatter):
    """JSON log formatter for structured logging."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": time.strftime(
                "%Y-%m-%dT%H:%M:%S.%fZ", time.gmtime(record.created)
            ),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)
        extra_fields = getattr(record, "extra", None)
        if isinstance(extra_fields, dict):
            log_entry.update(extra_fields)
        return json.dumps(log_entry)


def setup_logging(config: Config) -> logging.Logger:
    """Setup structured JSON logging."""
    logger = logging.getLogger("matrix_client")
    logger.setLevel(getattr(logging, config.log_level.upper()))

    for handler in logger.handlers[:]:
        logger.removeHandler(handler)

    handler = logging.StreamHandler()
    handler.setLevel(getattr(logging, config.log_level.upper()))
    handler.setFormatter(_JSONFormatter())
    logger.addHandler(handler)
    logger.propagate = False
    return logger
