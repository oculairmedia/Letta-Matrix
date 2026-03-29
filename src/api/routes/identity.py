from fastapi import APIRouter, Header, HTTPException, status
from contextlib import asynccontextmanager
from typing import AsyncIterator, Dict, List, Optional
from urllib.parse import unquote
import logging
import os
import aiohttp
import asyncio
import time
import re

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
    DMRoomNameReconcileRequest,
    DMRoomNameReconcileResponse,
    DMRoomNameReconcileDiff,
    SendAsIdentityRequest,
    SendAsIdentityResponse,
    SendAsAgentRequest,
    EditAsAgentRequest,
    IdentityProvisionRequest,
    IdentityProvisionResponse,
    IdentitySyncNamesRequest,
    IdentitySyncNamesResponse,
    IdentityNameSyncDiff,
    IdentityHealthResponse,
    IdentityHealthRecord,
    IdentityHealthCoverage,
)
from src.core.identity_health_monitor import get_identity_token_health_monitor
from src.core.identity_storage import get_identity_service, get_dm_room_service
from src.core.user_manager import MatrixUserManager
from src.matrix.identity_client_pool import get_identity_client_pool
from src.letta.client import LettaService
from src.core.mapping_service import get_all_mappings
from src.utils.password import generate_deterministic_identity_password

logger = logging.getLogger(__name__)


_IDENTITY_HTTP_SESSION: Optional[aiohttp.ClientSession] = None
_IDENTITY_HTTP_SESSION_LOCK = asyncio.Lock()


async def _get_identity_http_session() -> aiohttp.ClientSession:
    global _IDENTITY_HTTP_SESSION
    if _IDENTITY_HTTP_SESSION is not None and not _IDENTITY_HTTP_SESSION.closed:
        return _IDENTITY_HTTP_SESSION

    async with _IDENTITY_HTTP_SESSION_LOCK:
        if _IDENTITY_HTTP_SESSION is not None and not _IDENTITY_HTTP_SESSION.closed:
            return _IDENTITY_HTTP_SESSION
        connector = aiohttp.TCPConnector(
            limit=100,
            limit_per_host=50,
            ttl_dns_cache=300,
            keepalive_timeout=30,
        )
        _IDENTITY_HTTP_SESSION = aiohttp.ClientSession(connector=connector)
        return _IDENTITY_HTTP_SESSION


@asynccontextmanager
async def _identity_http_session_scope(
    session: Optional[aiohttp.ClientSession] = None,
) -> AsyncIterator[aiohttp.ClientSession]:
    if session is not None:
        yield session
        return
    pooled_session = await _get_identity_http_session()
    yield pooled_session

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


async def _get_matrix_display_name(
    mxid: str,
    access_token: str,
    session: Optional[aiohttp.ClientSession] = None,
) -> Optional[str]:
    homeserver_url = os.getenv("MATRIX_HOMESERVER_URL", "https://matrix.oculair.ca")
    url = f"{homeserver_url}/_matrix/client/v3/profile/{mxid}/displayname"
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        async with _identity_http_session_scope(session) as active_session:
            async with active_session.get(url, headers=headers) as response:
                if response.status != 200:
                    return None
                payload = await response.json()
                value = payload.get("displayname")
                return str(value) if value is not None else None
    except Exception:
        return None


async def _get_room_name(
    room_id: str,
    access_token: str,
    session: Optional[aiohttp.ClientSession] = None,
) -> Optional[str]:
    homeserver_url = os.getenv("MATRIX_HOMESERVER_URL", "https://matrix.oculair.ca")
    url = f"{homeserver_url}/_matrix/client/v3/rooms/{room_id}/state/m.room.name"
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        async with _identity_http_session_scope(session) as active_session:
            async with active_session.get(url, headers=headers) as response:
                if response.status != 200:
                    return None
                payload = await response.json()
                value = payload.get("name")
                return str(value) if value is not None else None
    except Exception:
        return None


async def _provision_login(
    homeserver_url: str,
    localpart: str,
    password: str,
    retries: int = 1,
    session: Optional[aiohttp.ClientSession] = None,
) -> Optional[str]:
    login_url = f"{homeserver_url}/_matrix/client/v3/login"
    login_data = {
        "type": "m.login.password",
        "identifier": {"type": "m.id.user", "user": localpart},
        "password": password,
    }

    for attempt in range(1, retries + 1):
        try:
            async with _identity_http_session_scope(session) as active_session:
                async with active_session.post(login_url, json=login_data) as resp:
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
    session: Optional[aiohttp.ClientSession] = None,
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
            async with _identity_http_session_scope(session) as active_session:
                async with active_session.put(
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
    session: Optional[aiohttp.ClientSession] = None,
) -> Optional[str]:
    for attempt in range(1, max_attempts + 1):
        reset_ok = await _send_admin_password_reset_command(
            user_manager,
            homeserver_url,
            localpart,
            password,
            session=session,
        )
        if not reset_ok:
            continue

        access_token = await _provision_login(
            homeserver_url,
            localpart,
            password,
            retries=1,
            session=session,
        )
        if access_token:
            return access_token

        if attempt < max_attempts:
            await asyncio.sleep(float(attempt))

    return None


def _is_valid_mxid(mxid: str) -> bool:
    return bool(re.match(r"^@[^:]+:[^:]+$", mxid))


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


@router.get("/identities/health", response_model=IdentityHealthResponse)
async def identity_health(identity_type: Optional[str] = None):
    identity_service = get_identity_service()
    dm_room_service = get_dm_room_service()
    monitor = get_identity_token_health_monitor()
    letta_service = LettaService()

    identities = identity_service.get_all(active_only=False)
    if identity_type is not None:
        identities = [i for i in identities if str(i.identity_type) == identity_type]

    try:
        letta_agents = letta_service.list_agents(limit=1000)
    except Exception:
        letta_agents = []

    letta_name_by_agent_id: dict[str, str] = {}
    for agent in letta_agents:
        agent_id_raw = getattr(agent, "id", None)
        agent_name_raw = getattr(agent, "name", None)
        if agent_id_raw is None or agent_name_raw is None:
            continue
        letta_name_by_agent_id[str(agent_id_raw)] = str(agent_name_raw)

    mappings = get_all_mappings(include_removed=False)
    all_dm_rooms = dm_room_service.get_all()
    known_mxids = {str(i.mxid) for i in identities}

    checked = 0
    healthy = 0
    degraded = 0
    critical = 0
    token_invalid = 0
    name_mismatches = 0
    password_mismatches = 0
    invalid_mxid = 0
    invalid_dm_rooms = 0

    letta_identity_ids = {
        str(i.id)
        for i in identities
        if str(i.identity_type) == "letta" and str(i.id).startswith("letta_")
    }
    expected_letta_identity_ids = {f"letta_{agent_id}" for agent_id in letta_name_by_agent_id}
    missing_letta_identities = sorted(list(expected_letta_identity_ids - letta_identity_ids))

    records: List[IdentityHealthRecord] = []
    actionable_agents: List[dict[str, object]] = []
    now_ts = int(time.time())

    identities_with_tokens = [
        identity
        for identity in identities
        if identity.access_token is not None and str(identity.access_token)
    ]

    token_validation_by_identity: Dict[str, tuple[bool, Optional[str]]] = {}
    if identities_with_tokens:
        token_results = await asyncio.gather(
            *(monitor._validate_identity_token(identity) for identity in identities_with_tokens),
            return_exceptions=True,
        )
        for identity, result in zip(identities_with_tokens, token_results):
            identity_id = str(identity.id)
            if isinstance(result, BaseException):
                token_validation_by_identity[identity_id] = (False, str(result))
            else:
                valid = bool(result)
                token_validation_by_identity[identity_id] = (
                    valid,
                    None if valid else "token_invalid",
                )

    matrix_display_name_by_identity: Dict[str, Optional[str]] = {}
    if identities_with_tokens:
        shared_session = await _get_identity_http_session()
        display_name_results = await asyncio.gather(
            *(
                _get_matrix_display_name(
                    str(identity.mxid),
                    str(identity.access_token),
                    session=shared_session,
                )
                for identity in identities_with_tokens
            ),
            return_exceptions=True,
        )
        for identity, result in zip(identities_with_tokens, display_name_results):
            identity_id = str(identity.id)
            if isinstance(result, BaseException):
                matrix_display_name_by_identity[identity_id] = None
            else:
                matrix_display_name_by_identity[identity_id] = (
                    result if isinstance(result, str) else None
                )

    for identity in identities:
        checked += 1
        identity_id = str(identity.id)
        identity_type_value = str(identity.identity_type)
        mxid = str(identity.mxid)
        identity_display_name = str(identity.display_name) if identity.display_name is not None else None
        access_token = str(identity.access_token) if identity.access_token is not None else ""
        password_hash = str(identity.password_hash) if identity.password_hash is not None else None

        issues: List[str] = []

        mxid_valid = _is_valid_mxid(mxid)
        if not mxid_valid:
            invalid_mxid += 1
            issues.append("invalid_mxid_format")

        token_valid = False
        token_error: Optional[str] = None
        if access_token:
            token_valid, token_error = token_validation_by_identity.get(
                identity_id,
                (False, "token_invalid"),
            )
        else:
            token_error = "missing_access_token"
        if not token_valid:
            token_invalid += 1
            issues.append("token_invalid")

        matrix_display_name: Optional[str] = matrix_display_name_by_identity.get(identity_id)

        mapping_agent_name: Optional[str] = None
        mapping_password: Optional[str] = None
        identity_mapping_name_match: Optional[bool] = None
        password_consistent: Optional[bool] = None
        identity_letta_name_match: Optional[bool] = None
        letta_display_name: Optional[str] = None

        if identity_id.startswith("letta_"):
            agent_id = identity_id[6:]
            mapping = mappings.get(agent_id)
            if mapping is not None:
                mapping_agent_name = str(mapping.get("agent_name") or "") or None
                mapping_password = str(mapping.get("matrix_password") or "") or None
                if identity_display_name is not None and mapping_agent_name is not None:
                    identity_mapping_name_match = identity_display_name == mapping_agent_name
                if password_hash is not None and mapping_password is not None:
                    password_consistent = password_hash == mapping_password
                    if not password_consistent:
                        password_mismatches += 1
                        issues.append("password_mismatch_identity_vs_mapping")

            letta_display_name = letta_name_by_agent_id.get(agent_id)
            if identity_display_name is not None and letta_display_name is not None:
                identity_letta_name_match = identity_display_name == letta_display_name

        identity_matrix_name_match: Optional[bool] = None
        if identity_display_name is not None and matrix_display_name is not None:
            identity_matrix_name_match = identity_display_name == matrix_display_name

        if identity_matrix_name_match is False:
            name_mismatches += 1
            issues.append("name_mismatch_identity_vs_matrix")
        if identity_letta_name_match is False:
            name_mismatches += 1
            issues.append("name_mismatch_identity_vs_letta")
        if identity_mapping_name_match is False:
            name_mismatches += 1
            issues.append("name_mismatch_identity_vs_mapping")

        dm_for_user = [
            room
            for room in all_dm_rooms
            if str(room.participant_1) == mxid or str(room.participant_2) == mxid
        ]
        dm_rooms_valid = True
        for room in dm_for_user:
            room_id = str(room.room_id)
            p1 = str(room.participant_1)
            p2 = str(room.participant_2)
            room_id_valid = room_id.startswith("!") and ":" in room_id
            participants_exist = p1 in known_mxids and p2 in known_mxids
            if not room_id_valid or not participants_exist:
                dm_rooms_valid = False
                break

        if not dm_rooms_valid:
            invalid_dm_rooms += 1
            issues.append("invalid_dm_room_reference")

        if not issues:
            healthy += 1
        elif "invalid_mxid_format" in issues or ("token_invalid" in issues and password_hash is None):
            critical += 1
        else:
            degraded += 1

        if issues:
            actionable_agents.append(
                {
                    "agent_id": identity_id[6:] if identity_id.startswith("letta_") else None,
                    "identity_id": identity_id,
                    "mxid": mxid,
                    "issues": issues,
                }
            )

        records.append(
            IdentityHealthRecord(
                identity_id=identity_id,
                identity_type=identity_type_value,
                mxid=mxid,
                is_active=bool(identity.is_active),
                token_valid=token_valid,
                token_checked_at=now_ts,
                token_error=token_error,
                identity_display_name=identity_display_name,
                matrix_display_name=matrix_display_name,
                letta_display_name=letta_display_name,
                mapping_agent_name=mapping_agent_name,
                identity_matrix_name_match=identity_matrix_name_match,
                identity_letta_name_match=identity_letta_name_match,
                identity_mapping_name_match=identity_mapping_name_match,
                password_consistent=password_consistent,
                mxid_valid=mxid_valid,
                dm_rooms_count=len(dm_for_user),
                dm_rooms_valid=dm_rooms_valid,
                issues=issues,
            )
        )

    coverage = IdentityHealthCoverage(
        letta_agents_total=len(letta_name_by_agent_id),
        letta_identities_total=len(letta_identity_ids),
        missing_letta_identities=missing_letta_identities,
    )

    return {
        "success": True,
        "checked": checked,
        "healthy": healthy,
        "degraded": degraded,
        "critical": critical,
        "coverage_percentage": (healthy / checked * 100.0) if checked else 100.0,
        "last_reconciliation_at": now_ts,
        "stale_token_count": token_invalid,
        "name_mismatch_count": name_mismatches,
        "token_invalid": token_invalid,
        "name_mismatches": name_mismatches,
        "password_mismatches": password_mismatches,
        "invalid_mxid": invalid_mxid,
        "invalid_dm_rooms": invalid_dm_rooms,
        "coverage": coverage,
        "actionable_agents": actionable_agents,
        "records": records,
    }


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
    
    password = generate_deterministic_identity_password(localpart, password_secret)

    user_manager = MatrixUserManager(homeserver_url, admin_username, admin_password)
    shared_session = await _get_identity_http_session()

    access_token = await _provision_login(
        homeserver_url,
        localpart,
        password,
        retries=3,
        session=shared_session,
    )
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
            access_token = await _provision_login(
                homeserver_url,
                localpart,
                password,
                retries=3,
                session=shared_session,
            )
        else:
            access_token = await _provision_login(
                homeserver_url,
                localpart,
                password,
                retries=3,
                session=shared_session,
            )

        if access_token is None:
            access_token = await _reset_password_and_verify_login(
                user_manager,
                homeserver_url,
                localpart,
                password,
                max_attempts=3,
                session=shared_session,
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
