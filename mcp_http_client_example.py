#!/usr/bin/env python3
"""
Example MCP HTTP Client demonstrating how to interact with the MCP HTTP Streaming Server
"""
import asyncio
import aiohttp
import json
import logging
from typing import Dict, Any, Optional, AsyncIterator
import uuid

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class MCPHTTPClient:
    """Example MCP HTTP client using the Streamable HTTP transport"""
    
    def __init__(self, server_url: str = "http://localhost:8006/mcp"):
        self.server_url = server_url
        self.session_id: Optional[str] = None
        self.request_counter = 0
        
    def _generate_request_id(self) -> str:
        """Generate unique request ID"""
        self.request_counter += 1
        return f"req-{self.request_counter}"
    
    async def initialize(self) -> Dict[str, Any]:
        """Initialize connection with MCP server"""
        async with aiohttp.ClientSession() as session:
            request = {
                "jsonrpc": "2.0",
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "clientInfo": {
                        "name": "example-client",
                        "version": "1.0.0"
                    }
                },
                "id": self._generate_request_id()
            }
            
            headers = {
                "Accept": "application/json",
                "Content-Type": "application/json"
            }
            
            async with session.post(self.server_url, json=request, headers=headers) as response:
                # Store session ID if provided
                if 'Mcp-Session-Id' in response.headers:
                    self.session_id = response.headers['Mcp-Session-Id']
                    logger.info(f"Received session ID: {self.session_id}")
                
                result = await response.json()
                
                # Send initialized notification
                if result.get("result"):
                    await self._send_notification("initialized", {})
                
                return result
    
    async def _send_notification(self, method: str, params: Dict[str, Any]):
        """Send a notification (no response expected)"""
        async with aiohttp.ClientSession() as session:
            notification = {
                "jsonrpc": "2.0",
                "method": method,
                "params": params
            }
            
            headers = {
                "Accept": "application/json",
                "Content-Type": "application/json"
            }
            
            if self.session_id:
                headers["Mcp-Session-Id"] = self.session_id
            
            await session.post(self.server_url, json=notification, headers=headers)
    
    async def list_tools(self) -> Dict[str, Any]:
        """List available tools"""
        async with aiohttp.ClientSession() as session:
            request = {
                "jsonrpc": "2.0",
                "method": "tools/list",
                "params": {},
                "id": self._generate_request_id()
            }
            
            headers = {
                "Accept": "application/json",
                "Content-Type": "application/json"
            }
            
            if self.session_id:
                headers["Mcp-Session-Id"] = self.session_id
            
            async with session.post(self.server_url, json=request, headers=headers) as response:
                return await response.json()
    
    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Call a specific tool"""
        async with aiohttp.ClientSession() as session:
            request = {
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": tool_name,
                    "arguments": arguments
                },
                "id": self._generate_request_id()
            }
            
            headers = {
                "Accept": "application/json",
                "Content-Type": "application/json"
            }
            
            if self.session_id:
                headers["Mcp-Session-Id"] = self.session_id
            
            async with session.post(self.server_url, json=request, headers=headers) as response:
                return await response.json()
    
    async def call_tool_streaming(self, tool_name: str, arguments: Dict[str, Any]) -> AsyncIterator[Dict[str, Any]]:
        """Call a tool and receive streaming response via SSE"""
        async with aiohttp.ClientSession() as session:
            request = {
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": tool_name,
                    "arguments": arguments
                },
                "id": self._generate_request_id()
            }
            
            headers = {
                "Accept": "text/event-stream",  # Request SSE stream
                "Content-Type": "application/json"
            }
            
            if self.session_id:
                headers["Mcp-Session-Id"] = self.session_id
            
            async with session.post(self.server_url, json=request, headers=headers) as response:
                async for line in response.content:
                    line = line.decode('utf-8').strip()
                    
                    if line.startswith('data: '):
                        data_str = line[6:]  # Remove 'data: ' prefix
                        try:
                            yield json.loads(data_str)
                        except json.JSONDecodeError:
                            logger.error(f"Failed to parse SSE data: {data_str}")
    
    async def open_event_stream(self) -> AsyncIterator[Dict[str, Any]]:
        """Open an SSE stream for server-initiated messages"""
        async with aiohttp.ClientSession() as session:
            headers = {
                "Accept": "text/event-stream"
            }
            
            if self.session_id:
                headers["Mcp-Session-Id"] = self.session_id
            
            async with session.get(self.server_url, headers=headers) as response:
                logger.info("Opened SSE stream for server messages")
                
                async for line in response.content:
                    line = line.decode('utf-8').strip()
                    
                    if line.startswith('data: '):
                        data_str = line[6:]
                        try:
                            yield json.loads(data_str)
                        except json.JSONDecodeError:
                            logger.error(f"Failed to parse SSE data: {data_str}")
                    elif line.startswith('id: '):
                        event_id = line[4:]
                        logger.debug(f"Received event ID: {event_id}")
    
    async def close_session(self):
        """Close the current session"""
        if not self.session_id:
            return
        
        async with aiohttp.ClientSession() as session:
            headers = {
                "Mcp-Session-Id": self.session_id
            }
            
            async with session.delete(self.server_url, headers=headers) as response:
                if response.status == 204:
                    logger.info("Session closed successfully")
                else:
                    logger.error(f"Failed to close session: {response.status}")


async def main():
    """Example usage of the MCP HTTP client"""
    client = MCPHTTPClient()
    
    try:
        # Initialize connection
        logger.info("Initializing MCP connection...")
        init_result = await client.initialize()
        logger.info(f"Initialization result: {json.dumps(init_result, indent=2)}")
        
        # List available tools
        logger.info("\nListing available tools...")
        tools_result = await client.list_tools()
        logger.info(f"Available tools: {json.dumps(tools_result, indent=2)}")
        
        # Example 1: Calculator tool
        logger.info("\nCalling calculator tool...")
        calc_result = await client.call_tool("calculator", {
            "expression": "2 + 2 * 3"
        })
        logger.info(f"Calculator result: {json.dumps(calc_result, indent=2)}")
        
        # Example 2: Web search tool
        logger.info("\nCalling web search tool...")
        search_result = await client.call_tool("web_search", {
            "query": "MCP protocol specification"
        })
        logger.info(f"Search result: {json.dumps(search_result, indent=2)}")
        
        # Example 3: File read tool (will fail due to security restrictions)
        logger.info("\nTrying file read tool...")
        file_result = await client.call_tool("file_read", {
            "path": "/etc/passwd"  # This should fail
        })
        logger.info(f"File read result: {json.dumps(file_result, indent=2)}")
        
        # Example 4: Streaming response
        logger.info("\nCalling tool with streaming response...")
        async for event in client.call_tool_streaming("calculator", {
            "expression": "sum([1, 2, 3, 4, 5])"
        }):
            logger.info(f"Streaming event: {json.dumps(event, indent=2)}")
        
        # Example 5: Batch requests
        logger.info("\nSending batch requests...")
        async with aiohttp.ClientSession() as session:
            batch_requests = [
                {
                    "jsonrpc": "2.0",
                    "method": "tools/call",
                    "params": {
                        "name": "calculator",
                        "arguments": {"expression": f"{i} * {i}"}
                    },
                    "id": f"batch-{i}"
                }
                for i in range(1, 4)
            ]
            
            headers = {
                "Accept": "text/event-stream",
                "Content-Type": "application/json"
            }
            
            if client.session_id:
                headers["Mcp-Session-Id"] = client.session_id
            
            async with session.post(client.server_url, json=batch_requests, headers=headers) as response:
                async for line in response.content:
                    line = line.decode('utf-8').strip()
                    if line.startswith('data: '):
                        data = json.loads(line[6:])
                        logger.info(f"Batch response: {json.dumps(data, indent=2)}")
        
    finally:
        # Close session
        await client.close_session()


async def streaming_listener_example():
    """Example of listening for server-initiated messages"""
    client = MCPHTTPClient()
    
    # Initialize first
    await client.initialize()
    
    # Open event stream
    logger.info("Starting event stream listener...")
    try:
        async for event in client.open_event_stream():
            logger.info(f"Server event: {json.dumps(event, indent=2)}")
    except KeyboardInterrupt:
        logger.info("Stopping event stream...")
    finally:
        await client.close_session()


if __name__ == "__main__":
    # Run the main example
    asyncio.run(main())
    
    # Uncomment to run the streaming listener example
    # asyncio.run(streaming_listener_example())