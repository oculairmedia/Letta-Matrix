import os
import sys
import types

from . import download as _download
from . import notify as _notify
from .cleanup import (
    CleanupArtifactsInput,
    CleanupArtifactsResult,
    cleanup_file_artifacts,
)
from .common import (
    DownloadError,
    FileActivityError,
    IngestError,
    MatrixAPIError,
    NotifyError,
    ParseError,
)
from .download import (
    MATRIX_ACCESS_TOKEN,
    MATRIX_HOMESERVER_URL,
    PERSISTENT_DOCUMENTS_DIR,
    DownloadInput,
    DownloadResult,
    _EXT_MAP,
    _HASH_INDEX_FILENAME,
    _HASH_LOCK_FILENAME,
    _hash_index_paths,
    _load_hash_index,
    _mutate_hash_index_locked,
    _persist_file,
    _save_hash_index,
    _sha256,
    download_file_from_matrix,
)
from .ingest import HAYHOOKS_INGEST_URL, IngestInput, IngestResult, ingest_to_haystack
from .notify import (
    LETTA_GATEWAY_API_KEY,
    LETTA_GATEWAY_URL,
    MATRIX_API_URL,
    MatrixStatusInput,
    MatrixStatusResult,
    NotifyAgentInput,
    NotifyAgentResult,
    notify_letta_agent,
    update_matrix_status,
)
from .deliver import (
    deliver_to_letta,
    dead_letter_message,
    send_delivery_ack,
    DeliverToLettaInput,
    DeliverToLettaResult,
    DeadLetterInput,
    DeadLetterResult,
    DeliveryAckInput,
    DeliveryAckResult,
)
from .parse import ParseInput, ParseResult, parse_with_markitdown

# Config vars from the original module not used by activities but kept for compat
LETTA_API_URL = os.getenv("LETTA_API_URL", "http://192.168.50.90:8289")
LETTA_TOKEN = os.getenv("LETTA_TOKEN", "")

# Module references for test monkeypatching (tests do activities.httpx / activities.websockets)
httpx = _notify.httpx
websockets = _notify.websockets


class _ActivitiesModule(types.ModuleType):
    def __setattr__(self, name, value):
        super().__setattr__(name, value)
        if name == "PERSISTENT_DOCUMENTS_DIR":
            dl = self.__dict__.get("_download")
            if dl is not None:
                dl.PERSISTENT_DOCUMENTS_DIR = value


sys.modules[__name__].__class__ = _ActivitiesModule

__all__ = [
    "DownloadInput",
    "DownloadResult",
    "ParseInput",
    "ParseResult",
    "IngestInput",
    "IngestResult",
    "NotifyAgentInput",
    "NotifyAgentResult",
    "MatrixStatusInput",
    "MatrixStatusResult",
    "CleanupArtifactsInput",
    "CleanupArtifactsResult",
    "download_file_from_matrix",
    "parse_with_markitdown",
    "ingest_to_haystack",
    "notify_letta_agent",
    "update_matrix_status",
    "cleanup_file_artifacts",
    "FileActivityError",
    "DownloadError",
    "ParseError",
    "IngestError",
    "NotifyError",
    "MatrixAPIError",
    "MATRIX_HOMESERVER_URL",
    "MATRIX_ACCESS_TOKEN",
    "MATRIX_API_URL",
    "LETTA_API_URL",
    "LETTA_TOKEN",
    "LETTA_GATEWAY_URL",
    "LETTA_GATEWAY_API_KEY",
    "HAYHOOKS_INGEST_URL",
    "PERSISTENT_DOCUMENTS_DIR",
    "deliver_to_letta",
    "dead_letter_message",
    "send_delivery_ack",
    "DeliverToLettaInput",
    "DeliverToLettaResult",
    "DeadLetterInput",
    "DeadLetterResult",
    "DeliveryAckInput",
    "DeliveryAckResult",
    "_sha256",
    "_hash_index_paths",
    "_load_hash_index",
    "_save_hash_index",
    "_mutate_hash_index_locked",
    "_persist_file",
    "_EXT_MAP",
    "_HASH_INDEX_FILENAME",
    "_HASH_LOCK_FILENAME",
    "httpx",
    "websockets",
]
