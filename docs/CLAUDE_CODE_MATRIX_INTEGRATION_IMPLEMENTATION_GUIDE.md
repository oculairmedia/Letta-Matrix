# Unified Agent Matrix Integration - Implementation Guide

## Executive Summary

This document provides detailed implementation specifications for creating a unified agent architecture that treats Claude Code, Letta, and any future agents as equal participants in the Matrix ecosystem. Rather than building Claude Code-specific tools, we create a universal agent bridge system that any agent type can use for bidirectional communication, persistent conversation threads, and multi-agent collaboration.

## Table of Contents

1. [Unified Agent Architecture](#unified-agent-architecture)
2. [Universal Agent Bridge System](#universal-agent-bridge-system)
3. [Agent Communication Protocol](#agent-communication-protocol)
4. [Thread Management System](#thread-management-system)
5. [MCP Tool Extensions](#mcp-tool-extensions)
6. [Agent Registration & Discovery](#agent-registration--discovery)
7. [Security & Validation](#security--validation)
8. [Testing Strategy](#testing-strategy)
9. [Deployment Guide](#deployment-guide)
10. [Migration from Current System](#migration-from-current-system)

## Unified Agent Architecture

### Core Principle: Agent Type Agnostic Design

Instead of building separate systems for Claude Code vs Letta agents, we create a universal agent bridge that treats all agents equally. Any agent (Claude Code, Letta, future AI systems) can:

1. **Register** with the Matrix system
2. **Create and join** conversation threads
3. **Send and receive** messages bidirectionally
4. **Use MCP tools** for Matrix operations
5. **Collaborate** with other agents seamlessly

### Current System Strengths (Reusable for All Agents)

#### 1. **Universal MCP Server Infrastructure**
- **File**: `mcp_http_server.py`
- **Capabilities**: HTTP streaming on port 8016, WebSocket on port 8015
- **Pattern**: Tool registration with pre-configured authentication
- **Extension**: Add universal agent communication tools (not Claude-specific)

#### 2. **Agent Management System (Already Universal)**
- **File**: `agent_user_manager.py`
- **Capabilities**: Agent-to-Matrix user mapping, automatic user creation
- **Pattern**: Stable agent IDs, individual Matrix identities
- **Current**: Works for Letta agents, can work for any agent type

#### 3. **Matrix API Service (Already Universal)**
- **File**: `matrix_api.py`
- **Capabilities**: FastAPI REST interface on port 8004
- **Pattern**: Authentication management, rate limiting, error handling
- **Extension**: Add agent-agnostic thread management endpoints

#### 4. **Bridge Architecture Pattern (Reusable)**
- **Example**: GMMessages bridge integration
- **Pattern**: Bidirectional message sync, automatic room creation
- **Application**: Same pattern works for any external agent system

### Agent Integration Patterns

Different agent types have different integration mechanisms, but they all need the same Matrix capabilities:

#### Claude Code Integration Pattern
- **Mechanism**: Hook system with 8 lifecycle events
- **Communication**: JSON via stdin/stdout with UV scripts
- **Capabilities**: Exit code control, security validation, audit logging

#### Letta Integration Pattern
- **Mechanism**: Direct API integration
- **Communication**: HTTP/WebSocket APIs
- **Capabilities**: Real-time messaging, memory management, tool access

#### Future Agent Patterns
- **OpenAI Assistants**: API-based integration
- **Local LLMs**: Direct Python integration
- **Custom Agents**: Webhook or WebSocket integration

#### Universal Bridge Requirements
All agent types need:
1. **Thread Management**: Create, join, leave conversation threads
2. **Message Routing**: Send/receive messages with proper attribution
3. **Tool Access**: Use MCP tools for Matrix operations
4. **Security**: Validation and sandboxing appropriate to agent type
5. **Monitoring**: Health checks, metrics, logging

## Universal Agent Bridge System

### Core Architecture: Agent-Agnostic Design

Instead of building Claude Code-specific tools, we create universal agent communication tools that any agent type can use:

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   Claude Code   │    │   Letta Agent   │    │  Future Agent   │
│   (via hooks)   │    │   (via API)     │    │  (via webhook)  │
└─────────┬───────┘    └─────────┬───────┘    └─────────┬───────┘
          │                      │                      │
          ▼                      ▼                      ▼
┌─────────────────────────────────────────────────────────────────┐
│                Universal Agent Bridge                           │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐ │
│  │ Message Router  │  │ Thread Manager  │  │ Agent Registry  │ │
│  └─────────────────┘  └─────────────────┘  └─────────────────┘ │
└─────────────────────────────┬───────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    MCP Server (Extended)                       │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐ │
│  │ agent_create_   │  │ agent_send_     │  │ agent_list_     │ │
│  │ thread          │  │ message         │  │ threads         │ │
│  └─────────────────┘  └─────────────────┘  └─────────────────┘ │
└─────────────────────────────┬───────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Matrix Synapse                            │
└─────────────────────────────────────────────────────────────────┘
```

### Implementation Strategy

#### Phase 1: Universal MCP Tools (Week 1)
**Objective**: Create agent-agnostic MCP tools that any agent type can use

**Deliverables**:
1. **Universal Thread Tools** (5 tools: create, join, send, list, monitor)
2. **Agent Registration System** (unified agent identity management)
3. **Message Attribution** (proper sender identification across agent types)

#### Phase 2: Agent Bridge Framework (Week 2)
**Objective**: Create pluggable bridge system for different agent types

**Deliverables**:
1. **Bridge Interface** (abstract base class for agent integrations)
2. **Claude Code Bridge** (implements interface using hooks)
3. **Letta Bridge Enhancement** (migrate existing system to new interface)

#### Phase 3: Bidirectional Communication (Week 3)
**Objective**: Enable Matrix-to-agent message routing for all agent types

**Deliverables**:
1. **Universal Message Router** (routes Matrix messages to appropriate agents)
2. **Session Management** (tracks active agent sessions across types)
3. **Conflict Resolution** (handles multiple agents in same thread)

#### Phase 4: Advanced Features (Week 4)
**Objective**: Add collaboration and monitoring features

**Deliverables**:
1. **Multi-Agent Collaboration** (agents can summon and work with each other)
2. **Universal Monitoring** (health checks, metrics for all agent types)
3. **Thread Analytics** (conversation insights across agent types)

## Agent Communication Protocol

### Universal MCP Tools (Agent-Agnostic)

Instead of Claude-specific tools, we create universal tools that any agent can use:

#### 1. `agent_create_thread` Tool
```python
class AgentCreateThreadTool(MCPTool):
    def __init__(self, matrix_api_url: str, matrix_homeserver: str, username: str, password: str):
        super().__init__(
            name="agent_create_thread",
            description="Create a new conversation thread for any agent type",
            input_schema={
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string", "description": "Unique agent identifier"},
                    "agent_type": {"type": "string", "enum": ["claude_code", "letta", "openai", "custom"], "description": "Type of agent"},
                    "thread_name": {"type": "string", "description": "Human-readable thread name"},
                    "project_context": {"type": "string", "description": "Project or context information"},
                    "participants": {"type": "array", "items": {"type": "string"}, "description": "Other agent IDs to invite"},
                    "metadata": {"type": "object", "description": "Agent-specific metadata"}
                },
                "required": ["agent_id", "agent_type", "thread_name"]
            }
        )
```

#### 2. `agent_join_thread` Tool
```python
class AgentJoinThreadTool(MCPTool):
    def __init__(self, matrix_api_url: str, matrix_homeserver: str, username: str, password: str):
        super().__init__(
            name="agent_join_thread",
            description="Join an existing conversation thread",
            input_schema={
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string", "description": "Agent identifier"},
                    "thread_id": {"type": "string", "description": "Thread to join"},
                    "join_reason": {"type": "string", "description": "Why agent is joining (for context)"}
                },
                "required": ["agent_id", "thread_id"]
            }
        )
```

#### 3. `agent_send_message` Tool
```python
class AgentSendMessageTool(MCPTool):
    def __init__(self, matrix_api_url: str, matrix_homeserver: str, username: str, password: str):
        super().__init__(
            name="agent_send_message",
            description="Send a message to any conversation thread",
            input_schema={
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string", "description": "Sending agent identifier"},
                    "thread_id": {"type": "string", "description": "Target thread identifier"},
                    "message": {"type": "string", "description": "Message content to send"},
                    "message_type": {"type": "string", "enum": ["text", "code", "error", "system"], "default": "text"},
                    "reply_to": {"type": "string", "description": "Message ID being replied to"},
                    "attachments": {"type": "array", "items": {"type": "string"}, "description": "File paths or URLs"},
                    "metadata": {"type": "object", "description": "Agent-specific message metadata"}
                },
                "required": ["agent_id", "thread_id", "message"]
            }
        )
```

#### 4. `agent_list_threads` Tool
```python
class AgentListThreadsTool(MCPTool):
    def __init__(self, matrix_api_url: str, matrix_homeserver: str, username: str, password: str):
        super().__init__(
            name="agent_list_threads",
            description="List conversation threads for any agent type",
            input_schema={
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string", "description": "Agent identifier (optional - lists all if omitted)"},
                    "agent_type": {"type": "string", "enum": ["claude_code", "letta", "openai", "custom"], "description": "Filter by agent type"},
                    "status": {"type": "string", "enum": ["active", "archived", "error"], "description": "Thread status"},
                    "limit": {"type": "integer", "default": 50, "description": "Maximum threads to return"},
                    "since": {"type": "string", "description": "ISO timestamp for filtering recent threads"}
                }
            }
        )
```

#### 5. `agent_monitor_threads` Tool
```python
class AgentMonitorThreadsTool(MCPTool):
    def __init__(self, matrix_api_url: str, matrix_homeserver: str, username: str, password: str):
        super().__init__(
            name="agent_monitor_threads",
            description="Monitor conversation threads for keywords and patterns (any agent type)",
            input_schema={
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string", "description": "Monitoring agent identifier"},
                    "keywords": {"type": "array", "items": {"type": "string"}, "description": "Keywords to monitor"},
                    "thread_filters": {"type": "object", "description": "Thread filtering criteria"},
                    "notification_config": {"type": "object", "description": "How to notify when matches found"},
                    "error_detection": {"type": "boolean", "default": True, "description": "Enable error pattern detection"}
                },
                "required": ["agent_id", "keywords"]
            }
        )
```

## Thread Management System

### Universal Thread Model

All agents use the same thread model regardless of their type:

```python
@dataclass
class UniversalThread:
    thread_id: str
    room_id: str  # Matrix room ID
    created_by: str  # Agent ID that created the thread
    agent_type: str  # Type of creating agent
    thread_name: str
    participants: List[str]  # List of agent IDs in thread
    status: str  # 'active', 'archived', 'error'
    project_context: Optional[str]
    created_at: datetime
    updated_at: datetime
    metadata: Dict[str, Any]  # Agent-specific data

    def can_agent_join(self, agent_id: str) -> bool:
        """Check if agent can join this thread"""
        # Universal access control logic
        return True  # For now, all agents can join any thread

    def add_participant(self, agent_id: str):
        """Add agent to thread participants"""
        if agent_id not in self.participants:
            self.participants.append(agent_id)
            self.updated_at = datetime.utcnow()
```

### Agent Bridge Interface

All agent integrations implement this interface:

```python
from abc import ABC, abstractmethod

class AgentBridge(ABC):
    """Universal interface for agent integrations"""

    def __init__(self, agent_type: str, config: Dict[str, Any]):
        self.agent_type = agent_type
        self.config = config
        self.active_sessions = {}  # agent_id -> session_info

    @abstractmethod
    async def start_bridge(self):
        """Start the bridge service"""
        pass

    @abstractmethod
    async def stop_bridge(self):
        """Stop the bridge service"""
        pass

    @abstractmethod
    async def send_to_agent(self, agent_id: str, message: str, thread_context: Dict[str, Any]):
        """Send message from Matrix to agent"""
        pass

    @abstractmethod
    async def register_agent(self, agent_id: str, agent_config: Dict[str, Any]) -> bool:
        """Register new agent instance"""
        pass

    @abstractmethod
    async def get_agent_status(self, agent_id: str) -> Dict[str, Any]:
        """Get current status of agent"""
        pass

    async def on_agent_message(self, agent_id: str, message: str, thread_id: str):
        """Called when agent sends message - routes to Matrix"""
        await self.universal_router.route_to_matrix(agent_id, message, thread_id)
```

### Claude Code Bridge Implementation

```python
class ClaudeCodeBridge(AgentBridge):
    """Bridge for Claude Code agents using hook system"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__("claude_code", config)
        self.hook_server = None
        self.active_claude_sessions = {}

    async def start_bridge(self):
        """Start Claude Code hook integration"""
        # Set up hook server to receive Claude Code events
        self.hook_server = await self.setup_hook_server()

        # Install hooks in Claude Code directory
        await self.install_claude_hooks()

    async def setup_hook_server(self):
        """Set up HTTP server to receive hook events from Claude Code"""
        from aiohttp import web

        app = web.Application()
        app.router.add_post('/hooks/user_prompt_submit', self.handle_user_prompt_submit)
        app.router.add_post('/hooks/pre_tool_use', self.handle_pre_tool_use)
        app.router.add_post('/hooks/post_tool_use', self.handle_post_tool_use)
        app.router.add_post('/hooks/stop', self.handle_stop)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, 'localhost', 8017)  # Different port from MCP
        await site.start()
        return runner

    async def handle_user_prompt_submit(self, request):
        """Handle Claude Code user prompt submission"""
        payload = await request.json()

        session_id = payload.get('session_id')
        prompt = payload.get('prompt', '')
        timestamp = payload.get('timestamp')

        # Generate universal thread ID
        thread_id = f"claude-{session_id}-{timestamp}"
        agent_id = f"claude-{session_id}"

        # Use universal thread creation
        await self.universal_router.create_thread(
            agent_id=agent_id,
            agent_type="claude_code",
            thread_id=thread_id,
            thread_name=f"Claude: {prompt[:50]}...",
            project_context=payload.get('project_path')
        )

        # Send initial message
        await self.universal_router.route_to_matrix(
            agent_id, f"**User**: {prompt}", thread_id
        )

        return web.json_response({"continue": True})
```

    async def handle_pre_tool_use(self, request):
        """Handle Claude Code tool use events"""
        payload = await request.json()

        tool_name = payload.get('tool_name')
        tool_input = payload.get('tool_input', {})
        session_id = payload.get('session_id')

        agent_id = f"claude-{session_id}"
        thread_id = await self.get_thread_id_from_session(session_id)

        # Send tool call notification to Matrix
        tool_message = f"🔧 **Tool Call**: {tool_name}\n```json\n{json.dumps(tool_input, indent=2)}\n```"
        await self.universal_router.route_to_matrix(
            agent_id, tool_message, thread_id, message_type="system"
        )

        # Universal security validation
        if await self.universal_router.validate_tool_use(tool_name, tool_input, agent_id):
            return web.json_response({"decision": "approve"})
        else:
            error_msg = f"🚫 BLOCKED: Dangerous {tool_name} command detected"
            await self.universal_router.route_to_matrix(
                agent_id, error_msg, thread_id, message_type="error"
            )
            return web.json_response({"decision": "block", "reason": error_msg})

    async def send_to_agent(self, agent_id: str, message: str, thread_context: Dict[str, Any]):
        """Send message from Matrix to Claude Code agent"""
        session_id = agent_id.replace("claude-", "")

        # Check if Claude session is active
        if session_id not in self.active_claude_sessions:
            raise Exception(f"Claude Code session {session_id} is not active")

        # Inject message into Claude Code session
        # This would use Claude Code's API or WebSocket interface
        session_info = self.active_claude_sessions[session_id]
        await self.inject_to_claude_session(session_info, message)

    async def inject_to_claude_session(self, session_info: Dict[str, Any], message: str):
        """Inject message into active Claude Code session"""
        # Implementation depends on Claude Code's injection capabilities
        # This might be WebSocket, named pipes, or API calls
        websocket_url = session_info.get('websocket_url')
        if websocket_url:
            import websockets
            async with websockets.connect(websocket_url) as ws:
                await ws.send(json.dumps({
                    "type": "user_message",
                    "content": message,
                    "session_id": session_info['session_id']
                }))
```

### Letta Bridge Implementation

```python
class LettaBridge(AgentBridge):
    """Bridge for Letta agents using existing API integration"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__("letta", config)
        self.letta_client = None
        self.agent_sessions = {}

    async def start_bridge(self):
        """Start Letta API integration"""
        # Use existing Letta client setup
        self.letta_client = LettaClient(
            base_url=self.config['letta_api_url'],
            token=self.config['letta_token']
        )

        # Set up message monitoring for existing Letta agents
        await self.setup_letta_monitoring()

    async def setup_letta_monitoring(self):
        """Monitor Letta agents for new messages"""
        # This would integrate with existing Letta monitoring
        # from agent_user_manager.py patterns
        pass

    async def send_to_agent(self, agent_id: str, message: str, thread_context: Dict[str, Any]):
        """Send message from Matrix to Letta agent"""
        # Use existing Letta API integration
        try:
            response = await self.letta_client.send_message(
                agent_id=agent_id,
                message=message,
                role="user"
            )

            # Route Letta's response back to Matrix
            await self.universal_router.route_to_matrix(
                agent_id, response.content, thread_context['thread_id']
            )

        except Exception as e:
            error_msg = f"❌ Error sending to Letta agent {agent_id}: {str(e)}"
            await self.universal_router.route_to_matrix(
                agent_id, error_msg, thread_context['thread_id'], message_type="error"
            )

    async def register_agent(self, agent_id: str, agent_config: Dict[str, Any]) -> bool:
        """Register new Letta agent"""
        # Use existing agent registration patterns
        try:
            # This would use existing agent_user_manager.py logic
            await self.create_letta_agent(agent_id, agent_config)
            return True
        except Exception as e:
            logger.error(f"Failed to register Letta agent {agent_id}: {e}")
            return False

    async def get_agent_status(self, agent_id: str) -> Dict[str, Any]:
        """Get Letta agent status"""
        try:
            agent_info = await self.letta_client.get_agent(agent_id)
            return {
                "status": "active" if agent_info else "inactive",
                "agent_type": "letta",
                "last_activity": agent_info.get('last_updated'),
                "memory_usage": agent_info.get('memory_usage', 0)
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}
```

### Universal Router Implementation

```python
class UniversalAgentRouter:
    """Central router for all agent types and Matrix communication"""

    def __init__(self, matrix_client, thread_storage, agent_manager):
        self.matrix_client = matrix_client
        self.thread_storage = thread_storage
        self.agent_manager = agent_manager
        self.bridges = {}  # agent_type -> bridge_instance
        self.security_validator = UniversalSecurityValidator()

    def register_bridge(self, agent_type: str, bridge: AgentBridge):
        """Register a bridge for an agent type"""
        self.bridges[agent_type] = bridge
        bridge.universal_router = self  # Give bridge access to router

    async def create_thread(self, agent_id: str, agent_type: str, thread_id: str,
                          thread_name: str, project_context: str = None) -> str:
        """Create new universal thread"""
        # Create Matrix room using existing patterns
        room_id = await self.agent_manager.create_matrix_room(
            name=f"{agent_type.title()}: {thread_name}",
            topic=f"Conversation thread {thread_id}",
            is_public=False
        )

        # Create universal thread record
        thread = UniversalThread(
            thread_id=thread_id,
            room_id=room_id,
            created_by=agent_id,
            agent_type=agent_type,
            thread_name=thread_name,
            participants=[agent_id],
            status="active",
            project_context=project_context,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
            metadata={}
        )

        await self.thread_storage.store_thread(thread)

        # Invite Letta user for MCP tool access
        await self.agent_manager.invite_user_to_room(room_id, "@letta:matrix.oculair.ca")

        return room_id

    async def route_to_matrix(self, agent_id: str, message: str, thread_id: str,
                            message_type: str = "text"):
        """Route message from any agent to Matrix"""
        # Get thread info
        thread = await self.thread_storage.get_thread(thread_id)
        if not thread:
            raise Exception(f"Thread {thread_id} not found")

        # Format message based on agent type and message type
        formatted_message = await self.format_agent_message(
            agent_id, message, message_type, thread.agent_type
        )

        # Send to Matrix room
        await self.matrix_client.room_send(
            thread.room_id,
            "m.room.message",
            {
                "msgtype": "m.text",
                "body": formatted_message,
                "formatted_body": formatted_message,
                "format": "org.matrix.custom.html"
            }
        )

        # Store in thread history
        await self.thread_storage.store_message(
            thread_id, agent_id, message, message_type
        )

    async def route_to_agent(self, matrix_user_id: str, message: str, room_id: str):
        """Route message from Matrix to appropriate agent"""
        # Get thread from room
        thread = await self.thread_storage.get_thread_by_room(room_id)
        if not thread:
            return  # Not an agent thread

        # Skip messages from bots to prevent loops
        if matrix_user_id.startswith('@agent-') or matrix_user_id == '@letta:matrix.oculair.ca':
            return

        # Route to all active participants
        for agent_id in thread.participants:
            agent_type = await self.get_agent_type(agent_id)
            bridge = self.bridges.get(agent_type)

            if bridge:
                try:
                    await bridge.send_to_agent(agent_id, message, {
                        'thread_id': thread.thread_id,
                        'room_id': room_id,
                        'sender': matrix_user_id
                    })
                except Exception as e:
                    logger.error(f"Failed to route message to {agent_id}: {e}")
```

    async def validate_tool_use(self, tool_name: str, tool_input: Dict[str, Any],
                              agent_id: str) -> bool:
        """Universal security validation for all agent types"""
        return await self.security_validator.validate_tool_use(
            tool_name, tool_input, agent_id
        )

class UniversalSecurityValidator:
    """Security validation that works across all agent types"""

    def __init__(self):
        self.dangerous_patterns = [
            r'rm\s+.*-[rf]',           # rm -rf variants
            r'sudo\s+rm',              # sudo rm commands
            r'chmod\s+777',            # Dangerous permissions
            r'>\s*/etc/',              # Writing to system directories
            r'curl.*\|\s*sh',          # Pipe to shell
            r'wget.*\|\s*sh',          # Pipe to shell
            r'eval\s*\(',              # Code evaluation
            r'exec\s*\(',              # Code execution
        ]

        self.sensitive_files = [
            ".env", "id_rsa", "private.key", "/etc/", "~/.ssh/",
            "config.json", "secrets.yaml", "credentials.json"
        ]

    async def validate_tool_use(self, tool_name: str, tool_input: Dict[str, Any],
                              agent_id: str) -> bool:
        """Validate tool use for any agent type"""

        # Get agent type for specific validation rules
        agent_type = await self.get_agent_type_from_id(agent_id)

        # Universal dangerous command patterns
        if tool_name in ["Bash", "Shell", "Execute", "Run"]:
            command = tool_input.get("command", "")
            for pattern in self.dangerous_patterns:
                if re.search(pattern, command, re.IGNORECASE):
                    logger.warning(f"Blocked dangerous command from {agent_id}: {command}")
                    return False

        # Universal file access validation
        if tool_name in ["Write", "WriteFile", "Save", "Create"]:
            file_path = tool_input.get("path", "")
            for sensitive in self.sensitive_files:
                if sensitive in file_path:
                    logger.warning(f"Blocked sensitive file access from {agent_id}: {file_path}")
                    return False

        # Agent-type specific validation
        if agent_type == "claude_code":
            return await self.validate_claude_code_tool(tool_name, tool_input)
        elif agent_type == "letta":
            return await self.validate_letta_tool(tool_name, tool_input)

        return True  # Allow by default for unknown tools

    async def validate_claude_code_tool(self, tool_name: str, tool_input: Dict[str, Any]) -> bool:
        """Claude Code specific validation"""
        # Add Claude Code specific security rules
        return True

    async def validate_letta_tool(self, tool_name: str, tool_input: Dict[str, Any]) -> bool:
        """Letta specific validation"""
        # Add Letta specific security rules
        return True

## MCP Tool Extensions

### Universal Tool Registration

Instead of separate Claude and Letta tools, we register universal tools:

```python
# In mcp_http_server.py _register_tools method
def _register_tools(self):
    # Existing Matrix tools...
    self.tools["matrix_list_rooms"] = MatrixListRoomsTool(...)

    # NEW: Universal agent tools (replace Claude-specific ones)
    self.tools["agent_create_thread"] = AgentCreateThreadTool(
        self.matrix_api_url, self.matrix_homeserver,
        self.letta_username, self.letta_password
    )
    self.tools["agent_join_thread"] = AgentJoinThreadTool(...)
    self.tools["agent_send_message"] = AgentSendMessageTool(...)
    self.tools["agent_list_threads"] = AgentListThreadsTool(...)
    self.tools["agent_monitor_threads"] = AgentMonitorThreadsTool(...)
```

## Agent Registration & Discovery

### Universal Agent Registry

```python
# File: universal_agent_registry.py
class UniversalAgentRegistry:
    """Registry for all agent types - extends existing agent_user_manager.py"""

    def __init__(self, db_path: str = "universal_agents.db"):
        self.db_path = db_path
        self.init_database()

    async def register_agent(self, agent_id: str, agent_type: str,
                           config: Dict[str, Any]) -> bool:
        """Register any type of agent"""
        try:
            # Create Matrix user using existing patterns
            matrix_user_id = await self.create_matrix_user_for_agent(agent_id, agent_type)

            # Store in universal registry
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("""
                    INSERT OR REPLACE INTO agent_registry
                    (agent_id, agent_type, matrix_user_id, config, status, created_at)
                    VALUES (?, ?, ?, ?, 'active', ?)
                """, (agent_id, agent_type, matrix_user_id, json.dumps(config), datetime.utcnow()))
                await db.commit()

            return True
        except Exception as e:
            logger.error(f"Failed to register agent {agent_id}: {e}")
            return False

    async def discover_agents(self, agent_type: str = None) -> List[Dict[str, Any]]:
        """Discover available agents of any or specific type"""
        async with aiosqlite.connect(self.db_path) as db:
            if agent_type:
                cursor = await db.execute(
                    "SELECT * FROM agent_registry WHERE agent_type = ? AND status = 'active'",
                    (agent_type,)
                )
            else:
                cursor = await db.execute(
                    "SELECT * FROM agent_registry WHERE status = 'active'"
                )

            results = await cursor.fetchall()
            return [dict(row) for row in results]

    async def get_agent_info(self, agent_id: str) -> Optional[Dict[str, Any]]:
        """Get info for any agent type"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT * FROM agent_registry WHERE agent_id = ?",
                (agent_id,)
            )
            result = await cursor.fetchone()
            return dict(result) if result else None

### Universal Database Schema

```sql
-- Universal agent and thread storage
CREATE TABLE agent_registry (
    agent_id TEXT PRIMARY KEY,
    agent_type TEXT NOT NULL,  -- 'claude_code', 'letta', 'openai', 'custom'
    matrix_user_id TEXT NOT NULL,
    config JSON,
    status TEXT DEFAULT 'active',  -- 'active', 'inactive', 'error'
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE universal_threads (
    thread_id TEXT PRIMARY KEY,
    room_id TEXT NOT NULL,
    created_by TEXT NOT NULL,  -- agent_id
    agent_type TEXT NOT NULL,
    thread_name TEXT NOT NULL,
    participants JSON,  -- List of agent_ids
    status TEXT DEFAULT 'active',
    project_context TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    metadata JSON,
    FOREIGN KEY (created_by) REFERENCES agent_registry(agent_id)
);

CREATE TABLE thread_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    message_type TEXT DEFAULT 'text',  -- 'text', 'code', 'error', 'system'
    content TEXT NOT NULL,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    metadata JSON,
    FOREIGN KEY (thread_id) REFERENCES universal_threads(thread_id),
    FOREIGN KEY (agent_id) REFERENCES agent_registry(agent_id)
);

CREATE INDEX idx_threads_agent ON universal_threads(created_by);
CREATE INDEX idx_messages_thread ON thread_messages(thread_id);
CREATE INDEX idx_agents_type ON agent_registry(agent_type);
```

## Benefits of Unified Approach

### 1. **Simplified Architecture**
- **Single codebase** for all agent integrations
- **Consistent patterns** across agent types
- **Reduced maintenance** burden
- **Easier testing** with unified interfaces

### 2. **Enhanced Collaboration**
- **Any agent can join any thread** regardless of type
- **Cross-agent communication** becomes natural
- **Shared context** across different AI systems
- **Multi-agent workflows** are easier to implement

### 3. **Future-Proof Design**
- **New agent types** just implement the AgentBridge interface
- **No architectural changes** needed for new integrations
- **Consistent security model** across all agents
- **Unified monitoring and metrics**

### 4. **Operational Benefits**
- **Single database** for all agent data
- **Unified logging** and debugging
- **Consistent security policies**
- **Simplified deployment** and configuration

## Migration from Current System

### Phase 1: Extend Current MCP Tools (No Breaking Changes)

```python
# Add universal tools alongside existing ones
def _register_tools(self):
    # Keep existing tools for backward compatibility
    self.tools["matrix_list_rooms"] = MatrixListRoomsTool(...)
    self.tools["matrix_send_message"] = MatrixSendMessageTool(...)

    # Add new universal tools
    self.tools["agent_create_thread"] = AgentCreateThreadTool(...)
    self.tools["agent_send_message"] = AgentSendMessageTool(...)
    # ... etc
```

### Phase 2: Migrate Letta Agents to Universal System

```python
# Migrate existing Letta agents to universal registry
async def migrate_letta_agents():
    """Migrate existing Letta agents to universal system"""
    # Read existing agent mappings
    with open('matrix_client_data/agent_user_mappings.json', 'r') as f:
        existing_mappings = json.load(f)

    registry = UniversalAgentRegistry()

    for agent_id, mapping in existing_mappings.items():
        await registry.register_agent(
            agent_id=agent_id,
            agent_type="letta",
            config={
                "matrix_user_id": mapping["matrix_user_id"],
                "letta_agent_id": mapping.get("letta_agent_id"),
                "migrated_from_legacy": True
            }
        )
```

### Phase 3: Add Claude Code Bridge

```python
# Start Claude Code bridge alongside existing system
async def start_universal_system():
    """Start the universal agent system"""
    router = UniversalAgentRouter(matrix_client, thread_storage, agent_manager)

    # Register Letta bridge (migrated from existing system)
    letta_bridge = LettaBridge(letta_config)
    router.register_bridge("letta", letta_bridge)

    # Register Claude Code bridge (new)
    claude_bridge = ClaudeCodeBridge(claude_config)
    router.register_bridge("claude_code", claude_bridge)

    # Start all bridges
    await letta_bridge.start_bridge()
    await claude_bridge.start_bridge()
```

### Phase 4: Gradual Migration and Cleanup

1. **Test universal tools** with existing Letta agents
2. **Gradually migrate** Letta workflows to use universal tools
3. **Add Claude Code integration** using universal system
4. **Deprecate old tools** once migration is complete
5. **Clean up legacy code** and databases

### Agent Monitoring Integration

#### Extend Existing Agent System
```python
# File: claude_agent_monitor.py
class ClaudeAgentMonitor:
    def __init__(self, agent_manager, thread_storage, matrix_client):
        self.agent_manager = agent_manager
        self.thread_storage = thread_storage
        self.matrix_client = matrix_client
        self.monitoring_rules = {}

    async def register_monitoring_rule(self, agent_id: str, keywords: List[str],
                                     error_patterns: List[str] = None):
        """Register agent to monitor specific keywords/patterns"""
        self.monitoring_rules[agent_id] = {
            'keywords': keywords,
            'error_patterns': error_patterns or [],
            'last_check': datetime.utcnow()
        }

    async def process_thread_message(self, thread_id: str, message: str,
                                   sender_type: str):
        """Process new thread message for monitoring triggers"""
        for agent_id, rules in self.monitoring_rules.items():
            if await self.should_trigger_agent(message, rules):
                await self.trigger_agent_response(agent_id, thread_id, message)

    async def should_trigger_agent(self, message: str, rules: dict) -> bool:
        """Check if message matches monitoring rules"""
        message_lower = message.lower()

        # Check keywords
        for keyword in rules['keywords']:
            if keyword.lower() in message_lower:
                return True

        # Check error patterns
        for pattern in rules['error_patterns']:
            if re.search(pattern, message, re.IGNORECASE):
                return True

        return False

    async def trigger_agent_response(self, agent_id: str, thread_id: str,
                                   trigger_message: str):
        """Trigger agent to respond to thread"""
        # Get agent mapping
        agent_mapping = await self.agent_manager.get_agent_user_mapping(agent_id)
        if not agent_mapping:
            return

        # Get thread room
        room_id = await self.thread_storage.get_room_from_thread(thread_id)
        if not room_id:
            return

        # Send agent response
        response = await self.generate_agent_response(agent_id, trigger_message)
        await self.send_as_agent(agent_mapping, room_id, response)

    async def generate_agent_response(self, agent_id: str, context: str) -> str:
        """Generate contextual agent response"""
        # Use existing Letta API integration
        # This would call the agent with context about the Claude Code thread
        pass
```

## Data Models

### Thread Metadata Model
```python
@dataclass
class ClaudeThread:
    thread_id: str
    room_id: str
    session_id: str
    project_path: Optional[str]
    created_at: datetime
    updated_at: datetime
    status: str  # 'active', 'completed', 'archived', 'error'
    metadata: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            'thread_id': self.thread_id,
            'room_id': self.room_id,
            'session_id': self.session_id,
            'project_path': self.project_path,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat(),
            'status': self.status,
            'metadata': self.metadata
        }
```

### Message Model
```python
@dataclass
class ThreadMessage:
    id: Optional[int]
    thread_id: str
    message_id: Optional[str]
    sender_type: str  # 'user', 'assistant', 'system', 'agent'
    sender_id: Optional[str]
    content: str
    timestamp: datetime
    metadata: Dict[str, Any]

    def to_matrix_format(self) -> Dict[str, Any]:
        """Convert to Matrix message format"""
        return {
            "msgtype": "m.text",
            "body": self.content,
            "formatted_body": self.format_for_matrix(),
            "format": "org.matrix.custom.html"
        }

    def format_for_matrix(self) -> str:
        """Format message for Matrix display"""
        if self.sender_type == "user":
            return f"<strong>User</strong>: {self.content}"
        elif self.sender_type == "assistant":
            return f"<strong>Claude</strong>: {self.content}"
        elif self.sender_type == "system":
            return f"<em>{self.content}</em>"
        elif self.sender_type == "agent":
            return f"<strong>Agent {self.sender_id}</strong>: {self.content}"
        return self.content
```

## Integration Patterns

### Following Existing Patterns

#### 1. MCP Tool Registration Pattern
```python
# In mcp_http_server.py _register_tools method
def _register_tools(self):
    # Existing tools...
    self.tools["matrix_list_rooms"] = MatrixListRoomsTool(...)

    # New Claude Code tools
    self.tools["claude_list_threads"] = ClaudeListThreadsTool(
        self.matrix_api_url,
        self.matrix_homeserver,
        self.letta_username,
        self.letta_password
    )
    self.tools["claude_read_thread"] = ClaudeReadThreadTool(...)
    self.tools["claude_send_to_thread"] = ClaudeSendToThreadTool(...)
    self.tools["claude_monitor_threads"] = ClaudeMonitorThreadsTool(...)
    self.tools["claude_get_thread_context"] = ClaudeGetThreadContextTool(...)
```

#### 2. Agent User Management Pattern
```python
# Extend agent_user_manager.py
class AgentUserManager:
    async def create_claude_thread_room(self, thread_id: str, thread_name: str,
                                      agent_ids: List[str] = None):
        """Create Matrix room for Claude Code thread and invite relevant agents"""
        # Create room using existing pattern
        room_id = await self.create_matrix_room(
            name=f"Claude Thread: {thread_name}",
            topic=f"Claude Code conversation thread {thread_id}",
            is_public=False
        )

        # Invite Letta user (for MCP tools access)
        await self.invite_user_to_room(room_id, "@letta:matrix.oculair.ca")

        # Invite relevant agents if specified
        if agent_ids:
            for agent_id in agent_ids:
                mapping = await self.get_agent_user_mapping(agent_id)
                if mapping and mapping.matrix_user_id:
                    await self.invite_user_to_room(room_id, mapping.matrix_user_id)

        return room_id
```

#### 3. Matrix API Service Extension
```python
# Add to matrix_api.py
@app.post("/claude/threads", response_model=CreateThreadResponse)
async def create_claude_thread(request: CreateThreadRequest):
    """Create new Claude Code thread with Matrix room"""
    # Implementation following existing endpoint patterns
    pass

@app.get("/claude/threads", response_model=ListThreadsResponse)
async def list_claude_threads(
    project_filter: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50
):
    """List Claude Code threads"""
    # Implementation following existing endpoint patterns
    pass

@app.post("/claude/threads/{thread_id}/messages", response_model=SendMessageResponse)
async def send_to_claude_thread(thread_id: str, request: SendMessageRequest):
    """Send message to Claude Code thread"""
    # Implementation following existing endpoint patterns
    pass
```

### Docker Integration Pattern
```yaml
# Add to docker-compose.yml
services:
  claude-bridge:
    build:
      context: .
      dockerfile: Dockerfile.claude-bridge
    restart: unless-stopped
    env_file:
      - .env
    environment:
      - MATRIX_HOMESERVER_URL=http://synapse:8008
      - CLAUDE_WEBSOCKET_URL=ws://localhost:8080
    volumes:
      - ./claude_threads.db:/app/claude_threads.db
      - ./claude_hooks:/app/hooks
    networks:
      - matrix-internal
    depends_on:
      synapse:
        condition: service_healthy
      matrix-api:
        condition: service_started
      mcp-server:
        condition: service_started
```

## Security Considerations

### Hook Security Validation
```python
# Security patterns from existing pre_tool_use.py
DANGEROUS_PATTERNS = [
    r'rm\s+.*-[rf]',           # rm -rf variants
    r'sudo\s+rm',              # sudo rm commands
    r'chmod\s+777',            # Dangerous permissions
    r'>\s*/etc/',              # Writing to system directories
    r'curl.*\|\s*sh',          # Pipe to shell
    r'wget.*\|\s*sh',          # Pipe to shell
    r'eval\s*\(',              # Code evaluation
    r'exec\s*\(',              # Code execution
]

def validate_claude_command(tool_name: str, tool_input: dict) -> bool:
    """Validate Claude Code tool calls for security"""
    if tool_name == "Bash":
        command = tool_input.get("command", "")
        for pattern in DANGEROUS_PATTERNS:
            if re.search(pattern, command, re.IGNORECASE):
                return False

    elif tool_name == "Write":
        file_path = tool_input.get("path", "")
        # Prevent writing to sensitive files
        sensitive_paths = [".env", "id_rsa", "private.key", "/etc/"]
        if any(sensitive in file_path for sensitive in sensitive_paths):
            return False

    return True
```

### Matrix Room Security
```python
# Room creation with proper permissions
async def create_secure_claude_room(thread_id: str, project_path: str):
    """Create Matrix room with appropriate security settings"""
    room_config = {
        "name": f"Claude Thread: {thread_id[:8]}",
        "topic": f"Claude Code thread for {project_path}",
        "visibility": "private",
        "preset": "private_chat",
        "power_level_content_override": {
            "events": {
                "m.room.message": 0,  # Anyone can send messages
                "m.room.name": 50,    # Only moderators can change name
                "m.room.topic": 50,   # Only moderators can change topic
            },
            "users": {
                "@letta:matrix.oculair.ca": 100,  # Admin access for MCP tools
            }
        },
        "initial_state": [
            {
                "type": "m.room.encryption",
                "content": {"algorithm": "m.megolm.v1.aes-sha2"}  # Enable encryption
            }
        ]
    }

    return await create_matrix_room(room_config)
```

## Testing Strategy

### Unit Tests
```python
# test_claude_integration.py
import pytest
from claude_thread_storage import ClaudeThreadStorage
from claude_websocket_bridge import ClaudeWebSocketBridge

@pytest.mark.asyncio
async def test_thread_creation():
    """Test thread-to-room mapping creation"""
    storage = ClaudeThreadStorage(":memory:")

    thread_id = "test-thread-123"
    room_id = "!test:matrix.oculair.ca"
    session_id = "session-456"

    await storage.create_thread_mapping(thread_id, room_id, session_id)

    retrieved_room = await storage.get_room_from_thread(thread_id)
    assert retrieved_room == room_id

@pytest.mark.asyncio
async def test_message_storage():
    """Test message storage and retrieval"""
    storage = ClaudeThreadStorage(":memory:")

    thread_id = "test-thread-123"
    await storage.store_message(thread_id, "user", "Hello Claude")

    messages = await storage.get_thread_messages(thread_id)
    assert len(messages) == 1
    assert messages[0]['content'] == "Hello Claude"

@pytest.mark.asyncio
async def test_hook_security_validation():
    """Test security validation in hooks"""
    from claude_hooks.pre_tool_use import validate_claude_command

    # Test dangerous command blocking
    assert not validate_claude_command("Bash", {"command": "rm -rf /"})
    assert not validate_claude_command("Write", {"path": ".env"})

    # Test safe commands
    assert validate_claude_command("Bash", {"command": "ls -la"})
    assert validate_claude_command("Write", {"path": "test.py"})
```

### Integration Tests
```python
# test_integration.py
@pytest.mark.asyncio
async def test_end_to_end_flow():
    """Test complete Claude Code to Matrix flow"""
    # 1. Simulate Claude Code session start
    session_id = "test-session-789"

    # 2. Trigger UserPromptSubmit hook
    await simulate_hook_event("UserPromptSubmit", {
        "session_id": session_id,
        "prompt": "Help me debug this Python code",
        "timestamp": datetime.utcnow().isoformat()
    })

    # 3. Verify Matrix room creation
    thread_id = f"claude-{session_id}-{timestamp}"
    room_id = await storage.get_room_from_thread(thread_id)
    assert room_id is not None

    # 4. Simulate tool use
    await simulate_hook_event("PreToolUse", {
        "session_id": session_id,
        "tool_name": "Read",
        "tool_input": {"path": "debug.py"}
    })

    # 5. Verify Matrix message
    messages = await get_matrix_room_messages(room_id)
    assert any("Tool Call: Read" in msg['body'] for msg in messages)
```

## Deployment Guide

### Environment Configuration
```bash
# Add to .env file
CLAUDE_BRIDGE_ENABLED=true
CLAUDE_WEBSOCKET_URL=ws://localhost:8080
CLAUDE_HOOKS_PATH=/app/claude_hooks
CLAUDE_THREADS_DB_PATH=/app/data/claude_threads.db
CLAUDE_SECURITY_VALIDATION=true
CLAUDE_MAX_MESSAGE_SIZE=10000
CLAUDE_THREAD_TIMEOUT=3600
```

### Installation Steps

#### 1. Install Claude Code Hooks
```bash
# Create hooks directory
mkdir -p .claude/hooks

# Copy hook scripts (using UV single-file pattern)
cp claude_hooks/*.py .claude/hooks/

# Configure Claude Code settings
cat > .claude/settings.json << EOF
{
  "hooks": {
    "UserPromptSubmit": [{
      "hooks": [{
        "type": "command",
        "command": "uv run .claude/hooks/user_prompt_submit.py"
      }]
    }],
    "PreToolUse": [{
      "matcher": ".*",
      "hooks": [{
        "type": "command",
        "command": "uv run .claude/hooks/pre_tool_use.py"
      }]
    }],
    "PostToolUse": [{
      "matcher": ".*",
      "hooks": [{
        "type": "command",
        "command": "uv run .claude/hooks/post_tool_use.py"
      }]
    }],
    "Stop": [{
      "hooks": [{
        "type": "command",
        "command": "uv run .claude/hooks/stop.py"
      }]
    }]
  }
}
EOF
```

#### 2. Deploy Bridge Service
```bash
# Build and start Claude bridge
docker-compose up -d claude-bridge

# Verify service health
curl http://localhost:8004/claude/health
```

#### 3. Configure Agent Monitoring
```bash
# Register agents for monitoring
curl -X POST http://localhost:8004/claude/agents/monitor \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "agent-123",
    "keywords": ["error", "bug", "help"],
    "error_patterns": ["Exception", "Error:", "Failed"]
  }'
```

## Monitoring & Observability

### Metrics Collection
```python
# claude_metrics.py
from prometheus_client import Counter, Histogram, Gauge

# Metrics
claude_threads_created = Counter('claude_threads_created_total', 'Total Claude threads created')
claude_messages_processed = Counter('claude_messages_processed_total', 'Messages processed', ['type'])
claude_hook_execution_time = Histogram('claude_hook_execution_seconds', 'Hook execution time')
claude_active_threads = Gauge('claude_active_threads', 'Currently active threads')

class ClaudeMetrics:
    @staticmethod
    def record_thread_created():
        claude_threads_created.inc()

    @staticmethod
    def record_message_processed(message_type: str):
        claude_messages_processed.labels(type=message_type).inc()

    @staticmethod
    def record_hook_execution(duration: float):
        claude_hook_execution_time.observe(duration)
```

### Health Checks
```python
# Add to matrix_api.py
@app.get("/claude/health")
async def claude_health_check():
    """Health check for Claude Code integration"""
    health_status = {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "components": {}
    }

    # Check database connectivity
    try:
        storage = ClaudeThreadStorage()
        await storage.health_check()
        health_status["components"]["database"] = "healthy"
    except Exception as e:
        health_status["components"]["database"] = f"unhealthy: {str(e)}"
        health_status["status"] = "degraded"

    # Check Matrix connectivity
    try:
        matrix_client = MatrixAPIClient()
        await matrix_client.health_check()
        health_status["components"]["matrix"] = "healthy"
    except Exception as e:
        health_status["components"]["matrix"] = f"unhealthy: {str(e)}"
        health_status["status"] = "degraded"

    return health_status
```

### Logging Configuration
```python
# claude_logging.py
import logging
import json
from datetime import datetime

class ClaudeJSONFormatter(logging.Formatter):
    def format(self, record):
        log_entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "level": record.levelname,
            "component": "claude-bridge",
            "message": record.getMessage(),
            "thread_id": getattr(record, 'thread_id', None),
            "session_id": getattr(record, 'session_id', None),
            "hook_type": getattr(record, 'hook_type', None)
        }
        return json.dumps(log_entry)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(message)s',
    handlers=[
        logging.FileHandler('/app/logs/claude-bridge.log'),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger('claude-bridge')
logger.addHandler(logging.FileHandler('/app/logs/claude-bridge.log'))
logger.handlers[0].setFormatter(ClaudeJSONFormatter())
```

This comprehensive implementation guide provides the detailed specifications needed to integrate Claude Code with your existing Matrix-Synapse deployment. The approach leverages your proven architectural patterns while extending them to support the new Claude Code bridge functionality.
```
