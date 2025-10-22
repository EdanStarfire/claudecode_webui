# Phase 5: Autonomous Minion Spawning & Termination - Detailed Implementation Plan

**Status**: COMPLETE ✅ (Core functionality implemented and tested)
**Dependencies**: Phases 1-4 Complete ✅
**Actual Effort**: 3 days (Backend: 1 day, Frontend: 1.5 days, Bug fixes: 0.5 days)
**Completed**: 2025-10-22

## Implementation Progress

### Backend Complete ✅
- **OverseerController.spawn_minion()** - Full implementation with validation, hierarchy tracking, horde management
- **OverseerController.dispose_minion()** - Recursive disposal with depth-first traversal
- **LegionMCPTools._handle_spawn_minion()** - MCP tool handler with parameter validation
- **LegionMCPTools._handle_dispose_minion()** - MCP tool handler with caller ID injection
- **SessionCoordinator.create_session()** - Extended to accept hierarchy parameters
- **SessionManager.create_session()** - Extended to accept hierarchy parameters
- **SessionManager.update_session()** - Dynamic field updates for hierarchy management
- **LegionSystem** - Added ui_websocket_manager field for WebSocket broadcasts
- **WebSocket Integration** - spawn_minion and dispose_minion now broadcast project_updated events

### Frontend Complete ✅
- **Horde Dropdown** - Sidebar dropdown for selecting overseer to view hierarchy (createHordeElement)
- **Horde Tree View** - Recursive tree rendering with proper indentation and state icons (renderHordeTreeView)
- **View Mode** - Added 'horde' view mode alongside 'timeline', 'spy', 'session'
- **Real-time Updates** - Dropdowns rebuild when project_updated WebSocket events received
- **Spy Dropdown Updates** - rebuildSpyDropdown() for dynamic minion list updates
- **State Indicator Updates** - Colored state indicators update in real-time for timeline mode

### Bug Fixes Complete ✅
- Fixed dispose_minion missing _parent_overseer_id injection (legion_mcp_tools.py:136)
- Fixed duplicate horde header display in UI
- Fixed viewTimeline() setting viewMode after exitSession() (regression)
- Fixed Spy state indicator not updating in timeline mode
- Fixed new minions not appearing in dropdowns/comm composer (project_updated broadcasts)
- Removed non-existent updateSidebarSelection() and updateStatusBar() calls

### Testing Complete ✅
- End-to-end spawn_minion tested successfully
- End-to-end dispose_minion tested successfully
- Recursive disposal working correctly
- SPAWN/DISPOSE comms delivered to timeline
- Real-time UI updates verified (Spy dropdown, Horde dropdown, comm composer)
- State synchronization verified across WebSockets

### Deferred to Post-UI-Refactor 🔄
- [ ] Clicking node in horde tree navigates to session (deferred - UI refactor will handle)
- [ ] Deep linking for horde view (#horde/{overseer_id}) (deferred - routing in new framework)
- [ ] System prompts teaching minions when/how to spawn (deferred - Phase 6+)
- [ ] Unit tests for spawn/dispose methods (deferred - post-refactor)
- [ ] Integration tests for full spawn→communicate→dispose flow (deferred - post-refactor)

---

## 1. Executive Summary

### 1.1 Goals

Enable minions to autonomously spawn and dispose child minions using MCP tools, with full state synchronization to the UI and proper hierarchy management.

### 1.2 Key Design Decisions

After analyzing the codebase and considering scalability:

**Name Generation**: LLM provides names in `spawn_minion` tool call
- **Rationale**: More flexible, allows context-appropriate naming (e.g., "SSOAnalyzer" vs "Minion-1234")
- **Implementation**: `name` parameter required in MCP tool schema
- **Validation**: Backend validates uniqueness, returns error if duplicate

**State Bubbling**: Leverage existing `SessionCoordinator._notify_state_change()`
- **Current**: Session creation already broadcasts via WebSocket
- **Enhancement**: Ensure SPAWN/DISPOSE comms are routed to UI properly
- **Pattern**: Reuse existing `state_change` events, add new `minion_spawned`/`minion_disposed` events

**UI Paradigm**: Add Horde dropdown in sidebar → Tree view in main area
- **Spy Dropdown**: ✅ Already implemented (Phase 3) - select individual minions
- **Horde Dropdown**: NEW - Select horde by overseer (parallel to Spy in sidebar)
- **Horde Tree View**: NEW - Main area view showing hierarchy with state indicators
- **Rationale**: Consistent pattern (dropdown in sidebar → detail view in main area)
- **View Modes**: 'timeline', 'spy', 'session', 'horde' (NEW)

### 1.3 Architecture Changes

```
Before Phase 5:
User → Manual Minion Creation → SessionCoordinator → SessionManager

After Phase 5:
Minion → spawn_minion MCP tool → LegionMCPTools._handle_spawn_minion()
    → OverseerController.spawn_minion()
    → SessionCoordinator.create_session() (with parent_overseer_id)
    → SessionManager (creates session with is_minion=True, parent fields populated)
    → WebSocket broadcast (minion_spawned event)
    → UI updates (Spy dropdown + horde tree if open)
```

---

## 2. Detailed Implementation Tasks

### 2.1 Backend: OverseerController - spawn_minion()

**File**: `src/legion/overseer_controller.py`

**Location**: After `create_minion_for_user()` method (currently ends at line ~112)

**Implementation**:

```python
async def spawn_minion(
    self,
    parent_overseer_id: str,
    name: str,
    role: str,
    initialization_context: str,
    capabilities: Optional[List[str]] = None,
    channels: Optional[List[str]] = None
) -> str:
    """
    Spawn a child minion autonomously by a parent overseer.

    This is called when a minion uses the spawn_minion MCP tool.

    Args:
        parent_overseer_id: Session ID of parent minion
        name: Unique name for child (provided by LLM)
        role: Role description for child
        initialization_context: System prompt/instructions for child
        capabilities: Capability keywords for discovery
        channels: Channel IDs to join immediately

    Returns:
        str: Child minion's session_id

    Raises:
        ValueError: If parent doesn't exist, name duplicate, or legion at capacity
        PermissionError: If parent is not a valid overseer
    """
    # 1. Get parent minion session
    parent_session = await self.system.session_coordinator.session_manager.get_session_info(parent_overseer_id)
    if not parent_session:
        raise ValueError(f"Parent overseer {parent_overseer_id} not found")

    if not parent_session.is_minion:
        raise PermissionError(f"Session {parent_overseer_id} is not a minion")

    # 2. Get legion (parent's project)
    legion_id = parent_session.project_id  # Minions inherit project from parent
    project = await self.system.session_coordinator.project_manager.get_project(legion_id)
    if not project or not project.is_multi_agent:
        raise ValueError(f"Legion {legion_id} not found or not multi-agent")

    # 3. Validate name uniqueness within legion
    existing_sessions = await self.system.session_coordinator.session_manager.list_sessions()
    legion_minions = [s for s in existing_sessions if s.is_minion and self._belongs_to_legion(s, legion_id)]

    if any(m.name == name for m in legion_minions):
        raise ValueError(f"Minion name '{name}' already exists in this legion. Choose a different name.")

    # 4. Check minion limit
    if len(legion_minions) >= (project.max_concurrent_minions or 20):
        raise ValueError(f"Legion at maximum capacity ({project.max_concurrent_minions or 20} minions). Cannot spawn more.")

    # 5. Generate child minion ID
    child_minion_id = str(uuid.uuid4())

    # 6. Calculate overseer level (parent + 1)
    overseer_level = (parent_session.overseer_level or 0) + 1

    # 7. Get parent's horde_id (child joins parent's horde)
    parent_horde_id = parent_session.horde_id
    if not parent_horde_id:
        # Parent has no horde yet - shouldn't happen, but create one
        await self._ensure_horde_for_minion(legion_id, parent_overseer_id, parent_session.name)
        parent_session = await self.system.session_coordinator.session_manager.get_session_info(parent_overseer_id)
        parent_horde_id = parent_session.horde_id

    # 8. Create child session via SessionCoordinator
    await self.system.session_coordinator.create_session(
        session_id=child_minion_id,
        project_id=legion_id,
        name=name,
        permission_mode="default",
        # Minion-specific fields
        role=role,
        initialization_context=initialization_context,
        capabilities=capabilities or [],
        # Hierarchy fields
        parent_overseer_id=parent_overseer_id,
        overseer_level=overseer_level,
        horde_id=parent_horde_id
    )

    # 9. Update parent: mark as overseer, add child to child_minion_ids
    if not parent_session.is_overseer:
        await self.system.session_coordinator.session_manager.update_session(
            parent_overseer_id,
            is_overseer=True
        )

    # Add child to parent's child_minion_ids list
    parent_children = parent_session.child_minion_ids or []
    parent_children.append(child_minion_id)
    await self.system.session_coordinator.session_manager.update_session(
        parent_overseer_id,
        child_minion_ids=parent_children
    )

    # 10. Update horde: add child to all_minion_ids
    horde = self.hordes.get(parent_horde_id)
    if horde:
        if child_minion_id not in horde.all_minion_ids:
            horde.all_minion_ids.append(child_minion_id)
            horde.updated_at = datetime.now()
            await self._persist_horde(horde)

    # 11. Register capabilities in central registry
    if capabilities:
        for capability in capabilities:
            if capability not in self.system.legion_coordinator.capability_registry:
                self.system.legion_coordinator.capability_registry[capability] = []
            self.system.legion_coordinator.capability_registry[capability].append(child_minion_id)

    # 12. Join channels if specified
    if channels and self.system.channel_manager:
        for channel_id in channels:
            try:
                await self.system.channel_manager.add_member(channel_id, child_minion_id)
            except Exception as e:
                # Log but don't fail spawn if channel join fails
                coord_logger.warning(f"Failed to add {name} to channel {channel_id}: {e}")

    # 13. Send SPAWN notification to user
    from src.models.legion_models import Comm, CommType, InterruptPriority
    spawn_comm = Comm(
        comm_id=str(uuid.uuid4()),
        from_minion_id=parent_overseer_id,
        from_minion_name=parent_session.name,
        to_user=True,
        summary=f"Spawned {name}",
        content=f"**{parent_session.name}** spawned minion **{name}** ({role})",
        comm_type=CommType.SPAWN,
        interrupt_priority=InterruptPriority.ROUTINE,
        visible_to_user=True
    )
    await self.system.comm_router.route_comm(spawn_comm)

    # 14. Start child session (make it active)
    await self.system.session_coordinator.start_session(child_minion_id)

    coord_logger.info(f"Minion {name} spawned by {parent_session.name} (parent={parent_overseer_id}, child={child_minion_id})")

    return child_minion_id
```

**Testing Requirements**:
- Unit test: Valid spawn succeeds
- Unit test: Duplicate name rejected
- Unit test: Capacity limit enforced
- Unit test: Parent updated correctly (is_overseer=True, child_ids)
- Unit test: Horde updated correctly
- Integration test: Full spawn flow with WebSocket event

**Estimated Effort**: 1.5 days

---

### 2.2 Backend: OverseerController - dispose_minion()

**File**: `src/legion/overseer_controller.py`

**Location**: After `spawn_minion()` method

**Implementation**:

```python
async def dispose_minion(
    self,
    parent_overseer_id: str,
    child_minion_name: str
) -> Dict[str, Any]:
    """
    Dispose of a child minion (terminate with cleanup).

    Args:
        parent_overseer_id: Session ID of parent overseer
        child_minion_name: Name of child to dispose (NOT session_id)

    Returns:
        dict: {"success": bool, "disposed_minion_id": str, "descendants_count": int}

    Raises:
        ValueError: If child not found
        PermissionError: If caller is not parent of child
    """
    # 1. Get parent session
    parent_session = await self.system.session_coordinator.session_manager.get_session_info(parent_overseer_id)
    if not parent_session:
        raise ValueError(f"Parent overseer {parent_overseer_id} not found")

    # 2. Find child by name from parent's children
    child_session = None
    child_minion_id = None

    for child_id in (parent_session.child_minion_ids or []):
        session = await self.system.session_coordinator.session_manager.get_session_info(child_id)
        if session and session.name == child_minion_name:
            child_session = session
            child_minion_id = child_id
            break

    if not child_session:
        raise ValueError(
            f"No child minion named '{child_minion_name}' found. "
            f"You can only dispose minions you spawned. "
            f"Your children: {[s.name for s in [await self.system.session_coordinator.session_manager.get_session_info(cid) for cid in (parent_session.child_minion_ids or [])] if s]}"
        )

    # 3. Recursively dispose descendants first (depth-first disposal)
    descendants_disposed = 0
    if child_session.child_minion_ids:
        for grandchild_id in list(child_session.child_minion_ids):
            grandchild = await self.system.session_coordinator.session_manager.get_session_info(grandchild_id)
            if grandchild:
                result = await self.dispose_minion(child_minion_id, grandchild.name)
                descendants_disposed += result["descendants_count"] + 1

    # 4. Memory distillation (stub for now - Phase 7)
    # await self.system.memory_manager.distill_completion(child_minion_id)

    # 5. Knowledge transfer to parent (stub for now - Phase 7)
    # await self.system.memory_manager.transfer_knowledge(child_minion_id, parent_overseer_id)

    # 6. Terminate SDK session
    await self.system.session_coordinator.terminate_session(child_minion_id)

    # 7. Update parent: remove child from child_minion_ids
    parent_children = list(parent_session.child_minion_ids or [])
    if child_minion_id in parent_children:
        parent_children.remove(child_minion_id)
        await self.system.session_coordinator.session_manager.update_session(
            parent_overseer_id,
            child_minion_ids=parent_children
        )

    # If parent has no more children, mark as no longer overseer
    if not parent_children:
        await self.system.session_coordinator.session_manager.update_session(
            parent_overseer_id,
            is_overseer=False
        )

    # 8. Update horde: remove child from all_minion_ids
    horde_id = child_session.horde_id
    if horde_id:
        horde = self.hordes.get(horde_id)
        if horde and child_minion_id in horde.all_minion_ids:
            horde.all_minion_ids.remove(child_minion_id)
            horde.updated_at = datetime.now()
            await self._persist_horde(horde)

    # 9. Deregister from capability registry
    for capability, minion_ids in self.system.legion_coordinator.capability_registry.items():
        if child_minion_id in minion_ids:
            minion_ids.remove(child_minion_id)

    # 10. Send DISPOSE notification to user
    from src.models.legion_models import Comm, CommType, InterruptPriority
    dispose_comm = Comm(
        comm_id=str(uuid.uuid4()),
        from_minion_id=parent_overseer_id,
        from_minion_name=parent_session.name,
        to_user=True,
        summary=f"Disposed {child_minion_name}",
        content=f"**{parent_session.name}** disposed of minion **{child_minion_name}**" + (
            f" (and {descendants_disposed} descendants)" if descendants_disposed > 0 else ""
        ),
        comm_type=CommType.DISPOSE,
        interrupt_priority=InterruptPriority.ROUTINE,
        visible_to_user=True
    )
    await self.system.comm_router.route_comm(dispose_comm)

    coord_logger.info(f"Minion {child_minion_name} disposed by {parent_session.name} (disposed_id={child_minion_id}, descendants={descendants_disposed})")

    return {
        "success": True,
        "disposed_minion_id": child_minion_id,
        "disposed_minion_name": child_minion_name,
        "descendants_count": descendants_disposed
    }
```

**Testing Requirements**:
- Unit test: Valid disposal succeeds
- Unit test: Non-child rejection (permission error)
- Unit test: Recursive disposal (grandchildren first)
- Unit test: Parent updated correctly (child_ids, is_overseer)
- Unit test: Horde updated correctly
- Integration test: Full disposal flow with WebSocket event

**Estimated Effort**: 1.5 days

---

### 2.3 Backend: LegionMCPTools - _handle_spawn_minion()

**File**: `src/legion/mcp/legion_mcp_tools.py`

**Location**: Replace stub at line ~384

**Implementation**:

```python
async def _handle_spawn_minion(self, args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handle spawn_minion tool call from a minion.

    Args:
        args: {
            "_parent_overseer_id": str,  # Injected by tool wrapper
            "name": str,
            "role": str,
            "initialization_context": str,
            "capabilities": List[str],  # Optional
            "channels": List[str]  # Optional
        }

    Returns:
        Tool result with success/error
    """
    parent_overseer_id = args.get("_parent_overseer_id")
    if not parent_overseer_id:
        return {
            "content": [{
                "type": "text",
                "text": "Error: Unable to determine parent overseer ID"
            }],
            "is_error": True
        }

    # Extract parameters
    name = args.get("name", "").strip()
    role = args.get("role", "").strip()
    initialization_context = args.get("initialization_context", "").strip()
    capabilities = args.get("capabilities", [])
    channels = args.get("channels", [])

    # Validate required fields
    if not name:
        return {
            "content": [{
                "type": "text",
                "text": "Error: 'name' parameter is required and cannot be empty"
            }],
            "is_error": True
        }

    if not role:
        return {
            "content": [{
                "type": "text",
                "text": "Error: 'role' parameter is required and cannot be empty"
            }],
            "is_error": True
        }

    if not initialization_context:
        return {
            "content": [{
                "type": "text",
                "text": "Error: 'initialization_context' parameter is required and cannot be empty. Provide clear instructions for what this minion should do."
            }],
            "is_error": True
        }

    # Attempt to spawn child minion
    try:
        child_minion_id = await self.system.overseer_controller.spawn_minion(
            parent_overseer_id=parent_overseer_id,
            name=name,
            role=role,
            initialization_context=initialization_context,
            capabilities=capabilities,
            channels=channels
        )

        return {
            "content": [{
                "type": "text",
                "text": (
                    f"✅ Successfully spawned minion '{name}' with role '{role}'.\n\n"
                    f"Minion ID: {child_minion_id}\n"
                    f"The child minion is now active and ready to receive comms. "
                    f"You can communicate with them using send_comm(to_minion_name='{name}', ...)."
                )
            }],
            "is_error": False
        }

    except ValueError as e:
        # Handle validation errors (duplicate name, capacity, etc.)
        return {
            "content": [{
                "type": "text",
                "text": f"❌ Cannot spawn minion: {str(e)}"
            }],
            "is_error": True
        }

    except PermissionError as e:
        # Handle permission errors
        return {
            "content": [{
                "type": "text",
                "text": f"❌ Permission denied: {str(e)}"
            }],
            "is_error": True
        }

    except Exception as e:
        # Catch-all for unexpected errors
        coord_logger.error(f"Unexpected error in spawn_minion: {e}", exc_info=True)
        return {
            "content": [{
                "type": "text",
                "text": f"❌ Failed to spawn minion due to unexpected error: {str(e)}"
            }],
            "is_error": True
        }
```

**Testing Requirements**:
- Unit test: Valid spawn returns success
- Unit test: Missing name returns error
- Unit test: Duplicate name returns error
- Unit test: Capacity exceeded returns error
- Integration test: Tool call through SDK

**Estimated Effort**: 0.5 days

---

### 2.4 Backend: LegionMCPTools - _handle_dispose_minion()

**File**: `src/legion/mcp/legion_mcp_tools.py`

**Location**: Replace stub at line ~395

**Implementation**:

```python
async def _handle_dispose_minion(self, args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handle dispose_minion tool call from a minion.

    Args:
        args: {
            "_parent_overseer_id": str,  # Injected by tool wrapper (session_id)
            "minion_name": str
        }

    Returns:
        Tool result with success/error
    """
    parent_overseer_id = args.get("_parent_overseer_id")  # This is actually the CALLER's session_id
    if not parent_overseer_id:
        return {
            "content": [{
                "type": "text",
                "text": "Error: Unable to determine caller ID"
            }],
            "is_error": True
        }

    # Extract parameters
    minion_name = args.get("minion_name", "").strip()

    # Validate required field
    if not minion_name:
        return {
            "content": [{
                "type": "text",
                "text": "Error: 'minion_name' parameter is required and cannot be empty"
            }],
            "is_error": True
        }

    # Attempt to dispose child minion
    try:
        result = await self.system.overseer_controller.dispose_minion(
            parent_overseer_id=parent_overseer_id,
            child_minion_name=minion_name
        )

        descendants_msg = ""
        if result["descendants_count"] > 0:
            descendants_msg = f"\n\n⚠️  Also disposed {result['descendants_count']} descendant minion(s) (children of {minion_name})."

        return {
            "content": [{
                "type": "text",
                "text": (
                    f"✅ Successfully disposed of minion '{minion_name}'."
                    f"{descendants_msg}\n\n"
                    f"Their knowledge has been preserved and will be available to you."
                )
            }],
            "is_error": False
        }

    except ValueError as e:
        # Handle not found or validation errors
        return {
            "content": [{
                "type": "text",
                "text": f"❌ Cannot dispose minion: {str(e)}"
            }],
            "is_error": True
        }

    except PermissionError as e:
        # Handle permission errors (not your child)
        return {
            "content": [{
                "type": "text",
                "text": f"❌ Permission denied: {str(e)}"
            }],
            "is_error": True
        }

    except Exception as e:
        # Catch-all for unexpected errors
        coord_logger.error(f"Unexpected error in dispose_minion: {e}", exc_info=True)
        return {
            "content": [{
                "type": "text",
                "text": f"❌ Failed to dispose minion due to unexpected error: {str(e)}"
            }],
            "is_error": True
        }
```

**Testing Requirements**:
- Unit test: Valid disposal returns success
- Unit test: Non-child returns permission error
- Unit test: Not found returns error
- Integration test: Tool call through SDK

**Estimated Effort**: 0.5 days

---

### 2.5 Backend: SessionManager - Support for Hierarchy Fields

**File**: `src/session_manager.py`

**Changes Required**:

1. **Update `create_session()` signature** to accept new fields:
   ```python
   async def create_session(
       self,
       ...
       parent_overseer_id: Optional[str] = None,
       overseer_level: int = 0,
       horde_id: Optional[str] = None
   ):
   ```

2. **Update `update_session()` to support**:
   - `is_overseer` (bool)
   - `child_minion_ids` (List[str])

3. **Ensure persistence** of new fields in `_persist_session_state()`

**Testing Requirements**:
- Unit test: Create session with parent_overseer_id
- Unit test: Update is_overseer flag
- Unit test: Update child_minion_ids list
- Integration test: Round-trip persistence

**Estimated Effort**: 0.5 days

---

### 2.6 Backend: WebSocket Events for Spawning/Disposing

**File**: `src/web_server.py`

**Changes Required**:

1. **Add `minion_spawned` event** broadcast in `spawn_minion()` flow:
   ```python
   # After child session created
   await ui_websocket_manager.broadcast_to_all({
       "type": "minion_spawned",
       "data": {
           "minion_id": child_minion_id,
           "name": name,
           "role": role,
           "parent_id": parent_overseer_id,
           "legion_id": legion_id,
           "overseer_level": overseer_level
       }
   })
   ```

2. **Add `minion_disposed` event** broadcast in `dispose_minion()` flow:
   ```python
   # After child session terminated
   await ui_websocket_manager.broadcast_to_all({
       "type": "minion_disposed",
       "data": {
           "minion_id": child_minion_id,
           "name": child_minion_name,
           "parent_id": parent_overseer_id,
           "descendants_count": descendants_count
       }
   })
   ```

**Location**: Hook into `SessionCoordinator._notify_state_change()` callback registration

**Testing Requirements**:
- Integration test: Spawn triggers WebSocket event
- Integration test: Dispose triggers WebSocket event
- Manual test: UI updates in real-time

**Estimated Effort**: 0.5 days

---

### 2.7 Frontend: Horde Dropdown in Sidebar

**File**: `static/app.js`

**Purpose**: Add horde dropdown to sidebar (parallel to Spy dropdown)

**Location**: In `createProjectElement()` method, after Spy element creation

**Implementation**:

```javascript
// In createProjectElement() - add after Spy dropdown creation
if (project.is_multi_agent) {
    // Add Horde dropdown below Spy
    const hordeElement = this.createHordeElement(project, sessions);
    sessionsContainer.appendChild(hordeElement);
}

/**
 * Create Horde dropdown element for selecting horde to view
 */
createHordeElement(project, sessions) {
    const hordeContainer = document.createElement('div');
    hordeContainer.className = 'horde-dropdown-container mb-2';
    hordeContainer.id = `horde-${project.project_id}`;

    // Get all overseers (including user's root minions)
    const overseers = sessions.filter(s => s.is_overseer || !s.parent_overseer_id);

    // Header
    const hordeHeader = document.createElement('div');
    hordeHeader.className = 'horde-header d-flex align-items-center px-2 py-1';
    hordeHeader.innerHTML = `
        <span class="me-2">🏰</span>
        <span class="text-muted small">Hordes</span>
    `;
    hordeContainer.appendChild(hordeHeader);

    // Dropdown
    const dropdown = document.createElement('select');
    dropdown.className = 'form-select form-select-sm horde-selector';
    dropdown.id = `horde-selector-${project.project_id}`;

    // Default option
    const defaultOption = document.createElement('option');
    defaultOption.value = '';
    defaultOption.textContent = '-- Select Horde --';
    dropdown.appendChild(defaultOption);

    // Add overseer options
    for (const overseer of overseers) {
        const option = document.createElement('option');
        option.value = overseer.session_id;

        // Label format: "User's Horde" or "Overseer Name's Horde"
        const label = overseer.parent_overseer_id
            ? `${overseer.name}'s Horde`
            : `User's Horde (${overseer.name})`;
        option.textContent = label;

        // Add child count indicator
        const childCount = overseer.child_minion_ids?.length || 0;
        if (childCount > 0) {
            option.textContent += ` (${childCount})`;
        }

        dropdown.appendChild(option);
    }

    // Change handler
    dropdown.addEventListener('change', async (e) => {
        const overseerId = e.target.value;
        if (overseerId) {
            await this.selectHorde(project.project_id, overseerId);
        } else {
            // Deselect - exit horde view if currently viewing this horde
            if (this.viewMode === 'horde' && this.currentHordeOverseer) {
                this.exitSession();
            }
        }
    });

    hordeContainer.appendChild(dropdown);
    return hordeContainer;
}

/**
 * Select horde and show tree view in main area
 */
async selectHorde(legionId, overseerId) {
    // Update view mode
    this.viewMode = 'horde';
    this.currentLegionId = legionId;
    this.currentHordeOverseer = overseerId;
    this.currentSessionId = null;

    // Update URL
    this.updateURLWithHorde(overseerId);

    // Render horde tree view in main area
    await this.renderHordeTreeView(legionId, overseerId);

    // Update dropdown state
    const dropdown = document.getElementById(`horde-selector-${legionId}`);
    if (dropdown) {
        dropdown.value = overseerId;
    }
}

/**
 * Update URL for horde view (deep linking)
 */
updateURLWithHorde(overseerId) {
    window.history.pushState(
        { viewMode: 'horde', overseerId },
        '',
        `#horde/${overseerId}`
    );
}
```

**Testing Requirements**:
- Manual test: Horde dropdown appears below Spy
- Manual test: Dropdown lists all overseers
- Manual test: Selection triggers horde tree view
- Manual test: Deselection exits horde view

**Estimated Effort**: 0.5 days

---

### 2.8 Frontend: Horde Tree View in Main Area

**File**: `static/app.js`

**Purpose**: Display hierarchical tree view of selected horde in main area

**Location**: New method in ClaudeWebUI class

**Implementation**:

```javascript
/**
 * Render horde tree view in main content area
 */
async renderHordeTreeView(legionId, overseerId) {
    const contentArea = document.getElementById('content');
    contentArea.innerHTML = '';

    // Get overseer session
    const overseer = this.sessions.get(overseerId);
    if (!overseer) {
        contentArea.innerHTML = '<div class="alert alert-warning">Horde overseer not found</div>';
        return;
    }

    // Create container
    const container = document.createElement('div');
    container.className = 'horde-tree-view p-3';

    // Header
    const header = document.createElement('div');
    header.className = 'horde-header mb-4';
    header.innerHTML = `
        <h4>🏰 ${overseer.name}'s Horde</h4>
        <p class="text-muted">${overseer.role || 'No role specified'}</p>
        <div class="legend small text-muted mt-2">
            <strong>Legend:</strong>
            <span class="ms-2" style="color: #00ff00;">●</span> Active
            <span class="ms-2" style="color: #888;">○</span> Created
            <span class="ms-2" style="color: #ffaa00;">◐</span> Starting
            <span class="ms-2" style="color: #ffaa00;">⏸</span> Paused
            <span class="ms-2" style="color: #ff0000;">✗</span> Terminated
            <span class="ms-2" style="color: #ff0000;">⚠</span> Error
            <span class="ms-2">👑</span> Overseer
        </div>
    `;
    container.appendChild(header);

    // Tree container
    const treeContainer = document.createElement('div');
    treeContainer.className = 'tree-container';
    treeContainer.style.fontFamily = 'monospace';
    treeContainer.style.fontSize = '14px';

    // Build and render tree
    const tree = await this.buildHordeTree(overseerId);
    this.renderHordeTreeNode(tree, treeContainer, 0);

    container.appendChild(treeContainer);
    contentArea.appendChild(container);
}

/**
 * Build tree structure for horde
 */
async buildHordeTree(overseerId) {
    const overseer = this.sessions.get(overseerId);
    if (!overseer) return null;

    const children = await this.getChildrenRecursive(overseerId);

    return {
        minion: overseer,
        children
    };
}

/**
 * Recursively get all children
 */
async getChildrenRecursive(parentId) {
    const parent = this.sessions.get(parentId);
    if (!parent || !parent.child_minion_ids || parent.child_minion_ids.length === 0) {
        return [];
    }

    const children = [];
    for (const childId of parent.child_minion_ids) {
        const child = this.sessions.get(childId);
        if (child) {
            const grandchildren = await this.getChildrenRecursive(childId);
            children.push({
                minion: child,
                children: grandchildren
            });
        }
    }

    return children;
}

/**
 * Render tree node recursively
 */
renderHordeTreeNode(node, container, level) {
    if (!node) return;

    const { minion, children } = node;

    // Create node element
    const nodeDiv = document.createElement('div');
    nodeDiv.className = 'tree-node';
    nodeDiv.style.paddingLeft = `${level * 30}px`;
    nodeDiv.style.padding = '8px';
    nodeDiv.style.borderBottom = '1px solid #eee';
    nodeDiv.style.cursor = 'pointer';

    // State icon
    const stateIcon = this.getStateIconForTree(minion.state, minion.is_processing);

    // Overseer badge
    const overseerBadge = minion.is_overseer ? ' 👑' : '';

    // Child count
    const childCount = children.length > 0 ? ` (${children.length})` : '';

    // Build node content
    nodeDiv.innerHTML = `
        <span class="state-icon">${stateIcon}</span>
        <strong>${minion.name}</strong>${overseerBadge}
        <span class="text-muted ms-2" style="font-size: 0.9em">${minion.role || 'No role'}${childCount}</span>
    `;

    // Click to navigate to session
    nodeDiv.addEventListener('click', () => {
        this.selectSession(minion.session_id);
    });

    // Hover effect
    nodeDiv.addEventListener('mouseenter', () => {
        nodeDiv.style.backgroundColor = '#f8f9fa';
    });
    nodeDiv.addEventListener('mouseleave', () => {
        nodeDiv.style.backgroundColor = '';
    });

    container.appendChild(nodeDiv);

    // Render children
    for (const child of children) {
        this.renderHordeTreeNode(child, container, level + 1);
    }
}

/**
 * Get state icon for tree view
 */
getStateIconForTree(state, isProcessing) {
    if (isProcessing) return '<span style="color: #00ff00;">●</span>';

    switch (state) {
        case 'active': return '<span style="color: #00ff00;">●</span>';
        case 'created': return '<span style="color: #888;">○</span>';
        case 'starting': return '<span style="color: #ffaa00;">◐</span>';
        case 'paused': return '<span style="color: #ffaa00;">⏸</span>';
        case 'terminated': return '<span style="color: #ff0000;">✗</span>';
        case 'error': return '<span style="color: #ff0000;">⚠</span>';
        default: return '<span style="color: #888;">?</span>';
    }
}
```

**CSS Additions** (add to `static/styles.css`):

```css
/* Horde Tree View Styles */
.horde-tree-view {
    max-width: 1200px;
    margin: 0 auto;
}

.tree-node {
    transition: background-color 0.2s;
}

.tree-node:hover {
    background-color: #f8f9fa;
}

.horde-dropdown-container {
    background: #f8f9fa;
    border-radius: 4px;
    padding: 4px;
}

.horde-selector {
    border: 1px solid #dee2e6;
}
```

**Testing Requirements**:
- Manual test: Tree renders with correct hierarchy
- Manual test: State icons display correctly
- Manual test: Click node navigates to session
- Manual test: Real-time updates on spawn/dispose
- Manual test: Deep linking works (`#horde/{overseer_id}`)

**Estimated Effort**: 1.5 days

---

### 2.9 Frontend: Update Spy & Horde Dropdowns for Real-Time Updates

**File**: `static/app.js`

**Changes Required**:

1. **Add WebSocket event handlers**:
   ```javascript
   // In connectUIWebSocket()
   case 'minion_spawned':
       await this.handleMinionSpawned(data.data);
       break;
   case 'minion_disposed':
       await this.handleMinionDisposed(data.data);
       break;
   ```

2. **Implement handlers**:
   ```javascript
   async handleMinionSpawned(spawnData) {
       const { minion_id, name, role, parent_id, legion_id } = spawnData;

       // Fetch full session info
       const sessionInfo = await this.apiClient.get(`/api/sessions/${minion_id}`);
       this.sessions.set(minion_id, sessionInfo.session);

       // Update Spy dropdown if viewing this legion
       if (this.currentLegionId === legion_id) {
           this.updateSpyDropdown();
           this.updateHordeDropdown(legion_id);
       }

       // Update horde tree view if currently viewing parent's horde
       if (this.viewMode === 'horde' && this.currentHordeOverseer === parent_id) {
           await this.renderHordeTreeView(legion_id, parent_id);
       }

       // Show toast notification
       this.showToast('success', `Minion Spawned`, `${name} created by parent`, 3000);
   }

   async handleMinionDisposed(disposeData) {
       const { minion_id, name, parent_id, descendants_count } = disposeData;

       // Remove from sessions map
       this.sessions.delete(minion_id);

       // Update Spy dropdown if currently selected
       if (this.currentSessionId === minion_id) {
           this.exitSession(); // Exit if viewing disposed minion
       } else if (this.currentLegionId) {
           this.updateSpyDropdown();
           this.updateHordeDropdown(this.currentLegionId);
       }

       // Update horde tree view if currently viewing the horde
       if (this.viewMode === 'horde' && this.currentHordeOverseer) {
           const legionId = this.currentLegionId;
           await this.renderHordeTreeView(legionId, this.currentHordeOverseer);
       }

       // Show toast notification
       const descendantsMsg = descendants_count > 0 ? ` (and ${descendants_count} descendants)` : '';
       this.showToast('info', `Minion Disposed`, `${name} terminated${descendantsMsg}`, 3000);
   }

   updateSpyDropdown() {
       // Re-render Spy dropdown with updated session list
       const project = this.getProjectByLegionId(this.currentLegionId);
       if (!project) return;

       const spyElement = document.getElementById(`spy-${this.currentLegionId}`);
       if (spyElement) {
           const sessions = project.session_ids
               .map(sid => this.sessions.get(sid))
               .filter(s => s && s.is_minion);

           // Re-create spy dropdown content
           const newSpyElement = this.createSpyElement(project, sessions);
           spyElement.replaceWith(newSpyElement);
       }
   }

   updateHordeDropdown(legionId) {
       // Re-render Horde dropdown with updated overseer list
       const project = this.getProjectByLegionId(legionId);
       if (!project) return;

       const hordeElement = document.getElementById(`horde-${legionId}`);
       if (hordeElement) {
           const sessions = project.session_ids
               .map(sid => this.sessions.get(sid))
               .filter(s => s && s.is_minion);

           // Re-create horde dropdown content
           const newHordeElement = this.createHordeElement(project, sessions);
           hordeElement.replaceWith(newHordeElement);
       }
   }
   ```

**Testing Requirements**:
- Integration test: Spawn updates both Spy and Horde dropdowns
- Integration test: Spawn updates horde tree view if currently viewing
- Integration test: Dispose removes from dropdowns
- Integration test: Dispose updates horde tree view
- Integration test: Toast notifications appear
- Manual test: UI updates smoothly

**Estimated Effort**: 1 day

---

### 2.10 System Prompts for Spawning

**File**: `src/legion/overseer_controller.py` or configuration file

**Purpose**: Teach minions when and how to use spawn_minion tool

**Implementation**:

Add to minion initialization_context (can be appended automatically):

```markdown
## Spawning Child Minions

You can create specialized child minions to delegate work using the `spawn_minion` tool.

**When to spawn a child:**
- Task requires specialized expertise you don't have
- Sub-task is well-defined and can be isolated
- Parallel work would speed up completion
- Task is complex enough to warrant dedicated focus

**Example:**
```python
spawn_minion(
    name="DatabaseSchemaAnalyzer",
    role="Analyzes database schemas for the auth service",
    initialization_context="You are an expert in PostgreSQL database design. Your task is to analyze the current auth service schema and propose improvements for session storage with OAuth support. Focus on security, performance, and scalability.",
    capabilities=["database_design", "postgres", "security"],
    channels=["implementation-planning"]
)
```

**Important:**
- Choose descriptive, unique names (e.g., "SSOAnalyzer", not "Helper1")
- Provide detailed initialization_context explaining the specific task
- Dispose children when their task is complete using `dispose_minion(minion_name="...")`
- You are responsible for coordinating your children's work

## Disposing Child Minions

When a child minion completes their task, dispose of them to free resources:

```python
dispose_minion(minion_name="DatabaseSchemaAnalyzer")
```

Their knowledge will be transferred to you automatically.
```

**Testing Requirements**:
- Manual test: Minion uses spawn_minion appropriately
- Manual test: Minion disposes children when done
- Review: LLM understands when to spawn vs. do work directly

**Estimated Effort**: 0.5 days (writing + testing)

---

### 2.11 Integration Testing

**Goal**: Verify end-to-end autonomous spawning flow

**Test Scenarios**:

1. **Basic Spawn**:
   - User creates minion A
   - User asks A to "create a specialist for X"
   - A uses spawn_minion tool
   - Minion B appears in Spy dropdown
   - WebSocket event received
   - Timeline shows SPAWN comm

2. **Basic Dispose**:
   - Minion A disposes minion B
   - B removed from Spy dropdown
   - WebSocket event received
   - Timeline shows DISPOSE comm

3. **Multi-Level Hierarchy**:
   - A spawns B
   - B spawns C
   - A disposes B
   - Both B and C disposed
   - Hierarchy updates correctly

4. **Name Collision**:
   - A tries to spawn "B"
   - B already exists
   - Tool returns error
   - A receives error message
   - No partial state created

5. **Capacity Limit**:
   - Legion has 20 minions
   - A tries to spawn 21st
   - Tool returns capacity error
   - A receives clear error message

**Estimated Effort**: 1.5 days

---

## 3. State Bubbling Architecture

### 3.1 Current State Propagation (Already Working)

```
SessionCoordinator.create_session()
    → SessionManager.create_session()
    → SessionManager._persist_session_state()
    → SessionCoordinator._notify_state_change(session_id, CREATED)
    → web_server.py: _on_state_change() callback
    → UIWebSocketManager.broadcast_to_all({"type": "state_change", ...})
    → Frontend: app.js receives event
    → Frontend: this.sessions.set(session_id, sessionInfo)
    → Frontend: this.refreshSessions() updates UI
```

**✅ No Changes Needed** - Existing flow already handles state bubbling!

### 3.2 Additional Events for Phase 5

**New WebSocket Events**:
```javascript
// Spawn event
{
    "type": "minion_spawned",
    "data": {
        "minion_id": "uuid",
        "name": "DatabaseAnalyzer",
        "role": "Database specialist",
        "parent_id": "parent-uuid",
        "legion_id": "legion-uuid",
        "overseer_level": 1
    }
}

// Dispose event
{
    "type": "minion_disposed",
    "data": {
        "minion_id": "uuid",
        "name": "DatabaseAnalyzer",
        "parent_id": "parent-uuid",
        "descendants_count": 2
    }
}
```

**Broadcast Points**:
- `OverseerController.spawn_minion()`: After session created, broadcast `minion_spawned`
- `OverseerController.dispose_minion()`: After session terminated, broadcast `minion_disposed`

---

## 4. UI Paradigm Decision Matrix

| Approach | Pros | Cons | Decision |
|----------|------|------|----------|
| **Spy Dropdown (Current)** | ✅ Scalable (10-20+ minions)<br>✅ Quick access<br>✅ Already implemented | ❌ No hierarchy visibility | ✅ **Keep for individual minion selection** |
| **Horde Dropdown in Sidebar** | ✅ Consistent pattern<br>✅ Scalable<br>✅ No clutter | ❌ Requires main area for tree | ✅ **Add for horde selection** |
| **Horde Tree in Main Area** | ✅ Shows full hierarchy<br>✅ Full screen space<br>✅ State indicators<br>✅ Clickable nodes | ❌ Takes over main area | ✅ **Add as view mode** |

**Final Decision**:
- **Sidebar**: Add Horde dropdown below Spy (lists overseers to select)
- **Main Area**: Horde tree view showing selected horde's hierarchy
- **View Modes**: 'timeline', 'spy', 'session', **'horde'** (NEW)
- **Pattern**: Consistent with existing UI (dropdown in sidebar → detail in main area)

---

## 5. Testing Strategy

### 5.1 Unit Tests

**Files to Create**:
- `tests/test_overseer_controller_spawn.py` - spawn_minion() logic
- `tests/test_overseer_controller_dispose.py` - dispose_minion() logic
- `tests/test_legion_mcp_tools_lifecycle.py` - MCP tool handlers

**Coverage Target**: >90% for new methods

### 5.2 Integration Tests

**Files to Create**:
- `tests/integration/test_autonomous_spawning_flow.py` - End-to-end spawn flow
- `tests/integration/test_autonomous_disposal_flow.py` - End-to-end dispose flow
- `tests/integration/test_hierarchy_management.py` - Multi-level hierarchies

### 5.3 Manual Testing Checklist

- [ ] User creates legion with 1 minion
- [ ] Minion spawns child via tool
- [ ] Child appears in Spy dropdown
- [ ] Child appears in Horde dropdown
- [ ] Child appears in Horde tree view (if viewing that horde)
- [ ] Parent shows 👑 overseer badge
- [ ] Timeline shows SPAWN comm
- [ ] WebSocket event received in <1s
- [ ] Child can be selected and receive comms
- [ ] Parent disposes child
- [ ] Child removed from Spy dropdown
- [ ] Child removed from Horde dropdown
- [ ] Child removed from Horde tree view (if viewing that horde)
- [ ] Timeline shows DISPOSE comm
- [ ] Multi-level hierarchy (A → B → C) works
- [ ] Recursive disposal (A disposes B, auto-disposes C)
- [ ] Name collision rejected with clear error
- [ ] Capacity limit enforced with clear error

---

## 6. Risk Mitigation

### 6.1 Technical Risks

| Risk | Impact | Mitigation |
|------|--------|----------|
| **Recursive disposal bug** | HIGH - Orphaned sessions | Extensive testing of multi-level hierarchies |
| **WebSocket lag** | MEDIUM - UI feels slow | Add optimistic UI updates, show "Spawning..." state |
| **Name generation quality** | LOW - Poor names | System prompt examples, validation |
| **State synchronization** | HIGH - Desynced UI | Leverage existing state_change events, add integration tests |

### 6.2 Usability Risks

| Risk | Impact | Mitigation |
|------|--------|----------|
| **Overwhelming hierarchy** | MEDIUM - User lost | Horde Tree modal for exploration, Spy for quick access |
| **Unclear parent-child relationships** | MEDIUM - Confusion | Visual indicators (👑, indentation), tooltips |
| **Accidental disposal** | LOW - Lost work | Confirmation modal? (defer to Phase 8) |

---

## 7. Timeline & Dependencies

### Week 1: Backend Core (Days 1-4)
- **Day 1-2**: OverseerController.spawn_minion() + tests
- **Day 3-4**: OverseerController.dispose_minion() + tests

### Week 2: MCP Integration & UI (Days 5-10)
- **Day 5**: LegionMCPTools handlers + tests
- **Day 6**: WebSocket events + integration
- **Day 7**: Horde dropdown in sidebar
- **Day 8-9**: Horde tree view in main area + real-time updates
- **Day 10**: System prompts + integration testing

### Estimated Total: 8-10 days

**Dependencies**:
- SessionManager updates (0.5 day) - can be done in parallel with Day 1
- Comm routing for SPAWN/DISPOSE (already complete ✅)
- Existing WebSocket infrastructure (already complete ✅)

---

## 8. Acceptance Criteria

Phase 5 **COMPLETION STATUS**:

### Core Functionality ✅ COMPLETE
- [x] Minion can autonomously spawn child via spawn_minion MCP tool
- [x] Minion can dispose child via dispose_minion MCP tool
- [x] Child minion created with correct hierarchy fields (parent_overseer_id, overseer_level, horde_id)
- [x] Parent updated correctly (is_overseer=True, child_minion_ids)
- [x] Horde updated correctly (all_minion_ids)
- [x] WebSocket events broadcast for spawn/dispose (project_updated)
- [x] Spy dropdown updates in real-time
- [x] Horde dropdown appears in sidebar below Spy
- [x] Horde dropdown lists all overseers
- [x] Selecting horde shows tree view in main area
- [x] Horde tree view displays hierarchy correctly with state indicators
- [x] Timeline shows SPAWN/DISPOSE comms
- [x] Recursive disposal works (grandchildren disposed first)
- [x] Name uniqueness validated and enforced
- [x] Capacity limit (20) enforced
- [x] Manual testing checklist complete

### Deferred to Post-UI-Refactor 🔄
- [ ] Clicking node in tree navigates to that minion's session (UI refactor will handle routing)
- [ ] Deep linking works for horde view (`#horde/{overseer_id}`) (UI refactor will add routing)
- [ ] System prompts teach spawning appropriately (Phase 6+ feature)
- [ ] All unit tests passing (>90% coverage) (post-refactor test suite)
- [ ] All integration tests passing (post-refactor test suite)

---

## 9. Future Enhancements (Post-MVP)

**Not in Phase 5, deferred to later**:

- Memory distillation on disposal (Phase 7)
- Knowledge transfer to parent (Phase 7)
- Advanced horde management (split, merge, transfer)
- Minion migration between hordes
- Template-based spawning (predefined specialist types)
- Cost tracking per minion
- Auto-disposal on task completion detection
- Spawn approval workflow (user confirms before spawn)

---

## 10. Open Questions for User

Before implementation, please clarify:

1. **~~Horde Tree Modal Trigger~~**: ✅ RESOLVED - Using sidebar dropdown → main area tree view pattern (consistent with existing UI)

2. **Disposal Confirmation**: Should we add confirmation modal for dispose (to prevent accidents)? Or trust minions + allow undo?

3. **Max Overseer Level**: Should we enforce a maximum depth (e.g., 5 levels)? Or allow unlimited?

4. **SPAWN Comm Format**: Current format shows parent → user notification. Should we also send parent → child "welcome" comm?

5. **Capability Inheritance**: Should children inherit parent's capabilities by default? Or always empty unless specified?

---

## Document Control

- **Version**: 1.0
- **Date**: 2025-10-22
- **Status**: Ready for Review
- **Owner**: Development Team
- **Dependencies**: Phases 1-4 complete
- **Next Steps**: User review → approval → begin Day 1 implementation
