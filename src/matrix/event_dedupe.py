import logging
import os
import sqlite3
import threading
import time
from typing import Optional

DB_PATH = os.getenv("MATRIX_EVENT_DEDUPE_DB", "/app/data/matrix_event_dedupe.db")
TTL_SECONDS = int(os.getenv("MATRIX_EVENT_DEDUPE_TTL", "3600"))

_lock = threading.Lock()

def _ensure_directory() -> None:
    directory = os.path.dirname(DB_PATH)
    if directory and not os.path.exists(directory):
        os.makedirs(directory, exist_ok=True)

def _get_connection() -> sqlite3.Connection:
    _ensure_directory()
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS processed_events (event_id TEXT PRIMARY KEY, processed_at INTEGER NOT NULL)"
    )
    return conn

def is_duplicate_event(event_id: Optional[str], logger: Optional[logging.Logger] = None) -> bool:
    """Return True if the event_id was recently processed, False otherwise.

    This implementation is multi-process safe by using an atomic INSERT operation.
    The PRIMARY KEY constraint ensures only one process can successfully insert a given event_id.
    """
    if not event_id:
        return False

    now = int(time.time())
    cutoff = now - TTL_SECONDS

    with _lock:
        conn = _get_connection()
        try:
            # Clean up old events
            conn.execute("DELETE FROM processed_events WHERE processed_at < ?", (cutoff,))

            # Atomic insert: Try to insert the event. If it already exists (PRIMARY KEY violation),
            # the INSERT will fail and we know it's a duplicate.
            # Using INSERT OR IGNORE makes it safe - if the key exists, nothing happens.
            cursor = conn.execute(
                "INSERT OR IGNORE INTO processed_events (event_id, processed_at) VALUES (?, ?)",
                (event_id, now),
            )
            conn.commit()

            # Check if the insert actually happened
            # rowcount = 1 means insert succeeded (new event)
            # rowcount = 0 means insert was ignored (duplicate event)
            is_duplicate = (cursor.rowcount == 0)

            if is_duplicate:
                if logger:
                    logger.debug("Duplicate Matrix event detected", extra={"event_id": event_id})
                return True
            else:
                if logger:
                    logger.debug("Recorded Matrix event", extra={"event_id": event_id})
                return False
        finally:
            conn.close()
