"""
Centralized Letta SDK client configuration.

This module provides a singleton Letta client instance that can be used
throughout the application for type-safe API interactions.

Usage:
    from src.letta.client import get_letta_client, LettaConfig
    
    # Get configured client
    client = get_letta_client()
    
    # List agents
    agents = client.agents.list()
    
    # Send message
    response = client.agents.messages.create(
        agent_id="agent-xxx",
        messages=[{"role": "user", "content": "Hello"}]
    )
"""

import os
import logging
from typing import Optional, List, Dict, Any
from dataclasses import dataclass
from functools import lru_cache

from letta_client import Letta
from letta_client.types import AgentState

logger = logging.getLogger(__name__)


@dataclass
class LettaConfig:
    """Configuration for Letta client."""
    base_url: str
    api_key: str
    timeout: float = 300.0  # 5 minutes default (increased for long-running agents)
    max_retries: int = 3
    
    @classmethod
    def from_env(cls) -> "LettaConfig":
        """Create configuration from environment variables."""
        base_url = os.getenv("LETTA_API_URL", "http://192.168.50.90:8289")
        api_key = os.getenv("LETTA_API_KEY") or os.getenv("LETTA_TOKEN", "")
        timeout = float(os.getenv("LETTA_TIMEOUT", "300"))
        max_retries = int(os.getenv("LETTA_MAX_RETRIES", "3"))
        
        if not api_key:
            logger.warning("LETTA_API_KEY not set. API calls may fail.")
        
        return cls(
            base_url=base_url,
            api_key=api_key,
            timeout=timeout,
            max_retries=max_retries
        )


# Global client instance
_client: Optional[Letta] = None
_config: Optional[LettaConfig] = None


def get_letta_client(config: Optional[LettaConfig] = None) -> Letta:
    """
    Get or create the Letta client singleton.
    
    Args:
        config: Optional configuration. If not provided, uses environment variables.
        
    Returns:
        Configured Letta client instance.
    """
    global _client, _config
    
    # Use provided config or create from env
    if config is None:
        config = LettaConfig.from_env()
    
    # Return cached client if config hasn't changed
    if _client is not None and _config == config:
        return _client
    
    # Create new client
    logger.info(f"Creating Letta client for {config.base_url}")
    _client = Letta(
        base_url=config.base_url,
        api_key=config.api_key,
        timeout=config.timeout,
        max_retries=config.max_retries
    )
    _config = config
    
    return _client


def reset_client() -> None:
    """Reset the client singleton (useful for testing)."""
    global _client, _config
    _client = None
    _config = None


# Convenience type aliases
Agent = AgentState


class LettaService:
    """
    High-level service class for Letta operations.
    
    Provides async-compatible methods that wrap the SDK client
    with proper error handling and logging.
    """
    
    def __init__(self, config: Optional[LettaConfig] = None):
        self.config = config or LettaConfig.from_env()
        self._client: Optional[Letta] = None
    
    @property
    def client(self) -> Letta:
        """Get the underlying Letta client."""
        if self._client is None:
            self._client = get_letta_client(self.config)
        return self._client
    
    def list_agents(
        self, 
        tags: Optional[List[str]] = None,
        limit: int = 100
    ) -> List[AgentState]:
        """
        List all agents, optionally filtered by tags.
        
        Args:
            tags: Optional list of tags to filter by
            limit: Maximum number of agents to return
            
        Returns:
            List of AgentState objects
        """
        try:
            if tags:
                agents = self.client.agents.list(tags=tags, limit=limit)
            else:
                agents = self.client.agents.list(limit=limit)
            
            # The SDK returns an iterable - convert to list
            all_agents: List[AgentState] = []
            for agent in agents:
                all_agents.append(agent)
            
            logger.info(f"Retrieved {len(all_agents)} agents from Letta")
            return all_agents
            
        except Exception as e:
            logger.error(f"Failed to list agents: {e}")
            raise
    
    def get_agent(self, agent_id: str) -> Optional[AgentState]:
        """
        Get a specific agent by ID.
        
        Args:
            agent_id: The agent ID
            
        Returns:
            AgentState or None if not found
        """
        try:
            agent = self.client.agents.retrieve(agent_id)
            return agent
        except Exception as e:
            logger.error(f"Failed to get agent {agent_id}: {e}")
            return None
    
    def send_message(
        self,
        agent_id: str,
        message: str,
        role: str = "user",
        timeout: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Send a message to an agent.
        
        Args:
            agent_id: The agent ID to send the message to
            message: The message content
            role: Message role (default: "user")
            timeout: Optional timeout override
            
        Returns:
            Response dictionary with messages
        """
        try:
            response = self.client.agents.messages.create(
                agent_id=agent_id,
                messages=[{"role": role, "content": message}],
                timeout=timeout or self.config.timeout
            )
            
            # Convert to dict for compatibility
            if hasattr(response, 'model_dump'):
                return response.model_dump()
            elif hasattr(response, 'dict'):
                return response.dict()
            else:
                return {"messages": response.messages if hasattr(response, 'messages') else []}
                
        except Exception as e:
            logger.error(f"Failed to send message to agent {agent_id}: {e}")
            raise
    
    def send_message_stream(
        self,
        agent_id: str,
        message: str,
        role: str = "user"
    ):
        """
        Send a message and stream the response.
        
        Args:
            agent_id: The agent ID
            message: The message content
            role: Message role
            
        Yields:
            Response chunks as they arrive
        """
        try:
            stream = self.client.agents.messages.stream(
                agent_id=agent_id,
                messages=[{"role": role, "content": message}]
            )
            
            for chunk in stream:
                yield chunk
                
        except Exception as e:
            logger.error(f"Failed to stream message to agent {agent_id}: {e}")
            raise


# Default service instance
_service: Optional[LettaService] = None


def get_letta_service(config: Optional[LettaConfig] = None) -> LettaService:
    """Get or create the LettaService singleton."""
    global _service
    
    if _service is None or config is not None:
        _service = LettaService(config)
    
    return _service
