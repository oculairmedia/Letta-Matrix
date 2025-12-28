"""
E2E Test Configuration

These tests run against live services:
- Matrix homeserver (Tuwunel)
- PostgreSQL database
- Letta API

Required environment variables:
- MATRIX_HOMESERVER_URL (default: http://127.0.0.1:6167)
- MATRIX_ADMIN_USERNAME (default: @admin:matrix.oculair.ca)
- MATRIX_ADMIN_PASSWORD (required)
- LETTA_API_URL (default: http://192.168.50.90:8283)
- LETTA_TOKEN (default: lettaSecurePass123)
- DATABASE_URL (default: postgresql://letta:letta@192.168.50.90:5432/matrix_letta)
"""

import os
import pytest
import aiohttp
import asyncio
from dataclasses import dataclass
from typing import Optional


@dataclass
class E2EConfig:
    """Configuration for E2E tests"""
    matrix_homeserver_url: str
    matrix_admin_username: str
    matrix_admin_password: str
    letta_api_url: str
    letta_token: str
    database_url: str
    
    @classmethod
    def from_env(cls) -> "E2EConfig":
        """Load configuration from environment variables"""
        password = os.getenv("MATRIX_ADMIN_PASSWORD")
        if not password:
            pytest.skip("MATRIX_ADMIN_PASSWORD not set - skipping E2E tests")
        
        return cls(
            matrix_homeserver_url=os.getenv("MATRIX_HOMESERVER_URL", "http://127.0.0.1:6167"),
            matrix_admin_username=os.getenv("MATRIX_ADMIN_USERNAME", "@admin:matrix.oculair.ca"),
            matrix_admin_password=password,
            letta_api_url=os.getenv("LETTA_API_URL", "http://192.168.50.90:8283"),
            letta_token=os.getenv("LETTA_TOKEN", "lettaSecurePass123"),
            database_url=os.getenv("DATABASE_URL", "postgresql://letta:letta@192.168.50.90:5432/matrix_letta"),
        )


class MatrixTestClient:
    """Direct Matrix API client for E2E test verification"""
    
    def __init__(self, homeserver_url: str, admin_username: str, admin_password: str):
        self.homeserver_url = homeserver_url
        self.admin_username = admin_username
        self.admin_password = admin_password
        self._token: Optional[str] = None
    
    async def login(self) -> Optional[str]:
        """Login and get access token"""
        if self._token:
            return self._token
            
        async with aiohttp.ClientSession() as session:
            url = f"{self.homeserver_url}/_matrix/client/v3/login"
            data = {
                "type": "m.login.password",
                "identifier": {"type": "m.id.user", "user": self.admin_username},
                "password": self.admin_password
            }
            async with session.post(url, json=data) as response:
                if response.status != 200:
                    raise Exception(f"Login failed: {await response.text()}")
                result = await response.json()
                self._token = result["access_token"]
                return self._token
    
    async def get_display_name(self, user_id: str) -> Optional[str]:
        """Get the display name of a Matrix user directly from the server"""
        async with aiohttp.ClientSession() as session:
            url = f"{self.homeserver_url}/_matrix/client/v3/profile/{user_id}/displayname"
            async with session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get("displayname")
                elif response.status == 404:
                    return None
                else:
                    raise Exception(f"Failed to get display name: {await response.text()}")
    
    async def user_exists(self, user_id: str) -> bool:
        """Check if a user exists on the Matrix server"""
        try:
            await self.get_display_name(user_id)
            return True
        except:
            return False
    
    async def get_room_members(self, room_id: str) -> list:
        """Get members of a room"""
        token = await self.login()
        async with aiohttp.ClientSession() as session:
            url = f"{self.homeserver_url}/_matrix/client/v3/rooms/{room_id}/members"
            headers = {"Authorization": f"Bearer {token}"}
            async with session.get(url, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get("chunk", [])
                else:
                    return []


class LettaTestClient:
    """Direct Letta API client for E2E tests"""
    
    def __init__(self, api_url: str, token: str):
        self.api_url = api_url
        self.token = token
    
    async def create_test_agent(self, name: str) -> dict:
        """Create a test agent in Letta"""
        async with aiohttp.ClientSession() as session:
            url = f"{self.api_url}/v1/agents"
            headers = {"Authorization": f"Bearer {self.token}"}
            data = {
                "name": name,
                "memory_blocks": [],
                "llm_config": {
                    "model": "claude-sonnet-4-20250514",
                    "model_endpoint_type": "anthropic",
                    "model_endpoint": "https://api.anthropic.com/v1",
                    "context_window": 200000
                },
                "embedding_config": {
                    "embedding_model": "text-embedding-3-small",
                    "embedding_endpoint_type": "openai",
                    "embedding_endpoint": "https://api.openai.com/v1",
                    "embedding_dim": 1536
                }
            }
            async with session.post(url, json=data, headers=headers) as response:
                if response.status in (200, 201):
                    return await response.json()
                else:
                    raise Exception(f"Failed to create agent: {await response.text()}")
    
    async def delete_agent(self, agent_id: str) -> bool:
        """Delete a test agent"""
        async with aiohttp.ClientSession() as session:
            url = f"{self.api_url}/v1/agents/{agent_id}"
            headers = {"Authorization": f"Bearer {self.token}"}
            async with session.delete(url, headers=headers) as response:
                return response.status in (200, 204)
    
    async def get_agent(self, agent_id: str) -> Optional[dict]:
        """Get agent details"""
        async with aiohttp.ClientSession() as session:
            url = f"{self.api_url}/v1/agents/{agent_id}"
            headers = {"Authorization": f"Bearer {self.token}"}
            async with session.get(url, headers=headers) as response:
                if response.status == 200:
                    return await response.json()
                return None
    
    async def rename_agent(self, agent_id: str, new_name: str) -> bool:
        """Rename an agent"""
        async with aiohttp.ClientSession() as session:
            url = f"{self.api_url}/v1/agents/{agent_id}"
            headers = {"Authorization": f"Bearer {self.token}"}
            data = {"name": new_name}
            async with session.patch(url, json=data, headers=headers) as response:
                return response.status == 200


@pytest.fixture
def e2e_config() -> E2EConfig:
    """Get E2E test configuration"""
    return E2EConfig.from_env()


@pytest.fixture
def matrix_client(e2e_config: E2EConfig) -> MatrixTestClient:
    """Get Matrix test client"""
    return MatrixTestClient(
        homeserver_url=e2e_config.matrix_homeserver_url,
        admin_username=e2e_config.matrix_admin_username,
        admin_password=e2e_config.matrix_admin_password
    )


@pytest.fixture
def letta_client(e2e_config: E2EConfig) -> LettaTestClient:
    """Get Letta test client"""
    return LettaTestClient(
        api_url=e2e_config.letta_api_url,
        token=e2e_config.letta_token
    )
