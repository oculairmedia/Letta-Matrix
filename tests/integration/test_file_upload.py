"""
Integration tests for Matrix file upload to Letta
"""

import pytest
import asyncio
import json
import os
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from src.matrix.file_handler import LettaFileHandler, FileMetadata, FileUploadError


@pytest.fixture
def file_handler():
    """Create a file handler instance for testing"""
    return LettaFileHandler(
        homeserver_url="http://test-matrix.local",
        letta_api_url="http://test-letta.local",
        letta_token="test-token"
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
    
    def test_validate_file_supported_type(self, file_handler, sample_file_metadata):
        """Test file validation for supported types"""
        assert file_handler._validate_file(sample_file_metadata) is True
    
    def test_validate_file_unsupported_type(self, file_handler, sample_file_metadata):
        """Test file validation rejects unsupported types"""
        sample_file_metadata.file_type = "application/zip"
        assert file_handler._validate_file(sample_file_metadata) is False
    
    def test_validate_file_too_large(self, file_handler, sample_file_metadata):
        """Test file validation rejects files that are too large"""
        sample_file_metadata.file_size = 100 * 1024 * 1024  # 100MB
        assert file_handler._validate_file(sample_file_metadata) is False
    
    @pytest.mark.asyncio
    async def test_download_matrix_file(self, file_handler, sample_file_metadata):
        """Test downloading file from Matrix media repository"""
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.content.iter_chunked = AsyncMock(return_value=[b"test content"])
        
        with patch('aiohttp.ClientSession') as mock_session:
            mock_session.return_value.__aenter__.return_value.get.return_value.__aenter__.return_value = mock_response
            
            file_path = await file_handler._download_matrix_file(sample_file_metadata)
            
            assert os.path.exists(file_path)
            assert file_path.endswith('.pdf')
            
            # Clean up
            os.unlink(file_path)
    
    @pytest.mark.asyncio
    async def test_get_or_create_folder_existing(self, file_handler):
        """Test getting existing folder from cache"""
        room_id = "!test:matrix.org"
        expected_folder_id = "folder-123"
        
        # Pre-populate cache
        file_handler._folder_cache[room_id] = expected_folder_id
        
        folder_id = await file_handler._get_or_create_folder(room_id)
        
        assert folder_id == expected_folder_id
    
    @pytest.mark.asyncio
    async def test_get_or_create_folder_new(self, file_handler):
        """Test creating new folder"""
        room_id = "!test:matrix.org"
        
        # Mock API responses
        mock_list_response = AsyncMock()
        mock_list_response.status = 200
        mock_list_response.json = AsyncMock(return_value=[])
        
        mock_create_response = AsyncMock()
        mock_create_response.status = 201
        mock_create_response.json = AsyncMock(return_value={"id": "folder-new-123"})
        
        with patch('aiohttp.ClientSession') as mock_session:
            session = mock_session.return_value.__aenter__.return_value
            session.get.return_value.__aenter__.return_value = mock_list_response
            session.post.return_value.__aenter__.return_value = mock_create_response
            
            folder_id = await file_handler._get_or_create_folder(room_id)
            
            assert folder_id == "folder-new-123"
            assert file_handler._folder_cache[room_id] == "folder-new-123"
    
    @pytest.mark.asyncio
    async def test_poll_job_completion_success(self, file_handler):
        """Test polling for successful job completion"""
        job_id = "job-123"
        
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"status": "completed"})
        
        with patch('aiohttp.ClientSession') as mock_session:
            session = mock_session.return_value.__aenter__.return_value
            session.get.return_value.__aenter__.return_value = mock_response
            
            success = await file_handler._poll_job_completion(job_id, timeout=5, interval=1)
            
            assert success is True
    
    @pytest.mark.asyncio
    async def test_poll_job_completion_failure(self, file_handler):
        """Test polling for failed job"""
        job_id = "job-123"
        
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"status": "failed"})
        
        with patch('aiohttp.ClientSession') as mock_session:
            session = mock_session.return_value.__aenter__.return_value
            session.get.return_value.__aenter__.return_value = mock_response
            
            success = await file_handler._poll_job_completion(job_id, timeout=5, interval=1)
            
            assert success is False
    
    @pytest.mark.asyncio
    async def test_handle_file_event_end_to_end(self, file_handler, mock_event):
        """Test complete file upload flow"""
        room_id = "!test:matrix.org"
        agent_id = "agent-123"
        
        # Mock all the necessary API calls
        with patch.object(file_handler, '_download_matrix_file', return_value='/tmp/test.pdf') as mock_download, \
             patch.object(file_handler, '_get_or_create_folder', return_value='folder-123') as mock_folder, \
             patch.object(file_handler, '_upload_to_letta', return_value='job-123') as mock_upload, \
             patch.object(file_handler, '_poll_job_completion', return_value=True) as mock_poll, \
             patch('os.path.exists', return_value=True), \
             patch('os.unlink'):
            
            success = await file_handler.handle_file_event(mock_event, room_id, agent_id)
            
            assert success is True
            mock_download.assert_called_once()
            mock_folder.assert_called_once_with(room_id, agent_id)
            mock_upload.assert_called_once()
            mock_poll.assert_called_once()


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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
