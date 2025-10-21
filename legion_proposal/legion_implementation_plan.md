# Legion Multi-Agent System - Implementation Plan

## 1. Overview

### 1.1 Implementation Strategy

**Phased Approach**: 8 phases over approximately 9 weeks
- Each phase builds on previous work
- Each phase has clear deliverables and acceptance criteria
- Testing integrated throughout, not just at end
- Documentation updated continuously

**Risk Mitigation**:
- Start with smallest useful increment (Phase 1)
- Validate architecture early (Phase 2)
- Get core functionality working before advanced features
- Regular review points after each phase

### 1.2 Dependencies

**Must Complete Before Starting**:
- Review and finalize requirements document
- Review and finalize technical design document
- Review and finalize UX design document
- Set up development environment
- Agree on coding standards and review process

**External Dependencies**:
- Claude Agent SDK (existing, stable)
- Existing Claude WebUI codebase (reference implementation)

---

## 2. Phase Breakdown

### Phase 1: Foundation & Data Models (Week 1-2)

**Goal**: Establish data models, file structure, and MCP tool framework without implementing full behavior.

#### Tasks

**1.1 Create Data Model Classes**
- [x] Create `src/models/legion_models.py`
  - LegionInfo dataclass
  - MinionInfo dataclass
  - Horde dataclass
  - Channel dataclass
  - Comm dataclass with CommType and InterruptPriority enums
  - MinionState enum
- [x] Create `src/models/memory_models.py`
  - MemoryEntry dataclass
  - MemoryType enum
  - MinionMemory dataclass
  - TaskMilestone dataclass
- [x] Add validation methods to each model (Comm.validate())
- [x] Add serialization methods (to_dict, from_dict)
- [x] Write unit tests for each model (23 tests passing)

**1.2 Create LegionSystem Dependency Injection Container**
- [x] Create `src/legion_system.py`
  - LegionSystem dataclass with all component references
  - Uses `field(init=False)` for legion components
  - `__post_init__` to wire components in correct order
- [x] Document component initialization order
- [x] Write unit tests for LegionSystem initialization
- [x] Verify no circular import issues

**1.3 Create MCP Tools Framework**
- [x] Create `src/legion/mcp/legion_mcp_tools.py`
  - LegionMCPTools class skeleton accepting LegionSystem
  - Tool definition structure
  - Tool handler method signatures (implementation in later phases)
- [x] Define all MCP tool schemas:
  - send_comm, send_comm_to_channel (with #tag syntax in descriptions)
  - spawn_minion, dispose_minion
  - search_capability (central registry), list_minions, get_minion_info
  - join_channel, create_channel, list_channels
- [x] Add tool validation helpers (async handle_tool_call router)
- [x] Write unit tests for tool schema validation (10 tests passing)

**1.4 Create Component Skeletons (Accept LegionSystem)**
- [x] Create `src/legion/legion_coordinator.py` skeleton
  - Constructor accepts `system: LegionSystem`
  - Add `capability_registry: Dict[str, List[str]]` field
  - Add method signatures for all coordinator operations
- [x] Create `src/legion/comm_router.py` skeleton
  - Constructor accepts `system: LegionSystem`
  - Add `_extract_tags()` method for #tag parsing
- [x] Create `src/legion/overseer_controller.py` skeleton
  - Constructor accepts `system: LegionSystem`
- [x] Create `src/legion/channel_manager.py` skeleton
  - Constructor accepts `system: LegionSystem`
- [x] Create `src/legion/memory_manager.py` skeleton
  - Constructor accepts `system: LegionSystem`
- [x] Write unit tests verifying component initialization via LegionSystem

**1.5 Extend Existing Models**
- [x] Extend `ProjectInfo` to support `is_multi_agent` flag
- [x] Ensure backward compatibility with existing projects (migration in from_dict)
- [x] Document `SessionInfo` compatibility with MinionInfo
- [x] Write migration logic and backward compatibility tests (6 tests passing)

**1.6 SDK Integration Test**
- [ ] Test MCP tool attachment to SDK session (using Claude Agent SDK docs)
- [ ] Verify tool definitions visible to SDK
- [ ] Test simple tool call with mock handler
- [ ] Validate tool result format
- [ ] Write integration test for SDK + MCP tools

**1.7 File System Structure**
- [ ] Create directory structure generator
  - `data/legions/{legion_id}/`
  - Subdirectories for hordes, channels, minions
- [ ] Implement path helpers (get_minion_path, get_channel_path, etc.)
- [ ] Write file existence checks and validation
- [ ] Write unit tests for path resolution

**1.8 Storage Layer Extension**
- [ ] Extend `DataStorageManager` with new methods:
  - `append_comm()` - Write Comm to JSONL
  - `read_comms()` - Read Comms with pagination
  - `get_comm_count()` - Count total Comms
- [ ] Add Comm storage in multiple locations (per-minion, per-channel, timeline)
- [ ] Ensure atomic writes (prevent corruption)
- [ ] Write unit tests for storage operations

**1.9 Configuration & Setup**
- [ ] Add legion-specific configuration to settings
  - Default max_concurrent_minions
  - Default permission modes
- [ ] Update logging configuration for new components
- [ ] Create debug flags: `--debug-legion`, `--debug-comms`

#### Deliverables
- [ ] All data model classes with tests (>90% coverage)
- [ ] LegionSystem dependency injection container
- [ ] All component skeletons accepting LegionSystem
- [ ] MCP tool framework with all tool schemas defined (including #tag syntax)
- [ ] SDK integration test validating MCP tool attachment
- [ ] Extended storage layer with tests
- [ ] File system structure working
- [ ] No breaking changes to existing WebUI functionality

#### Acceptance Criteria
- [ ] Can instantiate all data models
- [ ] Can serialize/deserialize all models to JSON
- [ ] LegionSystem can initialize all components without circular dependencies
- [ ] MCP tools can be attached to SDK session
- [ ] Simple tool call works end-to-end (mock handler)
- [ ] Can create legion directory structure
- [ ] Can write and read Comms from storage
- [ ] All tests passing

**Estimated Effort**: 7-9 days (added LegionSystem + component skeletons)

---

### Phase 2: MCP Tool Implementation & Communication (Week 2-3)

**Goal**: Implement MCP tool handlers and basic Comm routing.

#### Tasks

**2.1 MCP Tool Handlers - Communication**
- [ ] Implement `_handle_send_comm()` in LegionMCPTools
  - Validate target minion exists (by name)
  - Create Comm object
  - Route via CommRouter
  - Return success/error to SDK
- [ ] Implement `_handle_send_comm_to_channel()`
  - Validate channel exists
  - Create broadcast Comm
  - Route to ChannelManager
  - Return success/error
- [ ] Write unit tests for tool handlers

**2.2 CommRouter Core**
- [ ] Implement `src/comm_router.py`
  - `route_comm()` method (dispatch logic)
  - `_send_to_minion()` (inject Comm as Message to SDK)
  - `_format_comm_as_message()` (format for SDK injection)
  - `_send_to_user()` (send via WebSocket)
- [ ] Implement Comm validation (exactly one source, one destination)
- [ ] Implement basic routing logic (direct to minion or user)
- [ ] Write unit tests (mock SessionCoordinator, WebSocket)

**2.3 Tag Parsing Implementation**
- [ ] Implement `_extract_tags()` in CommRouter
  - Regex pattern for `#([a-zA-Z0-9_-]+)`
  - Validate tags against actual minion/channel names
  - Return dict with `{"minions": [...], "channels": [...]}`
- [ ] Add tag validation helpers
  - `_is_valid_minion_name()`
  - `_is_valid_channel_name()`
- [ ] Store extracted tags in Comm metadata
- [ ] Write unit tests for tag extraction

**2.4 Comm Persistence**
- [ ] Implement `_persist_to_timeline()` (legion-wide log)
- [ ] Implement `_persist_to_minion_log()` (per-minion log)
- [ ] Handle duplicate prevention (idempotency)
- [ ] Write integration tests (create Comm, verify persisted correctly)

**2.5 Integration with SessionCoordinator**
- [ ] Modify SessionCoordinator to attach MCP tools to minion sessions
  - Pass LegionMCPTools instance to create_session()
  - Attach tools via ClaudeAgentOptions
- [ ] Ensure non-legion sessions continue to work unchanged (no MCP tools)
- [ ] Write integration tests (MCP tool call through SDK)

**2.6 Interrupt Handling (Basic)**
- [ ] Implement interrupt detection (HALT, PIVOT)
- [ ] Call `SessionCoordinator.interrupt_session()` for HALT
- [ ] Implement queue clearing for PIVOT (stub for now)
- [ ] Write tests for interrupt flow

#### Deliverables
- [ ] MCP tool handlers for send_comm and send_comm_to_channel implemented
- [ ] CommRouter class fully implemented with tag parsing
- [ ] Tag extraction and validation working
- [ ] Comms persisted to storage (with tag metadata)
- [ ] MCP tools attached to SDK sessions
- [ ] Integration with SessionCoordinator complete

#### Acceptance Criteria
- [ ] Minion can use send_comm MCP tool successfully
- [ ] Tool call creates Comm, routes to target minion
- [ ] Target minion receives Comm as injected Message
- [ ] Tags (#minion-name, #channel-name) extracted and validated
- [ ] Comm persisted to timeline and minion logs
- [ ] HALT and PIVOT trigger interrupts
- [ ] Tool errors return clear messages to calling minion
- [ ] All tests passing
- [ ] Existing WebUI sessions unaffected

**Estimated Effort**: 5-7 days (added tag parsing)

---

### Phase 3: Basic UI - Timeline & Sidebar (Week 3-4)

**Goal**: Build minimal UI to visualize Comms and minions.

#### Tasks

**3.1 Sidebar - Legion Display**
- [ ] Extend sidebar to detect legion projects
- [ ] Show legion icon (🏛) for legion projects
- [ ] Add "Enable Multi-Agent" checkbox to project creation modal
- [ ] Create legion on project creation if checkbox enabled
- [ ] Write frontend tests (modal behavior)

**3.2 Sidebar - Minion List (Simple)**
- [ ] Display flat list of minions under legion (no hierarchy yet)
- [ ] Show minion name and state indicator (● ⏸ ✗)
- [ ] Click minion to select (highlight)
- [ ] Write frontend tests (list rendering)

**3.3 Timeline View (Minimal)**
- [ ] Create new "Timeline" tab in main view (parallel to existing messages view)
- [ ] Fetch recent 100 Comms via GET `/api/legions/{id}/timeline`
- [ ] Render Comm cards:
  - Source → Destination
  - Comm type badge
  - Content (truncated, with tag highlighting)
  - Timestamp
- [ ] Basic styling (color-coded borders by type)
- [ ] Implement tag rendering:
  - Parse `#minion-name` and `#channel-name` in content
  - Highlight tags with distinct background color (light blue)
  - Make tags clickable (navigate to minion/channel detail)
  - Add hover tooltips showing minion/channel info
- [ ] Write frontend tests (card rendering, tag parsing)

**3.4 Comm Composer (with Tag Autocomplete)**
- [ ] Add Comm input at bottom of timeline
- [ ] Dropdown to select recipient minion (by name)
- [ ] Dropdown to select Comm type (TASK, QUESTION, GUIDE)
- [ ] Text area for content with tag autocomplete:
  - Trigger autocomplete dropdown when user types `#`
  - Show filtered list of minions and channels
  - Filter as user continues typing (e.g., `#Auth` shows `AuthExpert`)
  - Select with Enter key or click
  - Insert full tag name (e.g., `#AuthExpert`)
- [ ] Send button (POST `/api/legions/{id}/comms`)
- [ ] Optimistic UI (show Comm immediately, confirm via WebSocket)
- [ ] Write frontend tests (send flow, autocomplete behavior)

**3.5 WebSocket Real-Time Updates**
- [ ] Extend WebSocket manager for legion events
  - `/ws/legion/{legion_id}` endpoint
- [ ] Subscribe to legion WebSocket on legion selection
- [ ] Handle `comm` event → append to timeline
- [ ] Handle `state_change` event → update sidebar
- [ ] Write integration tests (simulate WebSocket events)

**3.6 Backend API Endpoints (Minimal)**
- [ ] Implement basic endpoints:
  - `POST /api/legions` (create legion)
  - `GET /api/legions/{id}` (get legion with minions)
  - `GET /api/legions/{id}/timeline` (get Comms with pagination)
  - `POST /api/legions/{id}/comms` (send Comm)
- [ ] Wire up to LegionCoordinator (stub for now)
- [ ] Write API tests

#### Deliverables
- [ ] Legion creation via UI working
- [ ] Minions visible in sidebar
- [ ] Timeline displays Comms with tag highlighting
- [ ] Tag autocomplete in Comm composer
- [ ] Can send Comm from UI to minion (with tags)
- [ ] Real-time updates via WebSocket

#### Acceptance Criteria
- [ ] User can create legion with checkbox
- [ ] Legion appears in sidebar with icon
- [ ] Timeline loads and displays Comms
- [ ] Tags (#minion-name, #channel-name) highlighted and clickable
- [ ] Autocomplete appears when typing # in composer
- [ ] Can send Comm, appears in timeline immediately with rendered tags
- [ ] WebSocket updates timeline in real-time
- [ ] All tests passing

**Estimated Effort**: 8-10 days (added tag rendering + autocomplete)

---

### Phase 4: LegionCoordinator & Minion Creation (Week 4-5)

**Goal**: Implement LegionCoordinator and enable user to manually create minions.

#### Tasks

**4.1 LegionCoordinator Core**
- [ ] Create `src/legion_coordinator.py`
  - LegionCoordinator class
  - Initialize with references to SessionCoordinator, CommRouter
  - Maintain dicts of legions, minions, hordes, channels
- [ ] Implement `create_legion()`
  - Generate legion_id
  - Create LegionInfo
  - Create directory structure
  - Persist to storage
- [ ] Implement `delete_legion()`
  - Terminate all minions
  - Archive data
  - Clean up references
- [ ] Write unit tests

**4.2 Manual Minion Creation**
- [ ] Implement `create_minion_for_user()` in OverseerController
  - Validate name uniqueness
  - Check minion limit
  - Create MinionInfo
  - Create SDK session via SessionCoordinator
  - Start session
  - Create horde (minion as root)
  - Persist
- [ ] Wire up to LegionCoordinator
- [ ] Write integration tests

**4.3 Horde Management (Basic)**
- [ ] Implement horde creation on first minion
  - Generate horde_id
  - Create Horde object
  - Set minion as root_overseer
- [ ] Update horde when minion added (for future children)
- [ ] Persist horde state
- [ ] Write tests

**4.4 Backend API - Minion Creation**
- [ ] Implement `POST /api/legions/{id}/minions`
  - Accept name, role, initialization_context, channels
  - Call OverseerController.create_minion_for_user()
  - Return minion_id and state
- [ ] Implement `GET /api/minions/{id}`
  - Return full MinionInfo
  - Include children (empty for now), recent Comms
- [ ] Write API tests

**4.5 UI - Create Minion Modal**
- [ ] Build "Create Minion" modal (as per UX design)
  - Name, role, initialization context fields
  - Templates dropdown (stub for now)
  - Channel selection (checkboxes)
  - Advanced options (collapsed)
- [ ] Wire up to API endpoint
- [ ] Update sidebar on creation (WebSocket event)
- [ ] Write frontend tests

**4.6 Integration Testing**
- [ ] End-to-end test: Create legion → Create minion → Minion appears in sidebar
- [ ] Test: Send Comm to minion → Minion receives and responds
- [ ] Test: Multiple minions in same legion

#### Deliverables
- [ ] LegionCoordinator fully implemented
- [ ] User can manually create minions via UI
- [ ] Minions run in dedicated SDK sessions
- [ ] Hordes created automatically

#### Acceptance Criteria
- [ ] User can create legion
- [ ] User can create minion with custom name, role, context
- [ ] Minion appears in sidebar immediately
- [ ] Minion is active and can receive Comms
- [ ] Can have multiple minions in same legion
- [ ] Minion limit enforced (20 max)
- [ ] All tests passing

**Estimated Effort**: 6-7 days

---

### Phase 5: Autonomous Spawning via MCP Tools (Week 5-6)

**Goal**: Enable minions to autonomously spawn and dispose children using MCP tools.

#### Tasks

**5.1 MCP Tool Handlers - Lifecycle**
- [ ] Implement `_handle_spawn_minion()` in LegionMCPTools
  - Validate parent authority (caller is valid minion)
  - Validate name uniqueness within legion
  - Check minion limit
  - Call OverseerController.spawn_minion()
  - Return success with minion_id OR detailed error
- [ ] Implement `_handle_dispose_minion()` in LegionMCPTools
  - Verify parent authority (child belongs to caller)
  - Call OverseerController.dispose_minion()
  - Return success OR error with reason
- [ ] Write unit tests for both handlers

**5.2 OverseerController - Spawn Logic**
- [ ] Implement `spawn_minion()` in OverseerController
  - Create child MinionInfo (parent_overseer_id set)
  - Create SDK session with initialization_context + MCP tools
  - Update parent (is_overseer = True, add child_id)
  - Update horde (add to all_minion_ids)
  - Send SPAWN Comm to user (notification)
- [ ] Write unit tests

**5.3 OverseerController - Dispose Logic**
- [ ] Implement `dispose_minion()` in OverseerController
  - Recursively dispose children first
  - Distill memory (stub for now, Phase 7)
  - Transfer knowledge to parent (stub for now, Phase 7)
  - Terminate SDK session
  - Update parent (remove from child_ids)
  - Update horde (remove from all_minion_ids)
  - Send DISPOSE Comm to user (notification)
- [ ] Write unit tests

**5.4 System Prompts for Spawning**
- [ ] Design system prompt template that teaches MCP tool usage
  - Explain spawn_minion tool and when to use it
  - Provide examples of good initialization_context
  - Explain dispose_minion tool and cleanup responsibility
- [ ] Add to minion creation (all minions get this capability)
- [ ] Test with real SDK (validate minions understand tools)

**5.6 UI - Horde Hierarchy View**
- [ ] Update sidebar to show hierarchy (not flat list)
  - Indent children under parents
  - Show overseer icon (👑) for minions with children
  - Collapse/expand horde trees
- [ ] Show SPAWN/DISPOSE Comms in timeline with special styling
- [ ] Write frontend tests

**5.7 Integration Testing**
- [ ] End-to-end test: User sends task → Minion spawns child → Child appears in sidebar
- [ ] Test: Parent disposes child → Child removed from sidebar
- [ ] Test: Multi-level hierarchy (parent → child → grandchild)
- [ ] Test: Minion limit enforced during spawn

#### Deliverables
- [ ] Minions can autonomously spawn children
- [ ] Minions can dispose children
- [ ] Hierarchy visible in UI
- [ ] SPAWN/DISPOSE notifications in timeline

#### Acceptance Criteria
- [ ] Minion can spawn child via SDK reasoning
- [ ] Child minion created and started automatically
- [ ] Child appears under parent in sidebar hierarchy
- [ ] Parent shows as overseer (👑 icon)
- [ ] Parent can dispose child
- [ ] Child removed from sidebar on disposal
- [ ] Horde membership correct throughout lifecycle
- [ ] All tests passing

**Estimated Effort**: 6-7 days

---

### Phase 6: Channels & Capability Discovery (Week 6-7)

**Goal**: Implement channels for cross-horde collaboration and central capability registry.

#### Tasks

**6.1 ChannelManager Core**
- [ ] Implement `src/channel_manager.py` (skeleton created in Phase 1)
  - `create_channel()` method
  - `add_member()`, `remove_member()` methods
  - Persist channel state
- [ ] Write unit tests

**6.2 Channel Broadcasting**
- [ ] Implement `_broadcast_to_channel()` in CommRouter
  - Get channel members
  - Send direct Comm to each member (except sender)
  - Mark as reply to original Comm
  - Persist to channel log
- [ ] Write tests

**6.3 Central Capability Registry (MVP)**
- [ ] Implement `register_capability()` in LegionCoordinator
  - Add capability to `capability_registry` dict
  - Called when minion is created (user or spawned)
- [ ] Implement `search_capability_registry()` in LegionCoordinator
  - Case-insensitive keyword search
  - Return ranked results by expertise_score
  - Sort highest to lowest
- [ ] Implement `_handle_search_capability()` in LegionMCPTools
  - Call `legion_coordinator.search_capability_registry()`
  - Format results for SDK
  - Return success/error
- [ ] Update `create_minion_for_user()` and `spawn_minion()` in OverseerController
  - Register capabilities after session creation
  - Loop through `minion.capabilities` and call `register_capability()`
- [ ] Write tests with mock capability data

**6.4 Capability Initialization & Updates**
- [ ] Add capability field population during minion creation
  - Extract capabilities from initialization_context (future: LLM extraction)
  - For now: Allow user to specify capabilities manually
- [ ] Add expertise score tracking
  - Initialize scores to 0.5
  - Update based on task completion success/failure (stub for now)
- [ ] Write tests

**6.5 Backend API - Channels**
- [ ] Implement channel endpoints:
  - `POST /api/legions/{id}/channels` (create)
  - `GET /api/channels/{id}` (get details)
  - `POST /api/channels/{id}/join` (add member)
  - `POST /api/channels/{id}/leave` (remove member)
  - `POST /api/channels/{id}/broadcast` (send to channel)
  - `GET /api/channels/{id}/comms` (get channel Comms)
- [ ] Write API tests

**6.6 UI - Channel Display**
- [ ] Add "Channels" section to sidebar
  - List all channels
  - Show member count
  - Click to view channel details
- [ ] Create channel detail modal (as per UX design)
  - Overview, members, Comms tabs
  - Join/leave actions
- [ ] Add channel filter to timeline
- [ ] Write frontend tests

**6.7 UI - Create Channel Modal**
- [ ] Build "Create Channel" modal
  - Name, description, purpose fields
  - Initial member selection (checkboxes)
- [ ] Wire up to API endpoint
- [ ] Write frontend tests

**6.8 Integration Testing**
- [ ] Test: Create channel → Add members → Broadcast → All members receive
- [ ] Test: Member leaves channel → No longer receives broadcasts
- [ ] Test: Central registry search finds minions by capability keyword
- [ ] Test: Minion in multiple channels receives broadcasts from all
- [ ] Test: search_capability returns ranked results

#### Deliverables
- [ ] Channels working with broadcast functionality
- [ ] Central capability registry implemented
- [ ] Capability tracking and registration in place
- [ ] UI for channel management

#### Acceptance Criteria
- [ ] User can create channel
- [ ] User can add minions to channel
- [ ] User can broadcast to channel
- [ ] All channel members receive broadcast
- [ ] Timeline shows channel broadcasts
- [ ] Minion can search for capabilities via central registry
- [ ] Search returns ranked results by expertise score
- [ ] Channel detail modal shows members and Comms
- [ ] All tests passing

**Estimated Effort**: 5-6 days (simplified with central registry instead of gossip)

**Note**: Gossip-based discovery deferred to post-MVP for distributed search enhancements.

---

### Phase 7: Memory & Learning (Week 7-8)

**Goal**: Implement memory distillation, reinforcement, and forking.

#### Tasks

**7.1 MemoryManager Core**
- [ ] Create `src/memory_manager.py`
  - MemoryManager class
  - Initialize with SessionCoordinator reference
- [ ] Write skeleton for all methods (implementations below)

**7.2 Memory Distillation**
- [ ] Implement `distill_completion()`
  - Get messages since last distillation
  - Use Claude SDK to summarize (separate distillation session)
  - Extract structured MemoryEntry objects
  - Append to short_term_memory.json
  - Update last_distilled_at timestamp
- [ ] Add TaskMilestone detection
  - Detect when minion indicates task complete
  - Trigger distillation automatically
- [ ] Write tests (mock SDK responses)

**7.3 Memory Reinforcement**
- [ ] Implement `reinforce_memory()`
  - Trace which memories were used in task
  - Adjust quality_score based on success/failure
  - Update times_used_successfully/unsuccessfully
  - Save updated memories
- [ ] Add user feedback detection
  - User says "that's correct" → success
  - User says "actually..." → failure
  - Trigger reinforcement with outcome
- [ ] Write tests

**7.4 Long-Term Memory Promotion**
- [ ] Implement `promote_to_long_term()`
  - Identify high-quality memories (score >= 0.8)
  - Move from short_term to long_term
  - Save both memory files
- [ ] Add periodic promotion (after N tasks or time)
- [ ] Write tests

**7.5 Knowledge Transfer**
- [ ] Implement `transfer_knowledge()`
  - Load source minion's memories
  - Filter high-quality memories (score >= 0.6)
  - Append to destination minion's short_term
  - Save destination memory
- [ ] Call automatically on minion disposal
- [ ] Write tests

**7.6 Minion Forking**
- [ ] Implement `fork_minion()`
  - Create new minion with same initialization_context
  - Copy all memory files (short_term, long_term, capability_evidence)
  - Set forked_from, fork_generation metadata
  - Return new minion_id
- [ ] Write tests

**7.7 Backend API - Memory & Forking**
- [ ] Implement endpoints:
  - `GET /api/minions/{id}/memory` (view memories)
  - `POST /api/minions/{id}/fork` (fork minion)
- [ ] Write API tests

**7.8 UI - Memory Viewer**
- [ ] Add "Memory" tab to minion detail modal
  - Display short-term memories with quality scores
  - Display long-term memories
  - Color-code by quality (green=high, yellow=medium, red=low)
- [ ] Write frontend tests

**7.9 UI - Fork Modal**
- [ ] Build "Fork Minion" modal (as per UX design)
  - Show source minion stats
  - New name, role fields
  - Channel selection
  - Explanation of forking
- [ ] Wire up to API endpoint
- [ ] Write frontend tests

**7.10 Integration Testing**
- [ ] Test: Minion completes task → Memory distilled automatically
- [ ] Test: User corrects minion → Memory reinforced (score adjusted)
- [ ] Test: Child disposed → Knowledge transferred to parent
- [ ] Test: Fork minion → New minion has identical memory
- [ ] Test: Forked minions diverge over time (different experiences)

#### Deliverables
- [ ] Memory distillation working
- [ ] Memory reinforcement based on feedback
- [ ] Knowledge transfer on disposal
- [ ] Minion forking with memory copy
- [ ] Memory viewer in UI

#### Acceptance Criteria
- [ ] Minion memory distilled on task completion
- [ ] User feedback adjusts memory quality scores
- [ ] Disposed minion's knowledge transferred to parent
- [ ] Can fork minion, new minion has same memory
- [ ] Forked minions operate independently
- [ ] Memory viewer shows current memories
- [ ] All tests passing

**Estimated Effort**: 7-8 days

---

### Phase 8: Observability & Control (Week 8-9)

**Goal**: Complete observability features and fleet controls.

#### Tasks

**8.1 Fleet Controls UI**
- [ ] Build fleet controls panel (as per UX design)
  - Fleet status summary
  - Active/paused/error minion breakdown
  - Emergency halt button
  - Resume all button
- [ ] Wire up to API endpoints
- [ ] Write frontend tests

**8.2 Emergency Halt**
- [ ] Implement `emergency_halt_all()` in LegionCoordinator
  - Halt all active minions in legion
  - Update state to PAUSED
  - Do NOT terminate sessions
- [ ] Implement `resume_all()`
  - Resume all paused minions
  - Update state to ACTIVE
- [ ] Add confirmation modal for emergency halt
- [ ] Write tests

**8.3 Individual Minion Controls**
- [ ] Implement `halt_minion()` in LegionCoordinator
  - Call SessionCoordinator.interrupt_session()
  - Update state to PAUSED
- [ ] Implement `resume_minion()`
  - Update state to ACTIVE
  - Session remains active, continues processing queue
- [ ] Add UI buttons (in minion detail modal, fleet controls)
- [ ] Write tests

**8.4 Pivot Modal**
- [ ] Build "Pivot Minion" modal (as per UX design)
  - Show current task
  - Text area for new instructions
  - Warning about queue clearing
- [ ] Implement pivot logic
  - Halt minion
  - Clear message queue (implement in SessionCoordinator)
  - Send PIVOT Comm with new instructions
- [ ] Write frontend tests

**8.5 Minion Detail Enhancements**
- [ ] Complete minion detail modal
  - All tabs (Overview, Memory, History, Session Messages)
  - Recent activity timeline
  - Capability display with evidence
  - Hierarchy (parent, children)
- [ ] Add "View Full History" link (full-screen view)
- [ ] Write frontend tests

**8.6 Timeline Enhancements**
- [ ] Add advanced filtering
  - Multiple Comm types
  - Multiple minions
  - Date range
  - Search across content
- [ ] Add "Load More" pagination
- [ ] Add export functionality (optional)
- [ ] Write frontend tests

**8.7 Error Handling & Empty States**
- [ ] Implement all error states from UX design
  - Minion creation failed
  - Comm delivery failed
  - SDK session crash
- [ ] Implement all empty states
  - No minions in legion
  - No Comms in timeline
  - No channels
- [ ] Add error recovery flows
- [ ] Write tests for error scenarios

**8.8 Performance Optimization**
- [ ] Implement virtualized timeline (React Virtualized or similar)
- [ ] Optimize sidebar rendering (lazy load children)
- [ ] Batch WebSocket updates
- [ ] Add loading skeletons
- [ ] Profile and optimize slow queries

**8.9 Documentation**
- [ ] Write user documentation
  - How to create legion
  - How to create/spawn minions
  - How to use channels
  - How to interpret timeline
  - Common workflows
- [ ] Write developer documentation
  - Architecture overview
  - Adding new Comm types
  - Extending memory system
  - API reference
- [ ] Update README

**8.10 End-to-End Validation**
- [ ] Test all three use cases:
  - SaaS refactor scenario (5+ service experts, channels, spawning)
  - D&D campaign scenario (characters, NPCs, scenes)
  - Research scenario (lead + specialists, synthesis)
- [ ] Performance test with 20 concurrent minions
- [ ] Stress test (rapid spawning/disposing)
- [ ] Load test (1000+ Comms in timeline)

#### Deliverables
- [ ] Fleet controls fully functional
- [ ] All observability features complete
- [ ] Error handling comprehensive
- [ ] Performance optimized
- [ ] Documentation complete

#### Acceptance Criteria
- [ ] User can emergency halt entire fleet
- [ ] User can halt/resume individual minions
- [ ] User can pivot minion with new instructions
- [ ] Minion detail modal shows complete information
- [ ] Timeline filtering and search working
- [ ] All error states handled gracefully
- [ ] All three use cases validated end-to-end
- [ ] System supports 20 concurrent minions
- [ ] All tests passing
- [ ] Documentation complete and accurate

**Estimated Effort**: 8-10 days

---

## 3. Testing Strategy

### 3.1 Testing Levels

**Unit Tests**:
- All data models (validation, serialization)
- All business logic methods
- Utility functions
- Target: >90% code coverage

**Integration Tests**:
- CommRouter with SessionCoordinator
- OverseerController with CommRouter
- API endpoints with full stack
- WebSocket event flow

**End-to-End Tests**:
- Full user workflows (create legion → create minion → send Comm)
- Multi-minion scenarios
- Channel broadcasting
- Spawning and disposal

**Performance Tests**:
- 20 concurrent minions
- 1000+ Comms in timeline
- Rapid spawning/disposing

### 3.2 Testing Tools

**Backend**:
- pytest (unit and integration tests)
- pytest-asyncio (async tests)
- pytest-mock (mocking)
- pytest-cov (coverage)

**Frontend**:
- Jest (unit tests)
- React Testing Library (component tests)
- Cypress or Playwright (E2E tests)

**Performance**:
- locust or k6 (load testing)
- Python profiler (cProfile)

### 3.3 CI/CD

**Continuous Integration**:
- Run all tests on every commit
- Block merge if tests fail
- Generate coverage reports

**Deployment**:
- Manual deployment (local workstation for MVP)
- Future: Docker container, one-click deploy

---

## 4. Risk Management

### 4.1 Technical Risks

| Risk | Mitigation | Contingency |
|------|-----------|-------------|
| **SDK session overhead too high** | Start with 5-minion limit in testing | Implement minion pausing/pooling |
| **Message queue overflow** | Add rate limiting early | Implement queue depth limits |
| **Memory distillation loses critical info** | Manual review in Phase 7 | Add human review checkpoints |
| **WebSocket connection limits** | Monitor connection count | Implement connection pooling |
| **Timeline UI sluggish with many Comms** | Implement virtualization in Phase 8 | Pagination-only fallback |

### 4.2 Schedule Risks

| Risk | Mitigation | Contingency |
|------|-----------|-------------|
| **Phase takes longer than estimated** | Build buffer into each phase (1-2 days) | Descope non-critical features |
| **Dependency on SDK changes** | Pin SDK version, test early | Isolate SDK integration, easy to swap |
| **Integration issues between phases** | Integration tests at end of each phase | Refactor if architecture flawed |

### 4.3 Scope Risks

| Risk | Mitigation | Contingency |
|------|-----------|-------------|
| **Feature creep** | Strict adherence to MVP scope | Defer non-MVP features to backlog |
| **Requirements change mid-development** | Review requirements after each phase | Re-plan remaining phases |

---

## 5. Review Points

### After Each Phase

**Review Checklist**:
- [ ] All deliverables complete?
- [ ] All acceptance criteria met?
- [ ] All tests passing?
- [ ] No regressions in existing functionality?
- [ ] Performance acceptable?
- [ ] Documentation updated?

**Go/No-Go Decision**:
- If yes to all → proceed to next phase
- If no → identify blockers, address before proceeding

### Mid-Project Review (After Phase 4)

**Assessment**:
- Are we on schedule?
- Is architecture working as expected?
- Any major issues discovered?
- Do requirements need adjustment?

**Adjustments**:
- Revise remaining phases if needed
- Descope if behind schedule
- Add resources if available

### Pre-Launch Review (After Phase 8)

**Validation**:
- All use cases working?
- Performance acceptable?
- Documentation complete?
- Known bugs acceptable?

**Launch Decision**:
- Ready for production use?
- Or: need additional polish phase?

---

## 6. Post-MVP Roadmap

### 6.1 Deferred Features (High Priority)

**Advanced Memory Management** (2-3 weeks):
- Vector database integration for semantic search
- Automatic memory consolidation across minions
- Memory conflict resolution (forked minions)

**Advanced Coordination** (2-3 weeks):
- Consensus protocols for multi-minion decisions
- Voting mechanisms
- Automated performance scoring

**Enhanced Tooling** (1-2 weeks):
- Visual graph editor for horde structure
- Timeline playback/replay
- Performance profiling per minion

### 6.2 Deferred Features (Medium Priority)

**Multi-Tenancy** (3-4 weeks):
- Multiple users sharing legion
- Permission systems
- User roles

**Cost Management** (1-2 weeks):
- Per-minion token tracking
- Budget limits and alerts

**Advanced Execution** (2-3 weeks):
- Parallel execution optimization
- Checkpointing for long-running tasks
- Automatic retry on failure

### 6.3 Research & Exploration

**Agent Learning** (ongoing):
- Shared learning across agent instances
- Meta-learning (agents learn how to learn)
- Feedback loops for self-improvement

**Scaling** (as needed):
- Support for >20 minions
- Distributed coordination
- Cloud deployment

---

## 7. Success Metrics

### MVP Success Criteria

**Functional**:
- [ ] All 3 use cases work end-to-end
- [ ] 20 concurrent minions supported
- [ ] Autonomous spawning/disposal working
- [ ] Channels enable cross-horde collaboration
- [ ] Memory distillation preserves knowledge
- [ ] Full observability and control

**Quality**:
- [ ] >90% test coverage
- [ ] No critical bugs
- [ ] <5 known minor bugs
- [ ] Documentation complete

**Performance**:
- [ ] Comm latency <500ms (p95)
- [ ] Timeline loads <1s
- [ ] Emergency halt <1s

**Usability**:
- [ ] User can complete workflows without documentation
- [ ] Clear error messages
- [ ] No confusing states

---

## 8. Appendix: Estimation Details

### Estimation Methodology

**Story Points to Days**:
- Small task (1 point) = 0.5 days
- Medium task (2-3 points) = 1-2 days
- Large task (5 points) = 3-4 days
- Very large task (8 points) = 5+ days

**Buffer**:
- Each phase has 1-2 day buffer
- Total project buffer: 1 week (included in 9-week estimate)

### Assumptions

- Single developer, full-time
- No major architecture changes mid-project
- SDK remains stable
- No unexpected external dependencies

### Confidence Levels

- Phase 1-3: High confidence (well-defined, low risk)
- Phase 4-6: Medium confidence (some unknowns in SDK integration)
- Phase 7-8: Medium-low confidence (complex logic, many edge cases)

---

## Document Control

- **Version**: 1.0
-**Date**: 2025-10-19
- **Status**: Draft for Review
- **Dependencies**: All previous documents (requirements, technical design, UX design)
- **Next Steps**: Review and refine, then begin Phase 1 implementation
- **Owner**: Development Team
