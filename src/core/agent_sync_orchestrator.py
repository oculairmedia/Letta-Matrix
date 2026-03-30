"""
Agent sync orchestration — space readiness, provisioning, validation,
avatar setting, cleanup, and memory sync.
"""

import asyncio
import logging
import os
from typing import Optional, Set

from .types import AgentUserMapping

logger = logging.getLogger("matrix_client.agent_user_manager")


class AgentSyncOrchestratorMixin:
    """Sync orchestration methods mixed into AgentUserManager."""

    async def _ensure_space_ready(self) -> bool:
        await self.ensure_core_users_exist()
        await self.load_existing_mappings()
        await self.space_manager.load_space_config()

        existing_space_id = self.space_manager.get_space_id()
        if not existing_space_id:
            logger.info("Creating Letta Agents space")
            space_id = await self.space_manager.create_letta_agents_space()
            if space_id:
                logger.info(f"Successfully created Letta Agents space: {space_id}")
                return True
            logger.warning("Failed to create Letta Agents space, rooms will not be organized")
            return False

        logger.info(f"Validating existing Letta Agents space: {existing_space_id}")
        space_valid = await self.space_manager.check_room_exists(existing_space_id)
        if space_valid:
            logger.info(f"Using existing Letta Agents space: {existing_space_id}")
            return False

        logger.warning(f"Space {existing_space_id} is invalid, will recreate")
        old_space_id = existing_space_id
        self.space_manager.space_id = None

        space_id = await self.space_manager.create_letta_agents_space()
        space_just_created = False
        if space_id:
            logger.info(f"Successfully recreated Letta Agents space: {space_id}")
            new_space_valid = await self.space_manager.check_room_exists(space_id)
            if new_space_valid:
                logger.info(f"New space {space_id} validated successfully")
                space_just_created = True
            else:
                logger.error(f"New space {space_id} failed validation, keeping old space config")
                self.space_manager.space_id = old_space_id
                await self.space_manager.save_space_config()
        else:
            logger.error("Failed to recreate Letta Agents space")
            self.space_manager.space_id = old_space_id
            await self.space_manager.save_space_config()

        return space_just_created

    async def _provision_new_agents(self, letta_agents, existing_ids):
        new_agents = {agent["id"] for agent in letta_agents} - existing_ids
        for agent in letta_agents:
            if agent["id"] in new_agents:
                await self.create_user_for_agent(agent)

    async def _validate_existing_agents(self, existing_mappings):
        logger.info(f"Checking {len(existing_mappings)} existing agents for failed creation status or missing rooms")
        for agent in existing_mappings:
            mapping = self.mappings.get(agent["id"])
            logger.debug(f"Agent {agent['name']} - created: {mapping.created if mapping else 'No mapping'}, room: {mapping.room_created if mapping else 'No room'}")
            if not mapping:
                continue
            if mapping.agent_name != agent["name"]:
                logger.info(f"Agent name changed from '{mapping.agent_name}' to '{agent['name']}'")
                mapping.agent_name = agent["name"]
                if mapping.room_id and mapping.room_created:
                    logger.info(f"Updating room name for {mapping.room_id}")
                    success = await self.update_room_name(mapping.room_id, agent["name"])
                    if not success:
                        logger.warning(f"Failed to update room name for {mapping.room_id}")
                if mapping.matrix_user_id and mapping.matrix_password:
                    logger.info(f"Updating display name for {mapping.matrix_user_id}")
                    display_success = await self.update_display_name(mapping.matrix_user_id, agent["name"], mapping.matrix_password)
                    if not display_success:
                        logger.warning(f"Failed to update display name for {mapping.matrix_user_id}")
            if not mapping.created:
                logger.info(f"Retrying creation for existing agent {agent['name']} with failed status")
                await self.create_user_for_agent(agent)
                continue
            if not mapping.room_created:
                logger.info(f"Creating room for existing agent {agent['name']}")
                await self.create_or_update_agent_room(agent["id"])
                continue
            if not mapping.room_id:
                continue
            skip_invitation_acceptance = False
            try:
                actual_room_id = await self.discover_agent_room(mapping.matrix_user_id)
                if actual_room_id and actual_room_id != mapping.room_id:
                    logger.warning(f"🔄 Room drift detected for {agent['name']}!")
                    logger.warning(f"  Stored room:  {mapping.room_id}")
                    logger.warning(f"  Actual room:  {actual_room_id}")
                    mapping.room_id = actual_room_id
                    logger.info(f"✅ Fixed room mapping for {agent['name']}")
                if not actual_room_id:
                    room_exists = await self.space_manager.check_room_exists(mapping.room_id)
                    if not room_exists:
                        logger.warning(f"Room {mapping.room_id} for {agent['name']} is invalid, recreating")
                        mapping.room_id = None
                        mapping.room_created = False
                        await self.create_or_update_agent_room(agent["id"])
                        skip_invitation_acceptance = True
            except Exception as e:
                logger.error(f"Error checking room drift for {agent['name']}: {e}")
            if skip_invitation_acceptance or not mapping.room_id:
                continue
            logger.info(f"Ensuring invitations are accepted for room {mapping.room_id}")
            await self.auto_accept_invitations_with_tracking(mapping.room_id, mapping)
            member_results = await self.room_manager.ensure_required_members(mapping.room_id, agent["id"])
            for user_id, status in member_results.items():
                if status == "invited":
                    logger.info(f"✅ Invited {user_id} to {agent['name']}'s room")
                elif status == "failed":
                    logger.warning(f"⚠️  Failed to ensure {user_id} in {agent['name']}'s room")

    async def _set_missing_avatars(self, existing_mappings):
        for agent in existing_mappings:
            mapping = self.mappings.get(agent["id"])
            if not mapping:
                continue
            if not (mapping.created and mapping.room_created and mapping.room_id and mapping.matrix_user_id):
                continue
            asyncio.create_task(
                self.set_default_avatar_for_agent(agent["name"], mapping.matrix_user_id)
            )

    async def _cleanup_removed_agents(self, letta_agent_ids, existing_mappings):
        removed_agents = existing_mappings - letta_agent_ids
        if removed_agents:
            if not letta_agent_ids:
                logger.warning(
                    f"Letta API returned 0 agents but {len(existing_mappings)} exist in DB — "
                    f"skipping soft-delete to prevent mass removal (likely API error)"
                )
            else:
                from src.models.agent_mapping import AgentMappingDB

                db = AgentMappingDB()
                for agent_id in removed_agents:
                    mapping = self.mappings.get(agent_id)
                    if mapping and not mapping.removed_at:
                        logger.info(f"Agent {agent_id} removed from Letta — marking for cleanup (2h grace period)")
                        db.soft_delete(agent_id)
                        mapping.removed_at = "pending"

        for agent_id in letta_agent_ids:
            mapping = self.mappings.get(agent_id)
            if mapping and mapping.removed_at:
                logger.info(f"Agent {agent_id} reappeared — cancelling pending removal")
                from src.models.agent_mapping import AgentMappingDB

                db = AgentMappingDB()
                db.clear_removed(agent_id)
                mapping.removed_at = None

        await self._cleanup_expired_agents()
        self._removed_agents_last_sync = removed_agents
        return removed_agents

    async def _sync_matrix_memory(self):
        try:
            from src.letta.matrix_memory import sync_matrix_block_to_agents

            agent_ids = [aid for aid in self.mappings.keys() if aid not in self._removed_agents_last_sync]
            if agent_ids:
                result = await sync_matrix_block_to_agents(agent_ids)
                logger.info(f"[MatrixMemory] Block sync: {result.get('synced', 0)} agents updated")
        except Exception as e:
            logger.warning(f"[MatrixMemory] Block sync failed (non-critical): {e}")

    async def _cleanup_expired_agents(self, grace_period_hours: int = 2):
        """Clean up agents whose removal grace period has expired."""
        from src.models.agent_mapping import AgentMappingDB
        db = AgentMappingDB()
        expired = db.get_expired_removals(grace_period_hours)

        if not expired:
            return

        logger.info(f"Cleaning up {len(expired)} agents past {grace_period_hours}h grace period")

        space_id = self.space_manager.get_space_id()

        for mapping in expired:
            agent_id = str(mapping.agent_id)
            room_id = str(mapping.room_id) if mapping.room_id is not None else None
            matrix_user_id = str(mapping.matrix_user_id)
            username = matrix_user_id.split(":")[0].replace("@", "")
            password = str(mapping.matrix_password)

            logger.info(f"Cleaning up expired agent {agent_id} (user={matrix_user_id}, room={room_id})")

            if room_id:
                if space_id:
                    await self.room_manager.remove_room_from_space(room_id, space_id)

                await self.room_manager.leave_room_as_user(room_id, username, password)
                await self.room_manager.leave_room_as_admin(room_id)

                letta_username = os.getenv("MATRIX_LETTA_USERNAME", "letta")
                letta_password = os.getenv("MATRIX_LETTA_PASSWORD", "letta")
                await self.room_manager.leave_room_as_user(room_id, letta_username, letta_password)

            db.delete(agent_id)
            if agent_id in self.mappings:
                del self.mappings[agent_id]

            logger.info(f"Cleaned up agent {agent_id}: room left, mapping deleted")
