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
    with patch('src.matrix.file_handler.Letta') as mock_letta:
        # Mock the Letta client
        mock_client = MagicMock()
        mock_letta.return_value = mock_client
        
        handler = LettaFileHandler(
            homeserver_url="http://test-matrix.local",
            letta_api_url="http://test-letta.local",
            letta_token="test-token",
            max_retries=1,  # Reduce retries for faster tests
            retry_delay=0.1
        )
        return handler


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
        assert metadata.sender == "@user:matrix.org"
    
    def test_extract_file_metadata_non_file_event(self, file_handler):
        """Test that non-file events return None"""
        event = Mock()
        event.source = {
            "content": {
                "msgtype": "m.text",
                "body": "Hello"
            }
        }
        
        metadata = file_handler._extract_file_metadata(event, "!test:matrix.org")
        assert metadata is None
    
    def test_validate_file_supported_type(self, file_handler, sample_file_metadata):
        """Test validation of supported file type"""
        error = file_handler._validate_file(sample_file_metadata)
        assert error is None
    
    def test_validate_file_unsupported_type(self, file_handler, sample_file_metadata):
        """Test validation of unsupported file type"""
        sample_file_metadata.file_type = "application/x-unknown"
        error = file_handler._validate_file(sample_file_metadata)
        assert error is not None
        assert "not supported" in error
    
    def test_validate_file_too_large(self, file_handler, sample_file_metadata):
        """Test validation of file that's too large"""
        sample_file_metadata.file_size = MAX_FILE_SIZE + 1
        error = file_handler._validate_file(sample_file_metadata)
        assert error is not None
        assert "too large" in error
    
    def test_validate_file_all_supported_types(self, file_handler, sample_file_metadata):
        """Test all supported file types pass validation"""
        for mime_type in SUPPORTED_FILE_TYPES.keys():
            sample_file_metadata.file_type = mime_type
            sample_file_metadata.file_size = 1024  # Reset to valid size
            error = file_handler._validate_file(sample_file_metadata)
            assert error is None, f"Failed for {mime_type}"
    
    @pytest.mark.asyncio
    async def test_download_matrix_file(self, file_handler, sample_file_metadata):
        """Test downloading file from Matrix"""
        with aioresponses() as mocked:
            mocked.get(
                "http://test-matrix.local/_matrix/client/v1/media/download/matrix.org/abc123",
                body=b"test file content"
            )
            
            file_path = await file_handler._download_matrix_file(sample_file_metadata)
            
            assert os.path.exists(file_path)
            with open(file_path, 'rb') as f:
                assert f.read() == b"test file content"
            
            # Cleanup
            os.unlink(file_path)
    
    @pytest.mark.asyncio
    async def test_download_matrix_file_error(self, file_handler, sample_file_metadata):
        """Test handling download errors"""
        with aioresponses() as mocked:
            mocked.get(
                "http://test-matrix.local/_matrix/client/v1/media/download/matrix.org/abc123",
                status=404
            )
            
            with pytest.raises(FileUploadError):
                await file_handler._download_matrix_file(sample_file_metadata)
    
    @pytest.mark.asyncio
    async def test_download_matrix_file_invalid_url(self, file_handler, sample_file_metadata):
        """Test handling invalid mxc URL"""
        sample_file_metadata.file_url = "http://invalid.url"
        
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
        """Test creating new folder using SDK"""
        room_id = "!newroom:matrix.org"
        
        # Mock SDK v1.x folders methods
        file_handler.letta_client.folders.list.return_value = []  # No existing folders
        
        mock_folder = MagicMock()
        mock_folder.id = "source-new-123"
        mock_folder.name = "matrix_files_newroom"
        file_handler.letta_client.folders.create.return_value = mock_folder
        
        folder_id = await file_handler._get_or_create_folder(room_id)
        
        assert folder_id == "source-new-123"
        assert file_handler._folder_cache[room_id] == "source-new-123"
    
    @pytest.mark.asyncio
    async def test_get_or_create_folder_with_agent(self, file_handler):
        """Test creating folder and attaching to agent"""
        room_id = "!agentroom:matrix.org"
        agent_id = "agent-456"
        
        # Mock SDK v1.x folders methods
        file_handler.letta_client.folders.list.return_value = []  # No existing folders
        
        mock_agent = MagicMock()
        mock_agent.embedding_config = MagicMock()
        mock_agent.embedding_config.embedding_model = "text-embedding-3-small"
        mock_agent.embedding_config.embedding_endpoint_type = "openai"
        mock_agent.embedding_config.embedding_dim = 1536
        mock_agent.embedding_config.embedding_chunk_size = 300
        mock_agent.embedding_config.embedding_endpoint = None
        file_handler.letta_client.agents.retrieve.return_value = mock_agent
        
        mock_folder = MagicMock()
        mock_folder.id = "source-agent-123"
        mock_folder.name = "matrix_files_agentroom"
        file_handler.letta_client.folders.create.return_value = mock_folder
        
        file_handler.letta_client.agents.folders.list.return_value = []
        
        folder_id = await file_handler._get_or_create_folder(room_id, agent_id)
        
        assert folder_id == "source-agent-123"
    
    @pytest.mark.asyncio
    async def test_poll_file_status_success(self, file_handler):
        """Test polling for successful file processing"""
        folder_id = "source-123"
        file_id = "file-123"
        
        # Mock SDK v1.x folders.files.list to return completed file
        mock_file = MagicMock()
        mock_file.id = file_id
        mock_file.processing_status = "completed"
        file_handler.letta_client.folders.files.list.return_value = [mock_file]
        
        success = await file_handler._poll_file_status(folder_id, file_id, timeout=5, interval=0.1)
        
        assert success is True
    
    @pytest.mark.asyncio
    async def test_poll_file_status_failure(self, file_handler):
        """Test polling for failed file processing"""
        folder_id = "source-123"
        file_id = "file-123"
        
        # Mock SDK v1.x folders.files.list to return failed file
        mock_file = MagicMock()
        mock_file.id = file_id
        mock_file.processing_status = "failed"
        mock_file.error_message = "Processing error"
        file_handler.letta_client.folders.files.list.return_value = [mock_file]
        
        success = await file_handler._poll_file_status(folder_id, file_id, timeout=5, interval=0.1)
        
        assert success is False
    
    @pytest.mark.asyncio
    async def test_poll_file_status_sync(self, file_handler):
        """Test that sync-complete is handled immediately"""
        source_id = "source-123"
        file_id = "sync-complete"
        
        success = await file_handler._poll_file_status(source_id, file_id)
        
        assert success is True
    
    @pytest.mark.asyncio
    async def test_poll_file_status_not_found(self, file_handler):
        """Test polling when file is not found"""
        folder_id = "source-123"
        file_id = "file-123"
        
        # Mock SDK v1.x folders.files.list to return empty (file not found)
        file_handler.letta_client.folders.files.list.return_value = []
        
        success = await file_handler._poll_file_status(folder_id, file_id, timeout=1, interval=0.1)
        
        assert success is False
    
    @pytest.mark.asyncio
    async def test_upload_to_letta(self, file_handler, sample_file_metadata):
        """Test uploading file to Letta using SDK v1.x"""
        folder_id = "source-123"
        
        # Create a temporary test file
        with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as f:
            f.write(b"test content")
            temp_path = f.name
        
        try:
            # Mock SDK v1.x folders.files.upload
            mock_result = MagicMock()
            mock_result.id = "file-456"
            file_handler.letta_client.folders.files.upload.return_value = mock_result
            
            file_id = await file_handler._upload_to_letta(temp_path, folder_id, sample_file_metadata)
            
            assert file_id == "file-456"
            
            # Verify upload was called with correct params
            file_handler.letta_client.folders.files.upload.assert_called_once()
            call_args = file_handler.letta_client.folders.files.upload.call_args
            assert call_args[0][0] == folder_id  # folder_id
            
        finally:
            os.unlink(temp_path)
    
    @pytest.mark.asyncio
    async def test_handle_file_event_end_to_end(self, file_handler, mock_event):
        """Test full file handling flow"""
        room_id = "!test:matrix.org"
        agent_id = "agent-789"
        
        # Mock SDK v1.x folders methods
        mock_folder = MagicMock()
        mock_folder.id = "source-existing"
        mock_folder.name = "matrix_files_test"
        file_handler.letta_client.folders.list.return_value = [mock_folder]
        file_handler.letta_client.agents.folders.list.return_value = []
        
        mock_upload = MagicMock()
        mock_upload.id = "file-new"
        file_handler.letta_client.folders.files.upload.return_value = mock_upload
        
        mock_file = MagicMock()
        mock_file.id = "file-new"
        mock_file.processing_status = "completed"
        file_handler.letta_client.folders.files.list.return_value = [mock_file]
        
        with aioresponses() as mocked:
            # Mock Matrix file download
            mocked.get(
                "http://test-matrix.local/_matrix/client/v1/media/download/matrix.org/abc123",
                body=b"test pdf content"
            )
            
            result = await file_handler.handle_file_event(mock_event, room_id, agent_id)
            
            assert result is True
    
    @pytest.mark.asyncio
    async def test_handle_file_event_rejected_file(self, file_handler, mock_event):
        """Test that unsupported files are rejected"""
        room_id = "!test:matrix.org"
        
        # Modify event to have unsupported type
        mock_event.source["content"]["info"]["mimetype"] = "application/x-unknown"
        
        result = await file_handler.handle_file_event(mock_event, room_id)
        
        assert result is False
    
    @pytest.mark.asyncio
    async def test_retry_logic(self, file_handler):
        """Test that retry logic works correctly"""
        call_count = 0
        
        async def failing_func():
            nonlocal call_count
            call_count += 1
            raise Exception("Test error")
        
        with pytest.raises(Exception):
            await file_handler._retry_async(failing_func, "test operation")
        
        # Should have been called max_retries times (1 in test config)
        assert call_count == file_handler.max_retries
    
    @pytest.mark.asyncio
    async def test_attach_source_to_agent(self, file_handler):
        """Test attaching folder to agent"""
        folder_id = "source-123"
        agent_id = "agent-456"
        
        # Mock SDK v1.x - folder not yet attached
        file_handler.letta_client.agents.folders.list.return_value = []
        
        await file_handler._attach_source_to_agent(folder_id, agent_id)
        
        # Verify attach was called
        file_handler.letta_client.agents.folders.attach.assert_called_once_with(agent_id, folder_id)
    
    @pytest.mark.asyncio
    async def test_attach_source_to_agent_already_attached(self, file_handler):
        """Test that already attached folders don't get re-attached"""
        folder_id = "source-123"
        agent_id = "agent-456"
        
        # Mock SDK v1.x - folder already attached
        mock_folder = MagicMock()
        mock_folder.id = folder_id
        file_handler.letta_client.agents.folders.list.return_value = [mock_folder]
        
        await file_handler._attach_source_to_agent(folder_id, agent_id)
        
        # Verify attach was NOT called
        file_handler.letta_client.agents.folders.attach.assert_not_called()


class TestFileMetadata:
    """Test suite for FileMetadata dataclass"""
    
    def test_file_metadata_creation(self):
        """Test creating FileMetadata instance"""
        metadata = FileMetadata(
            file_url="mxc://test/123",
            file_name="test.pdf",
            file_type="application/pdf",
            file_size=1024,
            room_id="!test:matrix.org",
            sender="@user:matrix.org",
            timestamp=12345,
            event_id="$event123"
        )
        
        assert metadata.file_url == "mxc://test/123"
        assert metadata.file_name == "test.pdf"
        assert metadata.file_size == 1024
    
    def test_file_metadata_equality(self):
        """Test FileMetadata equality"""
        metadata1 = FileMetadata(
            file_url="mxc://test/123",
            file_name="test.pdf",
            file_type="application/pdf",
            file_size=1024,
            room_id="!test:matrix.org",
            sender="@user:matrix.org",
            timestamp=12345,
            event_id="$event123"
        )
        metadata2 = FileMetadata(
            file_url="mxc://test/123",
            file_name="test.pdf",
            file_type="application/pdf",
            file_size=1024,
            room_id="!test:matrix.org",
            sender="@user:matrix.org",
            timestamp=12345,
            event_id="$event123"
        )
        
        assert metadata1 == metadata2


class TestConstants:
    """Test suite for module constants"""
    
    def test_supported_file_types(self):
        """Test supported file types are defined"""
        assert 'application/pdf' in SUPPORTED_FILE_TYPES
        assert 'text/plain' in SUPPORTED_FILE_TYPES
        assert 'text/markdown' in SUPPORTED_FILE_TYPES
        assert 'application/json' in SUPPORTED_FILE_TYPES
    
    def test_max_file_size(self):
        """Test max file size is reasonable"""
        assert MAX_FILE_SIZE == 50 * 1024 * 1024  # 50MB
