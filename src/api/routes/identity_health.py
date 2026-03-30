"""
Identity health endpoint — checks token validity, name consistency,
DM room integrity, and Letta coverage.
"""

import asyncio
import logging
import time
from typing import Dict, List, Optional

from fastapi import APIRouter

from src.api.schemas.identity import (
    IdentityHealthResponse,
    IdentityHealthRecord,
    IdentityHealthCoverage,
)
from src.core.identity_health_monitor import get_identity_token_health_monitor
from src.core.identity_storage import get_identity_service, get_dm_room_service
from src.core.mapping_service import get_all_mappings
from src.letta.client import LettaService

from ._identity_helpers import (
    _get_identity_http_session,
    _get_matrix_display_name,
    _is_valid_mxid,
)

logger = logging.getLogger(__name__)

health_router = APIRouter()


@health_router.get("/identities/health", response_model=IdentityHealthResponse)
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
