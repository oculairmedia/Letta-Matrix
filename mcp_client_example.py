#!/usr/bin/env python3
"""
Example MCP Client for testing the MCP Server
Demonstrates how to connect and interact with the MCP server
"""
import asyncio
import json
import websockets
import sys
from typing import Optional

class MCPClient:
    def __init__(self, server_url: str = "ws://localhost:8005"):
        self.server_url = server_url
        self.websocket: Optional[websockets.WebSocketClientProtocol] = None
        self.session_id: Optional[str] = None
    
    async def connect(self):
        """Connect to the MCP server"""
        print(f"Connecting to MCP server at {self.server_url}...")
        self.websocket = await websockets.connect(self.server_url)
        
        # Wait for initialization message
        init_msg = await self.websocket.recv()
        init_data = json.loads(init_msg)
        
        if init_data.get("type") == "initialize":
            self.session_id = init_data.get("session_id")
            print(f"Connected! Session ID: {self.session_id}")
            print(f"Server capabilities: {init_data.get('capabilities', {})}")
            return True
        else:
            print(f"Unexpected initialization response: {init_data}")
            return False
    
    async def send_message(self, message: dict):
        """Send a message to the server"""
        await self.websocket.send(json.dumps(message))
    
    async def receive_message(self):
        """Receive a message from the server"""
        msg = await self.websocket.recv()
        return json.loads(msg)
    
    async def list_tools(self):
        """Request list of available tools"""
        await self.send_message({"type": "list_tools"})
        response = await self.receive_message()
        return response
    
    async def execute_tool(self, tool_name: str, parameters: dict):
        """Execute a tool with given parameters"""
        await self.send_message({
            "type": "execute_tool",
            "tool": tool_name,
            "parameters": parameters
        })
        response = await self.receive_message()
        return response
    
    async def send_to_matrix(self, room_id: str, message: str, access_token: str):
        """Send a message to a Matrix room"""
        await self.send_message({
            "type": "matrix_send",
            "room_id": room_id,
            "message": message,
            "access_token": access_token
        })
        response = await self.receive_message()
        return response
    
    async def close(self):
        """Close the connection"""
        if self.websocket:
            await self.websocket.close()

async def main():
    """Example usage of MCP client"""
    client = MCPClient()
    
    try:
        # Connect to server
        if not await client.connect():
            print("Failed to connect to MCP server")
            return
        
        # List available tools
        print("\n--- Listing available tools ---")
        tools_response = await client.list_tools()
        if tools_response.get("type") == "tools_list":
            tools = tools_response.get("tools", [])
            for tool in tools:
                print(f"- {tool['name']}: {tool['description']}")
                print(f"  Parameters: {tool['parameters']}")
        
        # Example 1: Use calculator tool
        print("\n--- Testing calculator tool ---")
        calc_result = await client.execute_tool("calculator", {
            "expression": "2 + 2 * 3"
        })
        print(f"Calculator result: {calc_result}")
        
        # Example 2: Use file read tool (will fail due to security restrictions)
        print("\n--- Testing file read tool ---")
        file_result = await client.execute_tool("file_read", {
            "path": "/etc/passwd"  # This should fail
        })
        print(f"File read result: {file_result}")
        
        # Example 3: Web search
        print("\n--- Testing web search tool ---")
        search_result = await client.execute_tool("web_search", {
            "query": "Matrix protocol MCP integration"
        })
        print(f"Search result: {search_result}")
        
        # Example 4: Send to Matrix (requires valid access token)
        # Uncomment and provide valid credentials to test
        # print("\n--- Testing Matrix send ---")
        # matrix_result = await client.send_to_matrix(
        #     room_id="!your_room_id:matrix.oculair.ca",
        #     message="Hello from MCP client!",
        #     access_token="your_access_token"
        # )
        # print(f"Matrix send result: {matrix_result}")
        
    except Exception as e:
        print(f"Error: {e}")
    finally:
        await client.close()
        print("\nConnection closed.")

if __name__ == "__main__":
    # Check if custom server URL is provided
    if len(sys.argv) > 1:
        server_url = sys.argv[1]
        print(f"Using custom server URL: {server_url}")
        asyncio.run(main())
    else:
        asyncio.run(main())