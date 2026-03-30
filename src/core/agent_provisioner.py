"""
Agent provisioning — create Matrix users and discover rooms for agents.
"""

import asyncio
import logging
from typing import Optional

from .types import AgentUserMapping

logger = logging.getLogger("matrix_client.agent_user_manager")


class AgentProvisionerMixin:
    """Provisioning methods mixed into AgentUserManager."""

    async def create_user_for_agent(self, agent: dict):
        """Create a Matrix user for a specific agent"""
        agent_id = agent["id"]
        agent_name = agent["name"]

        logger.info(f"Processing agent: {agent_name} ({agent_id})")

        if agent_id in self.mappings:
            existing_mapping = self.mappings[agent_id]
            logger.info(f"Found existing mapping for agent {agent_name}")
            logger.info(f"  User: {existing_mapping.matrix_user_id}, Created: {existing_mapping.created}")
            logger.info(f"  Room: {existing_mapping.room_id}, Room Created: {existing_mapping.room_created}")

            if existing_mapping.created and existing_mapping.room_created and existing_mapping.room_id:
                logger.debug(f"Validating room mapping for {agent_name}")

                try:
                    actual_room_id = await self.discover_agent_room(existing_mapping.matrix_user_id)

                    if actual_room_id:
                        if actual_room_id != existing_mapping.room_id:
                            logger.warning(f"Room drift detected for {agent_name}!")
                            logger.warning(f"  Stored room:  {existing_mapping.room_id}")
                            logger.warning(f"  Actual room:  {actual_room_id}")
                            logger.info(f"Updating mapping to use actual room")
                            existing_mapping.room_id = actual_room_id
                            await self.save_mappings()
                            logger.info(f"✅ Fixed room mapping for {agent_name}")
                        else:
                            logger.debug(f"Room mapping for {agent_name} is correct")
                        return
                    else:
                        room_exists = await self.space_manager.check_room_exists(existing_mapping.room_id)
                        if room_exists:
                            logger.info(f"Agent {agent_name} has valid room, no drift detected")
                            return
                        else:
                            logger.warning(f"Stored room {existing_mapping.room_id} for {agent_name} is invalid and no room discovered")
                            existing_mapping.room_id = None
                            existing_mapping.room_created = False
                            await self.save_mappings()

                except Exception as e:
                    logger.error(f"Error during room validation for {agent_name}: {e}")
                    room_exists = await self.space_manager.check_room_exists(existing_mapping.room_id or "")
                    if room_exists:
                        logger.info(f"Agent {agent_name} has valid room (discovery failed, using basic check)")
                        return
                    else:
                        logger.warning(f"Room {existing_mapping.room_id} for {agent_name} is invalid")
                        existing_mapping.room_id = None
                        existing_mapping.room_created = False
                        await self.save_mappings()

            if existing_mapping.created and not existing_mapping.room_created:
                logger.info(f"User exists but room missing for agent {agent_name}, creating room only")
                await self.create_or_update_agent_room(agent_id)
                return

        logger.info(f"Creating Matrix user for agent: {agent_name} ({agent_id})")

        username = self.generate_username(agent_name, agent_id)
        matrix_user_id = f"@{username}:matrix.oculair.ca"

        if agent_id in self.mappings and self.mappings[agent_id].matrix_password:
            password = self.mappings[agent_id].matrix_password
            logger.info(f"Using existing password for agent {agent_name}")
        else:
            password = self.generate_password()
            logger.info(f"Generated new password for agent {agent_name}")

        success = await self.create_matrix_user(username, password, agent_name)

        if agent_id in self.mappings:
            self.mappings[agent_id].created = success
            self.mappings[agent_id].matrix_user_id = matrix_user_id
            self.mappings[agent_id].matrix_password = password
        else:
            mapping = AgentUserMapping(
                agent_id=agent_id,
                agent_name=agent_name,
                matrix_user_id=matrix_user_id,
                matrix_password=password,
                created=success,
                room_id=None,
                room_created=False
            )
            self.mappings[agent_id] = mapping

        if success:
            logger.info(f"Successfully created Matrix user {matrix_user_id} for agent {agent_name}")
            await self.create_or_update_agent_room(agent_id)
            asyncio.create_task(self.set_default_avatar_for_agent(agent_name, matrix_user_id))
            await self.create_or_update_agent_room(agent_id)
        else:
            logger.error(f"Failed to create Matrix user for agent {agent_name}")

    async def discover_agent_room(self, agent_user_id: str) -> Optional[str]:
        """Discover the actual room for an agent by checking the database."""
        try:
            from src.core.mapping_service import get_mapping_by_matrix_user

            mapping = get_mapping_by_matrix_user(agent_user_id)
            if mapping:
                room_id = mapping.get("room_id")
                if room_id:
                    logger.info(f"Found room in database for {agent_user_id}: {room_id}")
                    return room_id
                else:
                    logger.warning(f"Agent {agent_user_id} has no room_id in database")
                    return None

            logger.warning(f"Agent {agent_user_id} not found in database mappings")
            return None

        except Exception as e:
            logger.error(f"Error discovering room from database for {agent_user_id}: {e}")
            return None
