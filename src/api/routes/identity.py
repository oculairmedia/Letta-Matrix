from fastapi import APIRouter, Header, HTTPException, status
from typing import List, Optional
from urllib.parse import unquote
import logging
import os
import aiohttp
import asyncio
import time

from src.api.schemas.identity import (
    IdentityCreate,
    IdentityUpdate,
    IdentityResponse,
    IdentityListResponse,
    FullIdentityResponse,
    FullIdentityListResponse,
    DMRoomCreate,
    DMRoomResponse,
    DMRoomListResponse,
    SendAsIdentityRequest,
    SendAsIdentityResponse,
    SendAsAgentRequest,
    EditAsAgentRequest,
    IdentityProvisionRequest,
    IdentityProvisionResponse,
    IdentitySyncNamesRequest,
    IdentitySyncNamesResponse,
    IdentityNameSyncDiff,
)
from src.core.identity_health_monitor import get_identity_token_health_monitor
from src.core.identity_storage import get_identity_service, get_dm_room_service
from src.core.user_manager import MatrixUserManager
from src.matrix.identity_client_pool import get_identity_client_pool
from src.letta.client import LettaService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["identities"])


async def _sync_identity_profile(identity_id: str, display_name: Optional[str]) -> None:
    if display_name is None:
        return

    service = get_identity_service()
    identity = service.get(identity_id)
    if not identity:
        raise HTTPException(status_code=404, detail=f"Identity {identity_id} not found")

    homeserver_url = os.getenv("MATRIX_HOMESERVER_URL", "https://matrix.oculair.ca")
    admin_username = os.getenv("MATRIX_ADMIN_USERNAME", "admin")
    admin_password = os.getenv("MATRIX_ADMIN_PASSWORD", "")
    user_manager = MatrixUserManager(homeserver_url, admin_username, admin_password)

    monitor = get_identity_token_health_monitor()
    token_ready = await monitor.ensure_identity_healthy(identity_id)

    refreshed_identity = service.get(identity_id)
    if not refreshed_identity:
        raise HTTPException(status_code=404, detail=f"Identity {identity_id} not found after refresh")

    sync_success = False
    if token_ready and refreshed_identity.access_token is not None:
        sync_success = await user_manager.set_user_display_name(
            str(refreshed_identity.mxid),
            display_name,
            str(refreshed_identity.access_token),
        )

    if not sync_success and refreshed_identity.password_hash is not None:
        sync_success = await user_manager.update_display_name(
            str(refreshed_identity.mxid),
            display_name,
            str(refreshed_identity.password_hash),
        )
        if sync_success:
            await monitor.ensure_identity_healthy(identity_id)

    if not sync_success:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to sync Matrix profile display name for {identity_id}",
        )


def _sanitize_letta_name(name: str, remove_legacy_huly_prefix: bool) -> str:
    if remove_legacy_huly_prefix and name.startswith("Huly - "):
        cleaned = name[7:].strip()
        return cleaned if cleaned else name
    return name


async def _get_matrix_display_name(mxid: str, access_token: str) -> Optional[str]:
    homeserver_url = os.getenv("MATRIX_HOMESERVER_URL", "https://matrix.oculair.ca")
    url = f"{homeserver_url}/_matrix/client/v3/profile/{mxid}/displayname"
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                if response.status != 200:
                    return None
                payload = await response.json()
                value = payload.get("displayname")
                return str(value) if value is not None else None
    except Exception:
        return None


async def _provision_login(
    homeserver_url: str,
    localpart: str,
    password: str,
    retries: int = 1,
) -> Optional[str]:
    login_url = f"{homeserver_url}/_matrix/client/v3/login"
    login_data = {
        "type": "m.login.password",
        "identifier": {"type": "m.id.user", "user": localpart},
        "password": password,
    }

    for attempt in range(1, retries + 1):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(login_url, json=login_data) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        token = result.get("access_token")
                        return str(token) if token is not None else None
        except Exception as exc:
            logger.debug("Provision login failed (attempt %s/%s): %s", attempt, retries, exc)
        if attempt < retries:
            await asyncio.sleep(float(attempt))

    return None


async def _send_admin_password_reset_command(
    user_manager: MatrixUserManager,
    homeserver_url: str,
    localpart: str,
    password: str,
) -> bool:
    admin_room_id = os.getenv("MATRIX_ADMIN_ROOM_ID", "!jmP5PQ2G13I4VcIcUT:matrix.oculair.ca")
    command = f"!admin users reset-password {localpart} {password}"
    url = f"{homeserver_url}/_matrix/client/v3/rooms/{admin_room_id}/send/m.room.message/{int(time.time() * 1000)}"

    for token_attempt in range(1, 3):
        try:
            admin_token = await user_manager.get_admin_token()
        except Exception as exc:
            logger.warning("Failed to obtain admin token for provisioning reset (attempt %s): %s", token_attempt, exc)
            user_manager.clear_admin_token_cache()
            continue
        if not admin_token:
            user_manager.clear_admin_token_cache()
            continue

        headers = {
            "Authorization": f"Bearer {admin_token}",
            "Content-Type": "application/json",
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.put(
                    url,
                    headers=headers,
                    json={"msgtype": "m.text", "body": command},
                ) as response:
                    if response.status == 200:
                        return True
                    if response.status in (401, 403):
                        user_manager.clear_admin_token_cache()
                        continue
                    return False
        except Exception as exc:
            logger.warning("Provisioning reset command failed (attempt %s): %s", token_attempt, exc)
            user_manager.clear_admin_token_cache()

    return False


async def _reset_password_and_verify_login(
    user_manager: MatrixUserManager,
    homeserver_url: str,
    localpart: str,
    password: str,
    max_attempts: int = 3,
) -> Optional[str]:
    for attempt in range(1, max_attempts + 1):
        reset_ok = await _send_admin_password_reset_command(
            user_manager,
            homeserver_url,
            localpart,
            password,
        )
        if not reset_ok:
            continue

        access_token = await _provision_login(homeserver_url, localpart, password, retries=1)
        if access_token:
            return access_token

        if attempt < max_attempts:
            await asyncio.sleep(float(attempt))

    return None


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


@router.post("/identities/sync-names", response_model=IdentitySyncNamesResponse)
async def sync_identity_names(request: IdentitySyncNamesRequest):
    identity_service = get_identity_service()
    letta_service = LettaService()

    try:
        letta_agents = letta_service.list_agents(limit=request.limit)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to fetch Letta agents: {exc}") from exc

    from src.core.mapping_service import invalidate_cache
    from src.models.agent_mapping import AgentMappingDB

    mapping_db = AgentMappingDB()
    checked = 0
    missing_identity = 0
    mismatched = 0
    updated_identity = 0
    updated_matrix = 0
    updated_mapping = 0
    failed = 0
    changes: List[IdentityNameSyncDiff] = []

    for agent in letta_agents:
        agent_id_raw = getattr(agent, "id", None)
        agent_name_raw = getattr(agent, "name", None)
        if agent_id_raw is None or agent_name_raw is None:
            continue

        checked += 1
        agent_id = str(agent_id_raw)
        letta_name = str(agent_name_raw)
        desired_name = _sanitize_letta_name(letta_name, request.remove_legacy_huly_prefix)
        identity_id = f"letta_{agent_id}"
        identity = identity_service.get(identity_id)
        if identity is None:
            missing_identity += 1
            continue

        mxid = str(identity.mxid)
        identity_display_name = str(identity.display_name) if identity.display_name is not None else None
        matrix_display_name = await _get_matrix_display_name(mxid, str(identity.access_token))

        mapping = mapping_db.get_by_agent_id(agent_id)
        mapping_agent_name = str(mapping.agent_name) if mapping is not None and mapping.agent_name is not None else None

        needs_identity_update = identity_display_name != desired_name
        needs_matrix_update = matrix_display_name != desired_name
        needs_mapping_update = mapping_agent_name != desired_name

        if not (needs_identity_update or needs_matrix_update or needs_mapping_update):
            continue

        mismatched += 1
        diff = IdentityNameSyncDiff(
            agent_id=agent_id,
            identity_id=identity_id,
            mxid=mxid,
            letta_name=letta_name,
            desired_name=desired_name,
            identity_display_name=identity_display_name,
            matrix_display_name=matrix_display_name,
            mapping_agent_name=mapping_agent_name,
            needs_identity_update=needs_identity_update,
            needs_matrix_update=needs_matrix_update,
            needs_mapping_update=needs_mapping_update,
        )

        if not request.dry_run:
            if needs_identity_update and request.sync_identity_db:
                try:
                    updated = identity_service.update(identity_id, display_name=desired_name)
                    if updated is None:
                        raise RuntimeError("identity update returned None")
                    diff.applied_identity_update = True
                    updated_identity += 1
                except Exception as exc:
                    diff.errors.append(f"identity_update_failed: {exc}")

            if needs_matrix_update and request.sync_matrix_profile:
                try:
                    await _sync_identity_profile(identity_id, desired_name)
                    diff.applied_matrix_update = True
                    updated_matrix += 1
                except Exception as exc:
                    diff.errors.append(f"matrix_update_failed: {exc}")

            if needs_mapping_update and request.sync_agent_mapping:
                try:
                    if mapping is not None:
                        updated = mapping_db.update(agent_id, agent_name=desired_name)
                        if updated is None:
                            raise RuntimeError("mapping update returned None")
                        diff.applied_mapping_update = True
                        updated_mapping += 1
                        invalidate_cache()
                except Exception as exc:
                    diff.errors.append(f"mapping_update_failed: {exc}")

            if diff.errors:
                failed += 1

        changes.append(diff)

    return IdentitySyncNamesResponse(
        success=True,
        dry_run=request.dry_run,
        checked=checked,
        missing_identity=missing_identity,
        mismatched=mismatched,
        updated_identity=updated_identity,
        updated_matrix=updated_matrix,
        updated_mapping=updated_mapping,
        failed=failed,
        changes=changes,
    )


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


@router.post("/messages/send-as-identity", response_model=SendAsIdentityResponse)
async def send_as_identity(request: SendAsIdentityRequest):
    service = get_identity_service()
    identity = service.get(request.identity_id)
    if not identity:
        return SendAsIdentityResponse(
            success=False,
            identity_id=request.identity_id,
            room_id=request.room_id,
            error=f"Identity {request.identity_id} not found"
        )
    
    if not bool(identity.is_active):
        return SendAsIdentityResponse(
            success=False,
            identity_id=request.identity_id,
            room_id=request.room_id,
            error=f"Identity {request.identity_id} is inactive"
        )
    
    pool = get_identity_client_pool()
    event_id = await pool.send_message(
        identity_id=request.identity_id,
        room_id=request.room_id,
        message=request.message,
        msgtype=request.msgtype
    )
    
    if event_id:
        service.mark_used(request.identity_id)
        return SendAsIdentityResponse(
            success=True,
            event_id=event_id,
            identity_id=request.identity_id,
            room_id=request.room_id
        )
    
    return SendAsIdentityResponse(
        success=False,
        identity_id=request.identity_id,
        room_id=request.room_id,
        error="Failed to send message"
    )


@router.post("/messages/send-as-agent", response_model=SendAsIdentityResponse)
async def send_as_agent(request: SendAsAgentRequest):
    identity_id = f"letta_{request.agent_id}"
    
    service = get_identity_service()
    identity = service.get(identity_id)
    if not identity:
        return SendAsIdentityResponse(
            success=False,
            identity_id=identity_id,
            room_id=request.room_id,
            error=f"No identity found for agent {request.agent_id}"
        )
    
    pool = get_identity_client_pool()
    event_id = await pool.send_as_agent(
        agent_id=request.agent_id,
        room_id=request.room_id,
        message=request.message,
        msgtype=request.msgtype
    )
    
    if event_id:
        service.mark_used(identity_id)
        return SendAsIdentityResponse(
            success=True,
            event_id=event_id,
            identity_id=identity_id,
            room_id=request.room_id
        )
    
    return SendAsIdentityResponse(
        success=False,
        identity_id=identity_id,
        room_id=request.room_id,
        error="Failed to send message"
    )


@router.post("/messages/edit-as-agent", response_model=SendAsIdentityResponse)
async def edit_as_agent(request: EditAsAgentRequest):
    identity_id = f"letta_{request.agent_id}"

    service = get_identity_service()
    identity = service.get(identity_id)
    if not identity:
        return SendAsIdentityResponse(
            success=False,
            identity_id=identity_id,
            room_id=request.room_id,
            error=f"No identity found for agent {request.agent_id}",
        )

    pool = get_identity_client_pool()
    event_id = await pool.edit_as_agent(
        agent_id=request.agent_id,
        room_id=request.room_id,
        event_id=request.event_id,
        message=request.message,
        msgtype=request.msgtype,
    )

    if event_id:
        service.mark_used(identity_id)
        return SendAsIdentityResponse(
            success=True,
            event_id=event_id,
            identity_id=identity_id,
            room_id=request.room_id,
        )

    return SendAsIdentityResponse(
        success=False,
        identity_id=identity_id,
        room_id=request.room_id,
        error="Failed to edit message",
    )


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


from src.api.auth import verify_internal_key

internal_router = APIRouter(prefix="/api/v1/internal", tags=["internal"])

@internal_router.get("/identities/{identity_id}", response_model=FullIdentityResponse)
async def get_full_identity(identity_id: str, x_internal_key: str = Header(...)):
    verify_internal_key(x_internal_key)
    service = get_identity_service()
    identity = service.get(identity_id)
    if not identity:
        raise HTTPException(status_code=404, detail=f"Identity {identity_id} not found")
    return FullIdentityResponse.from_identity(identity)


@internal_router.get("/identities/by-mxid/{mxid:path}", response_model=FullIdentityResponse)
async def get_full_identity_by_mxid(mxid: str, x_internal_key: str = Header(...)):
    verify_internal_key(x_internal_key)
    decoded_mxid = unquote(mxid)
    service = get_identity_service()
    identity = service.get_by_mxid(decoded_mxid)
    if not identity:
        raise HTTPException(status_code=404, detail=f"Identity with MXID {decoded_mxid} not found")
    return FullIdentityResponse.from_identity(identity)


@internal_router.get("/identities", response_model=FullIdentityListResponse)
async def list_full_identities(
    x_internal_key: str = Header(...),
    identity_type: Optional[str] = None,
    active_only: bool = True
):
    verify_internal_key(x_internal_key)
    service = get_identity_service()
    
    if identity_type is not None:
        identities = service.get_by_type(identity_type)
        if active_only:
            identities = [i for i in identities if bool(i.is_active)]
    else:
        identities = service.get_all(active_only=active_only)
    
    return FullIdentityListResponse(
        success=True,
        count=len(identities),
        identities=[FullIdentityResponse.from_identity(i) for i in identities]
    )


@internal_router.post("/identities/provision", response_model=IdentityProvisionResponse)
async def provision_identity(
    request: IdentityProvisionRequest,
    x_internal_key: str = Header(...)
):
    verify_internal_key(x_internal_key)
    
    import base64
    import hashlib
    from src.core.user_manager import MatrixUserManager
    
    homeserver_url = os.getenv("MATRIX_HOMESERVER_URL", "https://matrix.oculair.ca")
    server_name = os.getenv("MATRIX_SERVER_NAME", "matrix.oculair.ca")
    admin_username = os.getenv("MATRIX_ADMIN_USERNAME", "admin")
    admin_password = os.getenv("MATRIX_ADMIN_PASSWORD", "")
    password_secret = os.getenv("MATRIX_PASSWORD_SECRET", "mcp_identity_bridge_2024")
    
    import re
    encoded = base64.b64encode(request.directory.encode()).decode()
    encoded = encoded.rstrip("=").replace("+", "-").replace("/", "_")
    identity_id = f"{request.identity_type}_v2_{encoded}"
    
    project_name = request.directory.rstrip("/").split("/")[-1] or "project"
    project_name_clean = re.sub(r"[^a-z0-9_]", "_", project_name.lower())
    
    if request.identity_type == "opencode":
        localpart = f"oc_{project_name_clean}_v2"
        display_prefix = "OpenCode"
    else:
        localpart = f"cc_{project_name_clean}"
        display_prefix = "Claude Code"
    
    if request.display_name:
        display_name = request.display_name
    else:
        formatted = " ".join(
            word.capitalize() for word in project_name.replace("-", " ").replace("_", " ").split()
        )
        display_name = f"{display_prefix}: {formatted}"
    
    mxid = f"@{localpart}:{server_name}"
    
    service = get_identity_service()
    existing = service.get(identity_id)
    if existing is not None and existing.access_token is not None:
        return IdentityProvisionResponse(
            success=True,
            identity_id=identity_id,
            mxid=str(existing.mxid),
            access_token=str(existing.access_token),
            display_name=str(existing.display_name) if existing.display_name is not None else display_name
        )
    
    existing_mxid = service.get_by_mxid(mxid)
    if existing_mxid is not None and existing_mxid.access_token is not None:
        return IdentityProvisionResponse(
            success=True,
            identity_id=str(existing_mxid.id),
            mxid=str(existing_mxid.mxid),
            access_token=str(existing_mxid.access_token),
            display_name=str(existing_mxid.display_name) if existing_mxid.display_name is not None else display_name
        )
    
    hash_input = f"{localpart}:{password_secret}"
    hash_val = hashlib.sha256(hash_input.encode()).hexdigest()[:24]
    password = f"MCP_{hash_val}"
    
    user_manager = MatrixUserManager(homeserver_url, admin_username, admin_password)
    
    access_token = await _provision_login(homeserver_url, localpart, password, retries=3)
    if access_token is None:
        user_state = await user_manager.check_user_exists(localpart)

        if user_state == "not_found":
            created = await user_manager.create_matrix_user(localpart, password, display_name)
            if not created:
                return IdentityProvisionResponse(
                    success=False,
                    identity_id=identity_id,
                    mxid=mxid,
                    access_token="",
                    display_name=display_name,
                    error=f"Failed to create Matrix user {mxid}",
                )
            access_token = await _provision_login(homeserver_url, localpart, password, retries=3)
        else:
            access_token = await _provision_login(homeserver_url, localpart, password, retries=3)

        if access_token is None:
            access_token = await _reset_password_and_verify_login(
                user_manager,
                homeserver_url,
                localpart,
                password,
                max_attempts=3,
            )

    if access_token is None:
        return IdentityProvisionResponse(
            success=False,
            identity_id=identity_id,
            mxid=mxid,
            access_token="",
            display_name=display_name,
            error=f"Failed to login/provision Matrix user {mxid} after retries",
        )
    
    identity = service.create(
        identity_id=identity_id,
        identity_type=request.identity_type,
        mxid=mxid,
        access_token=access_token,
        display_name=display_name,
        password_hash=password
    )
    
    return IdentityProvisionResponse(
        success=True,
        identity_id=str(identity.id),
        mxid=str(identity.mxid),
        access_token=access_token,
        display_name=display_name
    )
