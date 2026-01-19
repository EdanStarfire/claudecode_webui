# Legion Multi-Agent System - Technical Design Document

## 1. System Architecture Overview

### 1.1 High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     Web Browser (Frontend)                       │
│  React/Vanilla JS + WebSocket + Timeline UI + Sidebar           │
└────────────────┬────────────────────────────────────────────────┘
                 │ WebSocket + REST API
┌────────────────▼────────────────────────────────────────────────┐
│               FastAPI Server (src/web_server.py)                 │
│  • REST endpoints (legion/minion/channel CRUD)                   │
│  • WebSocket managers (UI updates, real-time events)             │
│  • Permission callback coordination                              │
└────┬────────────────────────────┬────────────────────────────────┘
     │                            │
┌────▼──────────────────┐  ┌──────▼─────────────────────────────────┐
│  LegionCoordinator    │  │  LegionMCPTools                        │
│  (NEW)                │  │  (NEW)                                 │
│  • Legion lifecycle   │  │  • MCP tool definitions                │
│  • Horde management   │  │  • Tool handlers (send_comm, spawn,    │
│  • Fleet control      │  │    dispose, search, etc.)              │
│  • MCP tool registry  │  │  • Registered with ALL minion sessions │
└────┬──────────────────┘  └──────┬─────────────────────────────────┘
     │                            │
┌────▼──────────────────┐  ┌──────▼─────────────────────────────────┐
│  OverseerController   │  │  ChannelManager                        │
│  (NEW)                │  │  (NEW)                                 │
│  • Spawn minions      │  │  • Create/manage channels              │
│  • Dispose minions    │  │  • Broadcast to members                │
│  • Horde hierarchy    │  │  • Gossip search                       │
└────┬──────────────────┘  └────────────────────────────────────────┘
     │
┌────▼──────────────────────────────────────────────────────────────┐
│               SessionCoordinator (EXISTING)                        │
│  • Manages SDK sessions (one per minion)                           │
│  • Attaches MCP tools to each session on creation                 │
│  • Start/stop/interrupt sessions                                   │
│  • Message callbacks from SDK                                      │
└────┬───────────────────────────────────────────────────────────────┘
     │
┌────▼──────────────────┐  ┌─────────────────────────────────────────┐
│  MessageProcessor     │  │  DataStorageManager                     │
│  (EXISTING)           │  │  (EXISTING + EXTENDED)                  │
│  • Parse SDK messages │  │  • Per-minion session_messages.jsonl    │
│  • Format for storage │  │  • Per-minion comms.jsonl (NEW)         │
│  • Format for WS      │  │  • Per-channel comms.jsonl (NEW)        │
└───────────────────────┘  │  • Legion timeline.jsonl (NEW)          │
                           └─────────────────────────────────────────┘
```

### 1.2 Dependency Injection via LegionSystem

**Architecture Pattern**: To avoid circular dependencies between components, all Legion components are wired together through a central `LegionSystem` context object.

**Problem Solved**:
- Components like `LegionCoordinator`, `OverseerController`, `CommRouter`, `ChannelManager`, and `LegionMCPTools` need references to each other
- Direct constructor dependencies create circular import issues and initialization order problems
- Testing becomes difficult due to tight coupling

**Solution**: `LegionSystem` acts as a service locator and dependency container.

```python
# src/legion_system.py

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .session_coordinator import SessionCoordinator
    from .data_storage import DataStorageManager
    from .legion_coordinator import LegionCoordinator
    from .overseer_controller import OverseerController
    from .channel_manager import ChannelManager
    from .comm_router import CommRouter
    from .mcp.legion_mcp_tools import LegionMCPTools

@dataclass
class LegionSystem:
    """
    Central context container for all Legion components.
    Breaks circular dependencies by providing single initialization point.
    """
    # Existing infrastructure (injected at creation)
    session_coordinator: 'SessionCoordinator'
    data_storage_manager: 'DataStorageManager'

    # Legion components (initialized in __post_init__)
    legion_coordinator: 'LegionCoordinator' = field(init=False)
    overseer_controller: 'OverseerController' = field(init=False)
    channel_manager: 'ChannelManager' = field(init=False)
    comm_router: 'CommRouter' = field(init=False)
    mcp_tools: 'LegionMCPTools' = field(init=False)

    def __post_init__(self):
        """Wire up all components with reference to this system."""
        # Order matters: components with fewer dependencies first
        self.comm_router = CommRouter(self)
        self.channel_manager = ChannelManager(self)
        self.overseer_controller = OverseerController(self)
        self.legion_coordinator = LegionCoordinator(self)
        self.mcp_tools = LegionMCPTools(self)
```

**Component Usage Pattern**:
```python
# Components receive LegionSystem and access peers via self.system
class OverseerController:
    def __init__(self, system: 'LegionSystem'):
        self.system = system

    async def spawn_minion(self, ...):
        # Access other components via system
        session_id = await self.system.session_coordinator.create_session(...)
        await self.system.comm_router.route_comm(...)
        await self.system.channel_manager.add_member(...)
```

**Initialization in web_server.py**:
```python
# Create LegionSystem once at startup
legion_system = LegionSystem(
    session_coordinator=session_coordinator,
    data_storage_manager=data_storage_manager
)
# All components now wired and ready
```

**Benefits**:
- No circular dependencies
- Single initialization point
- Easy to test (mock LegionSystem)
- Clear component relationships
- Centralized wiring logic

### 1.3 Data Flow Architecture

#### User Sends Comm to Minion
```
1. User Interface
   └─> POST /api/legions/{id}/comms
       Body: {to_minion: "AuthExpert", content: "Analyze SSO", type: "TASK"}

2. LegionCoordinator.send_comm()
   └─> CommRouter.route_comm()
       └─> Identify target minion session_id
       └─> Convert Comm → Message format
       └─> SessionCoordinator.send_message(session_id, message_content)

3. Claude SDK Session
   └─> Processes message
   └─> Returns SDK messages (ToolUse, ToolResult, AssistantMessage)

4. SessionCoordinator message_callback
   └─> MessageProcessor.process_message()
   └─> CommRouter.sdk_message_to_comm()
       └─> If response is for user, create response Comm

5. CommRouter.route_comm() [response]
   └─> DataStorageManager.append_comm() [multiple locations]
   └─> WebSocketManager.broadcast() [to UI]

6. User Interface
   └─> Receives WebSocket event
   └─> Updates timeline with response Comm
```

#### Minion Spawns Child
```
1. Minion SDK Session
   └─> Assistant decides to spawn child
   └─> Uses MCP tool: spawn_minion(name, role, initialization_context, channels)

2. LegionMCPTools.handle_spawn_minion()
   └─> Validates parameters (name unique, minion limit)
   └─> Calls OverseerController.spawn_minion()
   └─> Returns tool result (success + minion_id OR error)

3. OverseerController.spawn_minion()
   └─> Create MinionInfo
   └─> SessionCoordinator.create_session(initialization_context, mcp_tools)
   └─> SessionCoordinator.start_session()
   └─> Update parent minion (is_overseer = True, child_ids.append())
   └─> Update/create horde
   └─> ChannelManager.add_member(child, channels)
   └─> Create SPAWN Comm to user

4. User Interface
   └─> Receives SPAWN Comm via WebSocket
   └─> Updates horde tree view
   └─> Shows notification
```

---

## 2. MCP Tools Architecture

### 2.1 Overview

**Key Insight**: All minion actions (sending Comms, spawning children, searching capabilities) are implemented as **MCP tools** that the Claude SDK can invoke. This provides:
- **Explicit intent**: No ambiguity in minion actions
- **Structured parameters**: Type-safe, validated inputs
- **Clear errors**: Immediate feedback on failures
- **Discoverability**: SDK sees available actions via tool definitions
- **Debuggability**: Tool calls visible in session messages

**Implementation**: Claude Agent SDK supports in-code MCP tool registration. A single `LegionMCPTools` instance is created and attached to ALL minion SDK sessions on creation.

### 2.2 MCP Tool Definitions

#### Core Communication Tools

**send_comm**
```python
{
    "name": "send_comm",
    "description": "Send a communication (message) to another minion by name. Use this for direct collaboration, asking questions, delegating tasks, or reporting findings. You can also reference other minions in your message content using #minion-name syntax (e.g., 'Check with #DatabaseArchitect about schema').",
    "parameters": {
        "to_minion_name": {
            "type": "string",
            "description": "Exact name of target minion (case-sensitive). Example: 'AuthExpert' or 'DatabaseArchitect'"
        },
        "content": {
            "type": "string",
            "description": "Message content. You can reference other minions using #minion-name or channels using #channel-name for clarity."
        },
        "comm_type": {
            "type": "string",
            "enum": ["task", "question", "report", "guide"],
            "description": "Type of communication: 'task' (assign work), 'question' (request info), 'report' (provide findings), 'guide' (additional instructions)"
        }
    },
    "required": ["to_minion_name", "content", "comm_type"]
}
```

**Example Usage**:
```python
# Direct communication
send_comm(
    to_minion_name="AuthExpert",
    content="Please analyze the OAuth implementation. Coordinate with #DatabaseArchitect on session storage.",
    comm_type="task"
)
```

**send_comm_to_channel**
```python
{
    "name": "send_comm_to_channel",
    "description": "Broadcast a message to all members of a channel. All channel members will receive your message. Use for group coordination, status updates, or questions to the team. Reference specific minions in your message using #minion-name syntax.",
    "parameters": {
        "channel_name": {
            "type": "string",
            "description": "Name of channel to broadcast to (e.g., 'implementation-planning', 'database-changes')"
        },
        "content": {
            "type": "string",
            "description": "Message to broadcast. Use #minion-name to reference specific team members (e.g., 'Great work #AuthExpert!')."
        },
        "comm_type": {
            "type": "string",
            "enum": ["task", "question", "report", "guide"],
            "default": "report",
            "description": "Type of communication"
        }
    },
    "required": ["channel_name", "content"]
}
```

**Example Usage**:
```python
# Channel broadcast with minion references
send_comm_to_channel(
    channel_name="implementation-planning",
    content="OAuth analysis complete. #DatabaseArchitect, I found we need schema changes. #PaymentExpert, your service will need token validation updates.",
    comm_type="report"
)
```

#### Minion Lifecycle Tools

**spawn_minion**
```python
{
    "name": "spawn_minion",
    "description": "Create a new child minion to delegate specialized work. You become the overseer of this minion and can later dispose of it when done. The child minion will be a full Claude agent with access to tools.",
    "parameters": {
        "name": {
            "type": "string",
            "description": "Unique name for new minion (must not already exist in legion). Choose descriptive names like 'SSOAnalyzer' or 'DatabaseMigrationPlanner'."
        },
        "role": {
            "type": "string",
            "description": "Human-readable role description (e.g., 'SSO Configuration Analyst', 'Database Migration Planner')"
        },
        "initialization_context": {
            "type": "string",
            "description": "System prompt defining the minion's expertise, responsibilities, and constraints. Be specific about what they should do and what they should know."
        },
        "channels": {
            "type": "array",
            "items": {"type": "string"},
            "description": "List of channel names the minion should join immediately",
            "default": []
        }
    },
    "required": ["name", "role", "initialization_context"]
}
```

**dispose_minion**
```python
{
    "name": "dispose_minion",
    "description": "Terminate a child minion you created when their task is complete. You can only dispose minions you spawned (your children). Their knowledge will be transferred to you before termination.",
    "parameters": {
        "minion_name": {
            "type": "string",
            "description": "Name of child minion to dispose (must be your child)"
        }
    },
    "required": ["minion_name"]
}
```

#### Discovery & Information Tools

**search_capability** (MVP: Central Registry)
```python
{
    "name": "search_capability",
    "description": "Find minions with a specific capability by searching the central capability registry. Returns ranked list of matching minions sorted by expertise scores. Use keywords like 'database', 'oauth', 'authentication', 'payment_processing', etc.",
    "parameters": {
        "capability": {
            "type": "string",
            "description": "Capability keyword to search for (e.g., 'database', 'oauth', 'authentication'). Case-insensitive partial matching."
        }
    },
    "required": ["capability"]
}
```

**Example**:
```python
# Minion searches for database expertise
result = search_capability(capability="database")

# Returns ranked list:
# [
#   {
#     "minion_name": "DatabaseArchitect",
#     "role": "Database architecture and migrations",
#     "capabilities": ["database_design", "database_migration", "postgres"],
#     "expertise_score": 0.92
#   },
#   {
#     "minion_name": "BackendExpert",
#     "role": "Backend services",
#     "capabilities": ["database_queries", "api_design"],
#     "expertise_score": 0.68
#   }
# ]

# Minion can then contact highest-ranked expert:
send_comm(
    to_minion_name="DatabaseArchitect",
    content="I need help with schema design for the new SSO feature.",
    comm_type="question"
)
```

**MVP Implementation**: Central keyword registry in `LegionCoordinator` for simplicity and performance. Post-MVP may add gossip-based distributed search through channels.

**list_minions**
```python
{
    "name": "list_minions",
    "description": "Get a list of all active minions in the legion with their names, roles, and current states. Useful for understanding who's available to collaborate with.",
    "parameters": {}
}
```

**get_minion_info**
```python
{
    "name": "get_minion_info",
    "description": "Get detailed information about a specific minion, including their role, capabilities, current task, parent/children, and channels.",
    "parameters": {
        "minion_name": {
            "type": "string",
            "description": "Name of minion to query"
        }
    },
    "required": ["minion_name"]
}
```

#### Channel Management Tools

**join_channel**
```python
{
    "name": "join_channel",
    "description": "Join an existing channel to collaborate with its members. You'll receive all future broadcasts to this channel.",
    "parameters": {
        "channel_name": {
            "type": "string",
            "description": "Name of channel to join"
        }
    },
    "required": ["channel_name"]
}
```

**create_channel**
```python
{
    "name": "create_channel",
    "description": "Create a new channel for group communication. You'll be automatically added as a member. Use channels to coordinate with multiple minions on a shared task.",
    "parameters": {
        "name": {
            "type": "string",
            "description": "Unique name for channel (e.g., 'DatabaseChanges', 'SSOImplementation')"
        },
        "description": {
            "type": "string",
            "description": "Purpose and description of the channel"
        },
        "purpose": {
            "type": "string",
            "enum": ["coordination", "planning", "research", "scene"],
            "description": "Channel purpose category"
        },
        "initial_members": {
            "type": "array",
            "items": {"type": "string"},
            "description": "List of minion names to add as initial members",
            "default": []
        }
    },
    "required": ["name", "description", "purpose"]
}
```

**list_channels**
```python
{
    "name": "list_channels",
    "description": "Get list of all channels in the legion with their names, descriptions, and member counts.",
    "parameters": {}
}
```

### 2.3 LegionMCPTools Implementation

```python
# src/mcp/legion_mcp_tools.py

from typing import Dict, Any, List
from claude_agent_sdk import Tool

class LegionMCPTools:
    """
    MCP tools for Legion multi-agent capabilities.
    Single instance shared across all minion SDK sessions.
    """

    def __init__(self, system: 'LegionSystem'):
        self.system = system

        # Define all tools
        self.tools = self._create_tool_definitions()

    def _create_tool_definitions(self) -> List[Tool]:
        """Create MCP tool definitions for SDK."""
        return [
            Tool(
                name="send_comm",
                description="Send a communication to another minion by name...",
                parameters={...},  # As defined above
                handler=self._handle_send_comm
            ),
            Tool(
                name="spawn_minion",
                description="Create a new child minion...",
                parameters={...},
                handler=self._handle_spawn_minion
            ),
            # ... all other tools
        ]

    async def _handle_send_comm(self,
                                minion_id: str,
                                to_minion_name: str,
                                content: str,
                                comm_type: str) -> Dict[str, Any]:
        """Handle send_comm tool call."""

        # Get calling minion
        caller = self.legion_coordinator.minions.get(minion_id)
        if not caller:
            return {"success": False, "error": "Caller minion not found"}

        # Find target minion by name
        target = None
        for m in self.legion_coordinator.minions.values():
            if m.name == to_minion_name and m.legion_id == caller.legion_id:
                target = m
                break

        if not target:
            return {
                "success": False,
                "error": f"Minion '{to_minion_name}' not found in legion"
            }

        # Create Comm
        comm = Comm(
            comm_id=generate_id(),
            from_minion_id=minion_id,
            to_minion_id=target.minion_id,
            content=content,
            comm_type=CommType(comm_type),
            timestamp=datetime.now()
        )

        # Route via CommRouter
        await self.legion_coordinator.comm_router.route_comm(comm)

        return {
            "success": True,
            "comm_id": comm.comm_id,
            "message": f"Comm sent to {to_minion_name}",
            "delivered_to_minion_id": target.minion_id
        }

    async def _handle_spawn_minion(self,
                                   minion_id: str,
                                   name: str,
                                   role: str,
                                   initialization_context: str,
                                   channels: List[str] = None) -> Dict[str, Any]:
        """Handle spawn_minion tool call."""

        # Get calling minion (parent overseer)
        parent = self.legion_coordinator.minions.get(minion_id)
        if not parent:
            return {"success": False, "error": "Parent minion not found"}

        # Validate name uniqueness
        for m in self.legion_coordinator.minions.values():
            if m.name == name and m.legion_id == parent.legion_id:
                return {
                    "success": False,
                    "error": f"Minion name '{name}' already exists in legion. Choose a different name."
                }

        # Check legion minion limit
        legion = self.legion_coordinator.legions[parent.legion_id]
        if legion.active_minion_count >= legion.max_concurrent_minions:
            return {
                "success": False,
                "error": f"Legion at maximum capacity ({legion.max_concurrent_minions} minions). Cannot spawn more."
            }

        try:
            # Spawn child minion
            child_id = await self.overseer_controller.spawn_minion(
                overseer_id=minion_id,
                name=name,
                role=role,
                initialization_context=initialization_context,
                channels=channels or []
            )

            return {
                "success": True,
                "minion_id": child_id,
                "name": name,
                "message": f"Successfully spawned minion '{name}' with role '{role}'"
            }

        except Exception as e:
            return {
                "success": False,
                "error": f"Failed to spawn minion: {str(e)}"
            }

    async def _handle_dispose_minion(self,
                                     minion_id: str,
                                     minion_name: str) -> Dict[str, Any]:
        """Handle dispose_minion tool call."""

        # Get calling minion (parent overseer)
        parent = self.legion_coordinator.minions.get(minion_id)
        if not parent:
            return {"success": False, "error": "Parent minion not found"}

        # Find child by name
        child = None
        for child_id in parent.child_minion_ids:
            c = self.legion_coordinator.minions.get(child_id)
            if c and c.name == minion_name:
                child = c
                break

        if not child:
            return {
                "success": False,
                "error": f"You have no child minion named '{minion_name}'. You can only dispose minions you spawned."
            }

        try:
            # Dispose child
            await self.overseer_controller.dispose_minion(minion_id, child.minion_id)

            return {
                "success": True,
                "message": f"Successfully disposed minion '{minion_name}'. Their knowledge has been transferred to you."
            }

        except Exception as e:
            return {
                "success": False,
                "error": f"Failed to dispose minion: {str(e)}"
            }

    async def _handle_search_capability(self,
                                        minion_id: str,
                                        capability: str) -> Dict[str, Any]:
        """
        Handle search_capability tool call.
        MVP: Uses central registry in LegionCoordinator.
        """
        try:
            # Search central capability registry
            results = self.system.legion_coordinator.search_capability_registry(capability)

            if not results:
                return {
                    "success": True,
                    "results": [],
                    "message": f"No minions found with capability matching '{capability}'"
                }

            # Format results for minion
            formatted_results = []
            for result in results:
                formatted_results.append({
                    "minion_name": result["name"],
                    "role": result["role"],
                    "capabilities": result["capabilities"],
                    "expertise_score": result["expertise_score"]
                })

            return {
                "success": True,
                "results": formatted_results,
                "count": len(formatted_results),
                "message": f"Found {len(formatted_results)} minion(s) with '{capability}' capability"
            }

        except Exception as e:
            return {
                "success": False,
                "error": f"Search failed: {str(e)}"
            }

    # ... other tool handlers (list_minions, get_minion_info, join_channel, etc.)
```

### 2.4 SDK Integration

```python
# In SessionCoordinator.create_session() or similar

from claude_agent_sdk import ClaudeAgentOptions

async def create_minion_session(self,
                                legion_id: str,
                                initialization_context: str,
                                mcp_tools: LegionMCPTools) -> str:
    """Create SDK session with MCP tools attached."""

    options = ClaudeAgentOptions(
        cwd=legion.working_directory,
        permission_mode="default",
        system_prompt={
            "type": "custom",
            "prompt": initialization_context
        },
        allowed_tools=["read", "write", "edit"],  # File tools
        # Attach MCP tools (available to this minion)
        tools=mcp_tools.tools,
        setting_sources=["user", "project", "local"]
    )

    # Create session with tools
    session_id = await self.session_coordinator.create_session(
        project_id=legion_id,
        options=options
    )

    return session_id
```

### 2.5 Tool Call Flow

```
1. Minion SDK Session decides to spawn child
   ↓
2. SDK invokes MCP tool: spawn_minion(name="SSOAnalyzer", role="...", ...)
   ↓
3. LegionMCPTools._handle_spawn_minion() called with minion_id + parameters
   ↓
4. Validates: name unique? minion limit OK? parent exists?
   ↓
5. Calls OverseerController.spawn_minion()
   ↓
6. Returns tool result to SDK: {"success": true, "minion_id": "...", "message": "..."}
   ↓
7. SDK receives result, continues processing
   ↓
8. If success: Minion knows child was created
   If failure: Minion sees error, can retry or adjust
```

### 2.6 Benefits of MCP Approach

**Reliability**: No ambiguity - tool calls are explicit actions, not parsed prose
**Validation**: Parameters validated before execution, clear errors returned
**Debuggability**: Tool calls visible in session messages, easy to trace
**Extensibility**: Add new tools without changing minion prompts
**Type Safety**: Structured parameters with types and constraints
**Error Handling**: Tool can return detailed error messages minion can act on
**Discoverability**: SDK shows minions what tools are available
**Self-Documenting**: Tool descriptions teach minions how to use them

---

## 2.7 Reference Tagging System

**Purpose**: Provide explicit, unambiguous way for minions and users to reference other minions and channels in communication.

### Syntax

**Minion References**: `#minion-name`
**Channel References**: `#channel-name`

### Examples

```
"Please coordinate with #AuthExpert on the SSO implementation."
"#DatabaseArchitect has completed the schema analysis."
"Broadcasting to #implementation-planning channel about OAuth."
```

### Parsing Logic

The `CommRouter._extract_tags()` method parses content for references:

```python
def _extract_tags(self, content: str) -> Dict[str, List[str]]:
    """
    Extract #minion-name and #channel-name tags from content.
    Returns: {"minions": [...], "channels": [...]}
    """
    import re
    # Find all #word patterns
    all_tags = re.findall(r'#([a-zA-Z0-9_-]+)', content)

    # Validate against actual minions/channels in legion
    minions = []
    channels = []

    for tag in all_tags:
        if self._is_valid_minion_name(tag):
            minions.append(tag)
        elif self._is_valid_channel_name(tag):
            channels.append(tag)

    return {"minions": minions, "channels": channels}
```

### Use Cases

1. **Cross-Reference in Reports**: `"As #PaymentExpert mentioned, we need..."`
2. **Delegation**: `"#DatabaseArchitect should review this schema"`
3. **Channel Context**: `"This was discussed in #implementation-planning"`
4. **User Clarity**: User can see which minions are being referenced
5. **Future Link Generation**: UI can make tags clickable

### Benefits

- **Unambiguous**: No confusion between minion names and regular words
- **Parseable**: Simple regex, no NLP required
- **Familiar**: Similar to @mentions in Slack/Discord
- **Extensible**: Can add more reference types in future (e.g., `#task-123`)
- **UI-Friendly**: Easy to highlight, make clickable, or autocomplete

### System Prompt Addition

All minions receive guidance on tag usage in their initialization context:

```
When referencing other minions or channels in your communications, use the # syntax:
- Reference minions: #MinionName (e.g., "#AuthExpert")
- Reference channels: #channel-name (e.g., "#implementation-planning")

This makes your communications clearer and helps the system route information correctly.
```

---

## 3. Data Models

### 2.1 Core Entity Models

#### LegionInfo
```python
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

@dataclass
class LegionInfo:
    """
    Top-level multi-agent container.
    Extends Project concept with multi-agent capabilities.
    """
    legion_id: str              # UUID, same as project_id
    name: str                   # "SaaS Platform Overhaul"
    working_directory: str      # Absolute path

    # Multi-agent specific
    is_multi_agent: bool = True # Always True for legions
    horde_ids: List[str]        # All hordes in this legion
    channel_ids: List[str]      # All channels in this legion
    minion_ids: List[str]       # All minions (for quick lookup)

    # Configuration
    max_concurrent_minions: int = 20

    # State
    active_minion_count: int = 0

    # Metadata
    created_at: datetime
    updated_at: datetime

    # Project compatibility
    session_ids: List[str] = []  # Maps to minion session_ids
    is_expanded: bool = True
    order: int = 0
```

**Storage**: `data/legions/{legion_id}/legion_state.json`

#### MinionInfo
```python
from enum import Enum
from dataclasses import dataclass, field
from typing import List, Dict, Optional
from datetime import datetime

class MinionState(Enum):
    CREATED = "created"
    ACTIVE = "active"
    PAUSED = "paused"
    PROCESSING = "processing"
    TERMINATED = "terminated"
    ERROR = "error"

@dataclass
class MinionInfo:
    """
    Represents a single minion (SDK session instance).
    Extends SessionInfo concept with multi-agent capabilities.
    """
    minion_id: str              # UUID
    name: str                   # Unique within legion, e.g. "AuthExpert"
    role: str                   # Description: "Auth Service Expert"

    # Classification
    is_overseer: bool = False   # True if has spawned children
    overseer_level: int = 0     # 0=user-created, 1=child, 2=grandchild, etc.

    # Hierarchy
    parent_overseer_id: Optional[str] = None  # None if user-created
    child_minion_ids: List[str] = field(default_factory=list)
    horde_id: str               # Which horde this minion belongs to
    legion_id: str              # Parent legion

    # Communication
    channel_ids: List[str] = field(default_factory=list)

    # Session (Claude SDK)
    session_id: str             # SDK session ID
    claude_code_session_id: Optional[str] = None  # For resumption
    state: MinionState = MinionState.CREATED
    is_processing: bool = False  # Currently processing user/minion input

    # Initialization
    initialization_context: str  # System prompt for this minion
    permission_mode: str = "default"
    model: str = "claude-3-sonnet-20241022"
    tools: List[str] = field(default_factory=list)

    # Forking
    forked_from: Optional[str] = None  # If cloned from another minion
    fork_generation: int = 0    # 0=original, 1=first fork, etc.

    # Capabilities (for central registry discovery - MVP)
    capabilities: List[str] = field(default_factory=list)
    # Example: ["database_design", "postgres", "schema_migration"]
    # Registered with LegionCoordinator.capability_registry on creation

    expertise_scores: Dict[str, float] = field(default_factory=dict)
    # Format: {capability: score} where score is 0.0-1.0
    # Example: {"database_design": 0.92, "postgres": 0.88}
    # Updated based on successful task completions

    # Known agents (for gossip search - POST-MVP)
    known_minions: Dict[str, List[str]] = field(default_factory=dict)
    # Format: {minion_id: [capability1, capability2, ...]}
    # Used for distributed gossip-based discovery (deferred to post-MVP)

    # Memory paths
    memory_directory: str       # data/legions/{legion}/minions/{minion}

    # Quality tracking (for memory reinforcement)
    success_count: int = 0
    failure_count: int = 0
    correction_count: int = 0

    # Lifecycle
    created_at: datetime
    last_activity: datetime
    disposed_at: Optional[datetime] = None

    # Error state
    error_message: Optional[str] = None
```

**Storage**: `data/legions/{legion_id}/minions/{minion_id}/minion_state.json`

#### Horde
```python
@dataclass
class Horde:
    """
    Hierarchical group: overseer + all descendant minions.
    """
    horde_id: str               # UUID
    legion_id: str              # Parent legion
    name: str                   # "Architecture Planning Team"

    # Hierarchy
    root_overseer_id: str       # Top-level overseer (user-created minion)
    all_minion_ids: List[str]   # Flattened list of all minions in tree

    # Metadata
    created_by: str             # "user" or minion_id
    created_at: datetime
    updated_at: datetime
```

**Storage**: `data/legions/{legion_id}/hordes/{horde_id}/horde_state.json`

#### Channel
```python
@dataclass
class Channel:
    """
    Purpose-driven communication group for cross-horde collaboration.
    """
    channel_id: str             # UUID
    legion_id: str              # Parent legion
    name: str                   # "Implementation Planning"
    description: str            # Purpose description
    purpose: str                # "coordination" | "planning" | "scene" | "research"

    # Membership
    member_minion_ids: List[str]
    created_by_minion_id: Optional[str] = None  # None if user-created

    # Communication log
    comm_log_path: str          # Path to comms.jsonl

    # Metadata
    created_at: datetime
    updated_at: datetime
```

**Storage**: `data/legions/{legion_id}/channels/{channel_id}/channel_state.json`

#### Comm
```python
class CommType(Enum):
    TASK = "task"               # Assign work
    QUESTION = "question"       # Request info
    REPORT = "report"           # Provide findings
    GUIDE = "guide"             # Non-interrupting instruction
    HALT = "halt"               # Stop and wait
    PIVOT = "pivot"             # Stop, clear, redirect
    THOUGHT = "thought"         # Minion self-talk
    SPAWN = "spawn"             # Minion created
    DISPOSE = "dispose"         # Minion terminated
    SYSTEM = "system"           # System notification

class InterruptPriority(Enum):
    ROUTINE = "routine"         # Normal queue
    IMPORTANT = "important"     # High priority (unused in MVP)
    PIVOT = "pivot"             # Clear queue, redirect
    CRITICAL = "critical"       # Emergency (unused in MVP)

@dataclass
class Comm:
    """
    High-level message in multi-agent system.
    """
    comm_id: str                # UUID

    # Source
    from_minion_id: Optional[str] = None  # None if from user
    from_user: bool = False

    # Destination
    to_minion_id: Optional[str] = None    # Direct to minion
    to_channel_id: Optional[str] = None   # Broadcast to channel
    to_user: bool = False

    # Content
    content: str
    comm_type: CommType
    interrupt_priority: InterruptPriority = InterruptPriority.ROUTINE

    # Context
    in_reply_to: Optional[str] = None
    related_task_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    # Visibility
    visible_to_user: bool = True  # Should this show in UI?

    # Timestamp
    timestamp: datetime

    # Validation
    def validate(self) -> bool:
        """Ensure Comm has valid routing."""
        destinations = sum([
            self.to_minion_id is not None,
            self.to_channel_id is not None,
            self.to_user
        ])
        if destinations != 1:
            raise ValueError("Comm must have exactly one destination")

        sources = sum([
            self.from_minion_id is not None,
            self.from_user
        ])
        if sources != 1:
            raise ValueError("Comm must have exactly one source")

        return True
```

**Storage**: Multiple locations for different access patterns
- Per-minion: `data/legions/{legion}/minions/{minion}/comms.jsonl`
- Per-channel: `data/legions/{legion}/channels/{channel}/comms.jsonl`
- Legion timeline: `data/legions/{legion}/timeline.jsonl`

### 2.2 Memory Models

#### MemoryEntry
```python
class MemoryType(Enum):
    FACT = "fact"               # Discrete piece of knowledge
    PATTERN = "pattern"         # Recognized pattern
    RULE = "rule"               # Procedural knowledge
    RELATIONSHIP = "relationship"  # Connection between entities
    EVENT = "event"             # Significant occurrence

@dataclass
class MemoryEntry:
    """
    Single unit of minion memory.
    """
    entry_id: str               # UUID
    content: str                # The actual knowledge
    entry_type: MemoryType

    # Source
    source_task_id: Optional[str] = None
    source_comm_id: Optional[str] = None
    created_at: datetime

    # Reinforcement learning
    quality_score: float = 0.5  # 0.0 to 1.0
    times_used_successfully: int = 0
    times_used_unsuccessfully: int = 0
    last_reinforcement: Optional[datetime] = None

    # Context
    related_capabilities: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
```

#### MinionMemory
```python
@dataclass
class MinionMemory:
    """
    Complete memory structure for a minion.
    """
    minion_id: str

    # Session memory (full conversation history)
    # Stored in: session_messages.jsonl (existing format)

    # Short-term memory (recent distillations)
    short_term: List[MemoryEntry] = field(default_factory=list)
    # Stored in: short_term_memory.json

    # Long-term memory (promoted patterns/rules)
    long_term: List[MemoryEntry] = field(default_factory=list)
    # Stored in: long_term_memory.json

    # Capability evidence (tasks demonstrating capabilities)
    capability_evidence: Dict[str, List[str]] = field(default_factory=dict)
    # Format: {capability: [task_id1, task_id2, ...]}
    # Stored in: capability_evidence.json

    # Last distillation timestamp
    last_distilled_at: Optional[datetime] = None
```

### 2.3 Task & Milestone Models

#### TaskMilestone
```python
@dataclass
class TaskMilestone:
    """
    Represents completion of a task or sub-task.
    Triggers memory distillation.
    """
    milestone_id: str           # UUID
    task_id: str                # Task identifier
    minion_id: str              # Which minion completed this
    description: str            # What was accomplished

    # Completion
    completed_at: datetime
    success: bool               # True if successful, False if failed

    # Distillation
    messages_to_distill: List[str]  # Message IDs from this chunk
    distilled: bool = False
    distilled_memory_ids: List[str] = field(default_factory=list)
```

---

## 3. Component Architecture

### 3.1 LegionCoordinator

**Purpose**: Top-level orchestrator for legion lifecycle and fleet management.

**Responsibilities**:
- Create/delete legions
- Track all hordes, channels, minions in legion
- Coordinate emergency halt/resume
- Provide fleet status
- Maintain central capability registry (MVP approach)

**Key Methods**:

```python
class LegionCoordinator:
    def __init__(self, system: 'LegionSystem'):
        self.system = system

        self.legions: Dict[str, LegionInfo] = {}
        self.minions: Dict[str, MinionInfo] = {}
        self.hordes: Dict[str, Horde] = {}
        self.channels: Dict[str, Channel] = {}

        # Central capability registry (MVP)
        # Format: {minion_id: [capability1, capability2, ...]}
        self.capability_registry: Dict[str, List[str]] = {}

    async def create_legion(self,
                           name: str,
                           working_directory: str,
                           max_concurrent_minions: int = 20) -> str:
        """Create new legion."""
        legion_id = generate_id()
        legion = LegionInfo(
            legion_id=legion_id,
            name=name,
            working_directory=working_directory,
            max_concurrent_minions=max_concurrent_minions,
            created_at=datetime.now(),
            updated_at=datetime.now()
        )

        # Create directory structure
        await self._create_legion_directories(legion_id)

        # Persist
        await self._save_legion(legion)

        self.legions[legion_id] = legion
        return legion_id

    async def delete_legion(self, legion_id: str):
        """Delete legion and all its minions."""
        legion = self.legions[legion_id]

        # Terminate all minions
        for minion_id in legion.minion_ids:
            await self.overseer_controller.terminate_minion(minion_id, force=True)

        # Archive legion data
        await self._archive_legion(legion_id)

        # Remove from memory
        del self.legions[legion_id]

    async def emergency_halt_all(self, legion_id: str):
        """Halt all minions in legion."""
        legion = self.legions[legion_id]

        for minion_id in legion.minion_ids:
            minion = self.minions[minion_id]
            if minion.state == MinionState.ACTIVE:
                await self.session_coordinator.interrupt_session(minion.session_id)
                minion.state = MinionState.PAUSED
                await self._save_minion(minion)

    async def resume_all(self, legion_id: str):
        """Resume all halted minions."""
        legion = self.legions[legion_id]

        for minion_id in legion.minion_ids:
            minion = self.minions[minion_id]
            if minion.state == MinionState.PAUSED:
                # Sessions remain active, just change state
                minion.state = MinionState.ACTIVE
                await self._save_minion(minion)

    async def get_fleet_status(self, legion_id: str) -> Dict:
        """Get current status of entire fleet."""
        legion = self.legions[legion_id]

        active_count = sum(1 for mid in legion.minion_ids
                          if self.minions[mid].state == MinionState.ACTIVE)

        return {
            "legion_id": legion_id,
            "name": legion.name,
            "active_minions": active_count,
            "max_minions": legion.max_concurrent_minions,
            "total_minions": len(legion.minion_ids),
            "horde_count": len(legion.horde_ids),
            "channel_count": len(legion.channel_ids)
        }

    # Capability Registry Methods (MVP Approach)

    def register_capability(self, minion_id: str, capability: str):
        """Add capability to central registry."""
        if minion_id not in self.capability_registry:
            self.capability_registry[minion_id] = []
        if capability not in self.capability_registry[minion_id]:
            self.capability_registry[minion_id].append(capability)

    def search_capability_registry(self, keyword: str) -> List[Dict[str, Any]]:
        """
        Search for minions with capability (case-insensitive keyword match).
        Returns list of {minion_id, name, capabilities, expertise_score}.
        """
        keyword_lower = keyword.lower()
        results = []

        for minion_id, capabilities in self.capability_registry.items():
            # Case-insensitive search
            matching_caps = [cap for cap in capabilities
                           if keyword_lower in cap.lower()]
            if matching_caps:
                minion = self.minions[minion_id]
                # Get expertise score if available
                expertise_score = minion.expertise_scores.get(keyword, 0.5)
                results.append({
                    "minion_id": minion_id,
                    "name": minion.name,
                    "role": minion.role,
                    "capabilities": matching_caps,
                    "expertise_score": expertise_score
                })

        # Sort by expertise score (highest first)
        results.sort(key=lambda x: x["expertise_score"], reverse=True)
        return results
```

### 3.2 CommRouter

**Purpose**: Translate between Comms and Messages, route to correct destinations.

**Responsibilities**:
- Convert Comm → Message for SDK injection
- Convert SDK Message → Comm for routing
- Route Comms to minions, channels, or user
- Handle interrupt priorities (Halt, Pivot)
- Persist Comms to multiple locations
- Parse `#minion-name` and `#channel-name` tags for explicit references

**Key Methods**:

```python
class CommRouter:
    def __init__(self, system: 'LegionSystem'):
        self.system = system

    async def route_comm(self, comm: Comm):
        """Route Comm to its destination."""
        # Validate
        comm.validate()

        # Persist to timeline
        await self._persist_to_timeline(comm)

        # Route based on destination
        if comm.to_minion_id:
            await self._send_to_minion(comm)
        elif comm.to_channel_id:
            await self._broadcast_to_channel(comm)
        elif comm.to_user:
            await self._send_to_user(comm)

    def _extract_tags(self, content: str) -> Dict[str, List[str]]:
        """
        Extract #minion-name and #channel-name tags from content.
        Returns: {"minions": [...], "channels": [...]}
        """
        import re
        minion_tags = re.findall(r'#([a-zA-Z0-9_-]+)', content)
        # Validate against actual minions/channels in system
        minions = [tag for tag in minion_tags
                  if self._is_minion_name(tag)]
        channels = [tag for tag in minion_tags
                   if self._is_channel_name(tag)]
        return {"minions": minions, "channels": channels}

    async def route_comm(self, comm: Comm):
        """Route Comm to its destination."""
        # Validate
        comm.validate()

        # Persist to timeline
        await self._persist_to_timeline(comm)

        # Route based on destination
        if comm.to_minion_id:
            await self._send_to_minion(comm)
        elif comm.to_channel_id:
            await self._broadcast_to_channel(comm)
        elif comm.to_user:
            await self._send_to_user(comm)

    async def _send_to_minion(self, comm: Comm):
        """Send Comm to specific minion."""
        minion = self.system.legion_coordinator.minions[comm.to_minion_id]

        # Persist to minion's Comm log
        await self._persist_to_minion_log(minion.minion_id, comm)

        # Handle interrupt behavior
        if comm.comm_type == CommType.PIVOT:
            # Hard interrupt: stop, clear queue
            await self.system.session_coordinator.interrupt_session(minion.session_id)
            await self._clear_message_queue(minion.session_id)
        elif comm.comm_type == CommType.HALT:
            # Soft interrupt: stop, keep queue
            await self.system.session_coordinator.interrupt_session(minion.session_id)
            minion.state = MinionState.PAUSED

        # Convert Comm → Message
        message_content = self._format_comm_as_message(comm)

        # Send to SDK session
        if minion.state != MinionState.PAUSED:
            await self.system.session_coordinator.send_message(
                minion.session_id,
                message_content
            )

    async def sdk_message_to_comm(self,
                                  minion_id: str,
                                  sdk_message: Any) -> Optional[Comm]:
        """
        Convert SDK Message to Comm if appropriate.
        Not all SDK messages become Comms (only substantive responses).
        """
        # Parse SDK message
        parsed = self.message_processor.process_message(sdk_message, source=minion_id)

        # Determine if this should become a Comm
        if not self._is_comm_worthy(parsed):
            return None

        # Detect if minion is responding to user or another minion
        # (This requires context tracking - see _detect_comm_destination)
        destination = await self._detect_comm_destination(minion_id, parsed)

        # Create Comm
        comm = Comm(
            comm_id=generate_id(),
            from_minion_id=minion_id,
            to_user=destination["to_user"],
            to_minion_id=destination.get("to_minion_id"),
            to_channel_id=destination.get("to_channel_id"),
            content=parsed.content,
            comm_type=self._infer_comm_type(parsed),
            timestamp=datetime.now()
        )

        return comm

    async def _broadcast_to_channel(self, comm: Comm):
        """Broadcast Comm to all channel members."""
        channel = self.channels[comm.to_channel_id]

        # Persist to channel log
        await self._persist_to_channel_log(channel.channel_id, comm)

        # Send to each member (except sender)
        for member_id in channel.member_minion_ids:
            if member_id != comm.from_minion_id:
                # Create direct Comm to member
                direct_comm = Comm(
                    comm_id=generate_id(),
                    from_minion_id=comm.from_minion_id,
                    to_minion_id=member_id,
                    content=f"[Channel: {channel.name}]\n{comm.content}",
                    comm_type=comm.comm_type,
                    in_reply_to=comm.comm_id,
                    timestamp=datetime.now()
                )
                await self._send_to_minion(direct_comm)

    async def _send_to_user(self, comm: Comm):
        """Send Comm to user via WebSocket."""
        # Persist to timeline (already done in route_comm)

        # Format for WebSocket
        ws_message = {
            "type": "comm",
            "comm": {
                "comm_id": comm.comm_id,
                "from": self._format_source(comm),
                "content": comm.content,
                "comm_type": comm.comm_type.value,
                "timestamp": comm.timestamp.isoformat()
            }
        }

        # Broadcast via WebSocket
        await self.websocket.broadcast(ws_message)
```

### 3.3 OverseerController

**Purpose**: Manage minion lifecycle (spawn, dispose, hierarchy).

**Responsibilities**:
- Create minions (user-initiated and minion-spawned)
- Dispose minions (parent authority enforcement)
- Track horde hierarchy
- Coordinate memory transfer on disposal

**Key Methods**:

```python
class OverseerController:
    def __init__(self, system: 'LegionSystem'):
        self.system = system

    async def create_minion_for_user(self,
                                    legion_id: str,
                                    name: str,
                                    role: str,
                                    initialization_context: str,
                                    channels: List[str] = None) -> str:
        """User creates a new minion (becomes horde root)."""
        legion = self.legions[legion_id]

        # Validate name uniqueness
        if any(m.name == name for m in self.minions.values()
               if m.legion_id == legion_id):
            raise ValueError(f"Minion name '{name}' already exists in legion")

        # Check minion limit
        if legion.active_minion_count >= legion.max_concurrent_minions:
            raise ValueError(f"Legion at max capacity ({legion.max_concurrent_minions})")

        # Create minion
        minion_id = generate_id()
        minion = MinionInfo(
            minion_id=minion_id,
            name=name,
            role=role,
            parent_overseer_id=None,  # User-created, no parent
            legion_id=legion_id,
            initialization_context=initialization_context,
            channel_ids=channels or [],
            created_at=datetime.now(),
            last_activity=datetime.now()
        )

        # Create horde (this minion is root, even if it never spawns children)
        horde_id = await self._create_horde(legion_id, minion_id, name)
        minion.horde_id = horde_id

        # Create SDK session
        session_id = await self.session_coordinator.create_session(
            project_id=legion_id,
            permission_mode="default",
            system_prompt=initialization_context,
            name=f"Minion: {name}"
        )
        minion.session_id = session_id

        # Start session
        await self.session_coordinator.start_session(session_id)
        minion.state = MinionState.ACTIVE

        # Register capabilities in central registry (MVP)
        if minion.capabilities:
            for capability in minion.capabilities:
                self.system.legion_coordinator.register_capability(minion_id, capability)

        # Update legion
        legion.minion_ids.append(minion_id)
        legion.active_minion_count += 1

        # Persist
        await self._save_minion(minion)
        await self._save_legion(legion)

        self.minions[minion_id] = minion

        return minion_id

    async def spawn_minion(self,
                          overseer_id: str,
                          name: str,
                          role: str,
                          initialization_context: str,
                          channels: List[str] = None) -> str:
        """Overseer spawns a child minion."""
        overseer = self.minions[overseer_id]
        legion = self.legions[overseer.legion_id]

        # Validate name uniqueness
        if any(m.name == name for m in self.minions.values()
               if m.legion_id == legion.legion_id):
            raise ValueError(f"Minion name '{name}' already exists in legion")

        # Check minion limit
        if legion.active_minion_count >= legion.max_concurrent_minions:
            raise ValueError(f"Legion at max capacity")

        # Create minion
        minion_id = generate_id()
        minion = MinionInfo(
            minion_id=minion_id,
            name=name,
            role=role,
            parent_overseer_id=overseer_id,
            overseer_level=overseer.overseer_level + 1,
            horde_id=overseer.horde_id,  # Join parent's horde
            legion_id=overseer.legion_id,
initialization_context=initialization_context,
            channel_ids=channels or [],
            created_at=datetime.now(),
            last_activity=datetime.now()
        )

        # Create SDK session
        session_id = await self.session_coordinator.create_session(
            project_id=legion.legion_id,
            permission_mode="default",
            system_prompt=initialization_context,
            name=f"Minion: {name}"
        )
        minion.session_id = session_id

        # Start session
        await self.session_coordinator.start_session(session_id)
        minion.state = MinionState.ACTIVE

        # Register capabilities in central registry (MVP)
        if minion.capabilities:
            for capability in minion.capabilities:
                self.system.legion_coordinator.register_capability(minion_id, capability)

        # Update overseer
        if not overseer.is_overseer:
            overseer.is_overseer = True
        overseer.child_minion_ids.append(minion_id)

        # Update horde
        horde = self.hordes[overseer.horde_id]
        horde.all_minion_ids.append(minion_id)

        # Update legion
        legion.minion_ids.append(minion_id)
        legion.active_minion_count += 1

        # Persist
        await self._save_minion(minion)
        await self._save_minion(overseer)
        await self._save_horde(horde)
        await self._save_legion(legion)

        self.minions[minion_id] = minion

        # Notify user
        spawn_comm = Comm(
            comm_id=generate_id(),
            from_minion_id=overseer_id,
            to_user=True,
            content=f"Spawned minion: {name} ({role})",
            comm_type=CommType.SPAWN,
            visible_to_user=True,
            timestamp=datetime.now()
        )
        await self.comm_router.route_comm(spawn_comm)

        return minion_id

    async def dispose_minion(self, overseer_id: str, minion_id: str):
        """Overseer disposes of child minion."""
        minion = self.minions[minion_id]
        overseer = self.minions[overseer_id]

        # Verify authority
        if minion.parent_overseer_id != overseer_id:
            raise PermissionError("Only parent overseer can dispose minion")

        # Recursively dispose children first
        for child_id in list(minion.child_minion_ids):
            await self.dispose_minion(minion_id, child_id)

        # Distill memory
        await self.memory_manager.distill_completion(minion_id)

        # Transfer knowledge to overseer
        await self.memory_manager.transfer_knowledge(minion_id, overseer_id)

        # Terminate SDK session
        await self.session_coordinator.terminate_session(minion.session_id)
        minion.state = MinionState.TERMINATED
        minion.disposed_at = datetime.now()

        # Update overseer
        overseer.child_minion_ids.remove(minion_id)

        # Update horde
        horde = self.hordes[minion.horde_id]
        horde.all_minion_ids.remove(minion_id)

        # Update legion
        legion = self.legions[minion.legion_id]
        legion.active_minion_count -= 1

        # Persist
        await self._save_minion(minion)
        await self._save_minion(overseer)
        await self._save_horde(horde)
        await self._save_legion(legion)

        # Notify user
        dispose_comm = Comm(
            comm_id=generate_id(),
            from_minion_id=overseer_id,
            to_user=True,
            content=f"Disposed minion: {minion.name}",
            comm_type=CommType.DISPOSE,
            visible_to_user=True,
            timestamp=datetime.now()
        )
        await self.comm_router.route_comm(dispose_comm)
```

### 3.4 ChannelManager

**Purpose**: Manage channels, membership, and broadcasting.

**Note**: Gossip search is **deferred to post-MVP**. MVP uses central capability registry in `LegionCoordinator`.

**Key Methods**:

```python
class ChannelManager:
    def __init__(self, system: 'LegionSystem'):
        self.system = system

    async def create_channel(self,
                            legion_id: str,
                            name: str,
                            description: str,
                            purpose: str,
                            creator_minion_id: Optional[str] = None) -> str:
        """Create new channel."""
        channel_id = generate_id()
        channel = Channel(
            channel_id=channel_id,
            legion_id=legion_id,
            name=name,
            description=description,
            purpose=purpose,
            created_by_minion_id=creator_minion_id,
            comm_log_path=f"data/legions/{legion_id}/channels/{channel_id}/comms.jsonl",
            created_at=datetime.now(),
            updated_at=datetime.now()
        )

        await self._save_channel(channel)
        self.channels[channel_id] = channel

        return channel_id

    async def add_member(self, channel_id: str, minion_id: str):
        """Add minion to channel."""
        channel = self.channels[channel_id]
        minion = self.minions[minion_id]

        if minion_id not in channel.member_minion_ids:
            channel.member_minion_ids.append(minion_id)
            await self._save_channel(channel)

        if channel_id not in minion.channel_ids:
            minion.channel_ids.append(channel_id)
            await self._save_minion(minion)

    async def remove_member(self, channel_id: str, minion_id: str):
        """Remove minion from channel."""
        channel = self.channels[channel_id]
        minion = self.minions[minion_id]

        if minion_id in channel.member_minion_ids:
            channel.member_minion_ids.remove(minion_id)
            await self._save_channel(channel)

        if channel_id in minion.channel_ids:
            minion.channel_ids.remove(channel_id)
            await self._save_minion(minion)

    # Note: gossip_search() deferred to post-MVP
    # MVP uses LegionCoordinator.search_capability_registry() instead
```

### 3.5 MemoryManager

**Purpose**: Handle memory distillation, reinforcement, and forking.

**Key Methods**:

```python
class MemoryManager:
    def __init__(self, session_coordinator: SessionCoordinator):
        self.session_coordinator = session_coordinator

        # References
        self.minions: Dict[str, MinionInfo] = {}

    async def distill_completion(self, minion_id: str):
        """Distill memories when task/milestone completes."""
        minion = self.minions[minion_id]

        # Get messages since last distillation
        messages = await self._get_messages_since_last_distill(minion_id)

        if not messages:
            return  # Nothing to distill

        # Use Claude SDK to summarize (separate distillation prompt)
        distilled_content = await self._distill_messages(messages, minion.role)

        # Extract structured knowledge
        memories = self._extract_memory_entries(distilled_content)

        # Load current short-term memory
        short_term = await self._load_short_term_memory(minion_id)

        # Append new memories
        short_term.extend(memories)

        # Save
        await self._save_short_term_memory(minion_id, short_term)

        # Update last distillation timestamp
        # (tracked in minion memory metadata)

    async def reinforce_memory(self,
                              minion_id: str,
                              task_id: str,
                              success: bool):
        """Adjust memory quality scores based on outcome."""
        # Trace which memories were used in this task
        used_memory_ids = await self._trace_memory_usage(minion_id, task_id)

        # Load memories
        short_term = await self._load_short_term_memory(minion_id)
        long_term = await self._load_long_term_memory(minion_id)

        # Update quality scores
        for memory in short_term + long_term:
            if memory.entry_id in used_memory_ids:
                if success:
                    memory.quality_score = min(1.0, memory.quality_score + 0.1)
                    memory.times_used_successfully += 1
                else:
                    memory.quality_score = max(0.0, memory.quality_score - 0.1)
                    memory.times_used_unsuccessfully += 1

                memory.last_reinforcement = datetime.now()

        # Save
        await self._save_short_term_memory(minion_id, short_term)
        await self._save_long_term_memory(minion_id, long_term)

    async def promote_to_long_term(self, minion_id: str):
        """Promote high-quality short-term memories to long-term."""
        short_term = await self._load_short_term_memory(minion_id)
        long_term = await self._load_long_term_memory(minion_id)

        # Identify memories with high quality scores
        promotable = [m for m in short_term if m.quality_score >= 0.8]

        # Move to long-term
        for memory in promotable:
            long_term.append(memory)
            short_term.remove(memory)

        # Save
        await self._save_short_term_memory(minion_id, short_term)
        await self._save_long_term_memory(minion_id, long_term)

    async def transfer_knowledge(self,
                                from_minion_id: str,
                                to_minion_id: str):
        """Transfer distilled knowledge from one minion to another."""
        # Load source memories
        source_short = await self._load_short_term_memory(from_minion_id)
        source_long = await self._load_long_term_memory(from_minion_id)

        # Load destination memories
        dest_short = await self._load_short_term_memory(to_minion_id)

        # Transfer high-quality memories
        for memory in source_short + source_long:
            if memory.quality_score >= 0.6:  # Only transfer reliable knowledge
                dest_short.append(memory)

        # Save
        await self._save_short_term_memory(to_minion_id, dest_short)

    async def fork_minion(self,
                         source_minion_id: str,
                         new_name: str,
                         new_role: str) -> str:
        """Create duplicate minion with identical memory at fork time."""
        source = self.minions[source_minion_id]

        # Create new minion with same initialization context
        # (Using OverseerController for actual creation)
        # This method just handles memory duplication

        # Copy all memory files
        await self._copy_memory(source_minion_id, new_minion_id)

        # Update forking metadata
        new_minion = self.minions[new_minion_id]
        new_minion.forked_from = source_minion_id
        new_minion.fork_generation = source.fork_generation + 1

        # After fork, memories diverge independently (no sync)

        return new_minion_id
```

---

## 4. File System Structure

```
data/
├── legions/{legion_id}/
│   ├── legion_state.json# LegionInfo
│   ├── timeline.jsonl                  # Unified Comm log
│   │
│   ├── hordes/{horde_id}/
│   │   └── horde_state.json            # Horde
│   │
│   ├── channels/{channel_id}/
│   │   ├── channel_state.json          # Channel
│   │   └── comms.jsonl                 # Channel Comm log
│   │
│   └── minions/{minion_id}/
│       ├── minion_state.json           # MinionInfo
│       ├── session_messages.jsonl      # SDK Messages (existing format)
│       ├── comms.jsonl                 # Comms involving this minion
│       ├── short_term_memory.json      # List[MemoryEntry]
│       ├── long_term_memory.json       # List[MemoryEntry]
│       └── capability_evidence.json    # Dict[capability, List[task_id]]
│
└── logs/                               # Existing debug logs
    ├── coordinator.log
    ├── sdk_debug.log
    └── ...
```

### File Format Examples

#### legion_state.json
```json
{
  "legion_id": "legion-abc123",
  "name": "SaaS Platform Overhaul",
  "working_directory": "/path/to/project",
  "is_multi_agent": true,
  "horde_ids": ["horde-xyz", "horde-uvw"],
  "channel_ids": ["channel-123", "channel-456"],
  "minion_ids": ["minion-001", "minion-002", "minion-003"],
  "max_concurrent_minions": 20,
  "active_minion_count": 3,
  "created_at": "2025-10-19T10:00:00Z",
  "updated_at": "2025-10-19T10:30:00Z"
}
```

#### minion_state.json
```json
{
  "minion_id": "minion-001",
  "name": "AuthExpert",
  "role": "Auth Service Expert",
  "is_overseer": true,
  "overseer_level": 0,
  "parent_overseer_id": null,
  "child_minion_ids": ["minion-002"],
  "horde_id": "horde-xyz",
  "legion_id": "legion-abc123",
  "channel_ids": ["channel-123"],
  "session_id": "session-def456",
  "claude_code_session_id": "cc-session-789",
  "state": "active",
  "is_processing": false,
  "initialization_context": "You are an expert in authentication systems...",
  "permission_mode": "default",
  "model": "claude-3-sonnet-20241022",
  "tools": ["read", "write", "edit"],
  "forked_from": null,
  "fork_generation": 0,
  "capabilities": ["auth_expertise", "oauth_integration"],
  "expertise_scores": {
    "auth_expertise": 0.92,
    "oauth_integration": 0.85
  },
  "known_minions": {
    "minion-003": ["database_expertise"]
  },
  "memory_directory": "data/legions/legion-abc123/minions/minion-001",
  "success_count": 5,
  "failure_count": 1,
  "correction_count": 2,
  "created_at": "2025-10-19T10:00:00Z",
  "last_activity": "2025-10-19T10:30:00Z",
  "disposed_at": null,
  "error_message": null
}
```

#### comms.jsonl (one Comm per line)
```jsonl
{"comm_id": "comm-001", "from_user": true, "from_minion_id": null, "to_minion_id": "minion-001", "content": "Analyze SSO requirements", "comm_type": "task", "interrupt_priority": "routine", "timestamp": "2025-10-19T10:15:00Z"}
{"comm_id": "comm-002", "from_minion_id": "minion-001", "to_user": true, "content": "I'll need to review the current auth setup first", "comm_type": "report", "timestamp": "2025-10-19T10:16:00Z"}
```

---

## 5. Integration with Existing Claude WebUI

### 5.1 Modifications to Existing Components

#### SessionCoordinator
**Changes**: Minimal - already supports multiple sessions
- Add awareness of minion_id in callbacks
- Pass minion_id to CommRouter when SDK messages received
- No breaking changes to existing single-session use case

#### ProjectManager
**Changes**: Extend to support legion flag
- Add `is_multi_agent` boolean to Project model
- Legion creation reuses project creation logic
- Non-legion projects continue to work unchanged

#### DataStorageManager
**Changes**: Add new file types for Comms
- New methods: `append_comm()`, `read_comms()`, `get_comm_count()`
- Existing methods (`append_message()`, etc.) unchanged
- New storage paths for Comms (parallel to messages)

#### MessageProcessor
**Changes**: Minimal - add hooks for Comm detection
- Add `is_comm_worthy()` method to detect substantive responses
- Existing message parsing unchanged

### 5.2 New Components Added

All new components listed in Section 3 are additions, not modifications:
- LegionCoordinator
- CommRouter
- OverseerController
- ChannelManager
- MemoryManager

### 5.3 UI Integration Points

#### Sidebar
- Extend existing project list to show legion icon
- Add collapsible horde/channel sections under legion
- Reuse existing session rendering for minions

#### Main View
- New "Timeline" tab (parallel to existing messages view)
- Timeline shows Comms (not SDK Messages)
- Existing single-session view unchanged for non-legion projects

#### Modals
- Extend project creation modal with "Enable Multi-Agent" checkbox
- New minion detail modal (similar to existing session detail)
- New channel detail modal

---

## 6. API Endpoints Specification

### 6.1 Legion Endpoints

```
POST   /api/legions
Request:
{
  "name": "SaaS Platform Overhaul",
  "working_directory": "/path/to/project",
  "max_concurrent_minions": 20
}
Response:
{
  "legion_id": "legion-abc123",
  "name": "SaaS Platform Overhaul",
  "created_at": "2025-10-19T10:00:00Z"
}

GET    /api/legions/{legion_id}
Response:
{
  "legion": {...},
  "hordes": [...],
  "channels": [...],
  "minions": [...],
  "fleet_status": {...}
}

DELETE /api/legions/{legion_id}
Response:
{
  "success": true,
  "minions_terminated": 7
}

GET    /api/legions/{legion_id}/status
Response:
{
  "active_minions": 7,
  "max_minions": 20,
  "total_minions": 7,
  "horde_count": 2,
  "channel_count": 3
}

POST   /api/legions/{legion_id}/emergency-halt
Response:
{
  "success": true,
  "halted_minions": 7
}

POST   /api/legions/{legion_id}/resume-all
Response:
{
  "success": true,
  "resumed_minions": 7
}
```

### 6.2 Minion Endpoints

```
POST   /api/legions/{legion_id}/minions
Request:
{
  "name": "AuthExpert",
  "role": "Auth Service Expert",
  "initialization_context": "You are an expert...",
  "channels": ["channel-123"]
}
Response:
{
  "minion_id": "minion-001",
  "name": "AuthExpert",
  "state": "active"
}

GET    /api/minions/{minion_id}
Response:
{
  "minion": {...},
  "children": [...],
  "recent_comms": [...]
}

DELETE /api/minions/{minion_id}
Response:
{
  "success": true,
  "disposed_children": 2
}

POST   /api/minions/{minion_id}/halt
Response:
{
  "success": true,
  "state": "paused"
}

POST   /api/minions/{minion_id}/resume
Response:
{
  "success": true,
  "state": "active"
}

GET    /api/minions/{minion_id}/comms?limit=50&offset=0
Response:
{
  "comms": [...],
  "total": 247,
  "limit": 50,
  "offset": 0
}

GET    /api/minions/{minion_id}/memory
Response:
{
  "short_term": [...],
  "long_term": [...],
  "capabilities": [...]
}

POST   /api/minions/{minion_id}/fork
Request:
{
  "new_name": "AuthExpert2",
  "new_role": "Auth Service Expert (Alt)"
}
Response:
{
  "new_minion_id": "minion-010",
  "forked_from": "minion-001"
}
```

### 6.3 Channel Endpoints

```
POST   /api/legions/{legion_id}/channels
Request:
{
  "name": "Implementation Planning",
  "description": "Coordinate implementation strategy",
  "purpose": "coordination",
  "initial_members": ["minion-001", "minion-002"]
}
Response:
{
  "channel_id": "channel-123",
  "name": "Implementation Planning"
}

GET    /api/channels/{channel_id}
Response:
{
  "channel": {...},
  "members": [...],
  "recent_comms": [...]
}

POST   /api/channels/{channel_id}/join
Request:
{
  "minion_id": "minion-003"
}
Response:
{
  "success": true
}

POST   /api/channels/{channel_id}/leave
Request:
{
  "minion_id": "minion-003"
}
Response:
{
  "success": true
}

GET    /api/channels/{channel_id}/comms?limit=50&offset=0
Response:
{
  "comms": [...],
  "total": 123,
  "limit": 50,
  "offset": 0
}
```

### 6.4 Communication Endpoints

```
POST   /api/legions/{legion_id}/comms
Request:
{
  "from_user": true,
  "to_minion_id": "minion-001",
  "content": "Analyze SSO requirements",
  "comm_type": "task"
}
Response:
{
  "comm_id": "comm-001",
  "timestamp": "2025-10-19T10:15:00Z"
}

POST   /api/channels/{channel_id}/broadcast
Request:
{
  "from_user": true,
  "content": "Consider performance in all analyses",
  "comm_type": "guide"
}
Response:
{
  "comm_id": "comm-002",
  "members_notified": 4
}

GET    /api/legions/{legion_id}/timeline?limit=100&offset=0&filter=minion:minion-001
Response:
{
  "comms": [...],
  "total": 573,
  "limit": 100,
  "offset": 0
}
```

### 6.5 Discovery Endpoints

```
POST   /api/legions/{legion_id}/search
Request:
{
  "minion_id": "minion-001",
  "capability": "database_expertise"
}
Response:
{
  "found_minions": [
    {"minion_id": "minion-003", "name": "DBArchitect", "expertise_score": 0.95}
  ]
}
```

### 6.6 Internal Endpoints (Minion-Initiated)

```
POST   /api/internal/minions/spawn
Request:
{
  "overseer_id": "minion-001",
  "name": "SSOAnalyzer",
  "role": "SSO Configuration Analyst",
  "initialization_context": "...",
  "channels": ["channel-123"]
}
Response:
{
  "minion_id": "minion-004"
}

POST   /api/internal/minions/{minion_id}/dispose
Request:
{
  "overseer_id": "minion-001"
}
Response:
{
  "success": true
}
```

### 6.7 WebSocket Events

```
WS     /ws/legion/{legion_id}

Client → Server:
{
  "type": "ping"
}

Server → Client:
{
  "type": "minion_spawned",
  "data": {
    "minion_id": "minion-004",
    "name": "SSOAnalyzer",
    "parent_id": "minion-001"
  }
}

{
  "type": "minion_disposed",
  "data": {
    "minion_id": "minion-004",
    "parent_id": "minion-001"
  }
}

{
  "type": "comm",
  "data": {
    "comm_id": "comm-123",
    "from": "minion-001",
    "content": "Analysis complete",
    "comm_type": "report"
  }
}

{
  "type": "state_change",
  "data": {
    "minion_id": "minion-001",
    "old_state": "active",
    "new_state": "paused"
  }
}

{
  "type": "channel_created",
  "data": {
    "channel_id": "channel-456",
    "name": "Database Changes"
  }
}
```

---

## Document Control

- **Version**: 1.0
- **Date**: 2025-10-19
- **Status**: Draft for Review
- **Dependencies**: legion_system_requirements.md
- **Next Steps**: Create UX design document and implementation plan
- **Owner**: Development Team
