# Legion Multi-Agent System - MCP Architecture Summary

> **NOTE**: This document is historical. The channel system and related MCP tools (send_comm_to_channel, create_channel, join_channel, list_channels) described throughout this document have been removed in issue #268. The system now uses only direct minion-to-minion communication via the send_comm tool. See the main CLAUDE.md for current MCP tool definitions.

## Executive Summary

The Legion Multi-Agent System uses **MCP (Model Context Protocol) tools** as the primary mechanism for minion actions. This architectural decision provides reliability, debuggability, and extensibility superior to natural language parsing approaches.

**Key Insight**: Claude Agent SDK supports in-code MCP tool registration, allowing us to define custom tools that minions can invoke explicitly rather than parsing intent from prose.

---

## Why MCP Tools?

### The Problem with Pattern Matching

**Original Concern**: How should minions communicate and spawn children?

**Option A: Parse Natural Language**
```
Minion: "I'm going to create a specialist called SSOAnalyzer to handle this..."
System: *Attempts to parse*
  - Is this intent to spawn? Or just thinking aloud?
  - What's the minion name? "SSOAnalyzer" or "specialist"?
  - What's the initialization context? Lost in prose.
  - Did spawn succeed? Minion has no feedback.
```

**Issues**:
- Ambiguous intent ("thinking about spawning" vs "spawning")
- Fragile parameter extraction
- No validation until execution
- Unclear error feedback
- Debugging nightmare

### The MCP Solution

**With MCP Tools**:
```python
# Minion uses explicit tool
spawn_minion(
    name="SSOAnalyzer",
    role="SSO Configuration Analyst",
    initialization_context="You are an expert in...",
    channels=["Implementation Planning"]
)

# Returns structured result
{"success": true, "minion_id": "minion-123", "message": "Successfully spawned..."}
```

**Benefits**:
✅ **Explicit intent** - No ambiguity
✅ **Structured parameters** - Type-safe, validated
✅ **Clear errors** - "Minion name already exists" returned to calling minion
✅ **Debuggable** - Tool calls visible in session messages
✅ **Extensible** - Add new tools without changing minion prompts
✅ **Self-documenting** - Tool descriptions teach minions

---

## MCP Tools Architecture

### Tool Categories

**1. Communication Tools**
- `send_comm(to_minion_name, content, comm_type)` - Direct message
- `send_comm_to_channel(channel_name, content, comm_type)` - Broadcast

**2. Lifecycle Tools**
- `spawn_minion(name, role, initialization_context, channels)` - Create child
- `dispose_minion(minion_name)` - Terminate child

**3. Discovery Tools**
- `search_capability(capability)` - Find minions via gossip
- `list_minions()` - See all active minions
- `get_minion_info(minion_name)` - Query minion details

**4. Channel Tools**
- `join_channel(channel_name)` - Join collaboration channel
- `create_channel(name, description, purpose, initial_members)` - Create new channel
- `list_channels()` - See all channels

### Single Shared MCP Tools Instance

**Key Design**: One `LegionMCPTools` instance per legion, attached to ALL minion SDK sessions.

```python
# One LegionMCPTools instance for entire legion
legion_mcp_tools = LegionMCPTools(
    legion_coordinator=legion_coordinator,
    channel_manager=channel_manager,
    overseer_controller=overseer_controller
)

# Attached to ALL minion SDK sessions
options = ClaudeAgentOptions(
    cwd=working_directory,
    permission_mode="default",
    system_prompt=initialization_context,
    allowed_tools=["read", "write", "edit"],  # File tools
    tools=legion_mcp_tools.tools,  # Legion MCP tools (shared)
    setting_sources=["user", "project", "local"]
)
```

### Tool Handler Pattern

```python
class LegionMCPTools:
    async def _handle_spawn_minion(self,
                                   minion_id: str,  # Auto-injected by SDK
                                   name: str,
                                   role: str,
                                   initialization_context: str,
                                   channels: List[str] = None) -> Dict[str, Any]:
        """
        MCP tool handler for spawn_minion.
        Called when minion invokes the tool.
        """

        # Validate
        if self._name_exists(name, minion_id):
            return {
                "success": False,
                "error": f"Minion name '{name}' already exists. Choose different name."
            }

        # Execute
        try:
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
                "message": f"Successfully spawned '{name}'"
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
```

---

## Complete Tool Definitions

### Communication Tools

#### send_comm
```python
{
    "name": "send_comm",
    "description": "Send a communication (message) to another minion by name. Use this for direct collaboration, asking questions, delegating tasks, or reporting findings. You can reference other minions in your message using #minion-name syntax (e.g., 'Check with #DatabaseArchitect').",
    "parameters": {
        "to_minion_name": {
            "type": "string",
            "description": "Exact name of target minion (case-sensitive). Example: 'AuthExpert'"
        },
        "content": {
            "type": "string",
            "description": "Message content. Use #minion-name or #channel-name to reference others for clarity."
        },
        "comm_type": {
            "type": "string",
            "enum": ["task", "question", "report", "guide"],
            "description": "Type: 'task' (assign work), 'question' (request info), 'report' (provide findings), 'guide' (additional instructions)"
        }
    },
    "required": ["to_minion_name", "content", "comm_type"]
}
```

**Example**:
```python
send_comm(
    to_minion_name="AuthExpert",
    content="Please analyze OAuth implementation. Coordinate with #DatabaseArchitect on session storage.",
    comm_type="task"
)
```

#### send_comm_to_channel
```python
{
    "name": "send_comm_to_channel",
    "description": "Broadcast a message to all members of a channel. All channel members will receive your message. Reference specific minions using #minion-name syntax.",
    "parameters": {
        "channel_name": {
            "type": "string",
            "description": "Name of channel to broadcast to"
        },
        "content": {
            "type": "string",
            "description": "Message to broadcast. Use #minion-name to reference team members."
        },
        "comm_type": {
            "type": "string",
            "enum": ["task", "question", "report", "guide"],
            "default": "report"
        }
    },
    "required": ["channel_name", "content"]
}
```

**Example**:
```python
send_comm_to_channel(
    channel_name="implementation-planning",
    content="OAuth analysis complete. #DatabaseArchitect, we need schema changes. #PaymentExpert, your service needs token validation updates.",
    comm_type="report"
)
```

### Lifecycle Tools

#### spawn_minion
```python
{
    "name": "spawn_minion",
    "description": "Create a new child minion to delegate specialized work. You become the overseer of this minion and can later dispose of it when done.",
    "parameters": {
        "name": {
            "type": "string",
            "description": "Unique name for new minion. Choose descriptive names like 'SSOAnalyzer' or 'DatabaseMigrationPlanner'."
        },
        "role": {
            "type": "string",
            "description": "Human-readable role description"
        },
        "initialization_context": {
            "type": "string",
            "description": "System prompt defining the minion's expertise, responsibilities, and constraints. Be specific."
        },
        "channels": {
            "type": "array",
            "items": {"type": "string"},
            "description": "List of channel names the minion should join",
            "default": []
        }
    },
    "required": ["name", "role", "initialization_context"]
}
```

#### dispose_minion
```python
{
    "name": "dispose_minion",
    "description": "Terminate a child minion you created when their task is complete. You can only dispose minions you spawned. Their knowledge will be transferred to you.",
    "parameters": {
        "minion_name": {
            "type": "string",
            "description": "Name of child minion to dispose"
        }
    },
    "required": ["minion_name"]
}
```

### Discovery Tools

#### search_capability (MVP: Central Registry)
```python
{
    "name": "search_capability",
    "description": "Find minions with a specific capability by searching the central capability registry. Returns ranked results based on expertise scores. Use keywords like 'database', 'oauth', 'authentication', 'payment_processing', etc.",
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
# Search for database experts
result = search_capability(capability="database")
# Returns: [
#   {"minion_name": "DatabaseArchitect", "role": "...", "capabilities": ["database_design", "database_migration"], "expertise_score": 0.92},
#   {"minion_name": "BackendExpert", "role": "...", "capabilities": ["database_queries"], "expertise_score": 0.68}
# ]
```

**Note**: MVP uses centralized keyword registry for simplicity and performance. Post-MVP may add gossip-based discovery for distributed search through channels.

#### list_minions
```python
{
    "name": "list_minions",
    "description": "Get list of all active minions in the legion with their basic info.",
    "parameters": {}
}
```

**Returns**:
```python
[
    {"minion_name": "LeadArchitect", "role": "Overall coordination", "state": "active"},
    {"minion_name": "AuthExpert", "role": "Auth Service Expert", "state": "active"},
    {"minion_name": "DatabaseArchitect", "role": "Database architecture", "state": "paused"}
]
```

#### get_minion_info
```python
{
    "name": "get_minion_info",
    "description": "Get detailed information about a specific minion (capabilities, channels, current task, etc.).",
    "parameters": {
        "minion_name": {
            "type": "string",
            "description": "Name of minion to query"
        }
    },
    "required": ["minion_name"]
}
```

**Returns**:
```python
{
    "minion_name": "AuthExpert",
    "role": "Auth Service Expert",
    "state": "active",
    "capabilities": ["oauth_integration", "authentication", "jwt_tokens"],
    "expertise_scores": {"oauth_integration": 0.85, "authentication": 0.92},
    "channels": ["implementation-planning", "security-review"],
    "current_task": "Analyzing SSO implementation requirements",
    "is_overseer": True,
    "child_count": 2
}
```

### Channel Tools

#### join_channel, create_channel, list_channels
```python
{
    "name": "join_channel",
    "description": "Join an existing channel to collaborate with its members.",
    "parameters": {
        "channel_name": {"type": "string"}
    }
}

{
    "name": "create_channel",
    "description": "Create a new channel for group communication.",
    "parameters": {
        "name": {"type": "string"},
        "description": {"type": "string"},
        "purpose": {
            "type": "string",
            "enum": ["coordination", "planning", "research", "scene"]
        },
        "initial_members": {
            "type": "array",
            "items": {"type": "string"},
            "default": []
        }
    }
}

{
    "name": "list_channels",
    "description": "Get list of all channels in the legion.",
    "parameters": {}
}
```

---

## Data Flow Example: Minion Spawns Child

```
1. User: "Analyze SSO requirements across all services"
   └─> Sent to LeadArchitect minion

2. LeadArchitect (SDK):
   "I need specialized help. I'll spawn an SSO expert."
   └─> Invokes MCP tool: spawn_minion(name="SSOExpert", role="...", ...)

3. LegionMCPTools._handle_spawn_minion():
   ├─ Validates: Name unique? ✓
   ├─ Validates: Minion limit OK? ✓
   ├─ Calls: OverseerController.spawn_minion()
   └─ Returns: {"success": true, "minion_id": "minion-456"}

4. OverseerController.spawn_minion():
   ├─ Creates MinionInfo
   ├─ Creates SDK session (with MCP tools attached)
   ├─ Updates parent (is_overseer=True)
   ├─ Updates horde
   └─ Sends SPAWN Comm to user (notification)

5. LeadArchitect SDK receives tool result:
   "Great! SSOExpert is now active. Let me delegate..."
   └─> Invokes: send_comm(to_minion_name="SSOExpert", content="...", comm_type="task")

6. LegionMCPTools._handle_send_comm():
   ├─ Validates: Target exists? ✓
   ├─ Creates Comm object
   ├─ Routes via CommRouter
   └─> Returns: {"success": true, "comm_id": "comm-789"}

7. CommRouter.route_comm():
   ├─ Persists Comm (timeline, minion logs)
   ├─ Formats as Message for SDK
   └─> Injects to SSOExpert's SDK session

8. SSOExpert receives and processes the task
```

---

## Error Handling Examples

### Spawn Failure - Name Exists
```python
# Minion calls
spawn_minion(name="AuthExpert", role="...", ...)

# Returns
{
    "success": False,
    "error": "Minion name 'AuthExpert' already exists. Choose different name."
}

# Minion sees error, can retry
spawn_minion(name="AuthExpert2", role="...", ...)  # Works
```

### SendComm Failure - Target Not Found
```python
# Minion calls
send_comm(to_minion_name="DatabaseGuru", content="...", comm_type="question")

# Returns
{
    "success": False,
    "error": "Minion 'DatabaseGuru' not found in legion"
}

# Minion can search
list_minions()  # See all available
send_comm(to_minion_name="DBArchitect", ...)  # Retry correctly
```

### Dispose Failure - Not Your Child
```python
# Minion A tries to dispose Minion B's child
dispose_minion(minion_name="SomeOtherMinion")

# Returns
{
    "success": False,
    "error": "You have no child minion named 'SomeOtherMinion'. You can only dispose minions you spawned."
}
```

---

## Advantages Over Pattern Matching

| Aspect | MCP Tools | Pattern Matching |
|--------|-----------|------------------|
| **Intent Clarity** | Explicit tool call | Inferred from text |
| **Parameter Validation** | Pre-execution | Post-execution (or never) |
| **Error Feedback** | Structured, actionable | Unclear or missing |
| **Debugging** | Tool calls in session log | Parse NLP logs |
| **Extensibility** | Add tool, minions discover | Change prompts, regex |
| **Reliability** | No false positives/negatives | Fragile patterns |
| **Type Safety** | Typed parameters | String parsing |
| **Maintenance** | Low (add tools) | High (tweak patterns) |

---

## Implementation Timeline

### Phase 1 (Week 1-2): Foundation + MCP Framework
- Data models (LegionInfo, MinionInfo, Horde, Channel, Comm)
- **MCP tool schema definitions**
- **LegionMCPTools class skeleton**
- **SDK integration test (attach tools to session)**
- File system structure
- Storage layer

### Phase 2 (Week 2-3): Communication Tools
- **Implement `_handle_send_comm()`**
- **Implement `_handle_send_comm_to_channel()`**
- CommRouter for Comm routing
- Message injection to SDK sessions
- Test end-to-end: tool call → Comm → delivery

### Phase 5 (Week 5-6): Lifecycle Tools
- **Implement `_handle_spawn_minion()`**
- **Implement `_handle_dispose_minion()`**
- OverseerController implementation
- Test autonomous spawning via MCP tools

### Phase 6 (Week 6-7): Discovery & Channel Tools
- **Implement `_handle_search_capability()`**
- **Implement `_handle_join_channel()`, `_handle_create_channel()`**
- ChannelManager implementation
- Gossip search algorithm

---

## Testing Strategy

### Unit Tests
```python
def test_handle_spawn_minion_success():
    """Test successful spawn via MCP tool."""
    result = await legion_mcp_tools._handle_spawn_minion(
        minion_id="parent-123",
        name="ChildMinion",
        role="Specialist",
        initialization_context="You are...",
        channels=[]
    )

    assert result["success"] == True
    assert "minion_id" in result

def test_handle_spawn_minion_name_exists():
    """Test spawn fails when name exists."""
    create_minion(name="DuplicateName")

    result = await legion_mcp_tools._handle_spawn_minion(
        minion_id="parent-123",
        name="DuplicateName",
        role="...",
        initialization_context="...",
        channels=[]
    )

    assert result["success"] == False
    assert "already exists" in result["error"]
```

### Integration Tests
```python
async def test_spawn_via_sdk_end_to_end():
    """Test minion can spawn child using SDK + MCP tools."""
    parent_id = await create_minion_with_mcp_tools(name="Parent")

    await send_message_to_minion(
        parent_id,
        "Create a specialist to analyze the database schema"
    )

    await asyncio.sleep(2)

    children = get_minion_children(parent_id)
    assert len(children) == 1
    assert "database" in children[0].role.lower()
```

---

## Quick Reference

### For Developers

**Adding a new tool**:
1. Define tool schema in `LegionMCPTools._create_tool_definitions()`
2. Implement handler method `_handle_your_tool()`
3. Add validation and error handling
4. Write unit tests
5. Tool automatically available to all minions

**Debugging tool calls**:
1. Check session messages (tool calls visible)
2. Check tool result (success/error returned to minion)
3. Check handler logs
4. Trace execution through handler

### For Users

**Minions can**:
- Send messages: `send_comm(to_minion_name, content, comm_type)`
- Spawn helpers: `spawn_minion(name, role, initialization_context, channels)`
- Clean up: `dispose_minion(minion_name)`
- Find expertise: `search_capability(capability)`
- Collaborate: `join_channel(channel_name)`, `send_comm_to_channel(...)`

**All actions explicit, validated, and logged**.

---

## Conclusion

**MCP tools are the foundation of Legion's reliability**. By making minion actions explicit, validated, and debuggable, we avoid the fragility of natural language parsing while maintaining the flexibility and intelligence of AI agents.

**Key Takeaway**: Let minions be intelligent about *when* to use tools (via SDK reasoning), but make the tool invocation itself unambiguous (via MCP).

---

**Document Version**: 1.0
**Date**: 2025-10-19
**Status**: Finalized
**Purpose**: Quick reference for MCP-based architecture
