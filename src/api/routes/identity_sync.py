"""
Identity name synchronisation endpoint — reconciles display names
across Letta, identity DB, Matrix profiles, and agent mappings.
"""

import logging
from typing import List

from fastapi import APIRouter, HTTPException

from src.api.schemas.identity import (
    IdentitySyncNamesRequest,
    IdentitySyncNamesResponse,
    IdentityNameSyncDiff,
)
from src.core.identity_storage import get_identity_service
from src.letta.client import LettaService

from ._identity_helpers import (
    _get_matrix_display_name,
    _sanitize_letta_name,
    _sync_identity_profile,
)

logger = logging.getLogger(__name__)

sync_router = APIRouter()


@sync_router.post("/identities/sync-names", response_model=IdentitySyncNamesResponse)
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
