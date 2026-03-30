from typing import List, Optional

from fastapi import APIRouter, Header, Request
from pydantic import BaseModel

from src.core.document_outline_index import get_outline_overview, list_outline_records


router = APIRouter(prefix="", tags=["messaging"])


class LoginRequest(BaseModel):
    homeserver: str
    user_id: str
    password: str
    device_name: Optional[str] = "matrix_api"


class LoginResponse(BaseModel):
    success: bool
    access_token: Optional[str] = None
    device_id: Optional[str] = None
    user_id: Optional[str] = None
    message: str


class SendMessageRequest(BaseModel):
    room_id: str
    message: str
    access_token: str
    homeserver: str


class SendMessageResponse(BaseModel):
    success: bool
    event_id: Optional[str] = None
    message: str


class GetMessagesRequest(BaseModel):
    room_id: str
    access_token: str
    homeserver: str
    limit: Optional[int] = 5


class MatrixMessage(BaseModel):
    sender: str
    body: str
    timestamp: int
    formatted_time: str
    event_id: str


class GetMessagesResponse(BaseModel):
    success: bool
    messages: List[MatrixMessage] = []
    message: str


class RoomInfo(BaseModel):
    room_id: str
    room_name: str


class ListRoomsResponse(BaseModel):
    success: bool
    rooms: List[RoomInfo] = []
    message: str


class DocumentSection(BaseModel):
    order: int
    level: int
    title: str
    line: Optional[int] = None


class DocumentCitation(BaseModel):
    chunk_id: str
    score: float
    excerpt: str
    line: Optional[int] = None
    source_coordinate: Optional[str] = None


class DocumentClaim(BaseModel):
    claim: str
    citations: List[DocumentCitation] = []


class DocumentOutlineRecord(BaseModel):
    document_id: str
    filename: str
    room_id: str
    document_type: str
    key_topics: List[str] = []
    has_heading_signals: bool
    graceful_fallback_used: bool
    sections: List[DocumentSection] = []
    ingested_at: Optional[str] = None


class DocumentOutlineResponse(BaseModel):
    success: bool
    outlines: List[DocumentOutlineRecord] = []
    message: str


class DocumentOverviewRecord(DocumentOutlineRecord):
    char_count: int = 0
    section_count: int = 0
    citations: List[DocumentCitation] = []
    claims: List[DocumentClaim] = []
    why_this_answer: Optional[str] = None


class DocumentOverviewResponse(BaseModel):
    success: bool
    overview: Optional[DocumentOverviewRecord] = None
    message: str


def get_matrix_client(request: Request):
    return request.app.state.matrix_client


@router.post("/login", response_model=LoginResponse)
async def login(request: LoginRequest, raw_request: Request):
    matrix_client = get_matrix_client(raw_request)
    result = await matrix_client.login(
        homeserver=request.homeserver,
        user_id=request.user_id,
        password=request.password,
        device_name=request.device_name,
    )
    return result


@router.get("/login/auto", response_model=LoginResponse)
async def auto_login(raw_request: Request):
    matrix_client = get_matrix_client(raw_request)
    result = await matrix_client.login()
    return result


@router.post("/messages/send", response_model=SendMessageResponse)
async def send_message(request: SendMessageRequest, raw_request: Request):
    matrix_client = get_matrix_client(raw_request)
    result = await matrix_client.send_message(
        homeserver=request.homeserver,
        access_token=request.access_token,
        room_id=request.room_id,
        message=request.message,
    )
    return result


@router.post("/messages/get", response_model=GetMessagesResponse)
async def get_messages(request: GetMessagesRequest, raw_request: Request):
    matrix_client = get_matrix_client(raw_request)
    result = await matrix_client.get_messages(
        homeserver=request.homeserver,
        access_token=request.access_token,
        room_id=request.room_id,
        limit=request.limit,
    )
    return result


@router.get("/rooms/list")
async def list_rooms(homeserver: str, raw_request: Request, access_token: str = Header(..., alias="X-Access-Token")):
    matrix_client = get_matrix_client(raw_request)
    result = await matrix_client.list_rooms(homeserver, access_token)
    return result


@router.get("/messages/recent")
async def get_recent_messages(homeserver: str, raw_request: Request, limit: int = 10, access_token: str = Header(..., alias="X-Access-Token")):
    try:
        matrix_client = get_matrix_client(raw_request)
        rooms_result = await matrix_client.list_rooms(homeserver, access_token)
        if not rooms_result.success:
            return {"success": False, "message": f"Failed to get rooms: {rooms_result.message}"}

        all_messages = []
        per_room_limit = limit if limit > 0 else 50

        for room in rooms_result.rooms:
            messages_result = await matrix_client.get_messages(
                homeserver,
                access_token,
                room.room_id,
                limit=per_room_limit,
            )

            if messages_result.success:
                for msg in messages_result.messages:
                    message_with_room = {
                        "room_id": room.room_id,
                        "room_name": room.room_name,
                        "sender": msg.sender,
                        "body": msg.body,
                        "timestamp": msg.timestamp,
                        "formatted_time": msg.formatted_time,
                        "event_id": msg.event_id,
                    }
                    all_messages.append(message_with_room)

        all_messages.sort(key=lambda x: x["timestamp"], reverse=True)
        recent_messages = all_messages[:limit]

        return {
            "success": True,
            "messages": recent_messages,
            "total_found": len(all_messages),
            "limit": limit,
            "message": f"Retrieved {len(recent_messages)} most recent messages from {len(rooms_result.rooms)} rooms",
        }

    except Exception as e:
        return {"success": False, "message": f"Error getting recent messages: {str(e)}"}


@router.get("/documents/outline", response_model=DocumentOutlineResponse)
async def get_document_outline(
    room_id: Optional[str] = None,
    filename: Optional[str] = None,
    document_id: Optional[str] = None,
):
    records = list_outline_records(room_id=room_id, filename=filename, document_id=document_id)
    if not records:
        return DocumentOutlineResponse(success=True, outlines=[], message="No document outlines found")

    outlines = [
        DocumentOutlineRecord(
            document_id=str(record.get("document_id", "")),
            filename=str(record.get("filename", "")),
            room_id=str(record.get("room_id", "")),
            document_type=str(record.get("document_type", "text_document")),
            key_topics=list(record.get("key_topics", [])),
            has_heading_signals=bool(record.get("has_heading_signals", False)),
            graceful_fallback_used=not bool(record.get("has_heading_signals", False)),
            sections=[DocumentSection(**section) for section in record.get("sections", [])],
            ingested_at=record.get("ingested_at"),
        )
        for record in records
    ]
    return DocumentOutlineResponse(success=True, outlines=outlines, message=f"Found {len(outlines)} outlines")


@router.get("/documents/overview", response_model=DocumentOverviewResponse)
async def whats_in_document(
    room_id: Optional[str] = None,
    filename: Optional[str] = None,
    document_id: Optional[str] = None,
):
    overview = get_outline_overview(room_id=room_id, filename=filename, document_id=document_id)
    if not overview:
        return DocumentOverviewResponse(success=True, overview=None, message="No matching document overview found")

    return DocumentOverviewResponse(
        success=True,
        overview=DocumentOverviewRecord(
            document_id=str(overview.get("document_id", "")),
            filename=str(overview.get("filename", "")),
            room_id=str(overview.get("room_id", "")),
            document_type=str(overview.get("document_type", "text_document")),
            key_topics=list(overview.get("key_topics", [])),
            char_count=int(overview.get("char_count", 0)),
            section_count=int(overview.get("section_count", 0)),
            has_heading_signals=bool(overview.get("has_heading_signals", False)),
            graceful_fallback_used=bool(overview.get("graceful_fallback_used", False)),
            sections=[DocumentSection(**section) for section in overview.get("sections", [])],
            citations=[DocumentCitation(**citation) for citation in overview.get("citations", [])],
            claims=[
                DocumentClaim(
                    claim=str(claim.get("claim", "")),
                    citations=[DocumentCitation(**citation) for citation in claim.get("citations", [])],
                )
                for claim in overview.get("claims", [])
            ],
            why_this_answer=overview.get("why_this_answer"),
            ingested_at=overview.get("ingested_at"),
        ),
        message="Document overview retrieved",
    )
