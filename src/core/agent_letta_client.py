"""
Letta SDK agent fetching — paginated agent list retrieval.
"""

import asyncio
import logging
from typing import List, Optional

logger = logging.getLogger("matrix_client.agent_user_manager")


class AgentLettaClientMixin:
    """Letta SDK agent fetching mixed into AgentUserManager."""

    async def get_letta_agents(self) -> Optional[List[dict]]:
        """Get all Letta agents using the Letta SDK with pagination support"""
        try:
            from src.letta.client import get_letta_client, LettaConfig
            from concurrent.futures import ThreadPoolExecutor

            sdk_config = LettaConfig(
                base_url="http://192.168.50.90:8289",
                api_key="lettaSecurePass123",
                timeout=30.0,
                max_retries=3
            )
            client = get_letta_client(sdk_config)

            agent_list = []
            seen_agent_ids = set()

            loop = asyncio.get_event_loop()
            with ThreadPoolExecutor(max_workers=1) as executor:
                agents = await loop.run_in_executor(
                    executor,
                    lambda: list(client.agents.list(limit=500))
                )

            logger.info(f"Retrieved {len(agents)} agents from SDK")

            for agent in agents:
                agent_id = str(agent.id) if agent.id else ""
                agent_name = str(agent.name) if agent.name else agent_id

                if agent_id and agent_id not in seen_agent_ids:
                    seen_agent_ids.add(agent_id)
                    agent_list.append({
                        "id": agent_id,
                        "name": agent_name
                    })

            logger.info(f"Found {len(agent_list)} unique Letta agents via SDK")
            return agent_list

        except Exception as e:
            logger.error(f"Error getting Letta agents via SDK: {e}", exc_info=True)
            return None
