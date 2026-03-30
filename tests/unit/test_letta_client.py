"""
Unit tests for letta/client.py

Tests cover:
- LettaConfig creation from env and defaults
- get_letta_client singleton behavior
- reset_client clears the singleton
- LettaService.list_agents — happy path and error
- LettaService.get_agent — happy path and not found
- LettaService.send_message — happy path and error
- get_letta_service singleton
"""

import pytest
from unittest.mock import Mock, MagicMock, patch

from src.letta.client import (
    LettaConfig,
    LettaService,
    get_letta_client,
    get_letta_service,
    reset_client,
)


class TestLettaConfig:
    def test_defaults(self):
        cfg = LettaConfig(base_url="http://localhost:8289", api_key="key123")
        assert cfg.base_url == "http://localhost:8289"
        assert cfg.api_key == "key123"
        assert cfg.timeout == 300.0
        assert cfg.max_retries == 3

    def test_from_env_defaults(self):
        with patch.dict("os.environ", {}, clear=True):
            cfg = LettaConfig.from_env()
        assert cfg.base_url == "http://192.168.50.90:8289"
        assert cfg.timeout == 300.0
        assert cfg.max_retries == 3

    def test_from_env_custom(self):
        env = {
            "LETTA_API_URL": "http://custom:9999",
            "LETTA_API_KEY": "my-key",
            "LETTA_TIMEOUT": "60",
            "LETTA_MAX_RETRIES": "5",
        }
        with patch.dict("os.environ", env, clear=True):
            cfg = LettaConfig.from_env()
        assert cfg.base_url == "http://custom:9999"
        assert cfg.api_key == "my-key"
        assert cfg.timeout == 60.0
        assert cfg.max_retries == 5

    def test_from_env_falls_back_to_letta_token(self):
        env = {"LETTA_TOKEN": "token-fallback"}
        with patch.dict("os.environ", env, clear=True):
            cfg = LettaConfig.from_env()
        assert cfg.api_key == "token-fallback"


class TestGetLettaClient:
    def test_creates_client(self):
        reset_client()
        cfg = LettaConfig(base_url="http://test:8289", api_key="k")
        with patch("src.letta.client.Letta") as MockLetta:
            MockLetta.return_value = Mock()
            client = get_letta_client(cfg)
            MockLetta.assert_called_once_with(
                base_url="http://test:8289",
                api_key="k",
                timeout=300.0,
                max_retries=3,
            )
        reset_client()

    def test_returns_cached_client(self):
        reset_client()
        cfg = LettaConfig(base_url="http://test:8289", api_key="k")
        with patch("src.letta.client.Letta") as MockLetta:
            mock_instance = Mock()
            MockLetta.return_value = mock_instance
            c1 = get_letta_client(cfg)
            c2 = get_letta_client(cfg)
            assert c1 is c2
            assert MockLetta.call_count == 1
        reset_client()

    def test_recreates_on_config_change(self):
        reset_client()
        cfg1 = LettaConfig(base_url="http://a:8289", api_key="k1")
        cfg2 = LettaConfig(base_url="http://b:8289", api_key="k2")
        with patch("src.letta.client.Letta") as MockLetta:
            MockLetta.return_value = Mock()
            c1 = get_letta_client(cfg1)
            MockLetta.return_value = Mock()
            c2 = get_letta_client(cfg2)
            assert c1 is not c2
            assert MockLetta.call_count == 2
        reset_client()


class TestResetClient:
    def test_clears_singleton(self):
        reset_client()
        cfg = LettaConfig(base_url="http://test:8289", api_key="k")
        with patch("src.letta.client.Letta") as MockLetta:
            MockLetta.return_value = Mock()
            get_letta_client(cfg)
            reset_client()
            MockLetta.return_value = Mock()
            get_letta_client(cfg)
            assert MockLetta.call_count == 2
        reset_client()


class TestLettaService:
    def _make_service(self, mock_client=None):
        cfg = LettaConfig(base_url="http://test:8289", api_key="k")
        svc = LettaService(cfg)
        if mock_client:
            svc._client = mock_client
        return svc

    def test_list_agents_happy_path(self):
        mock_client = Mock()
        agent1 = Mock(name="agent1")
        agent2 = Mock(name="agent2")
        mock_client.agents.list.return_value = [agent1, agent2]

        svc = self._make_service(mock_client)
        result = svc.list_agents()
        assert len(result) == 2
        mock_client.agents.list.assert_called_once_with(limit=100)

    def test_list_agents_with_tags(self):
        mock_client = Mock()
        mock_client.agents.list.return_value = []

        svc = self._make_service(mock_client)
        svc.list_agents(tags=["prod"], limit=10)
        mock_client.agents.list.assert_called_once_with(tags=["prod"], limit=10)

    def test_list_agents_error_raises(self):
        mock_client = Mock()
        mock_client.agents.list.side_effect = RuntimeError("API down")

        svc = self._make_service(mock_client)
        with pytest.raises(RuntimeError, match="API down"):
            svc.list_agents()

    def test_get_agent_happy_path(self):
        mock_client = Mock()
        mock_agent = Mock()
        mock_client.agents.retrieve.return_value = mock_agent

        svc = self._make_service(mock_client)
        result = svc.get_agent("agent-123")
        assert result is mock_agent
        mock_client.agents.retrieve.assert_called_once_with("agent-123")

    def test_get_agent_not_found(self):
        mock_client = Mock()
        mock_client.agents.retrieve.side_effect = Exception("Not found")

        svc = self._make_service(mock_client)
        result = svc.get_agent("agent-missing")
        assert result is None

    def test_send_message_happy_path(self):
        mock_client = Mock()
        mock_response = Mock()
        mock_response.model_dump.return_value = {"messages": ["hi"]}
        mock_client.agents.messages.create.return_value = mock_response

        svc = self._make_service(mock_client)
        result = svc.send_message("agent-1", "hello")
        assert result == {"messages": ["hi"]}

    def test_send_message_error_raises(self):
        mock_client = Mock()
        mock_client.agents.messages.create.side_effect = RuntimeError("timeout")

        svc = self._make_service(mock_client)
        with pytest.raises(RuntimeError, match="timeout"):
            svc.send_message("agent-1", "hello")


class TestGetLettaService:
    def test_returns_singleton(self):
        import src.letta.client as mod
        mod._service = None
        s1 = get_letta_service()
        s2 = get_letta_service()
        assert s1 is s2
        mod._service = None

    def test_recreates_with_new_config(self):
        import src.letta.client as mod
        mod._service = None
        cfg = LettaConfig(base_url="http://new:8289", api_key="k")
        s1 = get_letta_service()
        s2 = get_letta_service(cfg)
        assert s1 is not s2
        mod._service = None
