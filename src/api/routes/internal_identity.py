"""
Internal identity endpoints — full-detail reads and provisioning.
Protected by X-Internal-Key header.
"""

import base64
import logging
import os
import re
from typing import Optional

from fastapi import APIRouter, Header, HTTPException

from src.api.auth import verify_internal_key
from src.api.schemas.identity import (
    FullIdentityResponse,
    FullIdentityListResponse,
    IdentityProvisionRequest,
    IdentityProvisionResponse,
)
from src.core.identity_storage import get_identity_service
from src.core.user_manager import MatrixUserManager
from src.utils.password import generate_deterministic_identity_password

from ._identity_helpers import (
    _get_identity_http_session,
    _provision_login,
    _reset_password_and_verify_login,
)

logger = logging.getLogger(__name__)

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
    from urllib.parse import unquote
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

    homeserver_url = os.getenv("MATRIX_HOMESERVER_URL", "https://matrix.oculair.ca")
    server_name = os.getenv("MATRIX_SERVER_NAME", "matrix.oculair.ca")
    admin_username = os.getenv("MATRIX_ADMIN_USERNAME", "admin")
    admin_password = os.getenv("MATRIX_ADMIN_PASSWORD", "")
    password_secret = os.getenv("MATRIX_PASSWORD_SECRET", "mcp_identity_bridge_2024")

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
