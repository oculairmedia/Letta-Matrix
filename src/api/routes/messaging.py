from typing import List, Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel


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
async def list_rooms(homeserver: str, access_token: str, raw_request: Request):
    matrix_client = get_matrix_client(raw_request)
    result = await matrix_client.list_rooms(homeserver, access_token)
    return result


@router.get("/messages/recent")
async def get_recent_messages(homeserver: str, access_token: str, raw_request: Request, limit: int = 10):
    try:
        matrix_client = get_matrix_client(raw_request)
        rooms_result = await matrix_client.list_rooms(homeserver, access_token)
        if not rooms_result.success:
            return {"success": False, "message": f"Failed to get rooms: {rooms_result.message}"}

        all_messages = []

        for room in rooms_result.rooms:
            messages_result = await matrix_client.get_messages(homeserver, access_token, room.room_id, limit=5)

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
