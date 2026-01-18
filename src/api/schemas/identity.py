from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


class IdentityCreate(BaseModel):
    id: str = Field(..., description="Unique identity ID (e.g., letta_<agent_id>)")
    identity_type: str = Field(..., description="Type: letta, opencode, custom")
    mxid: str = Field(..., description="Matrix user ID (@user:domain)")
    access_token: str = Field(..., description="Matrix access token")
    display_name: Optional[str] = Field(None, description="Display name")
    avatar_url: Optional[str] = Field(None, description="Avatar mxc:// URL")
    password_hash: Optional[str] = Field(None, description="Password hash for re-login")
    device_id: Optional[str] = Field(None, description="Matrix device ID")


class IdentityUpdate(BaseModel):
    display_name: Optional[str] = None
    avatar_url: Optional[str] = None
    access_token: Optional[str] = None
    is_active: Optional[bool] = None


class IdentityResponse(BaseModel):
    id: str
    identity_type: str
    mxid: str
    display_name: Optional[str]
    avatar_url: Optional[str]
    device_id: Optional[str]
    created_at: Optional[int] = Field(None, description="Unix timestamp ms")
    updated_at: Optional[int] = Field(None, description="Unix timestamp ms")
    last_used_at: Optional[int] = Field(None, description="Unix timestamp ms")
    is_active: bool

    class Config:
        from_attributes = True

    @classmethod
    def from_identity(cls, identity) -> "IdentityResponse":
        return cls(
            id=identity.id,
            identity_type=identity.identity_type,
            mxid=identity.mxid,
            display_name=identity.display_name,
            avatar_url=identity.avatar_url,
            device_id=identity.device_id,
            created_at=int(identity.created_at.timestamp() * 1000) if identity.created_at else None,
            updated_at=int(identity.updated_at.timestamp() * 1000) if identity.updated_at else None,
            last_used_at=int(identity.last_used_at.timestamp() * 1000) if identity.last_used_at else None,
            is_active=identity.is_active
        )


class FullIdentityResponse(BaseModel):
    """Internal response that includes access_token - only for trusted services"""
    id: str
    identity_type: str
    mxid: str
    display_name: Optional[str]
    avatar_url: Optional[str]
    access_token: str
    password_hash: Optional[str]
    device_id: Optional[str]
    created_at: Optional[int] = Field(None, description="Unix timestamp ms")
    updated_at: Optional[int] = Field(None, description="Unix timestamp ms")
    last_used_at: Optional[int] = Field(None, description="Unix timestamp ms")
    is_active: bool

    class Config:
        from_attributes = True

    @classmethod
    def from_identity(cls, identity) -> "FullIdentityResponse":
        return cls(
            id=identity.id,
            identity_type=identity.identity_type,
            mxid=identity.mxid,
            display_name=identity.display_name,
            avatar_url=identity.avatar_url,
            access_token=identity.access_token,
            password_hash=identity.password_hash,
            device_id=identity.device_id,
            created_at=int(identity.created_at.timestamp() * 1000) if identity.created_at else None,
            updated_at=int(identity.updated_at.timestamp() * 1000) if identity.updated_at else None,
            last_used_at=int(identity.last_used_at.timestamp() * 1000) if identity.last_used_at else None,
            is_active=identity.is_active
        )


class FullIdentityListResponse(BaseModel):
    """Internal response with full identities including access tokens"""
    success: bool
    count: int
    identities: List[FullIdentityResponse]


class IdentityListResponse(BaseModel):
    success: bool
    count: int
    identities: List[IdentityResponse]


class DMRoomCreate(BaseModel):
    room_id: str = Field(..., description="Matrix room ID")
    mxid1: str = Field(..., description="First participant MXID")
    mxid2: str = Field(..., description="Second participant MXID")


class DMRoomResponse(BaseModel):
    room_id: str
    participant_1: str
    participant_2: str
    key: str
    created_at: Optional[int] = Field(None, description="Unix timestamp ms")
    last_activity_at: Optional[int] = Field(None, description="Unix timestamp ms")

    class Config:
        from_attributes = True

    @classmethod
    def from_dm_room(cls, dm_room) -> "DMRoomResponse":
        from src.models.identity import DMRoom as DMRoomModel
        return cls(
            room_id=dm_room.room_id,
            participant_1=dm_room.participant_1,
            participant_2=dm_room.participant_2,
            key=DMRoomModel.create_key(dm_room.participant_1, dm_room.participant_2),
            created_at=int(dm_room.created_at.timestamp() * 1000) if dm_room.created_at else None,
            last_activity_at=int(dm_room.last_activity_at.timestamp() * 1000) if dm_room.last_activity_at else None
        )


class DMRoomListResponse(BaseModel):
    success: bool
    count: int
    dm_rooms: List[DMRoomResponse]


class SendAsIdentityRequest(BaseModel):
    identity_id: str = Field(..., description="Identity ID to send as")
    room_id: str = Field(..., description="Target Matrix room ID")
    message: str = Field(..., description="Message content")
    msgtype: str = Field("m.text", description="Message type: m.text, m.notice, m.emote")


class SendAsAgentRequest(BaseModel):
    agent_id: str = Field(..., description="Letta agent ID")
    room_id: str = Field(..., description="Target Matrix room ID")
    message: str = Field(..., description="Message content")
    msgtype: str = Field("m.text", description="Message type")


class SendAsIdentityResponse(BaseModel):
    success: bool
    event_id: Optional[str] = None
    identity_id: str
    room_id: str
    error: Optional[str] = None


class IdentityProvisionRequest(BaseModel):
    """Request to provision a new Matrix user and create identity"""
    directory: str = Field(..., description="Working directory path (e.g., /opt/stacks/my-project)")
    identity_type: str = Field("claudecode", description="Type: claudecode, opencode, custom")
    display_name: Optional[str] = Field(None, description="Display name override (auto-generated if not provided)")


class IdentityProvisionResponse(BaseModel):
    """Response with provisioned identity including access token"""
    success: bool
    identity_id: str
    mxid: str
    access_token: str
    display_name: str
    error: Optional[str] = None
