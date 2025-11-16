#!/usr/bin/env python3
"""
MCP (Model Context Protocol) Server for Matrix Integration
Provides a bridge between Matrix rooms and MCP-compatible tools
"""
import asyncio
import json
import logging
import os
import sys
from typing import Dict, List, Optional, Any, Callable
from datetime import datetime
import aiohttp
import websockets
from websockets.server import WebSocketServerProtocol
import uuid
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv('.env')

class MCPTool:
    """Base class for MCP tools"""
    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description
        self.parameters = {}
    
    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Execute the tool with given parameters"""
        raise NotImplementedError

class ListRoomsTool(MCPTool):
    """Tool for listing Matrix rooms"""
    def __init__(self, matrix_api_url: str, matrix_homeserver: str, letta_username: str, letta_password: str):
        super().__init__(
            name="list_rooms",
            description="List all available Matrix rooms"
        )
        self.matrix_api_url = matrix_api_url
        self.matrix_homeserver = matrix_homeserver
        self.letta_username = letta_username
        self.letta_password = letta_password
        self.access_token = None  # Will be obtained on first use
        self.parameters = {
            "include_members": {"type": "boolean", "description": "Include member count for each room", "default": False}
        }
    
    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        include_members = params.get("include_members", False)
        
        # Get access token if we don't have one
        if not self.access_token:
            login_result = await self._login()
            if "error" in login_result:
                return login_result
            self.access_token = login_result["access_token"]
        
        try:
            async with aiohttp.ClientSession() as session:
                # Get joined rooms from Matrix API
                url = f"{self.matrix_api_url}/rooms/list"
                payload = {
                    "access_token": self.access_token,
                    "homeserver": self.matrix_homeserver,
                    "include_members": include_members
                }
                
                async with session.post(url, json=payload) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        return {"error": f"Failed to list rooms: {error_text}"}
                    
                    result = await response.json()
                    
                    if result.get("success"):
                        return {
                            "success": True,
                            "rooms": result.get("rooms", [])
                        }
                    else:
                        return {"error": f"Failed to list rooms: {result.get('message', 'Unknown error')}"}
                        
        except Exception as e:
            return {"error": f"Error listing rooms: {str(e)}"}
    
    async def _login(self) -> Dict[str, Any]:
        """Login to Matrix and get access token"""
        try:
            async with aiohttp.ClientSession() as session:
                url = f"{self.matrix_api_url}/login"
                payload = {
                    "homeserver": self.matrix_homeserver,
                    "user_id": self.letta_username,
                    "password": self.letta_password,
                    "device_name": "mcp_server"
                }
                
                async with session.post(url, json=payload) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        return {"error": f"Login failed: {error_text}"}
                    
                    result = await response.json()
                    if result.get("success"):
                        return {
                            "access_token": result.get("access_token"),
                            "device_id": result.get("device_id")
                        }
                    else:
                        return {"error": f"Login failed: {result.get('message', 'Unknown error')}"}
        except Exception as e:
            return {"error": f"Login error: {str(e)}"}

class MCPServer:
    """MCP Server implementation for Matrix integration"""
    
    def __init__(self):
        self.tools: Dict[str, MCPTool] = {}
        self.sessions: Dict[str, Dict] = {}  # Track client sessions
        self.matrix_api_url = os.getenv("MATRIX_API_URL", "http://matrix-api:8000")
        self.matrix_homeserver = os.getenv("MATRIX_HOMESERVER_URL", "http://synapse:8008")
        self.letta_username = os.getenv("MATRIX_USERNAME", "@letta:matrix.oculair.ca")
        self.letta_password = os.getenv("MATRIX_PASSWORD", "letta")
        self.access_token = None  # Will be obtained on first use
        
        # Register available tools
        self._register_tools()
    
    def _register_tools(self):
        """Register available MCP tools"""
        # Register the list_rooms tool
        self.tools["list_rooms"] = ListRoomsTool(
            self.matrix_api_url, 
            self.matrix_homeserver,
            self.letta_username,
            self.letta_password
        )
        
        logger.info(f"Registered {len(self.tools)} tools: {list(self.tools.keys())}")
    
    async def handle_client(self, websocket: WebSocketServerProtocol, path: str):
        """Handle a new MCP client connection"""
        session_id = str(uuid.uuid4())
        client_info = {
            "session_id": session_id,
            "connected_at": datetime.now().isoformat(),
            "websocket": websocket
        }
        self.sessions[session_id] = client_info
        
        logger.info(f"New MCP client connected: {session_id}")
        
        try:
            # Send initialization message
            await self.send_message(websocket, {
                "type": "initialize",
                "session_id": session_id,
                "version": "1.0",
                "capabilities": {
                    "tools": list(self.tools.keys()),
                    "features": ["matrix_integration", "async_execution"]
                }
            })
            
            # Handle incoming messages
            async for message in websocket:
                await self.handle_message(session_id, message)
                
        except websockets.exceptions.ConnectionClosed:
            logger.info(f"Client disconnected: {session_id}")
        except Exception as e:
            logger.error(f"Error handling client {session_id}: {e}")
        finally:
            # Clean up session
            if session_id in self.sessions:
                del self.sessions[session_id]
    
    async def handle_message(self, session_id: str, message: str):
        """Handle incoming MCP protocol message"""
        try:
            data = json.loads(message)
            message_type = data.get("type")
            
            logger.info(f"Received message type '{message_type}' from {session_id}")
            
            if message_type == "list_tools":
                await self.handle_list_tools(session_id)
            elif message_type == "execute_tool":
                await self.handle_execute_tool(session_id, data)
            elif message_type == "matrix_send":
                await self.handle_matrix_send(session_id, data)
            else:
                await self.send_error(session_id, f"Unknown message type: {message_type}")
                
        except json.JSONDecodeError:
            await self.send_error(session_id, "Invalid JSON message")
        except Exception as e:
            logger.error(f"Error handling message: {e}")
            await self.send_error(session_id, str(e))
    
    async def handle_list_tools(self, session_id: str):
        """Handle request to list available tools"""
        tools_info = []
        for name, tool in self.tools.items():
            tools_info.append({
                "name": name,
                "description": tool.description,
                "parameters": tool.parameters
            })
        
        await self.send_response(session_id, {
            "type": "tools_list",
            "tools": tools_info
        })
    
    async def handle_execute_tool(self, session_id: str, data: Dict):
        """Handle tool execution request"""
        tool_name = data.get("tool")
        params = data.get("parameters", {})
        
        if tool_name not in self.tools:
            await self.send_error(session_id, f"Unknown tool: {tool_name}")
            return
        
        tool = self.tools[tool_name]
        logger.info(f"Executing tool '{tool_name}' with params: {params}")
        
        try:
            result = await tool.execute(params)
            await self.send_response(session_id, {
                "type": "tool_result",
                "tool": tool_name,
                "result": result
            })
        except Exception as e:
            await self.send_error(session_id, f"Tool execution error: {str(e)}")
    
    async def handle_matrix_send(self, session_id: str, data: Dict):
        """Handle request to send message to Matrix room"""
        room_id = data.get("room_id")
        message = data.get("message")
        
        if not all([room_id, message]):
            await self.send_error(session_id, "Missing required parameters: room_id, message")
            return
        
        # Get access token if we don't have one
        if not self.access_token:
            login_result = await self._login()
            if "error" in login_result:
                await self.send_error(session_id, login_result["error"])
                return
            self.access_token = login_result["access_token"]
        
        try:
            # Use Matrix API to send message
            async with aiohttp.ClientSession() as session:
                url = f"{self.matrix_api_url}/messages/send"
                payload = {
                    "room_id": room_id,
                    "message": message,
                    "access_token": self.access_token,
                    "homeserver": self.matrix_homeserver
                }
                
                async with session.post(url, json=payload) as response:
                    result = await response.json()
                    
                    if result.get("success"):
                        await self.send_response(session_id, {
                            "type": "matrix_sent",
                            "event_id": result.get("event_id"),
                            "room_id": room_id
                        })
                    else:
                        await self.send_error(session_id, f"Matrix send failed: {result.get('message')}")
                        
        except Exception as e:
            await self.send_error(session_id, f"Error sending to Matrix: {str(e)}")
    
    async def send_message(self, websocket: WebSocketServerProtocol, data: Dict):
        """Send message to client"""
        message = json.dumps(data)
        await websocket.send(message)
    
    async def send_response(self, session_id: str, data: Dict):
        """Send response to specific session"""
        if session_id in self.sessions:
            websocket = self.sessions[session_id]["websocket"]
            await self.send_message(websocket, data)
    
    async def send_error(self, session_id: str, error_message: str):
        """Send error message to specific session"""
        await self.send_response(session_id, {
            "type": "error",
            "error": error_message
        })
    
    async def _login(self) -> Dict[str, Any]:
        """Login to Matrix and get access token"""
        try:
            async with aiohttp.ClientSession() as session:
                url = f"{self.matrix_api_url}/login"
                payload = {
                    "homeserver": self.matrix_homeserver,
                    "user_id": self.letta_username,
                    "password": self.letta_password,
                    "device_name": "mcp_server"
                }
                
                async with session.post(url, json=payload) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        return {"error": f"Login failed: {error_text}"}
                    
                    result = await response.json()
                    if result.get("success"):
                        return {
                            "access_token": result.get("access_token"),
                            "device_id": result.get("device_id")
                        }
                    else:
                        return {"error": f"Login failed: {result.get('message', 'Unknown error')}"}
        except Exception as e:
            return {"error": f"Login error: {str(e)}"}
    
    async def start(self, host: str = "0.0.0.0", port: int = 8005):
        """Start the MCP server"""
        logger.info(f"Starting MCP server on {host}:{port}")
        
        async with websockets.serve(self.handle_client, host, port):
            logger.info(f"MCP server running on ws://{host}:{port}")
            await asyncio.Future()  # Run forever

async def main():
    """Main entry point"""
    server = MCPServer()
    
    # Get configuration from environment
    host = os.getenv("MCP_HOST", "0.0.0.0")
    port = int(os.getenv("MCP_PORT", "8005"))
    
    try:
        await server.start(host, port)
    except KeyboardInterrupt:
        logger.info("MCP server shutting down...")
    except Exception as e:
        logger.error(f"Server error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())