# Legion Multi-Agent System - Requirements Document

## 1. Executive Summary

### 1.1 Purpose
Build a collaborative multi-agent system integrated into Claude WebUI that enables autonomous AI agents (minions) to work together on complex tasks through hierarchical organization, cross-team communication, and dynamic task delegation.

### 1.2 Primary Use Cases
1. **Complex Software Development**: 350-service SaaS platform with service-expert minions collaborating on cross-cutting changes
2. **Tabletop Game Simulation**: D&D campaign testing with character/NPC minions interacting in scenes
3. **Deep Research**: Multi-domain specialists synthesizing comprehensive analyses from diverse sources

### 1.3 Success Criteria
- Minions can autonomously spawn and dispose of child minions
- Cross-horde communication via channels enables collaboration
- User maintains full observability and control over all minions
- System supports 20 concurrent minions with acceptable performance
- Memory distillation and minion forking preserve expertise
- All three primary use cases validated end-to-end

---

## 2. Terminology & Concepts

### 2.1 Core Entities

#### Legion
- **Definition**: Top-level container for multi-agent collaboration, equivalent to a Claude WebUI project with multi-agent capabilities enabled
- **Scope**: Contains multiple hordes, channels, and manages minion lifecycle
- **Constraints**:
  - Maximum 20 concurrent active minions
  - Minions cannot cross legion boundaries
  - Minion names must be unique within a legion

#### Minion
- **Definition**: Single instance of claude-agent-SDK running in a dedicated session
- **Capabilities**: Can use all Claude SDK tools, send/receive Comms, spawn children if needed
- **Types**:
  - User-created (no parent, root of horde)
  - Spawned (created by another minion)
  - Persistent (long-lived, expertise retained)
  - Ephemeral (task-scoped, disposed after completion)

#### Overseer
- **Definition**: A minion that has spawned one or more child minions
- **Authority**: Can Halt, Pivot, Guide, and dispose its children
- **Note**: Overseer is a role, not a type - any minion can become an overseer by spawning children

#### Horde
- **Definition**: Hierarchical group consisting of an overseer and all its descendant minions
- **Structure**: Tree structure with single root overseer
- **Membership**: Fixed - minions belong to exactly one horde (their creator's horde)

#### Channel
- **Definition**: Purpose-driven communication group for cross-horde collaboration
- **Membership**: Dynamic - minions can join/leave channels
- **Scope**: Cross-horde - enables minions from different hordes to collaborate
- **Examples**: "Implementation Planning", "Database Changes", "Tavern Scene"

### 2.2 Communication Concepts

#### Comm
- **Definition**: High-level message between minions, user, or channels
- **Purpose**: Abstract communication layer above SDK Message protocol
- **Routing**: Can be directed to specific minion, broadcast to channel, or sent to user
- **Visibility**: Can be public (user sees) or internal (minions only)

#### Message
- **Definition**: Low-level SDK protocol communication (user→SDK, SDK→user)
- **Purpose**: Existing Claude WebUI terminology for session conversation
- **Distinction**: Messages are SDK-level, Comms are minion-level
- **Translation**: CommRouter translates between Comms and Messages

#### Comm Types
- **TASK**: Assign work to a minion
- **QUESTION**: Request information from a minion
- **REPORT**: Provide findings or status
- **GUIDE**: Additional instructions without interrupting current work
- **HALT**: Stop processing and wait for more information
- **PIVOT**: Stop immediately, clear queue, redirect to new instructions
- **THOUGHT**: Minion self-talk (internal reasoning, debugging)
- **SPAWN**: System notification of minion creation
- **DISPOSE**: System notification of minion termination
- **SYSTEM**: General system notifications

### 2.3 Control Operations

#### Halt
- **Effect**: Minion stops processing current work
- **Queue**: Existing queued Comms are preserved
- **Resumption**: Minion can resume when instructed
- **Use Case**: "Wait, I need to gather more information before you continue"

#### Pivot
- **Effect**: Minion stops immediately, clears message queue, receives new direction
- **Queue**: All queued Comms are discarded
- **Priority**: Highest priority interrupt
- **Use Case**: "Stop everything, we're changing direction completely"

#### Guide
- **Effect**: Additional instructions provided without interrupting
- **Queue**: Comm is queued normally
- **Priority**: Normal priority
- **Use Case**: "Also consider performance when you get to that part"

#### Emergency Halt
- **Effect**: All minions in legion stop processing
- **Sessions**: SDK sessions remain active (not terminated)
- **Resumption**: User can resume individual minions or entire fleet
- **Use Case**: "Everything stop, I need to assess the situation"

---

## 3. Functional Requirements

### 3.1 Legion Management

#### FR-L1: Legion Creation
- **Requirement**: User can create a new legion from project creation modal
- **Input**: Name, working directory, "Enable Multi-Agent" checkbox
- **Output**: Legion created with no initial minions or channels
- **Constraints**: Working directory must exist and be accessible

#### FR-L2: Legion Deletion
- **Requirement**: User can delete entire legion
- **Effect**: All minions terminated, all sessions cleaned up, all data archived
- **Safety**: Confirmation dialog required
- **Cleanup**: SDK sessions terminated gracefully, memory distilled before deletion

#### FR-L3: Fleet Status Monitoring
- **Requirement**: User can view real-time status of legion
- **Metrics**: Active minion count, max limit, channel count, recent activity
- **Display**: Persistent in UI header/sidebar

### 3.2 Minion Management

#### FR-M1: User Creates Minion
- **Requirement**: User can manually create a minion
- **Input**: Name (unique in legion), role description, initialization context/system prompt, channel memberships
- **Output**: Minion created with dedicated SDK session, started and ready
- **Horde**: Creates new horde with this minion as root overseer (if it spawns children)

#### FR-M2: Minion Spawns Child
- **Requirement**: Minion can autonomously spawn child minion
- **Trigger**: Minion decides child is needed (via SDK reasoning)
- **Input**: Role, initialization context, channel memberships
- **Output**: Child minion created, added to parent's horde, parent becomes overseer
- **Notification**: SPAWN Comm sent to user

#### FR-M3: Overseer Disposes Child
- **Requirement**: Overseer can terminate child minion when task complete
- **Authority**: Only parent overseer can dispose (not user, not other minions)
- **Process**: Memory distilled, knowledge transferred to parent, session terminated
- **Notification**: DISPOSE Comm sent to user

#### FR-M4: User Terminates Minion
- **Requirement**: User can forcibly terminate any minion
- **Effect**: Minion and all descendants terminated, memory distilled
- **Safety**: Confirmation if minion is overseer with active children
- **Cleanup**: Horde updated or deleted if root minion terminated

#### FR-M5: Minion Naming Uniqueness
- **Requirement**: Minion names must be unique within legion
- **Validation**: System rejects duplicate names
- **Purpose**: Enables natural language referencing ("check with Sylvester about DB setup")
- **Scope**: Uniqueness enforced per-legion, not globally

#### FR-M6: Halt Minion
- **Requirement**: User or parent overseer can halt minion
- **Effect**: Current processing stops, queue preserved
- **State**: Minion state changes to PAUSED
- **Resumption**: Explicit resume required

#### FR-M7: Resume Minion
- **Requirement**: User or parent overseer can resume halted minion
- **Effect**: Minion resumes processing from queue
- **State**: Minion state changes to ACTIVE

#### FR-M8: Emergency Halt All
- **Requirement**: User can halt entire fleet with single action
- **Effect**: All minions in legion halt
- **Sessions**: SDK sessions remain active (not terminated)
- **Use Case**: Emergency intervention, debugging, assessment

### 3.3 Communication

#### FR-C1: Send Direct Comm
- **Requirement**: User or minion can send Comm to specific minion
- **Input**: Target minion (by name or ID), content, Comm type
- **Routing**: CommRouter delivers to target minion's session
- **Interrupts**: HALT and PIVOT types trigger interrupt behavior

#### FR-C2: Broadcast to Channel
- **Requirement**: User or minion can broadcast Comm to channel
- **Effect**: All channel members receive Comm
- **Filtering**: Sender does not receive echo of own Comm
- **Logging**: Comm logged to channel's Comm log

#### FR-C3: Comm to User
- **Requirement**: Minion can send Comm to user
- **Display**: Appears in timeline UI
- **Notification**: Real-time via WebSocket
- **Visibility**: All user-directed Comms are visible by default

#### FR-C4: Comm Type Handling
- **GUIDE**: Queued normally, no interrupt
- **HALT**: Soft interrupt, preserve queue
- **PIVOT**: Hard interrupt, clear queue
- **TASK/QUESTION/REPORT**: Normal priority queue
- **THOUGHT**: Internal, optionally visible for debugging
- **SPAWN/DISPOSE/SYSTEM**: System notifications, always visible

#### FR-C5: Comm Persistence
- **Requirement**: All Comms must be persisted
- **Locations**:
  - Per-minion log (all Comms involving that minion)
  - Per-channel log (all Comms in that channel)
  - Legion timeline (unified view of all Comms)
- **Format**: JSONL (one Comm per line)

#### FR-C6: Natural Language Minion Referencing
- **Requirement**: Comms can reference other minions by name
- **Example**: "To Quickdraw: Sylvester knows about this, check with them"
- **Parsing**: System identifies minion names in Comm content
- **Suggestion**: UI provides autocomplete for minion names

### 3.4 Channels

#### FR-CH1: Create Channel
- **Requirement**: User or minion can create channel
- **Input**: Name, description, purpose, initial members
- **Output**: Channel created, members notified
- **Scope**: Legion-wide (cross-horde)

#### FR-CH2: Join Channel
- **Requirement**: Minion can join existing channel
- **Authority**: Any minion can join any channel (no permissions for MVP)
- **Effect**: Minion receives future broadcasts, can see history

#### FR-CH3: Leave Channel
- **Requirement**: Minion can leave channel
- **Effect**: No longer receives broadcasts
- **History**: Past Comms remain visible

#### FR-CH4: Channel Member List
- **Requirement**: User can view channel members
- **Display**: Shows minion names, roles, online status
- **Updates**: Real-time updates via WebSocket

### 3.5 Hordes

#### FR-H1: Horde Formation
- **Trigger**: Automatically created when user creates minion or minion spawns first child
- **Structure**: Tree with single root
- **Membership**: All descendants of root overseer

#### FR-H2: Horde Hierarchy View
- **Requirement**: User can view horde structure as tree
- **Display**: Indented list or tree visualization
- **Information**: Minion name, role, state, child count

#### FR-H3: Horde Boundary Enforcement
- **Requirement**: Minions cannot move between hordes
- **Constraint**: Minion's horde is determined at creation by parent
- **Exception**: None - horde membership is immutable

### 3.6 Memory & Learning

#### FR-MEM1: Task Completion Distillation
- **Trigger**: When minion completes task or milestone
- **Process**: Recent messages summarized into key facts, patterns, insights
- **Storage**: Moved from session memory to short-term memory
- **Purpose**: Reduce context size while preserving knowledge

#### FR-MEM2: Memory Reinforcement
- **Trigger**: When user provides correction or positive feedback
- **Effect**: Memories involved in decision are weighted higher (success) or lower (failure)
- **Tracking**: Quality scores adjusted based on outcomes
- **Purpose**: Minions learn what works over time

#### FR-MEM3: Long-Term Memory Promotion
- **Trigger**: Patterns appear repeatedly with high success rate
- **Process**: Short-term patterns promoted to long-term rules
- **Storage**: Long-term memory persisted across sessions
- **Purpose**: Build up expertise that persists

#### FR-MEM4: Minion Forking
- **Requirement**: User can fork minion to create duplicate
- **Effect**: New minion created with identical memory at fork time
- **Divergence**: After fork, minions operate independently, memories diverge
- **No Sync**: Forked minions do not sync memories (for MVP)
- **Use Case**: Create specialists with pre-built expertise

#### FR-MEM5: Knowledge Transfer on Disposal
- **Trigger**: When overseer disposes child minion
- **Process**: Child's distilled knowledge transferred to parent
- **Effect**: Parent gains insights from child's work
- **Purpose**: Preserve learnings from ephemeral minions

### 3.7 Discovery & Capability Search

#### FR-D1: Capability Advertising
- **Requirement**: Minions can advertise capabilities they acquire
- **Mechanism**: Update capability list in minion state
- **Visibility**: Other minions in same channels can discover
- **Examples**: "auth_expertise", "sql_analysis", "worldbuilding"

#### FR-D2: Gossip Protocol Search
- **Requirement**: Minion can search for other minions with capability
- **Algorithm**:
  1. Minion asks channels it's in: "Who can do X?"
  2. Channel members respond if they have capability or know someone who does
  3. Results aggregated and returned
- **Scope**: Limited to channels minion is in (not legion-wide)
- **Purpose**: Distributed discovery without central registry

#### FR-D3: Known Agents Tracking
- **Requirement**: Minions track other minions they've interacted with
- **Storage**: Map of minion_id → capabilities
- **Updates**: Updated when minion advertises new capability
- **Purpose**: Faster discovery on repeat collaborations

---

## 4. Non-Functional Requirements

### 4.1 Performance

#### NFR-P1: Concurrent Minion Limit
- **Requirement**: System must support 20 concurrent active minions
- **Measurement**: All 20 minions can send/receive Comms with <2s latency
- **Degradation**: Graceful degradation if limit exceeded (queue new spawns)

#### NFR-P2: Comm Latency
- **Requirement**: Comm delivery latency <500ms under normal load
- **Measurement**: Time from send_comm() to minion SDK receives message
- **Conditions**: <10 minions active, <5 Comms/second

#### NFR-P3: UI Responsiveness
- **Requirement**: Timeline UI remains responsive with 1000+ Comms
- **Solution**: Pagination, virtualized rendering, lazy loading
- **Measurement**: Page load <1s, scroll lag <100ms

#### NFR-P4: Memory Footprint
- **Requirement**: System memory usage scales linearly with minion count
- **Measurement**: <500MB per active minion average
- **Monitoring**: Memory usage tracked and logged

### 4.2 Reliability

#### NFR-R1: Minion Crash Recovery
- **Requirement**: SDK session crash does not break legion
- **Detection**: Heartbeat monitoring, crash detection
- **Recovery**: User notified, can restart minion with session resumption
- **Isolation**: Other minions continue unaffected

#### NFR-R2: Data Persistence
- **Requirement**: All Comms, minion state, memory persisted to disk
- **Durability**: Survive process restart, system crash
- **Recovery**: Legion state fully recoverable from disk

#### NFR-R3: Graceful Degradation
- **Requirement**: System degrades gracefully under load
- **Behaviors**:
  - Queue overload → slow response, not crash
  - Memory pressure → distill more aggressively
  - Too many minions → reject new spawns with clear error

### 4.3 Observability

#### NFR-O1: Full Audit Trail
- **Requirement**: Complete history of all Comms, spawns, disposals, state changes
- **Storage**: JSONL logs, never deleted
- **Access**: User can review full history for any minion or channel

#### NFR-O2: Real-Time Updates
- **Requirement**: UI updates in real-time as events occur
- **Mechanism**: WebSocket push notifications
- **Events**: Minion spawned, Comm sent, state change, channel activity

#### NFR-O3: Debug Visibility
- **Requirement**: User can view minion internal reasoning (THOUGHT Comms)
- **Toggle**: Optional display (can be hidden for clarity)
- **Purpose**: Debug unexpected minion behavior

### 4.4 Usability

#### NFR-U1: Intuitive UI
- **Requirement**: User can understand legion state without reading docs
- **Clarity**: Clear labels, icons, status indicators
- **Guidance**: Tooltips, contextual help

#### NFR-U2: Error Messages
- **Requirement**: Clear, actionable error messages
- **Examples**:
  - "Cannot spawn minion: legion at 20/20 limit"
  - "Minion name 'Sylvester' already exists in this legion"
- **Context**: Include relevant context (which minion, what action)

#### NFR-U3: Progressive Disclosure
- **Requirement**: Advanced features don't overwhelm new users
- **Approach**: Default views simple, advanced details expandable
- **Example**: Timeline shows summary, click for full Comm details

### 4.5 Security

#### NFR-S1: Tool Access Control
- **Requirement**: Minions respect permission modes
- **Enforcement**: Existing Claude WebUI permission system applies
- **Scope**: Per-session permissions (each minion has own permissions)

#### NFR-S2: Read-Only MVP
- **Requirement**: For MVP, limit minions to read-only and read-write file operations
- **Restriction**: No bash execution, no MCP tools (defer to post-MVP)
- **Rationale**: Reduce risk during initial testing

#### NFR-S3: Data Isolation
- **Requirement**: Minions cannot access other legion's data
- **Enforcement**: File system isolation, path validation
- **Note**: Not critical for MVP (single user, local deployment)

---

## 5. User Stories

### 5.1 Software Development Use Case

#### US-SD1: Create Service Expert Fleet
```
As a developer,
I want to create a legion with expert minions for each microservice,
So that I can get comprehensive impact analysis for cross-cutting changes.

Acceptance Criteria:
- Can create legion "SaaS Platform Overhaul"
- Can create minions: "Auth Expert", "Payment Expert", "Notification Expert"
- Each minion has initialization context with service-specific knowledge
- All minions active and ready to receive tasks
```

#### US-SD2: Delegate Cross-Service Analysis
```
As a developer,
I want to ask the Lead Architect to analyze a new feature,
So that service experts collaborate to identify all necessary changes.

Acceptance Criteria:
- Lead Architect spawns specialist minions for detailed analysis
- Specialists join "Implementation Planning" channel
- Specialists communicate findings to channel
- User can view all discussions in timeline
```

#### US-SD3: Pivot on Design Change
```
As a developer,
I want to tell Auth Expert to switch from custom auth to OAuth,
So that in-progress analysis redirects immediately.

Acceptance Criteria:
- User sends PIVOT Comm to Auth Expert
- Auth Expert halts current work, clears queue
- Auth Expert begins OAuth analysis
- Previous custom auth analysis discarded
```

### 5.2 D&D Campaign Use Case

#### US-DD1: Setup Campaign Characters
```
As a DM,
I want to create a legion with character minions,
So that I can simulate campaign sessions.

Acceptance Criteria:
- Create legion "Dragon Heist Campaign Test"
- Create minions: "Elara (Wizard)", "Thorne (Rogue)", "DM Orchestrator"
- Character minions have personality context in initialization
- All characters ready to roleplay
```

#### US-DD2: Run Scene with Dynamic NPCs
```
As a DM,
I want the DM Orchestrator to create NPCs as needed for scenes,
So that the story feels dynamic and responsive.

Acceptance Criteria:
- User sends "Start scene: Tavern encounter" to DM Orchestrator
- DM spawns ephemeral "Tavern Scene Builder" → generates description
- DM spawns "Mysterious Stranger NPC" for interaction
- Characters and NPC converse in "Tavern Scene" channel
- User can observe and guide as needed
```

#### US-DD3: Fork Character for Parallel Exploration
```
As a DM,
I want to fork Elara to explore two story paths,
So that I can compare different narrative outcomes.

Acceptance Criteria:
- Fork Elara → creates Elara_Fork1 and Elara_Fork2
- Forked characters have identical memories at fork time
- Fork1 pursues "trust stranger" path, Fork2 pursues "distrust" path
- Memories diverge based on different experiences
- User can compare outcomes
```

### 5.3 Research Use Case

#### US-R1: Create Research Team
```
As a researcher,
I want to create specialist minions for different research domains,
So that I get comprehensive multi-domain analysis.

Acceptance Criteria:
- Create legion "Intermittent Fasting Research"
- Create Lead Researcher minion
- Lead Researcher spawns Medical Specialist, Nutrition Specialist, Biochemistry Specialist
- Specialists have domain expertise in initialization context
```

#### US-R2: Resolve Conflicting Sources
```
As a researcher,
I want specialists to identify and resolve conflicting information,
So that I get a coherent synthesis.

Acceptance Criteria:
- Medical Specialist finds Study A (positive) and Study B (negative)
- Medical Specialist reports conflict to Lead Researcher
- Lead Researcher asks Medical Specialist to evaluate study quality
- Medical Specialist determines resolution based on methodology
- Final report includes rationale for resolution
```

#### US-R3: Iterative Deep Dive
```
As a researcher,
I want to guide the research team to dig deeper into specific areas,
So that I can refine the analysis interactively.

Acceptance Criteria:
- User sends GUIDE Comm: "Focus more on metabolic effects"
- Lead Researcher spawns Metabolism Specialist
- New specialist collaborates with existing team
- Findings integrated into synthesis
- User can see incremental progress
```

---

## 6. Constraints & Assumptions

### 6.1 Constraints

#### Technical Constraints
- Each minion requires dedicated Claude SDK session (resource intensive)
- Claude SDK API rate limits apply (affects concurrent minion count)
- File system I/O for message persistence (scalability bottleneck)
- WebSocket connection limits (typically 100-1000 per process)

#### Scope Constraints (MVP)
- Maximum 20 concurrent minions (hard limit)
- Single user deployment only (no multi-tenancy)
- Local execution only (no cloud deployment)
- Read/write file operations only (no bash, no MCP tools)
- No inter-legion communication
- No minion migration between legions
- Forked minions do not sync (independent after fork)

#### Design Constraints
- Must integrate with existing Claude WebUI codebase
- Must reuse existing session management infrastructure
- Must maintain backward compatibility with non-legion projects
- Must follow existing data storage patterns (JSONL, JSON state files)

### 6.2 Assumptions

#### User Assumptions
- User is technical (developer) comfortable with complex systems
- User understands AI agent limitations and potential errors
- User will monitor and intervene as needed (not fully autonomous)
- User has local workstation with sufficient resources (16GB+ RAM)

#### System Assumptions
- Claude SDK is stable and reliable
- API costs are acceptable to user (cost controls exist at account level)
- File system is reliable (no data corruption)
- Network connection is stable for WebSocket communication

#### Future Assumptions
- System may be extended to cloud deployment
- Multi-user collaboration may be added
- More sophisticated permission systems may be needed
- Memory management may require vector databases or similar

---

## 7. Out of Scope (Post-MVP)

### 7.1 Deferred Features

#### Advanced Memory Management
- Vector database for semantic memory search
- Automatic memory consolidation across minions
- Memory conflict resolution (forked minions)
- Temporal decay of old memories
- Memory compression with RAG

#### Advanced Coordination
- Consensus protocols for multi-minion decisions
- Voting mechanisms for conflict resolution
- Automated minion performance scoring
- Dynamic capability-based task routing
- Market-based resource allocation

#### Multi-Tenancy & Collaboration
- Multiple users sharing legion
- Permission systems for minion access
- User roles (admin, observer, participant)
- Inter-legion communication
- Legion forking/templating

#### Tooling & Debugging
- Visual graph editor for horde structure
- Timeline playback/replay
- Minion behavior debugging tools
- Performance profiling per minion
- Cost tracking per minion

#### Advanced Execution
- Parallel minion execution optimization
- Speculative execution paths
- Checkpointing for long-running tasks
- Automatic retry on failure
- Circuit breaker patterns

### 7.2 Explicitly Not Supported (MVP)

- Bash command execution by minions
- MCP tool integration
- External API calls by minions (beyond SDK)
- Multi-legion minion sharing
- Real-time collaborative editing by multiple minions
- Automatic cost budgeting per minion
- GPU/TPU resource allocation
- Custom tool creation by minions

---

## 8. Dependencies

### 8.1 External Dependencies

#### Required (Existing)
- Claude Agent SDK (claude-agent-sdk)
- Python 3.10+
- FastAPI
- uvicorn
- asyncio
- WebSockets

#### Required (Current Claude WebUI)
- SessionCoordinator
- SessionManager
- ProjectManager
- MessageProcessor
- DataStorageManager
- Existing UI components (sidebar, message timeline)

### 8.2 Internal Dependencies

#### New Components Required
- CommRouter (Comm ↔ Message translation)
- OverseerController (minion lifecycle management)
- ChannelManager (channel creation, membership, broadcasting)
- MemoryManager (distillation, reinforcement, forking)
- LegionManager (legion-level orchestration)
- GossipSearchService (capability discovery)

#### Data Model Dependencies
- LegionInfo (extends Project)
- MinionInfo (extends SessionInfo)
- Horde
- Channel
- Comm
- Memory structures (short-term, long-term, quality scores)

---

## 9. Success Metrics

### 9.1 Functional Success

#### Core Functionality
- ✅ User can create legion and spawn minions
- ✅ Minions can autonomously spawn/dispose children
- ✅ Comms route correctly (direct, channel, user)
- ✅ Halt/Pivot/Guide behaviors work correctly
- ✅ Channels enable cross-horde collaboration
- ✅ Emergency halt stops entire fleet
- ✅ Memory distillation preserves knowledge
- ✅ Minion forking transfers expertise

#### Use Case Validation
- ✅ SaaS refactor scenario: 5+ service experts collaborate on implementation plan
- ✅ D&D scenario: 3+ character minions interact dynamically across 2+ scenes
- ✅ Research scenario: Lead + 3 specialists synthesize multi-domain report

### 9.2 Performance Metrics

#### Quantitative Targets
- Comm latency: <500ms (p95)
- Timeline load time: <1s for 1000 Comms
- Minion spawn time: <3s
- Emergency halt time: <1s to stop all minions
- Memory footprint: <500MB per active minion
- Concurrent minions: 20 without degradation

### 9.3 Usability Metrics

#### Qualitative Assessment
- User can understand legion state without documentation
- User can find specific minion or Comm in <10s
- User can intervene (Halt/Pivot) in <5s from decision
- Error messages are clear and actionable
- UI does not feel overwhelming with 10+ minions

---

## 10. Risks & Mitigations

### 10.1 Technical Risks

#### Risk: SDK Session Overhead
- **Impact**: HIGH - 20 SDK sessions may exhaust resources
- **Probability**: MEDIUM
- **Mitigation**:
  - Start with 5-minion limit for testing
  - Monitor resource usage closely
  - Implement minion pausing to free resources
  - Consider session pooling in future

#### Risk: Message Queue Overflow
- **Impact**: MEDIUM - Minions spam each other, system freezes
- **Probability**: MEDIUM
- **Mitigation**:
  - Rate limiting per minion (max X Comms/minute)
  - Queue depth limits (max Y queued Comms)
  - Timeout and discard old Comms
  - Circuit breaker for spamming minions

#### Risk: Memory Distillation Quality
- **Impact**: HIGH - Lossy compression loses critical information
- **Probability**: HIGH
- **Mitigation**:
  - User review of distilled memories
  - Configurable distillation aggressiveness
  - Preserve full history in addition to distillation
  - Iterative refinement based on user feedback

#### Risk: Coordination Deadlock
- **Impact**: MEDIUM - Minions wait on each other indefinitely
- **Probability**: LOW
- **Mitigation**:
  - Timeout mechanisms (escalate to user after N seconds)
  - Deadlock detection (cycle detection in waiting graph)
  - User intervention always available

### 10.2 Usability Risks

#### Risk: UI Overwhelm
- **Impact**: MEDIUM - User lost in 20 minions × 100 Comms
- **Probability**: HIGH
- **Mitigation**:
  - Smart filtering (by horde, channel, minion, time)
  - Collapsible views (expand details on demand)
  - Search functionality
  - Summary views (key events only)

#### Risk: Unclear Minion Behavior
- **Impact**: MEDIUM - User doesn't understand why minion did X
- **Probability**: MEDIUM
- **Mitigation**:
  - THOUGHT Comms visible (internal reasoning)
  - Capability and task tracking (what minion thinks it's doing)
  - Clear state indicators (ACTIVE, PAUSED, PROCESSING)
  - Audit trail always available

### 10.3 Cost Risks

#### Risk: Runaway Token Usage
- **Impact**: HIGH - 20 minions × long tasks = expensive
- **Probability**: MEDIUM
- **Mitigation**:
  - User account-level budget controls (existing)
  - Visibility into token usage per minion
  - Warnings when approaching limits
  - Emergency halt available
  - Post-MVP: per-minion budgets

---

## 11. Acceptance Criteria

### 11.1 MVP Acceptance

The MVP is considered complete when:

1. **Legion Management**
   - [ ] User can create legion from project modal
   - [ ] User can view legion status (active minions, channels)
   - [ ] User can delete legion (all minions terminated cleanly)

2. **Minion Lifecycle**
   - [ ] User can create minion with custom initialization context
   - [ ] Minion can autonomously spawn child minion
   - [ ] Overseer can dispose child minion
   - [ ] User can terminate any minion
   - [ ] Minion names are unique within legion

3. **Communication**
   - [ ] User can send Comm to minion (direct)
   - [ ] Minion can send Comm to user
   - [ ] Minion can send Comm to another minion
   - [ ] User can broadcast to channel
   - [ ] Minion can broadcast to channel
   - [ ] HALT, PIVOT, GUIDE behaviors work correctly

4. **Channels**
   - [ ] User can create channel
   - [ ] Minion can join channel
   - [ ] Minion can leave channel
   - [ ] Channel broadcasts reach all members

5. **Hordes**
   - [ ] Horde automatically created when minion spawns child
   - [ ] Horde hierarchy viewable as tree
   - [ ] Minions cannot cross horde boundaries

6. **Memory**
   - [ ] Memory distilled on task completion
   - [ ] Knowledge transferred when minion disposed
   - [ ] Minion can be forked with memory
   - [ ] Forked minions diverge independently

7. **Control**
   - [ ] User can halt individual minion
   - [ ] User can resume halted minion
   - [ ] User can emergency halt all minions
   - [ ] All minions resume after emergency halt

8. **Observability**
   - [ ] Timeline shows all Comms in chronological order
   - [ ] User can filter timeline (by minion, channel, type)
   - [ ] User can view minion details (state, capabilities, children)
   - [ ] User can view minion full history
   - [ ] WebSocket updates in real-time

9. **Performance**
   - [ ] System supports 20 concurrent minions
   - [ ] Comm latency <500ms (p95)
   - [ ] Timeline loads <1s with 1000 Comms

10. **Use Cases**
    - [ ] SaaS refactor scenario completes end-to-end
    - [ ] D&D campaign scenario completes end-to-end
    - [ ] Research synthesis scenario completes end-to-end

---

## 12. Glossary

- **Agent**: Generic term for autonomous AI entity (we use "minion" for specificity)
- **Capability**: Skill or expertise a minion has (e.g., "sql_analysis")
- **Child**: Minion spawned by another minion (parent)
- **Comm**: High-level message in multi-agent system
- **Dispose**: Terminate a child minion (only parent can dispose)
- **Divergence**: Forked minions developing different memories over time
- **Ephemeral**: Short-lived minion, disposed after task completion
- **Fleet**: Informal term for all minions in a legion
- **Fork**: Create duplicate minion with identical memory
- **Gossip Protocol**: Distributed search via channel communication
- **Guide**: Provide instructions without interrupting
- **Halt**: Stop processing, wait for more info
- **Horde**: Hierarchical group of minions under one overseer
- **Initialization Context**: System prompt/expertise loaded at minion creation
- **Legion**: Top-level multi-agent container (project with multi-agent enabled)
- **Message**: Low-level SDK protocol communication
- **Minion**: Instance of claude-agent-SDK in dedicated session
- **Overseer**: Minion that has spawned children
- **Parent**: Minion that spawned a child
- **Persistent**: Long-lived minion, expertise retained across sessions
- **Pivot**: Stop immediately, clear queue, redirect
- **Root**: Top-level minion in horde (no parent)
- **Session**: Claude SDK conversation instance (one per minion)
- **Spawn**: Create a new child minion
- **THOUGHT Comm**: Minion's internal reasoning, visible for debugging

---

## Document Control

- **Version**: 1.0
- **Date**: 2025-10-19
- **Status**: Draft for Review
- **Next Review**: After initial feedback
- **Owner**: Development Team
