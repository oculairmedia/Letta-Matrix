import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="", tags=["portal-links"])
logger = logging.getLogger(__name__)


class PortalLinkRequest(BaseModel):
    room_id: str
    enabled: bool = True
    relay_mode: bool = True
    mention_enabled: bool = False


class PortalLinkUpdateRequest(BaseModel):
    enabled: Optional[bool] = None
    relay_mode: Optional[bool] = None
    mention_enabled: Optional[bool] = None


@router.get("/agents/portal-links")
async def list_all_portal_links():
    try:
        from src.core.mapping_service import get_all_portal_links

        links = get_all_portal_links()
        return {"success": True, "links": links, "count": len(links)}
    except Exception as e:
        logger.error(f"Error listing portal links: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/agents/{agent_id}/portal-links")
async def get_agent_portal_links(agent_id: str):
    try:
        from src.core.mapping_service import get_portal_links_by_agent

        links = get_portal_links_by_agent(agent_id)
        return {"success": True, "agent_id": agent_id, "links": links, "count": len(links)}
    except Exception as e:
        logger.error(f"Error getting portal links for {agent_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/agents/{agent_id}/portal-links")
async def create_agent_portal_link(agent_id: str, request: PortalLinkRequest):
    try:
        from src.core.mapping_service import create_portal_link, get_mapping_by_agent_id

        mapping = get_mapping_by_agent_id(agent_id)
        if not mapping:
            raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")
        link = create_portal_link(agent_id, request.room_id, request.enabled, request.relay_mode, request.mention_enabled)
        if not link:
            raise HTTPException(status_code=500, detail="Failed to create portal link")
        return {"success": True, "link": link}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating portal link: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/agents/{agent_id}/portal-links/{room_id:path}")
async def delete_agent_portal_link(agent_id: str, room_id: str):
    try:
        from src.core.mapping_service import delete_portal_link

        result = delete_portal_link(agent_id, room_id)
        if not result:
            raise HTTPException(status_code=404, detail="Portal link not found")
        return {"success": True, "message": f"Deleted portal link for agent {agent_id} in room {room_id}"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting portal link: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/agents/{agent_id}/portal-links/{room_id:path}")
async def update_agent_portal_link(agent_id: str, room_id: str, request: PortalLinkUpdateRequest):
    try:
        from src.core.mapping_service import update_portal_link

        updates = {k: v for k, v in request.model_dump().items() if v is not None}
        if not updates:
            raise HTTPException(status_code=400, detail="No fields to update")
        result = update_portal_link(agent_id, room_id, **updates)
        if not result:
            raise HTTPException(status_code=404, detail="Portal link not found")
        return {"success": True, "link": result}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating portal link: {e}")
        raise HTTPException(status_code=500, detail=str(e))
