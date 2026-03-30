"""
Agent provisioning health checks and sync entrypoint.
"""

import logging
import sys
from typing import Optional

logger = logging.getLogger("matrix_client.agent_user_manager")

# Global manager instance to preserve cache between sync runs
_global_manager = None


async def check_provisioning_health(config) -> dict:
    """
    Check the health of agent room provisioning.

    Returns a dict with:
    - total_agents: Number of Letta agents
    - agents_with_rooms: Number with valid room mappings
    - agents_missing_rooms: List of agents without rooms
    - status: 'healthy', 'degraded', or 'unhealthy'
    """
    global _global_manager

    # Import here to avoid circular imports
    from src.core.agent_user_manager import AgentUserManager

    if _global_manager is None:
        _global_manager = AgentUserManager(config)

    manager = _global_manager

    try:
        agents = await manager.get_letta_agents()
        if agents is None:
            return {
                "total_agents": 0,
                "agents_with_rooms": 0,
                "agents_missing_rooms": [],
                "missing_count": 0,
                "status": "unhealthy",
                "error": "Failed to fetch agents from Letta API",
            }
        total_agents = len(agents)

        await manager.load_existing_mappings()

        agents_with_rooms = 0
        agents_missing_rooms = []

        for agent in agents:
            agent_id = agent.get("id", "")
            agent_name = agent.get("name", "Unknown")

            mapping = manager.mappings.get(agent_id)
            if mapping and mapping.room_id and mapping.room_created:
                agents_with_rooms += 1
            else:
                agents_missing_rooms.append({
                    "agent_id": agent_id,
                    "agent_name": agent_name
                })

        missing_count = len(agents_missing_rooms)
        if missing_count == 0:
            status = "healthy"
        elif missing_count <= 3:
            status = "degraded"
        else:
            status = "unhealthy"

        return {
            "total_agents": total_agents,
            "agents_with_rooms": agents_with_rooms,
            "agents_missing_rooms": agents_missing_rooms,
            "missing_count": missing_count,
            "status": status
        }

    except Exception as e:
        logger.error(f"Error checking provisioning health: {e}")
        return {
            "total_agents": 0,
            "agents_with_rooms": 0,
            "agents_missing_rooms": [],
            "missing_count": -1,
            "status": "error",
            "error": str(e)
        }


async def run_agent_sync(config):
    """Run the agent sync process"""
    global _global_manager

    from src.core.agent_user_manager import AgentUserManager

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(getattr(logging, config.log_level.upper()))
    logger.addHandler(handler)
    logger.setLevel(getattr(logging, config.log_level.upper()))

    logger.info("Starting agent sync process from run_agent_sync")

    if _global_manager is None:
        logger.info("Creating new AgentUserManager instance")
        _global_manager = AgentUserManager(config)
    else:
        logger.debug("Reusing existing AgentUserManager instance (cache preserved)")

    manager = _global_manager

    logger.info("Ensuring core Matrix users exist...")
    core_users = [
        (config.username, config.password, "Letta Bot"),
        (manager.admin_username, manager.admin_password, "Matrix Admin")
    ]
    await manager.user_manager.ensure_core_users_exist(core_users)

    await manager.sync_agents_to_users()
    return manager
