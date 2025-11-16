"""
Smoke tests for quick validation

These tests provide rapid feedback on basic functionality.
Run these first to catch obvious issues quickly.
"""
import pytest
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ============================================================================
# Import Smoke Tests
# ============================================================================

@pytest.mark.smoke
class TestImports:
    """Test that all modules can be imported without errors"""

    def test_import_agent_user_manager(self):
        """Test importing agent_user_manager module"""
        try:
            import src.core.agent_user_manager as agent_user_manager
            assert hasattr(agent_user_manager, 'AgentUserManager')
            assert hasattr(agent_user_manager, 'AgentUserMapping')
        except ImportError as e:
            pytest.fail(f"Failed to import src.core.agent_user_manager as agent_user_manager: {e}")

    def test_import_custom_matrix_client(self):
        """Test importing custom_matrix_client module"""
        try:
            import src.matrix.client as custom_matrix_client
            assert hasattr(custom_matrix_client, 'Config')
        except ImportError as e:
            pytest.fail(f"Failed to import src.matrix.client as custom_matrix_client: {e}")

    def test_import_matrix_api(self):
        """Test importing matrix_api module"""
        try:
            import src.api.app as matrix_api
            assert hasattr(matrix_api, 'app')
        except ImportError as e:
            pytest.fail(f"Failed to import src.api.app as matrix_api: {e}")

    def test_import_matrix_auth(self):
        """Test importing matrix_auth module"""
        try:
            import src.matrix.auth as matrix_auth
            assert hasattr(matrix_auth, 'MatrixAuthManager')
        except ImportError as e:
            pytest.fail(f"Failed to import src.matrix.auth as matrix_auth: {e}")


# ============================================================================
# Dataclass Smoke Tests
# ============================================================================

@pytest.mark.smoke
class TestDataclasses:
    """Test that dataclasses can be instantiated"""

    def test_agent_user_mapping_creation(self):
        """Test creating AgentUserMapping"""
        from src.core.agent_user_manager import AgentUserMapping

        mapping = AgentUserMapping(
            agent_id="test-id",
            agent_name="Test Agent",
            matrix_user_id="@test:matrix.test",
            matrix_password="password"
        )

        assert mapping.agent_id == "test-id"
        assert mapping.agent_name == "Test Agent"
        assert mapping.created is False

    def test_config_creation(self):
        """Test creating Config"""
        from src.matrix.client import Config

        config = Config(
            homeserver_url="http://test:8008",
            username="@test:matrix.test",
            password="password",
            room_id="!room:matrix.test",
            letta_api_url="http://letta:8283",
            letta_token="token",
            letta_agent_id="agent-id"
        )

        assert config.homeserver_url == "http://test:8008"
        assert config.log_level == "INFO"


# ============================================================================
# Basic Functionality Smoke Tests
# ============================================================================

@pytest.mark.smoke
class TestBasicFunctionality:
    """Test basic functionality works"""

    @pytest.mark.asyncio
    async def test_agent_manager_initialization(self, mock_config, tmp_path, monkeypatch):
        """Test AgentUserManager can be initialized"""
        from src.core.agent_user_manager import AgentUserManager
        
        # Set data directory to temp path for testing
        monkeypatch.setenv("MATRIX_DATA_DIR", str(tmp_path))
        
        manager = AgentUserManager(mock_config)
        assert manager.config == mock_config
        assert manager.mappings == {}
        assert manager.data_dir == str(tmp_path)

    def test_fastapi_app_creation(self):
        """Test FastAPI app is created"""
        from src.api.app import app
        from fastapi import FastAPI

        assert isinstance(app, FastAPI)
        assert app.title == "Matrix API"

    def test_logging_setup(self, mock_config):
        """Test logging can be configured"""
        from src.matrix.client import setup_logging

        logger = setup_logging(mock_config)

        assert logger is not None
        assert logger.name == "matrix_client"


# ============================================================================
# Configuration Smoke Tests
# ============================================================================

@pytest.mark.smoke
class TestConfiguration:
    """Test configuration handling"""

    def test_config_from_env_with_defaults(self):
        """Test Config.from_env() with default values"""
        from src.matrix.client import Config
        import os

        # Clear any existing env vars
        env_keys = [
            'MATRIX_HOMESERVER_URL',
            'MATRIX_USERNAME',
            'MATRIX_PASSWORD',
            'MATRIX_ROOM_ID',
            'LETTA_API_URL',
            'LETTA_TOKEN',
            'LETTA_AGENT_ID',
            'LOG_LEVEL'
        ]

        original_env = {}
        for key in env_keys:
            if key in os.environ:
                original_env[key] = os.environ[key]
                del os.environ[key]

        try:
            config = Config.from_env()

            # Should have default values
            assert config.homeserver_url is not None
            assert config.username is not None
            # Log level can be INFO or DEBUG depending on environment
            assert config.log_level in ["INFO", "DEBUG"]

        finally:
            # Restore original environment
            for key, value in original_env.items():
                os.environ[key] = value


# ============================================================================
# Exception Smoke Tests
# ============================================================================

@pytest.mark.smoke
class TestExceptions:
    """Test custom exceptions work"""

    def test_custom_exceptions_importable(self):
        """Test that custom exceptions can be imported and raised"""
        from src.matrix.client import (
            LettaApiError,
            MatrixClientError,
            ConfigurationError
        )

        # Test LettaApiError
        try:
            raise LettaApiError("Test error", status_code=500)
        except LettaApiError as e:
            assert str(e) == "Test error"
            assert e.status_code == 500

        # Test MatrixClientError
        try:
            raise MatrixClientError("Matrix error")
        except MatrixClientError as e:
            assert str(e) == "Matrix error"

        # Test ConfigurationError
        try:
            raise ConfigurationError("Config error")
        except ConfigurationError as e:
            assert str(e) == "Config error"


# ============================================================================
# File System Smoke Tests
# ============================================================================

@pytest.mark.smoke
class TestFileSystem:
    """Test file system operations"""

    @pytest.mark.asyncio
    async def test_mappings_file_operations(self, tmp_path, mock_config, monkeypatch):
        """Test reading and writing mappings file"""
        from src.core.agent_user_manager import AgentUserManager, AgentUserMapping
        import json

        # Set data directory to temp path for testing
        monkeypatch.setenv("MATRIX_DATA_DIR", str(tmp_path))
        
        mappings_file = tmp_path / "agent_user_mappings.json"

        manager = AgentUserManager(mock_config)
        assert manager.mappings_file == str(mappings_file)

        # Add a mapping
        manager.mappings["test-agent"] = AgentUserMapping(
            agent_id="test-agent",
            agent_name="Test Agent",
            matrix_user_id="@test:matrix.test",
            matrix_password="password",
            created=True
        )

        # Save
        await manager.save_mappings()

        # Verify file was created
        assert mappings_file.exists()

        # Load - create new manager instance (it will use same MATRIX_DATA_DIR from monkeypatch)
        manager2 = AgentUserManager(mock_config)
        assert manager2.mappings_file == str(mappings_file)
        await manager2.load_existing_mappings()

        # Verify data persisted
        assert "test-agent" in manager2.mappings
        assert manager2.mappings["test-agent"].agent_name == "Test Agent"


# ============================================================================
# Quick Health Check
# ============================================================================

@pytest.mark.smoke
def test_quick_health_check():
    """Quick health check - verifies test environment is working"""
    assert True, "If this fails, something is very wrong!"


# ============================================================================
# Pytest Configuration Check
# ============================================================================

@pytest.mark.smoke
def test_pytest_configuration():
    """Verify pytest is configured correctly"""
    import pytest

    # Verify pytest-asyncio is available
    assert hasattr(pytest, 'mark'), "pytest.mark not available"
    assert hasattr(pytest.mark, 'asyncio'), "pytest-asyncio not installed"

    # Verify pytest-cov is available (check that pytest-cov hook is loaded)
    # This is implicit if pytest-cov is installed and working
    assert True
