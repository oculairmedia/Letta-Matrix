"""
Identity routes — slim CRUD router.

Domain-specific endpoints live in focused modules:
  - identity_health: /identities/health
  - identity_sync: /identities/sync-names
  - identity_messaging: /messages/send-as-identity, send-as-agent, edit-as-agent
  - dm_rooms: /dm-rooms CRUD + reconciliation
  - internal_identity: /internal/ full-detail reads + provisioning
  - _identity_helpers: shared HTTP session, profile sync, provisioning auth
"""

import logging
from typing import Optional
from urllib.parse import unquote

from fastapi import APIRouter, HTTPException, status

from src.api.schemas.identity import (
    IdentityCreate,
    IdentityUpdate,
    IdentityResponse,
    IdentityListResponse,
)
from src.core.identity_storage import get_identity_service
from src.matrix.identity_client_pool import get_identity_client_pool

from ._identity_helpers import _sync_identity_profile
from .identity_health import health_router
from .identity_sync import sync_router
from .identity_messaging import messaging_router
from .dm_rooms import dm_router  # noqa: F401 — re-exported for app.py
from .internal_identity import internal_router  # noqa: F401 — re-exported for app.py

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["identities"])
router.include_router(health_router)
router.include_router(sync_router)
router.include_router(messaging_router)


# ── CRUD endpoints ────────────────────────────────────────────────


@router.post("/identities", response_model=IdentityResponse, status_code=status.HTTP_201_CREATED)
async def create_identity(request: IdentityCreate):
    service = get_identity_service()

    existing = service.get(request.id)
    if existing:
        raise HTTPException(status_code=409, detail=f"Identity {request.id} already exists")

    existing_mxid = service.get_by_mxid(request.mxid)
    if existing_mxid:
        raise HTTPException(status_code=409, detail=f"MXID {request.mxid} already registered")

    identity = service.create(
        identity_id=request.id,
        identity_type=request.identity_type,
        mxid=request.mxid,
        access_token=request.access_token,
        display_name=request.display_name,
        avatar_url=request.avatar_url,
        password_hash=request.password_hash,
        device_id=request.device_id
    )
    return IdentityResponse.from_identity(identity)


@router.get("/identities", response_model=IdentityListResponse)
async def list_identities(
    identity_type: Optional[str] = None,
    active_only: bool = True
):
    service = get_identity_service()

    if identity_type:
        identities = service.get_by_type(identity_type)
        if active_only:
            identities = [i for i in identities if bool(i.is_active)]
    else:
        identities = service.get_all(active_only=active_only)

    return IdentityListResponse(
        success=True,
        count=len(identities),
        identities=[IdentityResponse.from_identity(i) for i in identities]
    )


@router.get("/identities/by-mxid/{mxid:path}", response_model=IdentityResponse)
async def get_identity_by_mxid(mxid: str):
    decoded_mxid = unquote(mxid)
    service = get_identity_service()
    identity = service.get_by_mxid(decoded_mxid)
    if not identity:
        raise HTTPException(status_code=404, detail=f"Identity with MXID {decoded_mxid} not found")
    return IdentityResponse.from_identity(identity)


@router.get("/identities/by-agent/{agent_id}", response_model=IdentityResponse)
async def get_identity_by_agent(agent_id: str):
    service = get_identity_service()
    identity = service.get_by_agent_id(agent_id)
    if not identity:
        raise HTTPException(status_code=404, detail=f"Identity for agent {agent_id} not found")
    return IdentityResponse.from_identity(identity)


@router.get("/identities/{identity_id}", response_model=IdentityResponse)
async def get_identity(identity_id: str):
    service = get_identity_service()
    identity = service.get(identity_id)
    if not identity:
        raise HTTPException(status_code=404, detail=f"Identity {identity_id} not found")
    return IdentityResponse.from_identity(identity)


@router.put("/identities/{identity_id}", response_model=IdentityResponse)
async def update_identity(identity_id: str, request: IdentityUpdate):
    service = get_identity_service()

    existing = service.get(identity_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Identity {identity_id} not found")

    update_data = request.model_dump(exclude_unset=True)
    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update")

    display_name_changed = (
        "display_name" in update_data and update_data["display_name"] != existing.display_name
    )

    identity = service.update(identity_id, **update_data)
    if not identity:
        raise HTTPException(status_code=500, detail="Update failed")

    if display_name_changed:
        await _sync_identity_profile(identity_id, str(identity.display_name) if identity.display_name is not None else None)

    return IdentityResponse.from_identity(identity)


@router.delete("/identities/{identity_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_identity(identity_id: str, hard_delete: bool = False):
    service = get_identity_service()

    existing = service.get(identity_id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Identity {identity_id} not found")

    pool = get_identity_client_pool()
    await pool.close_client(identity_id)

    if hard_delete:
        success = service.delete(identity_id)
    else:
        success = service.deactivate(identity_id)

    if not success:
        raise HTTPException(status_code=500, detail="Delete failed")

    return None
