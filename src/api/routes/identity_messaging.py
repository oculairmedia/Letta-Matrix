"""
Identity messaging endpoints — send/edit messages as identity or agent.
"""

import logging

from fastapi import APIRouter

from src.api.schemas.identity import (
    SendAsIdentityRequest,
    SendAsIdentityResponse,
    SendAsAgentRequest,
    EditAsAgentRequest,
)
from src.core.identity_storage import get_identity_service
from src.matrix.identity_client_pool import get_identity_client_pool

logger = logging.getLogger(__name__)

messaging_router = APIRouter()


@messaging_router.post("/messages/send-as-identity", response_model=SendAsIdentityResponse)
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


@messaging_router.post("/messages/send-as-agent", response_model=SendAsIdentityResponse)
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


@messaging_router.post("/messages/edit-as-agent", response_model=SendAsIdentityResponse)
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
