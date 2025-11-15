"""
Unit tests for event_dedupe_store.py

Tests cover:
- Event deduplication detection
- TTL-based cleanup
- Multi-process safety
- Database persistence
- Edge cases
"""
import pytest
import os
import time
import tempfile
import shutil
from pathlib import Path
from unittest.mock import Mock, patch

# Import the module to test
import event_dedupe_store


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def temp_db_dir(tmp_path):
    """Create a temporary directory for test databases"""
    db_dir = tmp_path / "test_dedupe_db"
    db_dir.mkdir()
    yield db_dir
    # Cleanup after test
    if db_dir.exists():
        shutil.rmtree(db_dir)


@pytest.fixture
def temp_db_path(temp_db_dir):
    """Create a temporary database path"""
    db_path = temp_db_dir / "test_events.db"
    return str(db_path)


@pytest.fixture
def mock_logger():
    """Create a mock logger"""
    return Mock()


# ============================================================================
# Event Deduplication Tests
# ============================================================================

@pytest.mark.unit
class TestEventDeduplication:
    """Test event deduplication functionality"""

    def test_is_duplicate_event_returns_false_for_new_events(self, temp_db_path, mock_logger):
        """Test that new events are not marked as duplicates"""
        with patch.dict(os.environ, {"MATRIX_EVENT_DEDUPE_DB": temp_db_path}):
            # Reload module to pick up new DB path
            import importlib
            importlib.reload(event_dedupe_store)

            event_id = "$new_event_123"
            is_duplicate = event_dedupe_store.is_duplicate_event(event_id, mock_logger)

            assert is_duplicate is False
            assert mock_logger.debug.called
            # Should log that event was recorded
            call_args = mock_logger.debug.call_args
            assert "Recorded" in call_args[0][0] or "recorded" in call_args[0][0].lower()

    def test_is_duplicate_event_returns_true_for_seen_events(self, temp_db_path, mock_logger):
        """Test that previously seen events are marked as duplicates"""
        with patch.dict(os.environ, {"MATRIX_EVENT_DEDUPE_DB": temp_db_path}):
            import importlib
            importlib.reload(event_dedupe_store)

            event_id = "$duplicate_event_456"

            # First call - should be new
            is_duplicate_first = event_dedupe_store.is_duplicate_event(event_id, mock_logger)
            assert is_duplicate_first is False

            # Second call - should be duplicate
            is_duplicate_second = event_dedupe_store.is_duplicate_event(event_id, mock_logger)
            assert is_duplicate_second is True

            # Verify logger was called for duplicate
            duplicate_calls = [call for call in mock_logger.debug.call_args_list
                              if "Duplicate" in str(call) or "duplicate" in str(call).lower()]
            assert len(duplicate_calls) > 0

    def test_is_duplicate_event_handles_none_event_id(self, temp_db_path):
        """Test that None event_id returns False without error"""
        with patch.dict(os.environ, {"MATRIX_EVENT_DEDUPE_DB": temp_db_path}):
            import importlib
            importlib.reload(event_dedupe_store)

            is_duplicate = event_dedupe_store.is_duplicate_event(None)
            assert is_duplicate is False

    def test_is_duplicate_event_handles_empty_event_id(self, temp_db_path):
        """Test that empty event_id returns False without error"""
        with patch.dict(os.environ, {"MATRIX_EVENT_DEDUPE_DB": temp_db_path}):
            import importlib
            importlib.reload(event_dedupe_store)

            is_duplicate = event_dedupe_store.is_duplicate_event("")
            assert is_duplicate is False


# ============================================================================
# TTL and Cleanup Tests
# ============================================================================

@pytest.mark.unit
class TestTTLCleanup:
    """Test TTL-based event cleanup"""

    def test_clean_old_events_removes_expired(self, temp_db_path, mock_logger):
        """Test that events older than TTL are removed"""
        # Set a very short TTL (1 second)
        with patch.dict(os.environ, {
            "MATRIX_EVENT_DEDUPE_DB": temp_db_path,
            "MATRIX_EVENT_DEDUPE_TTL": "1"
        }):
            import importlib
            importlib.reload(event_dedupe_store)

            old_event_id = "$old_event_789"

            # Add event
            event_dedupe_store.is_duplicate_event(old_event_id, mock_logger)

            # Wait for TTL to expire
            time.sleep(1.5)

            # Add a new event (this triggers cleanup of old events)
            new_event_id = "$new_event_after_cleanup"
            event_dedupe_store.is_duplicate_event(new_event_id, mock_logger)

            # Old event should no longer be a duplicate (it was cleaned up)
            is_duplicate = event_dedupe_store.is_duplicate_event(old_event_id, mock_logger)
            assert is_duplicate is False

    def test_clean_old_events_preserves_recent(self, temp_db_path, mock_logger):
        """Test that recent events are preserved during cleanup"""
        with patch.dict(os.environ, {
            "MATRIX_EVENT_DEDUPE_DB": temp_db_path,
            "MATRIX_EVENT_DEDUPE_TTL": "3600"  # 1 hour TTL
        }):
            import importlib
            importlib.reload(event_dedupe_store)

            recent_event_id = "$recent_event_101"

            # Add recent event
            event_dedupe_store.is_duplicate_event(recent_event_id, mock_logger)

            # Immediately check again (should still be duplicate)
            is_duplicate = event_dedupe_store.is_duplicate_event(recent_event_id, mock_logger)
            assert is_duplicate is True


# ============================================================================
# Database Persistence Tests
# ============================================================================

@pytest.mark.unit
class TestDatabasePersistence:
    """Test that events persist across module reloads/restarts"""

    def test_event_store_persistence_across_restarts(self, temp_db_path, mock_logger):
        """Test that events persist when the module is reloaded"""
        event_id = "$persistent_event_202"

        # First session - add event
        with patch.dict(os.environ, {"MATRIX_EVENT_DEDUPE_DB": temp_db_path}):
            import importlib
            importlib.reload(event_dedupe_store)

            is_duplicate_first = event_dedupe_store.is_duplicate_event(event_id, mock_logger)
            assert is_duplicate_first is False

        # Simulate restart by reloading module
        with patch.dict(os.environ, {"MATRIX_EVENT_DEDUPE_DB": temp_db_path}):
            import importlib
            importlib.reload(event_dedupe_store)

            # Event should still be marked as duplicate
            is_duplicate_after_restart = event_dedupe_store.is_duplicate_event(event_id, mock_logger)
            assert is_duplicate_after_restart is True

    def test_database_creates_table_on_first_use(self, temp_db_path):
        """Test that database table is created on first connection"""
        with patch.dict(os.environ, {"MATRIX_EVENT_DEDUPE_DB": temp_db_path}):
            import importlib
            importlib.reload(event_dedupe_store)

            # Database file shouldn't exist yet
            assert not os.path.exists(temp_db_path)

            # First call should create database
            event_dedupe_store.is_duplicate_event("$first_event")

            # Database should now exist
            assert os.path.exists(temp_db_path)


# ============================================================================
# Multi-Process Safety Tests
# ============================================================================

@pytest.mark.unit
class TestMultiProcessSafety:
    """Test thread/process safety of deduplication"""

    def test_concurrent_duplicate_detection(self, temp_db_path, mock_logger):
        """Test that concurrent checks for same event work correctly"""
        with patch.dict(os.environ, {"MATRIX_EVENT_DEDUPE_DB": temp_db_path}):
            import importlib
            import threading
            importlib.reload(event_dedupe_store)

            event_id = "$concurrent_event_303"
            results = []

            def check_duplicate():
                is_dup = event_dedupe_store.is_duplicate_event(event_id, mock_logger)
                results.append(is_dup)

            # Simulate concurrent checks
            threads = [threading.Thread(target=check_duplicate) for _ in range(5)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            # Only one thread should see it as new (False), others as duplicate (True)
            false_count = results.count(False)
            true_count = results.count(True)

            assert false_count == 1, f"Expected exactly 1 False, got {false_count}"
            assert true_count == 4, f"Expected exactly 4 True, got {true_count}"


# ============================================================================
# Directory Creation Tests
# ============================================================================

@pytest.mark.unit
class TestDirectoryCreation:
    """Test automatic directory creation"""

    def test_ensure_directory_creates_missing_dirs(self, tmp_path):
        """Test that missing directories are created automatically"""
        nested_path = tmp_path / "deeply" / "nested" / "path" / "events.db"

        with patch.dict(os.environ, {"MATRIX_EVENT_DEDUPE_DB": str(nested_path)}):
            import importlib
            importlib.reload(event_dedupe_store)

            # Directory shouldn't exist yet
            assert not nested_path.parent.exists()

            # First call should create all directories
            event_dedupe_store.is_duplicate_event("$test_event")

            # Directory should now exist
            assert nested_path.parent.exists()

    def test_ensure_directory_handles_existing_dirs(self, temp_db_path):
        """Test that existing directories don't cause errors"""
        # Pre-create the directory
        os.makedirs(os.path.dirname(temp_db_path), exist_ok=True)

        with patch.dict(os.environ, {"MATRIX_EVENT_DEDUPE_DB": temp_db_path}):
            import importlib
            importlib.reload(event_dedupe_store)

            # Should work without error
            is_duplicate = event_dedupe_store.is_duplicate_event("$test_event")
            assert is_duplicate is False


# ============================================================================
# Logger Tests
# ============================================================================

@pytest.mark.unit
class TestLogging:
    """Test logging behavior"""

    def test_logging_without_logger(self, temp_db_path):
        """Test that function works without a logger provided"""
        with patch.dict(os.environ, {"MATRIX_EVENT_DEDUPE_DB": temp_db_path}):
            import importlib
            importlib.reload(event_dedupe_store)

            # Should work without error when logger is None
            is_duplicate = event_dedupe_store.is_duplicate_event("$test_event", logger=None)
            assert is_duplicate is False

    def test_logging_records_event_details(self, temp_db_path, mock_logger):
        """Test that logger receives event_id in extra data"""
        with patch.dict(os.environ, {"MATRIX_EVENT_DEDUPE_DB": temp_db_path}):
            import importlib
            importlib.reload(event_dedupe_store)

            event_id = "$logged_event_404"
            event_dedupe_store.is_duplicate_event(event_id, mock_logger)

            # Check that logger.debug was called with extra event_id
            assert mock_logger.debug.called
            call_kwargs = mock_logger.debug.call_args[1]
            assert "extra" in call_kwargs
            assert call_kwargs["extra"]["event_id"] == event_id
