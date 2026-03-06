import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel


router = APIRouter(prefix="", tags=["agent-sync"])
logger = logging.getLogger(__name__)


class NewAgentNotification(BaseModel):
    agent_id: str
    timestamp: str


class WebhookResponse(BaseModel):
    success: bool
    message: str
    timestamp: str


class AgentMappingUpsertRequest(BaseModel):
    agent_name: str
    matrix_user_id: str
    matrix_password: str
    room_id: Optional[str] = None
    room_created: bool = False


class AgentMappingUpdateRequest(BaseModel):
    agent_name: Optional[str] = None
    matrix_user_id: Optional[str] = None
    matrix_password: Optional[str] = None
    room_id: Optional[str] = None
    room_created: Optional[bool] = None


@router.post("/webhook/new-agent", response_model=WebhookResponse)
async def new_agent_webhook(notification: NewAgentNotification, background_tasks: BackgroundTasks):
    try:
        logger.info(f"Received new agent notification: {notification.agent_id}")

        from src.api import app as app_module

        if not app_module.AGENT_SYNC_AVAILABLE:
            return WebhookResponse(
                success=False,
                message="Agent sync functionality not available",
                timestamp=datetime.now().isoformat(),
            )

        async def trigger_agent_sync():
            try:
                config = app_module.Config.from_env()
                await app_module.run_agent_sync(config)
                logger.info(f"Successfully synced new agent: {notification.agent_id}")
            except Exception as e:
                logger.error(f"Failed to sync new agent {notification.agent_id}: {e}")

        background_tasks.add_task(trigger_agent_sync)

        return WebhookResponse(
            success=True,
            message=f"Triggered sync for new agent: {notification.agent_id}",
            timestamp=datetime.now().isoformat(),
        )

    except Exception as e:
        logger.error(f"Error processing new agent webhook: {e}")
        return WebhookResponse(
            success=False,
            message=f"Error: {str(e)}",
            timestamp=datetime.now().isoformat(),
        )


@router.get("/agents/mappings")
async def get_agent_mappings():
    try:
        from src.core.mapping_service import get_all_mappings

        mappings = get_all_mappings()
        return {
            "success": True,
            "message": f"Retrieved {len(mappings)} agent mappings",
            "mappings": mappings,
        }
    except Exception as e:
        logger.error(f"Error reading agent mappings: {e}")
        return {
            "success": False,
            "message": f"Error: {str(e)}",
            "mappings": {},
        }


@router.post("/agents/matrix-memory/sync")
async def sync_matrix_memory():
    try:
        from src.core.mapping_service import get_all_mappings
        from src.letta.matrix_memory import sync_matrix_block_to_agents

        mappings = get_all_mappings()
        agent_ids = list(mappings.keys())
        result = await sync_matrix_block_to_agents(agent_ids)
        return {"success": True, **result}
    except Exception as e:
        logger.error(f"Matrix memory sync failed: {e}")
        return {"success": False, "error": str(e)}


@router.get("/agents/{agent_id}/room")
async def get_agent_room(agent_id: str):
    try:
        from src.core.mapping_service import get_mapping_by_agent_id

        mapping = get_mapping_by_agent_id(agent_id)
        if not mapping:
            raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found in mappings")
        if not mapping.get("room_created") or not mapping.get("room_id"):
            raise HTTPException(status_code=404, detail=f"Agent {agent_id} does not have a room created yet")

        return {
            "success": True,
            "agent_id": agent_id,
            "agent_name": mapping.get("agent_name"),
            "room_id": mapping.get("room_id"),
            "matrix_user_id": mapping.get("matrix_user_id"),
            "room_created": mapping.get("room_created"),
            "invitation_status": mapping.get("invitation_status", {}),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting agent room for {agent_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@router.put("/agents/{agent_id}/mapping")
async def upsert_agent_mapping(agent_id: str, request: AgentMappingUpsertRequest):
    try:
        from src.core.mapping_service import upsert_mapping

        mapping = upsert_mapping(
            agent_id=agent_id,
            agent_name=request.agent_name,
            matrix_user_id=request.matrix_user_id,
            matrix_password=request.matrix_password,
            room_id=request.room_id,
            room_created=request.room_created,
        )
        if not mapping:
            raise HTTPException(status_code=500, detail="Failed to upsert mapping")
        return {
            "success": True,
            "agent_id": agent_id,
            "mapping": mapping,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error upserting mapping for {agent_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@router.patch("/agents/{agent_id}/mapping")
async def update_agent_mapping(agent_id: str, request: AgentMappingUpdateRequest):
    try:
        from src.core.mapping_service import get_mapping_by_agent_id, invalidate_cache
        from src.models.agent_mapping import AgentMappingDB

        existing = get_mapping_by_agent_id(agent_id)
        if not existing:
            raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")

        db = AgentMappingDB()
        update_kwargs = {}
        if request.agent_name is not None:
            update_kwargs["agent_name"] = request.agent_name
        if request.matrix_user_id is not None:
            update_kwargs["matrix_user_id"] = request.matrix_user_id
        if request.matrix_password is not None:
            update_kwargs["matrix_password"] = request.matrix_password
        if request.room_id is not None:
            update_kwargs["room_id"] = request.room_id
        if request.room_created is not None:
            update_kwargs["room_created"] = request.room_created

        if not update_kwargs:
            return {"success": True, "agent_id": agent_id, "message": "No fields to update"}

        updated = db.update(agent_id, **update_kwargs)
        if not updated:
            raise HTTPException(status_code=500, detail="Update failed")

        invalidate_cache()

        return {
            "success": True,
            "agent_id": agent_id,
            "mapping": updated.to_dict(),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating mapping for {agent_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")
