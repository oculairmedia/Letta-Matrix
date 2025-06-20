#!/usr/bin/env python3
"""
MCP (Model Context Protocol) HTTP Streaming Server for Matrix Integration
Implements the Streamable HTTP transport as per MCP protocol specification
"""
import asyncio
import json
import logging
import os
import sys
import uuid
from typing import Dict, List, Optional, Any, Callable, Union
from datetime import datetime
from dataclasses import dataclass, field
import aiohttp
from aiohttp import web
from aiohttp.web import Request, Response, StreamResponse
import secrets
import time
from dotenv import load_dotenv
from urllib.parse import quote

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv('.env')


@dataclass
class Session:
    """Represents an MCP session"""
    id: str
    created_at: datetime
    last_activity: datetime
    metadata: Dict[str, Any] = field(default_factory=dict)
    pending_responses: Dict[str, StreamResponse] = field(default_factory=dict)
    event_counter: int = 0
    
    def generate_event_id(self) -> str:
        """Generate a unique event ID for SSE"""
        self.event_counter += 1
        return f"{self.id}-{self.event_counter}"


class MCPTool:
    """Base class for MCP tools"""
    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description
        self.parameters = {}
    
    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Execute the tool with given parameters"""
        raise NotImplementedError


class FileReadTool(MCPTool):
    """Tool for reading files"""
    def __init__(self):
        super().__init__(
            name="file_read",
            description="Read contents of a file"
        )
        self.parameters = {
            "path": {"type": "string", "description": "Path to the file to read"}
        }
    
    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        try:
            path = params.get("path")
            if not path:
                return {"error": "Path parameter is required"}
            
            # Security: Restrict to certain directories
            allowed_dirs = ["/tmp", "/app/data", "/opt/stacks/matrix-synapse-deployment/matrix_store"]
            if not any(path.startswith(d) for d in allowed_dirs):
                return {"error": f"Access denied. Allowed directories: {allowed_dirs}"}
            
            with open(path, 'r') as f:
                content = f.read()
            
            return {
                "success": True,
                "content": content,
                "path": path
            }
        except FileNotFoundError:
            return {"error": f"File not found: {path}"}
        except Exception as e:
            return {"error": f"Error reading file: {str(e)}"}


class WebSearchTool(MCPTool):
    """Tool for web searching"""
    def __init__(self):
        super().__init__(
            name="web_search",
            description="Search the web for information"
        )
        self.parameters = {
            "query": {"type": "string", "description": "Search query"}
        }
    
    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        query = params.get("query")
        if not query:
            return {"error": "Query parameter is required"}
        
        # Placeholder for web search - in production, integrate with actual search API
        return {
            "success": True,
            "query": query,
            "results": [
                {
                    "title": f"Sample result for: {query}",
                    "url": "https://example.com",
                    "snippet": "This is a placeholder search result."
                }
            ]
        }


class CalculatorTool(MCPTool):
    """Tool for mathematical calculations"""
    def __init__(self):
        super().__init__(
            name="calculator",
            description="Perform mathematical calculations"
        )
        self.parameters = {
            "expression": {"type": "string", "description": "Mathematical expression to evaluate"}
        }
    
    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        expression = params.get("expression")
        if not expression:
            return {"error": "Expression parameter is required"}
        
        try:
            # Safety: Only allow basic math operations
            allowed_names = {
                'abs': abs, 'round': round, 'min': min, 'max': max,
                'sum': sum, 'pow': pow, 'sqrt': __import__('math').sqrt
            }
            
            # Evaluate expression safely
            result = eval(expression, {"__builtins__": {}}, allowed_names)
            
            return {
                "success": True,
                "expression": expression,
                "result": result
            }
        except Exception as e:
            return {"error": f"Calculation error: {str(e)}"}


class MatrixSendMessageTool(MCPTool):
    """Tool for sending messages to Matrix rooms"""
    def __init__(self, matrix_api_url: str):
        super().__init__(
            name="matrix_send_message",
            description="Send a message to a Matrix room"
        )
        self.matrix_api_url = matrix_api_url
        self.parameters = {
            "room_id": {"type": "string", "description": "The Matrix room ID (e.g., !abc123:matrix.org)"},
            "message": {"type": "string", "description": "The message to send"},
            "access_token": {"type": "string", "description": "Matrix access token for authentication"}
        }
    
    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        room_id = params.get("room_id")
        message = params.get("message")
        access_token = params.get("access_token")
        
        if not all([room_id, message, access_token]):
            return {"error": "Missing required parameters: room_id, message, access_token"}
        
        try:
            async with aiohttp.ClientSession() as session:
                url = f"{self.matrix_api_url}/messages/send"
                payload = {
                    "room_id": room_id,
                    "message": message,
                    "access_token": access_token
                }
                
                async with session.post(url, json=payload) as response:
                    result = await response.json()
                    
                    if response.status == 200 and result.get("success"):
                        return {
                            "success": True,
                            "event_id": result.get("event_id"),
                            "room_id": room_id
                        }
                    else:
                        return {
                            "error": f"Failed to send message: {result.get('message', 'Unknown error')}"
                        }
        except Exception as e:
            return {"error": f"Error sending message: {str(e)}"}


class MatrixReadRoomTool(MCPTool):
    """Tool for reading messages from a Matrix room"""
    def __init__(self, matrix_homeserver: str):
        super().__init__(
            name="matrix_read_room",
            description="Read recent messages from a Matrix room"
        )
        self.matrix_homeserver = matrix_homeserver
        self.parameters = {
            "room_id": {"type": "string", "description": "The Matrix room ID"},
            "access_token": {"type": "string", "description": "Matrix access token"},
            "limit": {"type": "integer", "description": "Number of messages to retrieve (default: 10)"}
        }
    
    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        room_id = params.get("room_id")
        access_token = params.get("access_token")
        limit = params.get("limit", 10)
        
        if not all([room_id, access_token]):
            return {"error": "Missing required parameters: room_id, access_token"}
        
        try:
            async with aiohttp.ClientSession() as session:
                # Get room messages
                url = f"{self.matrix_homeserver}/_matrix/client/r0/rooms/{quote(room_id)}/messages"
                headers = {"Authorization": f"Bearer {access_token}"}
                params = {"limit": limit, "dir": "b"}  # b = backwards (most recent first)
                
                async with session.get(url, headers=headers, params=params) as response:
                    if response.status == 200:
                        data = await response.json()
                        messages = []
                        
                        for event in data.get("chunk", []):
                            if event.get("type") == "m.room.message":
                                messages.append({
                                    "sender": event.get("sender"),
                                    "content": event.get("content", {}).get("body"),
                                    "timestamp": event.get("origin_server_ts"),
                                    "event_id": event.get("event_id")
                                })
                        
                        return {
                            "success": True,
                            "room_id": room_id,
                            "messages": messages,
                            "count": len(messages)
                        }
                    else:
                        error_data = await response.text()
                        return {"error": f"Failed to read room: {response.status} - {error_data}"}
                        
        except Exception as e:
            return {"error": f"Error reading room: {str(e)}"}


class MatrixJoinRoomTool(MCPTool):
    """Tool for joining a Matrix room"""
    def __init__(self, matrix_homeserver: str):
        super().__init__(
            name="matrix_join_room",
            description="Join a Matrix room by ID or alias"
        )
        self.matrix_homeserver = matrix_homeserver
        self.parameters = {
            "room_id_or_alias": {"type": "string", "description": "Room ID (!abc:server) or alias (#room:server)"},
            "access_token": {"type": "string", "description": "Matrix access token"}
        }
    
    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        room_id_or_alias = params.get("room_id_or_alias")
        access_token = params.get("access_token")
        
        if not all([room_id_or_alias, access_token]):
            return {"error": "Missing required parameters: room_id_or_alias, access_token"}
        
        try:
            async with aiohttp.ClientSession() as session:
                url = f"{self.matrix_homeserver}/_matrix/client/r0/join/{quote(room_id_or_alias)}"
                headers = {"Authorization": f"Bearer {access_token}"}
                
                async with session.post(url, headers=headers, json={}) as response:
                    if response.status == 200:
                        data = await response.json()
                        return {
                            "success": True,
                            "room_id": data.get("room_id"),
                            "joined": True
                        }
                    else:
                        error_data = await response.text()
                        return {"error": f"Failed to join room: {response.status} - {error_data}"}
                        
        except Exception as e:
            return {"error": f"Error joining room: {str(e)}"}


class MatrixCreateRoomTool(MCPTool):
    """Tool for creating a new Matrix room"""
    def __init__(self, matrix_homeserver: str):
        super().__init__(
            name="matrix_create_room",
            description="Create a new Matrix room"
        )
        self.matrix_homeserver = matrix_homeserver
        self.parameters = {
            "name": {"type": "string", "description": "Room name"},
            "topic": {"type": "string", "description": "Room topic (optional)"},
            "is_public": {"type": "boolean", "description": "Whether the room is public (default: false)"},
            "access_token": {"type": "string", "description": "Matrix access token"}
        }
    
    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        name = params.get("name")
        topic = params.get("topic", "")
        is_public = params.get("is_public", False)
        access_token = params.get("access_token")
        
        if not all([name, access_token]):
            return {"error": "Missing required parameters: name, access_token"}
        
        try:
            async with aiohttp.ClientSession() as session:
                url = f"{self.matrix_homeserver}/_matrix/client/r0/createRoom"
                headers = {"Authorization": f"Bearer {access_token}"}
                
                payload = {
                    "name": name,
                    "visibility": "public" if is_public else "private",
                    "preset": "public_chat" if is_public else "private_chat"
                }
                
                if topic:
                    payload["topic"] = topic
                
                async with session.post(url, headers=headers, json=payload) as response:
                    if response.status == 200:
                        data = await response.json()
                        return {
                            "success": True,
                            "room_id": data.get("room_id"),
                            "room_alias": data.get("room_alias")
                        }
                    else:
                        error_data = await response.text()
                        return {"error": f"Failed to create room: {response.status} - {error_data}"}
                        
        except Exception as e:
            return {"error": f"Error creating room: {str(e)}"}


class MCPHTTPServer:
    """MCP HTTP Streaming Server implementation"""
    
    def __init__(self):
        self.tools: Dict[str, MCPTool] = {}
        self.sessions: Dict[str, Session] = {}
        self.app = web.Application()
        self.matrix_api_url = os.getenv("MATRIX_API_URL", "http://matrix-api:8000")
        self.matrix_homeserver = os.getenv("MATRIX_HOMESERVER_URL", "http://synapse:8008")
        
        # Register available tools
        self._register_tools()
        
        # Setup routes
        self._setup_routes()
    
    def _register_tools(self):
        """Register available MCP tools"""
        # Basic tools
        self.tools["file_read"] = FileReadTool()
        self.tools["web_search"] = WebSearchTool()
        self.tools["calculator"] = CalculatorTool()
        
        # Matrix tools
        self.tools["matrix_send_message"] = MatrixSendMessageTool(self.matrix_api_url)
        self.tools["matrix_read_room"] = MatrixReadRoomTool(self.matrix_homeserver)
        self.tools["matrix_join_room"] = MatrixJoinRoomTool(self.matrix_homeserver)
        self.tools["matrix_create_room"] = MatrixCreateRoomTool(self.matrix_homeserver)
        
        logger.info(f"Registered {len(self.tools)} tools: {list(self.tools.keys())}")
    
    def _setup_routes(self):
        """Setup HTTP routes"""
        # Main MCP endpoint supporting both POST and GET
        self.app.router.add_post('/mcp', self.handle_mcp_post)
        self.app.router.add_get('/mcp', self.handle_mcp_get)
        
        # Session management endpoint
        self.app.router.add_delete('/mcp', self.handle_session_delete)
        
        # Health check
        self.app.router.add_get('/health', self.handle_health)
    
    def _validate_origin(self, request: Request) -> bool:
        """Validate Origin header to prevent DNS rebinding attacks"""
        origin = request.headers.get('Origin', '')
        
        # Allow localhost and specific trusted origins
        allowed_origins = [
            'http://localhost',
            'http://127.0.0.1',
            'https://matrix.oculair.ca',
            'http://192.168.50.90',
            'http://192.168.50.1',
            'https://claude.ai'
        ]
        
        # Allow requests without Origin header (e.g., direct API calls)
        if not origin:
            return True
        
        # Check if origin starts with any allowed origin
        return any(origin.startswith(allowed) for allowed in allowed_origins)
    
    def _get_or_create_session(self, request: Request) -> Optional[Session]:
        """Get existing session or create new one"""
        session_id = request.headers.get('Mcp-Session-Id')
        
        if session_id and session_id in self.sessions:
            session = self.sessions[session_id]
            session.last_activity = datetime.now()
            return session
        
        # Create new session if no ID provided
        if not session_id:
            session_id = secrets.token_urlsafe(32)
            session = Session(
                id=session_id,
                created_at=datetime.now(),
                last_activity=datetime.now()
            )
            self.sessions[session_id] = session
            return session
        
        # Session ID provided but not found
        return None
    
    async def handle_health(self, request: Request) -> Response:
        """Health check endpoint"""
        return web.json_response({
            "status": "healthy",
            "sessions": len(self.sessions),
            "tools": list(self.tools.keys())
        })
    
    async def handle_mcp_post(self, request: Request) -> Union[Response, StreamResponse]:
        """Handle POST requests to MCP endpoint"""
        # Validate origin
        if not self._validate_origin(request):
            return web.json_response(
                {"jsonrpc": "2.0", "error": {"code": -32600, "message": "Invalid origin"}},
                status=403
            )
        
        # Check Accept header
        accept = request.headers.get('Accept', '')
        if 'application/json' not in accept and 'text/event-stream' not in accept:
            return web.json_response(
                {"jsonrpc": "2.0", "error": {"code": -32600, "message": "Accept header must include application/json or text/event-stream"}},
                status=400
            )
        
        # Get or create session
        session = self._get_or_create_session(request)
        if not session and request.headers.get('Mcp-Session-Id'):
            return web.json_response(
                {"jsonrpc": "2.0", "error": {"code": -32600, "message": "Session not found"}},
                status=404
            )
        
        try:
            body = await request.json()
            
            # Handle batch or single request
            if isinstance(body, list):
                messages = body
            else:
                messages = [body]
            
            # Separate requests from notifications/responses
            requests = []
            notifications = []
            responses = []
            
            for msg in messages:
                if 'method' in msg and 'id' in msg:
                    requests.append(msg)
                elif 'method' in msg:
                    notifications.append(msg)
                elif 'result' in msg or 'error' in msg:
                    responses.append(msg)
            
            # If only notifications/responses, return 202 Accepted
            if not requests:
                await self._process_notifications(session, notifications)
                await self._process_responses(session, responses)
                return web.Response(status=202)
            
            # Determine response type based on client preference
            if 'text/event-stream' in accept:
                # Return SSE stream
                return await self._handle_sse_response(request, session, requests, notifications)
            else:
                # Return single JSON response
                return await self._handle_json_response(session, requests[0])
        
        except json.JSONDecodeError:
            return web.json_response(
                {"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error"}},
                status=400
            )
        except Exception as e:
            logger.error(f"Error handling POST: {e}")
            return web.json_response(
                {"jsonrpc": "2.0", "error": {"code": -32603, "message": str(e)}},
                status=500
            )
    
    async def handle_mcp_get(self, request: Request) -> Union[Response, StreamResponse]:
        """Handle GET requests to open SSE stream"""
        # Validate origin
        if not self._validate_origin(request):
            return web.Response(text="Forbidden", status=403)
        
        # Check Accept header
        if 'text/event-stream' not in request.headers.get('Accept', ''):
            return web.Response(text="Method Not Allowed", status=405)
        
        # Get session
        session = self._get_or_create_session(request)
        if not session and request.headers.get('Mcp-Session-Id'):
            return web.Response(text="Session not found", status=404)
        
        # Handle resumption
        last_event_id = request.headers.get('Last-Event-ID')
        
        # Create SSE stream
        response = web.StreamResponse()
        response.headers['Content-Type'] = 'text/event-stream'
        response.headers['Cache-Control'] = 'no-cache'
        response.headers['Connection'] = 'keep-alive'
        
        if session and not request.headers.get('Mcp-Session-Id'):
            response.headers['Mcp-Session-Id'] = session.id
        
        await response.prepare(request)
        
        # Store response for server-initiated messages
        if session:
            stream_id = str(uuid.uuid4())
            session.pending_responses[stream_id] = response
        
        try:
            # Keep connection alive
            while True:
                await asyncio.sleep(30)
                await response.write(b': keepalive\n\n')
        except Exception:
            pass
        finally:
            if session and stream_id in session.pending_responses:
                del session.pending_responses[stream_id]
        
        return response
    
    async def handle_session_delete(self, request: Request) -> Response:
        """Handle DELETE request to terminate session"""
        session_id = request.headers.get('Mcp-Session-Id')
        
        if not session_id:
            return web.Response(text="Bad Request", status=400)
        
        if session_id in self.sessions:
            # Close any pending streams
            session = self.sessions[session_id]
            for response in session.pending_responses.values():
                try:
                    await response.write_eof()
                except:
                    pass
            
            del self.sessions[session_id]
            return web.Response(status=204)
        
        return web.Response(text="Not Found", status=404)
    
    async def _handle_json_response(self, session: Session, request: Dict[str, Any]) -> Response:
        """Handle single JSON response"""
        result = await self._process_request(session, request)
        
        response = web.json_response(result)
        if session:
            response.headers['Mcp-Session-Id'] = session.id
        
        return response
    
    async def _handle_sse_response(
        self, 
        request: Request, 
        session: Session, 
        requests: List[Dict], 
        notifications: List[Dict]
    ) -> StreamResponse:
        """Handle SSE stream response"""
        response = web.StreamResponse()
        response.headers['Content-Type'] = 'text/event-stream'
        response.headers['Cache-Control'] = 'no-cache'
        response.headers['Connection'] = 'keep-alive'
        
        if session:
            response.headers['Mcp-Session-Id'] = session.id
        
        await response.prepare(request)
        
        try:
            # Process notifications first
            await self._process_notifications(session, notifications)
            
            # Process each request and send response
            for req in requests:
                result = await self._process_request(session, req)
                event_id = session.generate_event_id() if session else str(uuid.uuid4())
                
                # Send SSE event
                data = f"id: {event_id}\n"
                data += f"data: {json.dumps(result)}\n\n"
                await response.write(data.encode('utf-8'))
            
            # Close stream after all responses sent
            await response.write_eof()
            
        except Exception as e:
            logger.error(f"Error in SSE stream: {e}")
            try:
                error_data = json.dumps({
                    "jsonrpc": "2.0",
                    "error": {"code": -32603, "message": str(e)}
                })
                await response.write(f"data: {error_data}\n\n".encode('utf-8'))
            except:
                pass
        
        return response
    
    async def _process_request(self, session: Session, request: Dict[str, Any]) -> Dict[str, Any]:
        """Process a single JSON-RPC request"""
        method = request.get('method')
        params = request.get('params', {})
        request_id = request.get('id')
        
        try:
            # Handle different methods
            if method == 'initialize':
                result = await self._handle_initialize(session, params)
            elif method == 'initialized':
                result = {"success": True}
            elif method == 'tools/list':
                result = await self._handle_list_tools()
            elif method == 'tools/call':
                result = await self._handle_tool_call(params)
            else:
                return {
                    "jsonrpc": "2.0",
                    "error": {"code": -32601, "message": f"Method not found: {method}"},
                    "id": request_id
                }
            
            return {
                "jsonrpc": "2.0",
                "result": result,
                "id": request_id
            }
            
        except Exception as e:
            logger.error(f"Error processing request: {e}")
            return {
                "jsonrpc": "2.0",
                "error": {"code": -32603, "message": str(e)},
                "id": request_id
            }
    
    async def _process_notifications(self, session: Session, notifications: List[Dict]):
        """Process notifications (no response needed)"""
        for notification in notifications:
            method = notification.get('method')
            params = notification.get('params', {})
            
            logger.info(f"Processing notification: {method}")
            
            # Handle specific notifications if needed
            if method == 'cancelled':
                # Handle cancellation
                pass
    
    async def _process_responses(self, session: Session, responses: List[Dict]):
        """Process responses from client"""
        for response in responses:
            logger.info(f"Received response: {response.get('id')}")
    
    async def _handle_initialize(self, session: Session, params: Dict[str, Any]) -> Dict[str, Any]:
        """Handle initialization request"""
        client_info = params.get('clientInfo', {})
        
        return {
            "protocolVersion": "2025-03-26",
            "serverInfo": {
                "name": "matrix-mcp-server",
                "version": "1.0.0"
            },
            "capabilities": {
                "tools": {
                    "available": True
                }
            }
        }
    
    async def _handle_list_tools(self) -> Dict[str, Any]:
        """Handle tools list request"""
        tools = []
        for name, tool in self.tools.items():
            tools.append({
                "name": name,
                "description": tool.description,
                "inputSchema": {
                    "type": "object",
                    "properties": tool.parameters,
                    "required": list(tool.parameters.keys())
                }
            })
        
        return {"tools": tools}
    
    async def _handle_tool_call(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Handle tool execution request"""
        tool_name = params.get('name')
        tool_args = params.get('arguments', {})
        
        if tool_name not in self.tools:
            raise ValueError(f"Unknown tool: {tool_name}")
        
        tool = self.tools[tool_name]
        result = await tool.execute(tool_args)
        
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(result)
                }
            ],
            "isError": "error" in result
        }
    
    async def send_server_message(self, session_id: str, message: Dict[str, Any]):
        """Send server-initiated message to client streams"""
        if session_id not in self.sessions:
            return
        
        session = self.sessions[session_id]
        
        # Send to all active streams for this session
        for stream_id, response in list(session.pending_responses.items()):
            try:
                event_id = session.generate_event_id()
                data = f"id: {event_id}\n"
                data += f"data: {json.dumps(message)}\n\n"
                await response.write(data.encode('utf-8'))
            except Exception as e:
                logger.error(f"Error sending to stream {stream_id}: {e}")
                # Remove failed stream
                del session.pending_responses[stream_id]
    
    async def cleanup_sessions(self):
        """Periodic cleanup of inactive sessions"""
        while True:
            try:
                await asyncio.sleep(300)  # Run every 5 minutes
                
                now = datetime.now()
                expired_sessions = []
                
                for session_id, session in self.sessions.items():
                    # Remove sessions inactive for more than 1 hour
                    if (now - session.last_activity).total_seconds() > 3600:
                        expired_sessions.append(session_id)
                
                for session_id in expired_sessions:
                    logger.info(f"Cleaning up expired session: {session_id}")
                    session = self.sessions[session_id]
                    
                    # Close any pending streams
                    for response in session.pending_responses.values():
                        try:
                            await response.write_eof()
                        except:
                            pass
                    
                    del self.sessions[session_id]
                    
            except Exception as e:
                logger.error(f"Error in session cleanup: {e}")
    
    async def start(self, host: str = "127.0.0.1", port: int = 8006):
        """Start the HTTP server"""
        # Start cleanup task
        asyncio.create_task(self.cleanup_sessions())
        
        # Configure and start server
        runner = web.AppRunner(self.app)
        await runner.setup()
        
        site = web.TCPSite(runner, host, port)
        await site.start()
        
        logger.info(f"MCP HTTP Server running on http://{host}:{port}")
        logger.info(f"MCP endpoint: http://{host}:{port}/mcp")
        
        # Keep running
        await asyncio.Future()


async def main():
    """Main entry point"""
    server = MCPHTTPServer()
    
    # Get configuration from environment
    host = os.getenv("MCP_HTTP_HOST", "127.0.0.1")
    port = int(os.getenv("MCP_HTTP_PORT", "8006"))
    
    try:
        await server.start(host, port)
    except KeyboardInterrupt:
        logger.info("MCP HTTP server shutting down...")
    except Exception as e:
        logger.error(f"Server error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())