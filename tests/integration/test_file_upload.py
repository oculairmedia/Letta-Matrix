"""
Integration tests for Matrix file upload to Letta
"""

import pytest
import asyncio
import json
import os
import tempfile
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from aioresponses import aioresponses

from src.matrix.file_handler import (
    LettaFileHandler,
    FileMetadata,
    FileUploadError,
    SUPPORTED_FILE_TYPES,
    MAX_FILE_SIZE
)


@pytest.fixture
def file_handler():
    """Create a file handler instance for testing"""
    return LettaFileHandler(
        homeserver_url="http://test-matrix.local",
        letta_api_url="http://test-letta.local",
        letta_token="test-token",
        max_retries=1,  # Reduce retries for faster tests
        retry_delay=0.1
    )


@pytest.fixture
def sample_file_metadata():
    """Sample file metadata for testing"""
    return FileMetadata(
        file_url="mxc://matrix.org/abc123",
        file_name="test_document.pdf",
        file_type="application/pdf",
        file_size=1024 * 100,  # 100KB
        room_id="!test:matrix.org",
        sender="@user:matrix.org",
        timestamp=1234567890,
        event_id="$event123"
    )


@pytest.fixture
def mock_event():
    """Create a mock Matrix file event"""
    event = Mock()
    event.event_id = "$test123"
    event.sender = "@user:matrix.org"
    event.server_timestamp = 1234567890
    event.source = {
        "content": {
            "msgtype": "m.file",
            "url": "mxc://matrix.org/abc123",
            "body": "test_document.pdf",
            "info": {
                "mimetype": "application/pdf",
                "size": 102400
            }
        }
    }
    return event


class TestFileHandler:
    """Test suite for LettaFileHandler"""
    
    def test_extract_file_metadata(self, file_handler, mock_event):
        """Test extracting metadata from Matrix event"""
        metadata = file_handler._extract_file_metadata(mock_event, "!test:matrix.org")
        
        assert metadata is not None
        assert metadata.file_name == "test_document.pdf"
        assert metadata.file_type == "application/pdf"
        assert metadata.file_size == 102400
        assert metadata.room_id == "!test:matrix.org"
    
    def test_extract_file_metadata_non_file_event(self, file_handler):
        """Test that non-file events return None"""
        event = Mock()
        event.source = {
            "content": {
                "msgtype": "m.text",
                "body": "Hello world"
            }
        }
        
        metadata = file_handler._extract_file_metadata(event, "!test:matrix.org")
        assert metadata is None
    
    def test_validate_file_supported_type(self, file_handler, sample_file_metadata):
        """Test file validation for supported types"""
        result = file_handler._validate_file(sample_file_metadata)
        assert result is None  # None means valid
    
    def test_validate_file_unsupported_type(self, file_handler, sample_file_metadata):
        """Test file validation rejects unsupported types"""
        sample_file_metadata.file_type = "application/zip"
        result = file_handler._validate_file(sample_file_metadata)
        assert result is not None
        assert "not supported" in result
        assert "application/zip" in result
    
    def test_validate_file_too_large(self, file_handler, sample_file_metadata):
        """Test file validation rejects files that are too large"""
        sample_file_metadata.file_size = 100 * 1024 * 1024  # 100MB
        result = file_handler._validate_file(sample_file_metadata)
        assert result is not None
        assert "too large" in result
    
    def test_validate_file_all_supported_types(self, file_handler):
        """Test all supported file types pass validation"""
        for mime_type in SUPPORTED_FILE_TYPES.keys():
            metadata = FileMetadata(
                file_url="mxc://test/123",
                file_name="test.file",
                file_type=mime_type,
                file_size=1024,
                room_id="!test:matrix.org",
                sender="@user:matrix.org",
                timestamp=123456,
                event_id="$event"
            )
            assert file_handler._validate_file(metadata) is None
    
    @pytest.mark.asyncio
    async def test_download_matrix_file(self, file_handler, sample_file_metadata):
        """Test downloading file from Matrix media repository"""
        with aioresponses() as mocked:
            download_url = "http://test-matrix.local/_matrix/media/v3/download/matrix.org/abc123"
            mocked.get(download_url, body=b"test pdf content")
            
            file_path = await file_handler._download_matrix_file(sample_file_metadata)
            
            try:
                assert os.path.exists(file_path)
                assert file_path.endswith('.pdf')
                with open(file_path, 'rb') as f:
                    assert f.read() == b"test pdf content"
            finally:
                # Clean up
                if os.path.exists(file_path):
                    os.unlink(file_path)
    
    @pytest.mark.asyncio
    async def test_download_matrix_file_error(self, file_handler, sample_file_metadata):
        """Test download error handling"""
        with aioresponses() as mocked:
            download_url = "http://test-matrix.local/_matrix/media/v3/download/matrix.org/abc123"
            mocked.get(download_url, status=404, body="Not found")
            
            with pytest.raises(FileUploadError) as exc_info:
                await file_handler._download_matrix_file(sample_file_metadata)
            
            assert "Failed to download file" in str(exc_info.value)
    
    @pytest.mark.asyncio
    async def test_download_matrix_file_invalid_url(self, file_handler, sample_file_metadata):
        """Test invalid mxc URL handling"""
        sample_file_metadata.file_url = "http://invalid/url"
        
        with pytest.raises(FileUploadError) as exc_info:
            await file_handler._download_matrix_file(sample_file_metadata)
        
        assert "Invalid mxc://" in str(exc_info.value)
    
    @pytest.mark.asyncio
    async def test_get_or_create_folder_existing(self, file_handler):
        """Test getting existing folder from cache"""
        room_id = "!test:matrix.org"
        expected_folder_id = "source-123"
        
        # Pre-populate cache
        file_handler._folder_cache[room_id] = expected_folder_id
        
        folder_id = await file_handler._get_or_create_folder(room_id)
        
        assert folder_id == expected_folder_id
    
    @pytest.mark.asyncio
    async def test_get_or_create_folder_new(self, file_handler):
        """Test creating new folder"""
        room_id = "!newroom:matrix.org"
        
        with aioresponses() as mocked:
            # Mock list sources - empty
            mocked.get(
                "http://test-letta.local/v1/sources/",
                payload=[]
            )
            
            # Mock create source
            mocked.post(
                "http://test-letta.local/v1/sources/",
                payload={"id": "source-new-123", "name": "matrix-newroom-matrix.org"}
            )
            
            folder_id = await file_handler._get_or_create_folder(room_id)
            
            assert folder_id == "source-new-123"
            assert file_handler._folder_cache[room_id] == "source-new-123"
    
    @pytest.mark.asyncio
    async def test_get_or_create_folder_with_agent(self, file_handler):
        """Test creating folder and attaching to agent"""
        room_id = "!agentroom:matrix.org"
        agent_id = "agent-456"
        
        with aioresponses() as mocked:
            # Mock list sources - empty
            mocked.get(
                "http://test-letta.local/v1/sources/",
                payload=[]
            )
            
            # Mock create source
            mocked.post(
                "http://test-letta.local/v1/sources/",
                payload={"id": "source-agent-123"}
            )
            
            # Mock attach to agent
            mocked.post(
                "http://test-letta.local/v1/agents/agent-456/sources/source-agent-123",
                status=200
            )
            
            folder_id = await file_handler._get_or_create_folder(room_id, agent_id)
            
            assert folder_id == "source-agent-123"
    
    @pytest.mark.asyncio
    async def test_poll_job_completion_success(self, file_handler):
        """Test polling for successful job completion"""
        job_id = "job-123"
        
        with aioresponses() as mocked:
            mocked.get(
                f"http://test-letta.local/v1/jobs/{job_id}",
                payload={"status": "completed"}
            )
            
            success = await file_handler._poll_job_completion(job_id, timeout=5, interval=0.1)
            
            assert success is True
    
    @pytest.mark.asyncio
    async def test_poll_job_completion_failure(self, file_handler):
        """Test polling for failed job"""
        job_id = "job-123"
        
        with aioresponses() as mocked:
            mocked.get(
                f"http://test-letta.local/v1/jobs/{job_id}",
                payload={"status": "failed", "error": "Processing error"}
            )
            
            success = await file_handler._poll_job_completion(job_id, timeout=5, interval=0.1)
            
            assert success is False
    
    @pytest.mark.asyncio
    async def test_poll_job_completion_sync(self, file_handler):
        """Test sync-complete job returns immediately"""
        success = await file_handler._poll_job_completion("sync-complete")
        assert success is True
    
    @pytest.mark.asyncio
    async def test_poll_job_completion_not_found(self, file_handler):
        """Test job not found assumes success"""
        job_id = "job-missing"
        
        with aioresponses() as mocked:
            mocked.get(
                f"http://test-letta.local/v1/jobs/{job_id}",
                status=404
            )
            
            success = await file_handler._poll_job_completion(job_id, timeout=5, interval=0.1)
            
            assert success is True  # 404 assumes completed
    
    @pytest.mark.asyncio
    async def test_upload_to_letta(self, file_handler, sample_file_metadata):
        """Test uploading file to Letta"""
        folder_id = "source-123"
        
        # Create a temporary test file
        with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as f:
            f.write(b"test pdf content")
            temp_path = f.name
        
        try:
            with aioresponses() as mocked:
                mocked.post(
                    f"http://test-letta.local/v1/sources/{folder_id}/upload",
                    payload={"job_id": "job-upload-123"}
                )
                
                job_id = await file_handler._upload_to_letta(temp_path, folder_id, sample_file_metadata)
                
                assert job_id == "job-upload-123"
        finally:
            os.unlink(temp_path)
    
    @pytest.mark.asyncio
    async def test_handle_file_event_end_to_end(self, file_handler, mock_event):
        """Test complete file upload flow"""
        room_id = "!test:matrix.org"
        agent_id = "agent-123"
        
        # Mock all the necessary API calls
        with patch.object(file_handler, '_download_matrix_file', return_value='/tmp/test.pdf') as mock_download, \
             patch.object(file_handler, '_get_or_create_folder', return_value='source-123') as mock_folder, \
             patch.object(file_handler, '_upload_to_letta', return_value='job-123') as mock_upload, \
             patch.object(file_handler, '_poll_job_completion', return_value=True) as mock_poll, \
             patch.object(file_handler, '_notify', new_callable=AsyncMock) as mock_notify, \
             patch('os.path.exists', return_value=True), \
             patch('os.unlink'):
            
            success = await file_handler.handle_file_event(mock_event, room_id, agent_id)
            
            assert success is True
            mock_download.assert_called_once()
            mock_folder.assert_called_once_with(room_id, agent_id)
            mock_upload.assert_called_once()
            mock_poll.assert_called_once()
            # Should have notified start and success
            assert mock_notify.call_count >= 2
    
    @pytest.mark.asyncio
    async def test_handle_file_event_rejected_file(self, file_handler):
        """Test file rejection sends notification"""
        room_id = "!test:matrix.org"
        
        # Create event with unsupported file type
        event = Mock()
        event.event_id = "$test123"
        event.sender = "@user:matrix.org"
        event.server_timestamp = 1234567890
        event.source = {
            "content": {
                "msgtype": "m.file",
                "url": "mxc://matrix.org/abc123",
                "body": "archive.zip",
                "info": {
                    "mimetype": "application/zip",
                    "size": 1024
                }
            }
        }
        
        with patch.object(file_handler, '_notify', new_callable=AsyncMock) as mock_notify:
            success = await file_handler.handle_file_event(event, room_id)
            
            assert success is False
            mock_notify.assert_called_once()
            # Check notification contains rejection message
            call_args = mock_notify.call_args[0]
            assert "not supported" in call_args[1]
    
    @pytest.mark.asyncio
    async def test_retry_logic(self, file_handler):
        """Test retry logic with exponential backoff"""
        call_count = 0
        
        async def failing_then_success():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise Exception("Temporary failure")
            return "success"
        
        # Set up handler with 3 retries
        file_handler.max_retries = 3
        file_handler.retry_delay = 0.01
        
        result = await file_handler._retry_async(failing_then_success, "test operation")
        
        assert result == "success"
        assert call_count == 2


class TestFileMetadata:
    """Test FileMetadata dataclass"""
    
    def test_file_metadata_creation(self):
        """Test creating FileMetadata instance"""
        metadata = FileMetadata(
            file_url="mxc://test",
            file_name="test.pdf",
            file_type="application/pdf",
            file_size=1024,
            room_id="!room:test",
            sender="@user:test",
            timestamp=123456,
            event_id="$event"
        )
        
        assert metadata.file_name == "test.pdf"
        assert metadata.file_type == "application/pdf"
        assert metadata.file_size == 1024
    
    def test_file_metadata_equality(self):
        """Test FileMetadata equality"""
        metadata1 = FileMetadata(
            file_url="mxc://test",
            file_name="test.pdf",
            file_type="application/pdf",
            file_size=1024,
            room_id="!room:test",
            sender="@user:test",
            timestamp=123456,
            event_id="$event"
        )
        metadata2 = FileMetadata(
            file_url="mxc://test",
            file_name="test.pdf",
            file_type="application/pdf",
            file_size=1024,
            room_id="!room:test",
            sender="@user:test",
            timestamp=123456,
            event_id="$event"
        )
        
        assert metadata1 == metadata2


class TestConstants:
    """Test module constants"""
    
    def test_supported_file_types(self):
        """Test supported file types are correct"""
        assert 'application/pdf' in SUPPORTED_FILE_TYPES
        assert 'text/plain' in SUPPORTED_FILE_TYPES
        assert 'text/markdown' in SUPPORTED_FILE_TYPES
        assert 'application/json' in SUPPORTED_FILE_TYPES
    
    def test_max_file_size(self):
        """Test max file size is 50MB"""
        assert MAX_FILE_SIZE == 50 * 1024 * 1024


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
