"""
DM room management endpoints — create, list, lookup, reconcile, delete.
"""

import logging
from typing import List, Optional
from urllib.parse import unquote

from fastapi import APIRouter, HTTPException, status

from src.api.schemas.identity import (
    DMRoomCreate,
    DMRoomResponse,
    DMRoomListResponse,
    DMRoomNameReconcileRequest,
    DMRoomNameReconcileResponse,
    DMRoomNameReconcileDiff,
)
from src.core.identity_storage import get_identity_service, get_dm_room_service

from ._identity_helpers import (
    _get_matrix_display_name,
    _get_room_name,
    _sync_identity_profile,
)

logger = logging.getLogger(__name__)

dm_router = APIRouter(prefix="/api/v1", tags=["dm-rooms"])


@dm_router.post("/dm-rooms", response_model=DMRoomResponse, status_code=status.HTTP_201_CREATED)
async def create_or_get_dm_room(request: DMRoomCreate):
    service = get_dm_room_service()
    dm_room = service.get_or_create(request.room_id, request.mxid1, request.mxid2)
    return DMRoomResponse.from_dm_room(dm_room)


@dm_router.get("/dm-rooms", response_model=DMRoomListResponse)
async def list_dm_rooms(user_mxid: Optional[str] = None):
    service = get_dm_room_service()

    if user_mxid:
        dm_rooms = service.get_for_user(unquote(user_mxid))
    else:
        dm_rooms = service.get_all()

    return DMRoomListResponse(
        success=True,
        count=len(dm_rooms),
        dm_rooms=[DMRoomResponse.from_dm_room(r) for r in dm_rooms]
    )


@dm_router.post("/dm-rooms/reconcile-names", response_model=DMRoomNameReconcileResponse)
async def reconcile_dm_room_names(request: DMRoomNameReconcileRequest):
    identity_service = get_identity_service()
    dm_room_service = get_dm_room_service()

    dm_rooms = dm_room_service.get_all()
    if request.limit > 0:
        dm_rooms = dm_rooms[: request.limit]

    checked = 0
    agent_dm_rooms = 0
    mismatched_rooms = 0
    profile_mismatch_count = 0
    profiles_synced_count = 0
    failed = 0
    changes: List[DMRoomNameReconcileDiff] = []

    for room in dm_rooms:
        checked += 1
        room_id = str(room.room_id)
        participants = [str(room.participant_1), str(room.participant_2)]
        participant_identity_ids: List[str] = []
        profile_mismatches: List[str] = []
        profiles_synced: List[str] = []
        errors: List[str] = []

        identity_by_mxid = {}
        for mxid in participants:
            identity = identity_service.get_by_mxid(mxid)
            if identity is not None:
                identity_by_mxid[mxid] = identity
                participant_identity_ids.append(str(identity.id))

        agent_identity_id: Optional[str] = None
        for identity in identity_by_mxid.values():
            identity_id = str(identity.id)
            if identity_id.startswith("letta_"):
                agent_identity_id = identity_id
                break

        if agent_identity_id is None:
            continue
        agent_dm_rooms += 1

        room_name: Optional[str] = None
        access_token_identity = identity_by_mxid.get(str(room.participant_1)) or identity_by_mxid.get(str(room.participant_2))
        if access_token_identity is not None and access_token_identity.access_token is not None:
            room_name = await _get_room_name(room_id, str(access_token_identity.access_token))

        p1_name = None
        p2_name = None
        p1_identity = identity_by_mxid.get(str(room.participant_1))
        p2_identity = identity_by_mxid.get(str(room.participant_2))
        if p1_identity is not None and p1_identity.display_name is not None:
            p1_name = str(p1_identity.display_name)
        if p2_identity is not None and p2_identity.display_name is not None:
            p2_name = str(p2_identity.display_name)
        expected_room_name = f"{p1_name} ↔ {p2_name}" if p1_name is not None and p2_name is not None else None
        room_name_mismatch = expected_room_name is not None and room_name is not None and room_name != expected_room_name

        for identity in identity_by_mxid.values():
            identity_id = str(identity.id)
            expected_display_name = str(identity.display_name) if identity.display_name is not None else None
            if expected_display_name is None:
                continue

            actual_display_name = None
            if identity.access_token is not None:
                actual_display_name = await _get_matrix_display_name(str(identity.mxid), str(identity.access_token))

            if actual_display_name != expected_display_name:
                profile_mismatch_count += 1
                profile_mismatches.append(identity_id)
                if not request.dry_run and request.sync_profiles:
                    try:
                        await _sync_identity_profile(identity_id, expected_display_name)
                        profiles_synced.append(identity_id)
                        profiles_synced_count += 1
                    except Exception as exc:
                        errors.append(f"profile_sync_failed:{identity_id}:{exc}")

        has_mismatch = room_name_mismatch or bool(profile_mismatches)
        if has_mismatch:
            mismatched_rooms += 1

        if errors:
            failed += 1

        if has_mismatch or errors:
            changes.append(
                DMRoomNameReconcileDiff(
                    room_id=room_id,
                    agent_identity_id=agent_identity_id,
                    participant_identity_ids=participant_identity_ids,
                    room_name=room_name,
                    expected_room_name=expected_room_name,
                    room_name_mismatch=room_name_mismatch,
                    profile_mismatches=profile_mismatches,
                    profiles_synced=profiles_synced,
                    errors=errors,
                )
            )

    return DMRoomNameReconcileResponse(
        success=True,
        dry_run=request.dry_run,
        checked=checked,
        agent_dm_rooms=agent_dm_rooms,
        mismatched_rooms=mismatched_rooms,
        profile_mismatches=profile_mismatch_count,
        profiles_synced=profiles_synced_count,
        failed=failed,
        changes=changes,
    )


@dm_router.get("/dm-rooms/lookup", response_model=DMRoomResponse)
async def lookup_dm_room(mxid1: str, mxid2: str):
    service = get_dm_room_service()
    dm_room = service.get(unquote(mxid1), unquote(mxid2))
    if not dm_room:
        raise HTTPException(status_code=404, detail="DM room not found for these participants")
    return DMRoomResponse.from_dm_room(dm_room)


@dm_router.get("/dm-rooms/by-room-id/{room_id}", response_model=DMRoomResponse)
async def get_dm_room_by_id(room_id: str):
    service = get_dm_room_service()
    dm_room = service.get_by_room_id(unquote(room_id))
    if dm_room is None:
        raise HTTPException(status_code=404, detail=f"DM room {room_id} not found")
    return DMRoomResponse.from_dm_room(dm_room)


@dm_router.delete("/dm-rooms", status_code=status.HTTP_204_NO_CONTENT)
async def delete_dm_room(mxid1: str, mxid2: str):
    service = get_dm_room_service()
    success = service.delete(unquote(mxid1), unquote(mxid2))
    if not success:
        raise HTTPException(status_code=404, detail="DM room not found")
    return None
