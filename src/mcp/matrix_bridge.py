#!/usr/bin/env python3
"""
Matrix-MCP Bridge
Connects Matrix rooms to MCP tools, allowing users to execute tools via chat commands
"""
import asyncio
import json
import logging
import os
import re
from typing import Dict, Optional, List
from nio import AsyncClient, RoomMessageText, LoginError
import websockets
from dotenv import load_dotenv

from src.matrix.event_dedupe import is_duplicate_event


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv('.env')

class MatrixMCPBridge:
    """Bridge between Matrix rooms and MCP server"""
    
    def __init__(self):
        # Matrix configuration
        self.homeserver_url = os.getenv("MATRIX_HOMESERVER_URL", "http://localhost:8008")
        self.username = os.getenv("MATRIX_MCP_USERNAME", "@mcp-bot:matrix.oculair.ca")
        self.password = os.getenv("MATRIX_MCP_PASSWORD", "mcp-bot-password")
        self.allowed_rooms = os.getenv("MATRIX_MCP_ALLOWED_ROOMS", "").split(",")
        
        # MCP configuration
        self.mcp_server_url = os.getenv("MCP_SERVER_URL", "ws://mcp-server:8005")
        
        # Clients
        self.matrix_client: Optional[AsyncClient] = None
        self.mcp_websocket: Optional[websockets.WebSocketClientProtocol] = None
        self.mcp_session_id: Optional[str] = None
        
        # Tool command pattern: !mcp <tool_name> <parameters>
        self.command_pattern = re.compile(r'^!mcp\s+(\w+)\s*(.*)?$')
    
    async def connect_matrix(self):
        """Connect and login to Matrix server"""
        self.matrix_client = AsyncClient(self.homeserver_url, self.username)
        
        try:
            response = await self.matrix_client.login(self.password)
            if isinstance(response, LoginError):
                logger.error(f"Failed to login to Matrix: {response.message}")
                return False
            
            logger.info(f"Successfully logged in to Matrix as {self.username}")
            logger.info(f"Device ID: {self.matrix_client.device_id}")
            
            # Set up message callback
            self.matrix_client.add_event_callback(self.message_callback, RoomMessageText)
            
            return True
            
        except Exception as e:
            logger.error(f"Error connecting to Matrix: {e}")
            return False
    
    async def connect_mcp(self):
        """Connect to MCP server"""
        try:
            logger.info(f"Connecting to MCP server at {self.mcp_server_url}...")
            self.mcp_websocket = await websockets.connect(self.mcp_server_url)
            
            # Wait for initialization
            init_msg = await self.mcp_websocket.recv()
            init_data = json.loads(init_msg)
            
            if init_data.get("type") == "initialize":
                self.mcp_session_id = init_data.get("session_id")
                logger.info(f"Connected to MCP server. Session: {self.mcp_session_id}")
                return True
            else:
                logger.error(f"Unexpected MCP init response: {init_data}")
                return False
                
        except Exception as e:
            logger.error(f"Error connecting to MCP server: {e}")
            return False
    
    async def message_callback(self, room, event):
        """Handle incoming Matrix messages"""
        # Ignore our own messages
        if event.sender == self.matrix_client.user_id:
            return

        # Drop duplicate events using shared store
        event_id = getattr(event, "event_id", None)
        if event_id and is_duplicate_event(event_id, logger):
            return
        
        # Check if room is allowed (if restrictions are set)
        if self.allowed_rooms and room.room_id not in self.allowed_rooms:
            return
        
        message = event.body

        match = self.command_pattern.match(message)
        
        if match:
            tool_name = match.group(1)
            params_str = match.group(2) or ""
            
            logger.info(f"MCP command from {event.sender}: {tool_name} {params_str}")
            
            # Handle special commands
            if tool_name == "help":
                await self.send_help(room.room_id)
            elif tool_name == "list":
                await self.list_tools(room.room_id)
            else:
                await self.execute_tool(room.room_id, tool_name, params_str, event.sender)
    
    async def send_help(self, room_id: str):
        """Send help message to Matrix room"""
        help_text = """**MCP Bot Commands:**
• `!mcp help` - Show this help message
• `!mcp list` - List available tools
• `!mcp <tool> <parameters>` - Execute a tool

**Examples:**
• `!mcp calculator expression="2+2*3"`
• `!mcp web_search query="Matrix protocol"`
• `!mcp file_read path="/tmp/test.txt"`
"""
        
        await self.send_matrix_message(room_id, help_text, formatted=True)
    
    async def list_tools(self, room_id: str):
        """List available MCP tools in Matrix room"""
        try:
            # Request tools list from MCP server
            await self.mcp_websocket.send(json.dumps({"type": "list_tools"}))
            response = await self.mcp_websocket.recv()
            data = json.loads(response)
            
            if data.get("type") == "tools_list":
                tools = data.get("tools", [])
                
                # Format tools list
                message = "**Available MCP Tools:**\n"
                for tool in tools:
                    message += f"• **{tool['name']}** - {tool['description']}\n"
                    if tool.get('parameters'):
                        params = ", ".join(f"`{p}`" for p in tool['parameters'].keys())
                        message += f"  Parameters: {params}\n"
                
                await self.send_matrix_message(room_id, message, formatted=True)
            else:
                await self.send_matrix_message(room_id, "Failed to get tools list")
                
        except Exception as e:
            logger.error(f"Error listing tools: {e}")
            await self.send_matrix_message(room_id, f"Error: {str(e)}")
    
    async def execute_tool(self, room_id: str, tool_name: str, params_str: str, sender: str):
        """Execute an MCP tool and send result to Matrix"""
        try:
            # Parse parameters (simple key=value format)
            params = {}
            if params_str:
                # Match key=value or key="quoted value" patterns
                param_pattern = re.compile(r'(\w+)=(?:"([^"]*)"|(\S+))')
                for match in param_pattern.finditer(params_str):
                    key = match.group(1)
                    value = match.group(2) or match.group(3)
                    params[key] = value
            
            logger.info(f"Executing tool {tool_name} with params: {params}")
            
            # Send to MCP server
            await self.mcp_websocket.send(json.dumps({
                "type": "execute_tool",
                "tool": tool_name,
                "parameters": params
            }))
            
            # Wait for result
            response = await self.mcp_websocket.recv()
            data = json.loads(response)
            
            if data.get("type") == "tool_result":
                result = data.get("result", {})
                
                # Format result for Matrix
                if result.get("success"):
                    message = f"**Tool: {tool_name}**\n"
                    message += f"Requested by: {sender}\n\n"
                    
                    # Format based on tool type
                    if tool_name == "calculator":
                        message += f"Expression: `{result.get('expression')}`\n"
                        message += f"Result: **{result.get('result')}**"
                    elif tool_name == "file_read":
                        content = result.get('content', '')[:500]  # Limit content length
                        if len(result.get('content', '')) > 500:
                            content += "\n... (truncated)"
                        message += f"File: `{result.get('path')}`\n"
                        message += f"```\n{content}\n```"
                    elif tool_name == "web_search":
                        message += f"Query: `{result.get('query')}`\n\n"
                        for res in result.get('results', [])[:3]:  # Show top 3 results
                            message += f"• [{res.get('title')}]({res.get('url')})\n"
                            message += f"  {res.get('snippet')}\n\n"
                    else:
                        # Generic result display
                        message += f"```json\n{json.dumps(result, indent=2)}\n```"
                    
                    await self.send_matrix_message(room_id, message, formatted=True)
                else:
                    error = result.get("error", "Unknown error")
                    await self.send_matrix_message(room_id, f"❌ Tool error: {error}")
            
            elif data.get("type") == "error":
                await self.send_matrix_message(room_id, f"❌ MCP error: {data.get('error')}")
            
        except json.JSONDecodeError:
            await self.send_matrix_message(room_id, "❌ Invalid parameters format")
        except Exception as e:
            logger.error(f"Error executing tool: {e}")
            await self.send_matrix_message(room_id, f"❌ Error: {str(e)}")
    
    async def send_matrix_message(self, room_id: str, message: str, formatted: bool = False):
        """Send a message to a Matrix room"""
        content = {
            "msgtype": "m.text",
            "body": message
        }
        
        if formatted:
            # Convert markdown-style formatting to HTML
            html_message = message
            # Bold: **text** -> <strong>text</strong>
            html_message = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', html_message)
            # Code: `text` -> <code>text</code>
            html_message = re.sub(r'`([^`]+)`', r'<code>\1</code>', html_message)
            # Links: [text](url) -> <a href="url">text</a>
            html_message = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', html_message)
            # Line breaks
            html_message = html_message.replace('\n', '<br>')
            
            content["format"] = "org.matrix.custom.html"
            content["formatted_body"] = html_message
        
        try:
            await self.matrix_client.room_send(
                room_id,
                "m.room.message",
                content
            )
        except Exception as e:
            logger.error(f"Error sending Matrix message: {e}")
    
    async def run(self):
        """Main run loop"""
        # Connect to both services
        if not await self.connect_matrix():
            logger.error("Failed to connect to Matrix")
            return
        
        if not await self.connect_mcp():
            logger.error("Failed to connect to MCP server")
            return
        
        # Join configured rooms
        for room_id in self.allowed_rooms:
            if room_id:
                try:
                    await self.matrix_client.join(room_id)
                    logger.info(f"Joined room: {room_id}")
                except Exception as e:
                    logger.error(f"Failed to join room {room_id}: {e}")
        
        logger.info("Matrix-MCP bridge is running. Send '!mcp help' in a Matrix room to get started.")
        
        try:
            # Run Matrix sync
            await self.matrix_client.sync_forever(timeout=30000)
        except KeyboardInterrupt:
            logger.info("Bridge shutting down...")
        finally:
            if self.mcp_websocket:
                await self.mcp_websocket.close()
            if self.matrix_client:
                await self.matrix_client.close()

async def main():
    bridge = MatrixMCPBridge()
    await bridge.run()

if __name__ == "__main__":
    asyncio.run(main())