#!/usr/bin/env python3
"""
MCP Agent Mail <-> Matrix Bridge

Bidirectional bridge that:
1. Forwards Agent Mail messages to Matrix rooms
2. Forwards Matrix messages to Agent Mail inboxes
3. Maps identities between systems
4. Handles file reservation notifications

Architecture:
    Matrix Rooms ←→ Bridge Service ←→ Agent Mail Server
    
Identity Mapping:
    Letta agent_id ←→ Agent Mail name
    Matrix user_id ←→ Agent Mail name
    Matrix room_id ←→ Agent Mail project
"""

import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import httpx
from nio import AsyncClient, MatrixRoom, RoomMessageText, SyncResponse

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class AgentMailBridge:
    """Bridge between MCP Agent Mail and Matrix"""
    
    def __init__(
        self,
        matrix_homeserver: str,
        matrix_user_id: str,
        matrix_access_token: str,
        agent_mail_url: str,
        data_dir: str = "/opt/stacks/matrix-synapse-deployment/matrix_client_data",
        poll_interval: int = 30
    ):
        """
        Initialize bridge service
        
        Args:
            matrix_homeserver: Matrix homeserver URL (e.g., http://synapse:8008)
            matrix_user_id: Bridge bot user ID (e.g., @agent_mail_bridge:matrix.oculair.ca)
            matrix_access_token: Bridge bot access token
            agent_mail_url: MCP Agent Mail server URL (e.g., http://127.0.0.1:8766/mcp/)
            data_dir: Path to matrix_client_data directory
            poll_interval: Seconds between Agent Mail inbox polls
        """
        self.matrix_homeserver = matrix_homeserver
        self.matrix_user_id = matrix_user_id
        self.matrix_access_token = matrix_access_token
        self.agent_mail_url = agent_mail_url
        self.data_dir = Path(data_dir)
        self.poll_interval = poll_interval
        
        # Matrix client
        self.matrix_client = AsyncClient(matrix_homeserver, matrix_user_id)
        self.matrix_client.access_token = matrix_access_token
        
        # HTTP client for Agent Mail (with required headers)
        self.http_client = httpx.AsyncClient(
            timeout=30.0,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream"
            }
        )
        
        # Identity mapping
        self.identity_map = {}
        self.agent_mappings_file = self.data_dir / "agent_mail_mappings.json"
        
        # Last poll timestamps per agent
        self.last_poll_times = {}
        
        # Track processed messages per agent to avoid duplicates
        # Key: (agent_id, message_id) to allow same message to different recipients
        self.processed_messages = set()
        
        # Reverse lookup: code_name -> friendly_name
        self.code_name_to_friendly = {}
    
    def load_identity_mapping(self) -> Dict[str, dict]:
        """
        Load or generate Matrix <-> Agent Mail identity mapping
        
        Returns mapping dictionary with keys:
            - agent_id (Letta UUID) → agent_mail_name
            - matrix_user_id → agent_mail_name
            - agent_mail_name → {agent_id, matrix_user_id, room_id, matrix_name}
        """
        # Load existing mapping if available
        if self.agent_mappings_file.exists():
            logger.info(f"Loading existing identity mapping from {self.agent_mappings_file}")
            with open(self.agent_mappings_file) as f:
                return json.load(f)
        
        # Generate new mapping from agent_user_mappings.json
        logger.info("Generating new identity mapping from Matrix data")
        mappings = {}
        
        agent_file = self.data_dir / "agent_user_mappings.json"
        if not agent_file.exists():
            logger.error(f"Matrix agent mappings not found: {agent_file}")
            return mappings
        
        with open(agent_file) as f:
            agent_data = json.load(f)
        
        for agent_id, data in agent_data.items():
            matrix_name = data.get('agent_name', 'UnknownAgent')
            matrix_user_id = data.get('matrix_user_id')
            room_id = data.get('room_id')
            
            if not matrix_user_id or not room_id:
                logger.warning(f"Skipping {agent_id}: missing user_id or room_id")
                continue
            
            # Generate Agent Mail name
            mail_name = self.sanitize_for_agent_mail(matrix_name)
            
            # Store bidirectional mappings
            mappings[agent_id] = {
                'matrix_user_id': matrix_user_id,
                'matrix_room_id': room_id,
                'matrix_name': matrix_name,
                'agent_mail_name': mail_name,
                'agent_mail_registered': False,
                'last_sync': datetime.now(timezone.utc).isoformat()
            }
        
        # Save mapping file
        with open(self.agent_mappings_file, 'w') as f:
            json.dump(mappings, f, indent=2)
        
        logger.info(f"Generated identity mapping for {len(mappings)} agents")
        return mappings
    
    def sanitize_for_agent_mail(self, matrix_name: str) -> str:
        """
        Convert Matrix agent name to valid Agent Mail name
        
        Rules:
        - Remove special characters
        - CamelCase words
        - Max 3 words (keep it short)
        
        Examples:
            "Huly - Matrix Synapse Deployment" → "HulyMatrixSynapse"
            "BMO" → "BMO"
            "Meridian" → "Meridian"
            "GraphitiExplorer" → "GraphitiExplorer"
        """
        # Remove special chars except spaces
        clean = re.sub(r'[^a-zA-Z0-9\s]', '', matrix_name)
        
        # Split and capitalize
        words = clean.split()
        
        # Handle single word
        if len(words) == 1:
            return words[0].capitalize()
        
        # Take first 3 words, CamelCase
        return ''.join(word.capitalize() for word in words[:3])
    
    async def register_agent_in_mail(self, agent_id: str) -> bool:
        """
        Register agent in MCP Agent Mail if not already registered
        
        Args:
            agent_id: Letta agent ID
            
        Returns:
            True if successful, False otherwise
        """
        agent_info = self.identity_map.get(agent_id)
        if not agent_info:
            logger.error(f"Agent {agent_id} not found in identity mapping")
            return False
        
        if agent_info.get('agent_mail_registered'):
            return True
        
        mail_name = agent_info['agent_mail_name']
        
        logger.info(f"Registering {mail_name} in Agent Mail")
        
        try:
            # Call Agent Mail register_agent tool
            payload = {
                "jsonrpc": "2.0",
                "id": f"register-{agent_id}",
                "method": "tools/call",
                "params": {
                    "name": "register_agent",
                    "arguments": {
                        "project_key": "/opt/stacks/matrix-synapse-deployment",
                        "program": "letta",
                        "model": "unknown",  # Could fetch from Letta API
                        "name": mail_name,
                        "task_description": agent_info['matrix_name']
                    }
                }
            }
            
            # Add authentication header if token is available
            headers = {}
            api_token = os.getenv('AGENT_MAIL_API_TOKEN')
            if api_token:
                headers['Authorization'] = f'Bearer {api_token}'
            
            response = await self.http_client.post(
                self.agent_mail_url, 
                json=payload,
                headers=headers if headers else None
            )
            
            if response.status_code == 200:
                # Parse the response to get the assigned agent name
                result = response.json()
                
                # MCP response structure: check for the assigned name
                assigned_name = None
                if isinstance(result, dict):
                    # Try to extract from MCP response structure
                    if 'result' in result:
                        mcp_result = result['result']
                        if isinstance(mcp_result, dict) and 'content' in mcp_result:
                            # Parse structured content
                            for content_item in mcp_result['content']:
                                if content_item.get('type') == 'text':
                                    # Extract agent name from text response
                                    text = content_item.get('text', '')
                                    if 'assigned name:' in text.lower():
                                        # Extract the name after "assigned name:"
                                        parts = text.split('assigned name:', 1)
                                        if len(parts) > 1:
                                            assigned_name = parts[1].strip().split()[0]
                
                # Update mapping with assigned name if we got one
                if assigned_name:
                    agent_info['agent_mail_name'] = assigned_name
                    logger.info(f"Registered {mail_name} -> assigned name: {assigned_name}")
                else:
                    logger.info(f"Successfully registered {mail_name}")
                    
                agent_info['agent_mail_registered'] = True
                self.save_identity_mapping()
                return True
            else:
                logger.error(f"Failed to register {mail_name}: {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"Error registering {mail_name}: {e}")
            return False
    
    async def fetch_agent_mail_inbox(self, mail_name: str) -> List[dict]:
        """
        Fetch new messages from Agent Mail inbox
        
        Args:
            mail_name: Agent Mail agent name
            
        Returns:
            List of message dictionaries
        """
        try:
            # Get last poll time
            last_poll = self.last_poll_times.get(mail_name)
            since_ts = None
            if last_poll:
                since_ts = datetime.fromtimestamp(last_poll, tz=timezone.utc).isoformat()
            
            # Call fetch_inbox tool
            payload = {
                "jsonrpc": "2.0",
                "id": f"fetch-{mail_name}-{time.time()}",
                "method": "tools/call",
                "params": {
                    "name": "fetch_inbox",
                    "arguments": {
                        "project_key": "/opt/stacks/matrix-synapse-deployment",
                        "agent_name": mail_name,
                        "include_bodies": True,
                        "limit": 50
                    }
                }
            }
            
            if since_ts:
                payload["params"]["arguments"]["since_ts"] = since_ts
            
            # Add authentication header if token is available
            headers = {}
            api_token = os.getenv('AGENT_MAIL_API_TOKEN')
            if api_token:
                headers['Authorization'] = f'Bearer {api_token}'
            
            response = await self.http_client.post(
                self.agent_mail_url, 
                json=payload,
                headers=headers if headers else None
            )
            
            if response.status_code == 200:
                result = response.json()
                
                # MCP response structure: result.structuredContent.result contains the actual data
                messages = []
                if isinstance(result, dict):
                    # Try structuredContent.result first (MCP format)
                    if 'result' in result and isinstance(result['result'], dict):
                        structured = result['result'].get('structuredContent', {})
                        if 'result' in structured:
                            messages = structured['result']
                        else:
                            messages = result.get('result', [])
                    else:
                        messages = result.get('result', [])
                elif isinstance(result, list):
                    messages = result
                
                # Ensure messages is a list
                if not isinstance(messages, list):
                    # Not an error - agent likely not registered yet or no messages
                    return []
                
                # Update last poll time
                self.last_poll_times[mail_name] = time.time()
                
                if messages:
                    logger.info(f"Fetched {len(messages)} messages for {mail_name}")
                return messages
            else:
                logger.error(f"Failed to fetch inbox for {mail_name}: {response.text}")
                return []
                
        except Exception as e:
            logger.error(f"Error fetching inbox for {mail_name}: {e}")
            return []
    
    def format_for_matrix(self, message: dict) -> str:
        """
        Format Agent Mail message for Matrix display
        
        Args:
            message: Agent Mail message dict
            
        Returns:
            Formatted markdown string
        """
        importance = message.get('importance', 'normal')
        importance_icon = ''
        if importance in ['high', 'urgent']:
            importance_icon = '⚠️ '
        
        # Look up friendly name from code name
        code_name = message.get('from', 'Unknown')
        friendly_name = self.code_name_to_friendly.get(code_name, code_name)
        
        lines = [
            "📬 **Agent Mail Message**",
            "",
            f"**From:** {friendly_name}",
            f"**Subject:** {message.get('subject', 'No subject')}",
        ]
        
        if importance_icon:
            lines.append(f"**Importance:** {importance_icon}{importance}")
        
        if message.get('created_ts'):
            lines.append(f"**Time:** {message['created_ts']}")
        
        lines.extend([
            "",
            message.get('body_md', message.get('body', ''))
        ])
        
        if message.get('ack_required'):
            lines.extend([
                "",
                "⚡ *Acknowledgement required*"
            ])
        
        return "\n".join(lines)
    
    async def send_to_matrix(self, agent_id: str, message: dict):
        """
        Send Agent Mail message to Matrix room
        
        Args:
            agent_id: Letta agent ID
            message: Message from Agent Mail inbox
        """
        agent_info = self.identity_map.get(agent_id)
        if not agent_info:
            logger.error(f"Agent {agent_id} not found in mapping")
            return
        
        room_id = agent_info.get('matrix_room_id')
        if not room_id:
            logger.error(f"No room_id for agent {agent_id}")
            return
        
        # Check if already processed for THIS agent
        msg_id = message.get('id')
        dedup_key = (agent_id, msg_id) if msg_id else None
        if dedup_key and dedup_key in self.processed_messages:
            return
        
        # Format message
        body = self.format_for_matrix(message)
        
        logger.info(f"Sending to room {room_id}: {body[:100]}...")
        
        try:
            # Send to Matrix room via direct HTTP (avoids nio sync issues)
            import uuid
            txn_id = str(uuid.uuid4())
            url = f"{self.matrix_homeserver}/_matrix/client/r0/rooms/{room_id}/send/m.room.message/{txn_id}"
            
            response = await self.http_client.put(
                url,
                params={"access_token": self.matrix_access_token},
                json={
                    "msgtype": "m.text",
                    "body": body,
                    "format": "org.matrix.custom.html",
                    "formatted_body": self.markdown_to_html(body)
                }
            )
            
            logger.info(f"Forwarded message to Matrix room {room_id}, status: {response.status_code}")
            
            # Mark as processed for this agent
            if dedup_key:
                self.processed_messages.add(dedup_key)
                
        except Exception as e:
            logger.error(f"Error sending to Matrix room {room_id}: {e}", exc_info=True)
    
    def markdown_to_html(self, markdown: str) -> str:
        """Simple markdown to HTML conversion for Matrix"""
        html = markdown
        # Bold
        html = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', html)
        # Italic
        html = re.sub(r'\*(.+?)\*', r'<em>\1</em>', html)
        # Code
        html = re.sub(r'`(.+?)`', r'<code>\1</code>', html)
        # Line breaks
        html = html.replace('\n', '<br/>')
        return html
    
    async def forward_agent_mail_to_matrix(self):
        """
        Poll Agent Mail inboxes and forward new messages to Matrix
        
        Runs continuously until stopped
        """
        logger.info("Starting Agent Mail → Matrix forwarding loop")
        
        while True:
            try:
                # Process each agent
                for agent_id, agent_info in self.identity_map.items():
                    mail_name = agent_info['agent_mail_name']
                    
                    # Auto-register agent if not already registered
                    if not agent_info.get('agent_mail_registered'):
                        await self.register_agent_in_mail(agent_id)
                    
                    # Fetch new messages (will return empty if agent not yet registered)
                    messages = await self.fetch_agent_mail_inbox(mail_name)
                    
                    # Forward to Matrix
                    for msg in messages:
                        await self.send_to_matrix(agent_id, msg)
                    
                    # Small delay between agents to avoid overwhelming Agent Mail
                    await asyncio.sleep(0.1)
                
                # Wait before next poll
                await asyncio.sleep(self.poll_interval)
                
            except Exception as e:
                logger.error(f"Error in forwarding loop: {e}")
                await asyncio.sleep(60)
    
    def is_dev_message(self, body: str) -> bool:
        """
        Check if Matrix message is dev-related and should go to Agent Mail
        
        Keywords: file, reserve, reservation, conflict, edit, commit, etc.
        """
        keywords = [
            'file', 'reserve', 'reservation', 'conflict',
            'edit', 'commit', 'push', 'pull', 'merge',
            'lock', 'unlock', 'coordinate', 'working on',
            'blocked', 'lease', 'claim'
        ]
        body_lower = body.lower()
        return any(keyword in body_lower for keyword in keywords)
    
    async def matrix_message_callback(self, room, event):
        """
        Handle incoming Matrix messages
        
        Forward dev-related messages to Agent Mail
        """
        # Ignore own messages
        if event.sender == self.matrix_user_id:
            return
        
        # Ignore non-text messages
        if not isinstance(event, RoomMessageText):
            return
        
        # Find agent for this room
        agent_id = None
        for aid, info in self.identity_map.items():
            if info.get('matrix_room_id') == room.room_id:
                agent_id = aid
                break
        
        if not agent_id:
            return
        
        # Check if dev message
        body = event.body
        if not self.is_dev_message(body):
            return
        
        # Forward to Agent Mail
        agent_info = self.identity_map[agent_id]
        mail_name = agent_info['agent_mail_name']
        
        logger.info(f"Forwarding Matrix message to Agent Mail: {mail_name}")
        
        try:
            # Send to Agent Mail
            payload = {
                "jsonrpc": "2.0",
                "id": f"send-{time.time()}",
                "method": "tools/call",
                "params": {
                    "name": "send_message",
                    "arguments": {
                        "project_key": "/opt/stacks/matrix-synapse-deployment",
                        "sender_name": "MatrixBridge",
                        "to": [mail_name],
                        "subject": f"Message from {event.sender}",
                        "body_md": body,
                        "importance": "normal"
                    }
                }
            }
            
            response = await self.http_client.post(self.agent_mail_url, json=payload)
            
            if response.status_code == 200:
                logger.info(f"Forwarded to Agent Mail: {mail_name}")
            else:
                logger.error(f"Failed to forward to Agent Mail: {response.text}")
                
        except Exception as e:
            logger.error(f"Error forwarding to Agent Mail: {e}")
    
    async def join_agent_rooms(self):
        """Join all agent rooms so bridge can listen to messages"""
        logger.info("Joining all agent rooms")
        
        # First, check for and accept any invitations (with timeout)
        try:
            sync_response = await asyncio.wait_for(
                self.matrix_client.sync(timeout=10000), 
                timeout=15
            )
        except asyncio.TimeoutError:
            logger.warning("Sync for room invitations timed out, skipping invitation check")
            sync_response = None
        
        # Check if sync was successful (SyncResponse type, not SyncError)
        if sync_response and isinstance(sync_response, SyncResponse) and sync_response.rooms.invite:
            invited_rooms = sync_response.rooms.invite
            logger.info(f"Found {len(invited_rooms)} room invitations")
            for room_id in invited_rooms:
                try:
                    await self.matrix_client.join(room_id)
                    logger.info(f"Accepted invitation and joined room {room_id}")
                except Exception as e:
                    logger.warning(f"Could not accept invitation to room {room_id}: {e}")
        
        # Then join/verify membership in all mapped agent rooms
        for agent_id, agent_info in self.identity_map.items():
            room_id = agent_info.get('matrix_room_id')
            if not room_id:
                continue
            
            try:
                await self.matrix_client.join(room_id)
                logger.info(f"Joined room {room_id}")
            except Exception as e:
                # Ignore "already joined" errors
                if "already" not in str(e).lower():
                    logger.warning(f"Could not join room {room_id}: {e}")
    
    def save_identity_mapping(self):
        """Save identity mapping to file"""
        with open(self.agent_mappings_file, 'w') as f:
            json.dump(self.identity_map, f, indent=2)
    
    async def start(self):
        """Start the bridge service"""
        logger.info("Starting Agent Mail <-> Matrix Bridge")
        
        # Load identity mapping
        self.identity_map = self.load_identity_mapping()
        logger.info(f"Loaded {len(self.identity_map)} agent identities")
        
        # Build reverse lookup: code_name -> friendly_name
        self.code_name_to_friendly = {}
        for agent_id, info in self.identity_map.items():
            code_name = info.get('agent_mail_name')
            friendly_name = info.get('matrix_name')
            if code_name and friendly_name:
                self.code_name_to_friendly[code_name] = friendly_name
        logger.info(f"Built code name lookup for {len(self.code_name_to_friendly)} agents")
        
        # Connect to Matrix - skip initial sync since we just need to send messages
        logger.info(f"Connecting to Matrix as {self.matrix_user_id}")
        # Use a short timeout for initial sync to avoid blocking
        try:
            await asyncio.wait_for(self.matrix_client.sync(timeout=10000), timeout=30)
            logger.info("Initial sync completed")
        except asyncio.TimeoutError:
            logger.warning("Initial sync timed out, continuing anyway (sending should still work)")
        
        # Skip room joins - room_send works without joining, and join causes slow syncs
        # await self.join_agent_rooms()
        logger.info("Skipping room joins (sending works without joining)")
        
        # Register Matrix message callback
        self.matrix_client.add_event_callback(
            self.matrix_message_callback,
            RoomMessageText
        )
        
        # Start forwarding tasks
        tasks = [
            asyncio.create_task(self.forward_agent_mail_to_matrix()),
            asyncio.create_task(self.matrix_client.sync_forever(timeout=30000))
        ]
        
        logger.info("Bridge service started successfully")
        
        # Wait for all tasks
        await asyncio.gather(*tasks)
    
    async def stop(self):
        """Stop the bridge service"""
        logger.info("Stopping bridge service")
        await self.http_client.aclose()
        await self.matrix_client.close()


async def main():
    """Main entry point"""
    # Load config from environment
    matrix_homeserver = os.getenv('MATRIX_HOMESERVER_URL', 'http://synapse:8008')
    matrix_user_id = os.getenv('MATRIX_USER_ID', '@agent_mail_bridge:matrix.oculair.ca')
    matrix_access_token = os.getenv('MATRIX_ACCESS_TOKEN')
    agent_mail_url = os.getenv('AGENT_MAIL_URL', 'http://127.0.0.1:8766/mcp/')
    data_dir = os.getenv('DATA_DIR', '/opt/stacks/matrix-synapse-deployment/matrix_client_data')
    poll_interval = int(os.getenv('POLL_INTERVAL', '30'))
    
    if not matrix_access_token:
        logger.error("MATRIX_ACCESS_TOKEN not set")
        return
    
    # Create and start bridge
    bridge = AgentMailBridge(
        matrix_homeserver=matrix_homeserver,
        matrix_user_id=matrix_user_id,
        matrix_access_token=matrix_access_token,
        agent_mail_url=agent_mail_url,
        data_dir=data_dir,
        poll_interval=poll_interval
    )
    
    try:
        await bridge.start()
    except KeyboardInterrupt:
        logger.info("Received interrupt signal")
    finally:
        await bridge.stop()


if __name__ == '__main__':
    asyncio.run(main())
