# Legion Multi-Agent System - UX Design Document

## 1. Design Principles

### 1.1 Core UX Principles

**Progressive Disclosure**
- Simple default views that don't overwhelm
- Advanced details available on demand
- Clear visual hierarchy

**Clarity Over Cleverness**
- Explicit state indicators
- Clear labels and actions
- No ambiguous icons without labels

**Immediate Feedback**
- Real-time updates via WebSocket
- Loading states for async operations
- Clear success/error messages

**User Control**
- Can pause/halt/pivot at any time
- Emergency stop always accessible
- No destructive actions without confirmation

**Observability First**
- Full visibility into minion state and communication
- Easy debugging of unexpected behavior
- Complete audit trail accessible

---

## 2. UI Component Specifications

### 2.1 Project/Legion Creation Modal

**Trigger**: User clicks "Create Project" button in sidebar

**Layout**:
```
┌─────────────────────────────────────────────────────┐
│ Create New Project                           [×]    │
├─────────────────────────────────────────────────────┤
│                                                     │
│ Project Name:                                       │
│ [____________________]                              │
│                                                     │
│ Working Directory:                                  │
│ [/path/to/project              ] [Browse...]        │
│                                                     │
│ ┌─────────────────────────────────────────────────┐│
│ │ ☑ Enable Multi-Agent (Legion)                   ││
│ │                                                  ││
│ │ Allows minions to collaborate on complex tasks. ││
│ │ Max concurrent minions: [20 ▼]                  ││
│ └─────────────────────────────────────────────────┘│
│                                                     │
│                        [Cancel]  [Create Project]  │
└─────────────────────────────────────────────────────┘
```

**Behavior**:
- Checkbox unchecked = create standard project (existing behavior)
- Checkbox checked = create legion with multi-agent features
- Max minions dropdown: 5, 10, 15, 20 (default: 20)
- Browse opens directory picker (existing functionality)
- Validation: name not empty, directory exists

**States**:
- Default: All fields empty, multi-agent unchecked
- Creating: Loading spinner, buttons disabled
- Success: Modal closes, sidebar updates with new legion
- Error: Red error message below failing field

---

### 2.2 Sidebar - Legion View

**Context**: Left sidebar, below header

**Layout**:
```
┌─────────────────────────────────────────────────┐
│ Projects                         [+] [⚙]        │
├─────────────────────────────────────────────────┤
│                                                 │
│ ▼ 🏛 SaaS Platform Overhaul (Legion)            │
│   ├─ 👥 Hordes (2)                              │
│   │  ├─ ▼ 🎯 Architecture Planning              │
│   │  │  ├─ 👑 Lead Architect          [●]       │
│   │  │  ├─ 🔧 Requirements Analyzer   [●]       │
│   │  │  └─ ▼ 🔧 Service Discovery     [●]       │
│   │  │     ├─ 🔧 Auth Expert          [●]       │
│   │  │     └─ 🔧 Payment Expert       [⏸]       │
│   │  │                                          │
│   │  └─ ▶ 🎯 Database Team                      │
│   │                                             │
│   ├─ 💬 Channels (3)                            │
│   │  ├─ Implementation Planning (4)             │
│   │  ├─ Database Changes (3)                    │
│   │  └─ Testing Strategy (2)                    │
│   │                                             │
│   └─ 📊 Fleet: 5/20 active                      │
│                                                 │
│ ▼ 📦 Regular Project                            │
│   └─ Sessions (2)                               │
│      ├─ Main Session                            │
│      └─ Debug Session                           │
│                                                 │
└─────────────────────────────────────────────────┘
```

**Icons**:
- 🏛 Legion (project with multi-agent)
- 👥 Hordes section
- 🎯 Horde (group of minions)
- 👑 Overseer (minion with children)
- 🔧 Minion (worker)
- 💬 Channels section
- 📦 Regular project
- [●] Active minion (green dot)
- [⏸] Paused minion (yellow pause icon)
- [✗] Error state (red X)

**Interactions**:
- Click minion name → Select minion, show detail view
- Click horde name → Expand/collapse horde tree
- Click channel name → Show channel detail/timeline
- Hover minion → Tooltip with role and current task
- Right-click minion → Context menu (Halt, Pivot, Terminate)
- Fleet status → Click to show fleet control panel

**Visual Hierarchy**:
- Legion name: Bold, larger font
- Horde/Channel headers: Medium weight, gray
- Minion names: Regular weight
- Indentation: 16px per level
- Active state indicator: 8px colored circle

---

### 2.3 Main View - Legion Timeline

**Context**: Main content area when legion selected

**Layout**:
```
┌──────────────────────────────────────────────────────────────────────┐
│ Legion: SaaS Platform Overhaul                                       │
│ ┌──────────────────────────────────────────────────────────────────┐ │
│ │ [Timeline] [Minions] [Channels] [Fleet Controls]                 │ │
│ └──────────────────────────────────────────────────────────────────┘ │
│ ┌──────────────────────────────────────────────────────────────────┐ │
│ │ Filter: [All Comms ▼] [All Minions ▼] [All Channels ▼]          │ │
│ │ Search: [________________________________] [🔍]                   │ │
│ └──────────────────────────────────────────────────────────────────┘ │
│ ┌──────────────────────────────────────────────────────────────────┐ │
│ │                      Timeline                                     │ │
│ ├──────────────────────────────────────────────────────────────────┤ │
│ │ 10:23 AM                                                         │ │
│ │ ┌────────────────────────────────────────────────────────────┐   │ │
│ │ │ 👤 YOU → 👑 Lead Architect                        [TASK]   │   │ │
│ │ │ "Analyze the new SSO requirements for all services"        │   │ │
│ │ │                                          [View Details ▼]  │   │ │
│ │ └────────────────────────────────────────────────────────────┘   │ │
│ │                                                                   │ │
│ │ 10:24 AM                                                         │ │
│ │ ┌────────────────────────────────────────────────────────────┐   │ │
│ │ │ 👑 Lead Architect → 💬 Implementation Planning   [REPORT]  │   │ │
│ │ │ "I'll need specialized analysis from service experts."     │   │ │
│ │ │                                          [View Details ▼]  │   │ │
│ │ └────────────────────────────────────────────────────────────┘   │ │
│ │                                                                   │ │
│ │ 10:24 AM                                                         │ │
│ │ ┌────────────────────────────────────────────────────────────┐   │ │
│ │ │ 🔧 Service Discovery → SPAWNED MINION            [SYSTEM]  │   │ │
│ │ │ Created: 🔧 Auth Expert                                    │   │ │
│ │ │ Channels: [Implementation Planning]                        │   │ │
│ │ │ Initialization: "You are an expert in auth systems..."     │   │ │
│ │ │                                          [View Details ▼]  │   │ │
│ │ └────────────────────────────────────────────────────────────┘   │ │
│ ││ │
│ │ 10:25 AM                                                         │ │
│ │ ┌────────────────────────────────────────────────────────────┐   │ │
│ │ │ 🔧 Auth Expert → 💬 Implementation Planning     [REPORT]   │   │ │
│ │ │ "SSO changes will impact 12 downstream services:           │   │ │
│ │ │ • User Service (session validation)                        │   │ │
│ │ │ • API Gateway (token verification)                         │   │ │
│ │ │ • ..."                                                     │   │ │
│ │ │                                          [View Details ▼]  │   │ │
│ │ └────────────────────────────────────────────────────────────┘   │ │
│ │                                                                   │ │
│ │ [Load More (573 total comms)]                                    │ │
│ └──────────────────────────────────────────────────────────────────┘ │
│ ┌──────────────────────────────────────────────────────────────────┐ │
│ │ Send Comm:                                                        │ │
│ │ To: [Select Minion/Channel ▼]    Type: [GUIDE ▼]                │ │
│ │ ┌────────────────────────────────────────────────────────────┐   │ │
│ │ │ Type your message here...                                   │   │ │
│ │ │ Use #minion-name or #channel-name to reference others      │   │ │
│ │ │                                                             │   │ │
│ │ │ [Autocomplete dropdown appears when typing #]              │   │ │
│ │ └────────────────────────────────────────────────────────────┘   │ │
│ │                                                     [Send]        │ │
│ └──────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────┘
```

**Tag Autocomplete Behavior**:
```
User types: "Check with #"

┌────────────────────────────────────┐
│ # Autocomplete                     │
├────────────────────────────────────┤
│ 🔧 AuthExpert                      │ ← Arrow keys to navigate
│ 👑 LeadArchitect                   │
│ 🔧 DatabaseArchitect               │
│ 💬 implementation-planning         │
│ 💬 database-changes                │
└────────────────────────────────────┘

• Triggered when user types "#"
• Shows minions (with icons) and channels
• Filter as user types: "#Auth" shows only AuthExpert
• Select with Enter or click
• Inserts full tag: "#AuthExpert"
```

**Tag Rendering in Comms**:
- Tags highlighted with distinct background color (light blue)
- Clickable: clicking #AuthExpert navigates to minion detail modal
- Hovering shows tooltip with minion/channel info

**Example Rendered Comm**:
```
┌────────────────────────────────────────────────────────┐
│ 🔧 Auth Expert → 💬 Implementation Planning  [REPORT] │
│                                                        │
│ OAuth analysis complete. #DatabaseArchitect, I found  │
│ we need schema changes. #PaymentExpert, your service  │
│ will need token validation updates.                   │
│                                                        │
│ See discussion in #implementation-planning.            │
└────────────────────────────────────────────────────────┘

(#DatabaseArchitect, #PaymentExpert, #implementation-planning
 are highlighted and clickable)
```

**Comm Card Design**:
- Header: Time | Source → Destination | [Comm Type Badge]
- Body: Content (truncated if >200 chars, expand to read more)
  - Tags rendered with highlight and clickable
- Footer: [View Details] button (expands full context)
- Color coding by Comm type:
  - TASK: Blue border-left
  - QUESTION: Purple border-left
  - REPORT: Green border-left
  - GUIDE: Yellow border-left
  - HALT/PIVOT: Red border-left
  - SYSTEM: Gray border-left

**Expanded Comm Details**:
```
┌────────────────────────────────────────────────────────────┐
│ 10:25 AM                                                   │
│ ┌──────────────────────────────────────────────────────────┐│
│ │ 🔧 Auth Expert → 💬 Implementation Planning    [REPORT]  ││
│ │                                                          ││
│ │ SSO changes will impact 12 downstream services:          ││
│ │ • User Service (session validation)                      ││
│ │ • API Gateway (token verification)                       ││
│ │ • Billing Service (subscription checks)                  ││
│ │ ... (full content)                                       ││
│ │                                                          ││
│ │ ┌────────────────────────────────────────────────────────┐││
│ │ │ Metadata:                                              │││
│ │ │ • Comm ID: comm-abc123                                 │││
│ │ │ • In reply to: comm-xyz789                             │││
│ │ │ • Related task: task-456                               │││
│ │ │ • Priority: ROUTINE                                    │││
│ │ └────────────────────────────────────────────────────────┘││
│ │                                                          ││
│ │ [Reply to Auth Expert] [Reply to Channel] [Collapse ▲]  ││
│ └──────────────────────────────────────────────────────────┘│
└────────────────────────────────────────────────────────────┘
```

**Filtering**:
- Comm type filter: All / TASK / QUESTION / REPORT / GUIDE / HALT / PIVOT / SYSTEM
- Minion filter: All / [list of minion names]
- Channel filter: All / [list of channel names]
- Search: Full-text search across Comm content
- Multiple filters combine with AND logic

**Pagination**:
- Initial load: 100 most recent Comms
- "Load More" button: Fetch next 100
- Infinite scroll (optional enhancement)
- Total count displayed

---

### 2.4 Minion Detail Modal

**Trigger**: Click minion name in sidebar or timeline

**Layout**:
```
┌────────────────────────────────────────────────────────────┐
│ Minion: Auth Expert                                   [×]  │
├────────────────────────────────────────────────────────────┤
│ ┌──────────────────────────────────────────────────────────┐│
│ │ [Overview] [Memory] [History] [Session Messages]        ││
│ └──────────────────────────────────────────────────────────┘│
│                                                            │
│ Overview                                                   │
│ ┌──────────────────────────────────────────────────────────┐│
│ │ Status: 🟢 Active (processing)                           ││
│ │ Role: Auth Service Expert                                ││
│ │ Type: Overseer (2 children)                              ││
│ │                                                          ││
│ │ Hierarchy:                                               ││
│ │ • Parent: 🔧 Service Discovery                           ││
│ │ • Children:                                              ││
│ │   - 🔧 SSO Analyzer (active)                             ││
│ │   - 🔧 Session Config Expert (paused)                    ││
│ │                                                          ││
│ │ Channels:                                                ││
│ │ • 💬 Implementation Planning (4 members)                 ││
│ │ • 💬 Database Changes (3 members)                        ││
│ │                                                          ││
│ │ Current Task:                                            ││
│ │ "Analyzing SSO impact on auth service and dependencies"  ││
│ │                                                          ││
│ │ Created: 2025-10-19 10:15 AM                             ││
│ │ Last Activity: 2025-10-19 10:32 AM (2 minutes ago)       ││
│ └──────────────────────────────────────────────────────────┘│
│                                                            │
│ Capabilities:                                              │
│ ┌──────────────────────────────────────────────────────────┐│
│ │ • auth_expertise (expertise: 0.92) ★★★★★                ││
│ │   Evidence: 5 successful tasks                           ││
│ │                                                          ││
│ │ • oauth_integration (expertise: 0.85) ★★★★☆              ││
│ │   Evidence: 3 successful tasks                           ││
│ │                                                          ││
│ │ • session_management (expertise: 0.78) ★★★★☆             ││
│ │   Evidence: 4 successful tasks, 1 correction             ││
│ └──────────────────────────────────────────────────────────┘│
│                                                            │
│ Recent Activity:                                           │
│ ┌──────────────────────────────────────────────────────────┐│
│ │ 10:32 AM - Sent REPORT to Implementation Planning       ││
│ │ 10:30 AM - Received GUIDE from USER                     ││
│ │ 10:28 AM - Spawned SSO Analyzer                         ││
│ │ 10:25 AM - Sent QUESTION to Database Team channel       ││
│ └──────────────────────────────────────────────────────────┘│
│                                                            │
│ Actions:                                                   │
│ [Send Comm] [Halt] [Pivot] [View Full History]            │
│ [Fork Minion] [Terminate]                                 │
└────────────────────────────────────────────────────────────┘
```

**Memory Tab**:
```
│ Memory                                                     │
│ ┌──────────────────────────────────────────────────────────┐│
│ │ Short-Term Memory (last 3 tasks):                        ││
│ │                                                          ││
│ │ • SSO requires SAML 2.0 support (quality: 0.9) ✓        ││
│ │   Used successfully: 2 times                             ││
│ │   Last reinforced: 2 min ago                             ││
│ │                                                          ││
│ │ • Current auth uses JWT tokens (quality: 1.0) ✓         ││
│ │   Used successfully: 5 times                             ││
│ │   Last reinforced: 10 min ago                            ││
│ │                                                          ││
│ │ • Session timeout is 24 hours (quality: 0.6) ⚠          ││
│ │   Used successfully: 1 time                              ││
│ │   Used unsuccessfully: 1 time (corrected by user)        ││
│ │   Last reinforced: 15 min ago                            ││
│ │                                                          ││
│ │ [View All (23 entries)]                                  ││
│ ├──────────────────────────────────────────────────────────┤│
│ │ Long-Term Memory (patterns & rules):                     ││
│ │                                                          ││
│ │ • Always check downstream service dependencies           ││
│ │   Pattern recognized: 5 tasks                            ││
│ │   Quality: 0.95 ✓                                        ││
│ │                                                          ││
│ │ • Authentication changes require database migration      ││
│ │   Pattern recognized: 3 tasks                            ││
│ │   Quality: 0.88 ✓                                        ││
│ │                                                          ││
│ │ [View All (7 patterns)]                                  ││
│ └──────────────────────────────────────────────────────────┘│
```

**History Tab**:
- Shows all Comms involving this minion (sent and received)
- Chronological order, newest first
- Same card design as timeline
- Filter by Comm type
- Pagination (100 per page)

**Session Messages Tab**:
- Shows raw SDK conversation (Messages, not Comms)
- Same format as existing single-session view
- Useful for debugging SDK-level behavior
- Can see tool calls, results, internal reasoning

**Actions**:
- **Send Comm**: Opens Comm composer pre-filled with this minion as recipient
- **Halt**: Confirms, then halts minion
- **Pivot**: Opens modal to provide new instructions
- **View Full History**: Opens dedicated history view (full screen)
- **Fork Minion**: Opens fork modal (name, role, channel selection)
- **Terminate**: Confirms (warning if has children), then terminates

---

### 2.5 Channel Detail Modal

**Trigger**: Click channel name in sidebar

**Layout**:
```
┌────────────────────────────────────────────────────────────┐
│ Channel: Implementation Planning                     [×]  │
├────────────────────────────────────────────────────────────┤
│ ┌──────────────────────────────────────────────────────────┐│
│ │ [Overview] [Members] [Comms]                            ││
│ └──────────────────────────────────────────────────────────┘│
│                                                            │
│ Overview                                                   │
│ ┌──────────────────────────────────────────────────────────┐│
│ │ Purpose: Coordination                                    ││
│ │ Description: Coordinate overall implementation strategy  ││
│ │   for new SSO feature across all affected services.     ││
│ │                                                          ││
│ │ Created: 2025-10-19 10:20 AM                             ││
│ │ Created by: 👑 Lead Architect                            ││
│ │ Members: 4 minions                                       ││
│ │ Total Comms: 87                                          ││
│ └──────────────────────────────────────────────────────────┘│
│                                                            │
│ Members                                                    │
│ ┌──────────────────────────────────────────────────────────┐│
│ │ 👑 Lead Architect (active) [View] [Send Comm]            ││
│ │   Role: Overall architecture and coordination            ││
│ │   Joined: 2025-10-19 10:20 AM                            ││
│ │                                                          ││
│ │ 🔧 Auth Expert (active) [View] [Send Comm]               ││
│ │   Role: Auth Service Expert                              ││
│ │   Joined: 2025-10-19 10:24 AM                            ││
│ │                                                          ││
│ │ 🔧 Payment Expert (paused) [View] [Send Comm]            ││
│ │   Role: Payment Service Expert                           ││
│ │   Joined: 2025-10-19 10:25 AM                            ││
│ │                                                          ││
│ │ 🔧 Database Architect (active) [View] [Send Comm]        ││
│ │   Role: Database architecture and migrations             ││
│ │   Joined: 2025-10-19 10:26 AM                            ││
│ └──────────────────────────────────────────────────────────┘│
│                                                            │
│ Actions:                                                   │
│ [Broadcast to Channel] [Add Member] [Leave Channel]       │
└────────────────────────────────────────────────────────────┘
```

**Comms Tab**:
- Identical to timeline view, but filtered to this channel only
- Shows all broadcasts to channel
- Same Comm card design
- Pagination

**Actions**:
- **Broadcast**: Opens Comm composer with channel pre-selected as recipient
- **Add Member**: Dropdown of available minions (not already members)
- **Leave Channel**: Removes selected member (requires selection)

---

### 2.6 Fleet Controls Panel

**Trigger**: Click fleet status in sidebar OR "Fleet Controls" tab in main view

**Layout**:
```
┌────────────────────────────────────────────────────────────┐
│ Fleet Controls: SaaS Platform Overhaul                     │
├────────────────────────────────────────────────────────────┤
│                                                            │
│ Fleet Status:                                              │
│ ┌──────────────────────────────────────────────────────────┐│
│ │ Active Minions: 7 / 20 max                               ││
│ │ Hordes: 2                                                ││
│ │ Channels: 3                                              ││
│ │ Total Comms: 573                                         ││
│ │                                                          ││
│ │ State Breakdown:                                         ││
│ │ • Active: 5 minions [●●●●●]                              ││
│ │ • Paused: 2 minions [⏸⏸]                                 ││
│ │ • Error: 0 minions                                       ││
│ └──────────────────────────────────────────────────────────┘│
│                                                            │
│ Fleet Actions:                                             │
│ ┌──────────────────────────────────────────────────────────┐│
│ │ [Create Minion]  [Create Channel]  [View Timeline]      ││
│ │                                                          ││
│ │ [Resume All Paused (2)]                                  ││
│ │                                                          ││
│ │ [🚨 Emergency Halt All (7)]                              ││
│ │   Stops all active minions without terminating sessions  ││
│ └──────────────────────────────────────────────────────────┘│
│                                                            │
│ Active Minions:                                            │
│ ┌──────────────────────────────────────────────────────────┐│
│ │ 👑 Lead Architect          [●] [View] [Halt]             ││
│ │    Processing: "Reviewing implementation plan"           ││
│ │                                                          ││
│ │ 🔧 Auth Expert             [●] [View] [Halt]││
│ │    Processing: "Analyzing SSO impact"                    ││
│ │                                                          ││
│ │ 🔧 Database Architect      [●] [View] [Halt]             ││
│ │    Idle                                                  ││
│ │                                                          ││
│ │ ... (2 more)                                             ││
│ └──────────────────────────────────────────────────────────┘│
│                                                            │
│ Paused Minions:                                            │
│ ┌──────────────────────────────────────────────────────────┐│
│ │ 🔧 Payment Expert          [⏸] [View] [Resume]           ││
│ │    Halted by: USER at 10:28 AM                           ││
│ │                                                          ││
│ │ 🔧 SSO Analyzer            [⏸] [View] [Resume]           ││
│ │    Halted by: Auth Expert at 10:30 AM                    ││
│ └──────────────────────────────────────────────────────────┘│
│                                                            │
│ [Close]                                                    │
└────────────────────────────────────────────────────────────┘
```

**Emergency Halt Confirmation**:
```
┌────────────────────────────────────────────────────┐
│ Confirm Emergency Halt                        [×] │
├────────────────────────────────────────────────────┤
│                                                    │
│ This will halt all 7 active minions immediately.  │
│                                                    │
│ • Minions will stop processing                     │
│ • Sessions remain active (can resume later)        │
│ • Queued Comms will be preserved                   │
│                                                    │
│ Use this for debugging or emergency intervention.  │
│                                                    │
│                          [Cancel]  [Halt All]     │
└────────────────────────────────────────────────────┘
```

---

### 2.7 Create Minion Modal

**Trigger**: "Create Minion" button in fleet controls or sidebar

**Layout**:
```
┌────────────────────────────────────────────────────────────┐
│ Create New Minion                                     [×] │
├────────────────────────────────────────────────────────────┤
│                                                            │
│ Minion Name: (unique within legion)                       │
│ [____________________]                                     │
│                                                            │
│ Role Description:                                          │
│ [____________________]                                     │
│ Example: "Auth Service Expert", "Database Architect"      │
│                                                            │
│ Initialization Context (System Prompt):                    │
│ ┌──────────────────────────────────────────────────────────┐│
│ │ You are an expert in authentication systems and OAuth.   ││
│ │ Your role is to analyze auth-related changes and         ││
│ │ identify impacts on downstream services.                 ││
│ │                                                          ││
│ │ [Load from Template ▼]                                   ││
│ └──────────────────────────────────────────────────────────┘│
│                                                            │
│ Join Channels: (optional)                                  │
│ ☑ Implementation Planning                                  │
│ ☐ Database Changes                                         │
│ ☐ Testing Strategy                                         │
│                                                            │
│ Advanced Options: [Show ▼]                                 │
│                                                            │
│                              [Cancel]  [Create Minion]    │
└────────────────────────────────────────────────────────────┘
```

**Advanced Options** (collapsed by default):
```
│ Advanced Options: [Hide ▲]                                 │
│ ┌──────────────────────────────────────────────────────────┐│
│ │ Permission Mode: [default ▼]                             ││
│ │ Model: [claude-3-sonnet-20241022 ▼]                      ││
│ │ Tools: [☑ read] [☑ write] [☑ edit] [☐ bash]              ││
│ └──────────────────────────────────────────────────────────┘│
```

**Templates Dropdown**:
- Predefined templates for common roles:
  - "Service Expert" (generic microservice expert)
  - "Database Specialist" (schema, migrations)
  - "Character (D&D)" (roleplay character)
  - "Researcher" (research and analysis)
  - "Custom" (user provides full prompt)

**Validation**:
- Name required, must be unique
- Role required
- Initialization context required
- Templates pre-fill initialization context

---

### 2.8 Pivot Minion Modal

**Trigger**: "Pivot" button in minion detail or fleet controls

**Layout**:
```
┌────────────────────────────────────────────────────────────┐
│ Pivot Minion: Auth Expert                            [×] │
├────────────────────────────────────────────────────────────┤
│                                                            │
│ This will HALT the minion, CLEAR its queue, and provide   │
│ immediate new instructions.                                │
│                                                            │
│ Current Task:                                              │
│ ┌──────────────────────────────────────────────────────────┐│
│ │ "Analyzing SSO impact on auth service dependencies"      ││
│ └──────────────────────────────────────────────────────────┘│
│                                                            │
│ New Instructions:                                          │
│ ┌──────────────────────────────────────────────────────────┐│
│ │ Actually, switch to analyzing OAuth instead of SAML.     ││
│ │ Focus on OAuth 2.0 and PKCE implementation requirements. ││
│ │                                                          ││
│ │                                                          ││
│ └──────────────────────────────────────────────────────────┘│
│                                                            │
│ Warning: All queued Comms will be discarded.               │
│                                                            │
│                              [Cancel]  [Pivot Now]        │
└────────────────────────────────────────────────────────────┘
```

**Behavior**:
- Shows current task for context
- Large text area for new instructions
- Clear warning about queue clearing
- "Pivot Now" button styled in warning color (red/orange)

---

### 2.9 Fork Minion Modal

**Trigger**: "Fork Minion" button in minion detail

**Layout**:
```
┌────────────────────────────────────────────────────────────┐
│ Fork Minion: Auth Expert                              [×] │
├────────────────────────────────────────────────────────────┤
│                                                            │
│ Create a duplicate minion with identical memory at this   │
│ moment. After forking, memories will diverge.              │
│                                                            │
│ Source Minion:                                             │
│ ┌──────────────────────────────────────────────────────────┐│
│ │ Name: Auth Expert                                        ││
│ │ Role: Auth Service Expert                                ││
│ │ Capabilities: auth_expertise (0.92), oauth (0.85)        ││
│ │ Memory Entries: 23 short-term, 7 long-term               ││
│ └──────────────────────────────────────────────────────────┘│
│                                                            │
│ New Minion:                                                │
│ Name: (must be unique)│
│ [AuthExpert_Alt1___________________]                       │
│                                                            │
│ Role: (can be same or different)                           │
│ [Auth Service Expert - Alt Approach]                       │
│                                                            │
│ Join Channels:                                             │
│ ☑ Implementation Planning (same as source)                 │
│ ☐ Database Changes                                         │
│                                                            │
│ Note: Forked minion will have identical memory NOW,        │
│ but will operate independently after creation.             │
│                                                            │
│                              [Cancel]  [Fork Minion]      │
└────────────────────────────────────────────────────────────┘
```

---

## 3. User Workflows

### 3.1 Workflow: Create Legion and First Minion

**Steps**:
1. User clicks "Create Project" in sidebar
2. Modal opens
3. User enters name "SaaS Platform Overhaul"
4. User selects working directory
5. User checks "Enable Multi-Agent (Legion)"
6. User clicks "Create Project"
7. Legion created, sidebar updates with legion icon
8. User clicks "+" next to legion name (or fleet controls)
9. "Create Minion" modal opens
10. User enters name "LeadArchitect", role "Lead Architect"
11. User writes initialization context or selects template
12. User clicks "Create Minion"
13. Minion created, appears in sidebar under new horde
14. Minion automatically active and ready

**Expected Time**: ~2 minutes

---

### 3.2 Workflow: Send Comm to Minion

**Steps**:
1. User selects legion in sidebar
2. Timeline view appears
3. User types in Comm composer at bottom
4. User selects "To: Lead Architect" from dropdown
5. User selects "Type: TASK"
6. User types message "Analyze new SSO requirements"
7. User clicks "Send"
8. Comm appears in timeline immediately (optimistic UI)
9. WebSocket confirms delivery
10. Minion processes, responds with Comm
11. Response appears in timeline in real-time

**Expected Time**: ~30 seconds

---

### 3.3 Workflow: Overseer Spawns Child

**Steps**:
1. User sends TASK to Lead Architect minion
2. Lead Architect (SDK) decides it needs specialist
3. SDK indicates spawn intent (via pattern or explicit tool call)
4. OverseerController.spawn_minion() triggered
5. New minion "AuthExpert" created
6. SPAWN Comm appears in timeline (notification)
7. Sidebar updates with new minion under Lead Architect
8. Lead Architect now shows as Overseer (👑 icon)
9. New minion joins channels, starts processing

**Expected Time**: ~3-5 seconds (automated)

---

### 3.4 Workflow: Emergency Halt Fleet

**Steps**:
1. User realizes minions going in wrong direction
2. User clicks fleet status in sidebar
3. Fleet controls panel opens
4. User clicks "Emergency Halt All (7)"
5. Confirmation modal appears
6. User clicks "Halt All"
7. All 7 active minions halt immediately
8. Sidebar updates (green dots → yellow pause icons)
9. Fleet controls shows all in "Paused" section
10. User reviews timeline, identifies issue
11. User sends PIVOT to specific minion with correction
12. User clicks "Resume" on individual minions as needed

**Expected Time**: ~10 seconds to halt, varies for resolution

---

### 3.5 Workflow: Fork Minion for A/B Testing

**Steps**:
1. User has minion "AuthExpert" with good expertise
2. User wants to explore two approaches
3. User clicks "AuthExpert" in sidebar
4. Minion detail modal opens
5. User clicks "Fork Minion"
6. Fork modal opens, pre-filled with source info
7. User enters name "AuthExpert_OAuth", role "Auth Expert - OAuth Approach"
8. User selects same channels
9. User clicks "Fork Minion"
10. New minion created with identical memory
11. User sends different TASK to each minion
12. Both work independently, memories diverge
13. User compares results in timeline

**Expected Time**: ~1 minute to fork, varies for comparison

---

## 4. Responsive Design & Accessibility

### 4.1 Responsive Breakpoints

**Desktop (1200px+)**:
- Full sidebar (300px wide)
- Full timeline with details
- Modals at 600px width

**Tablet (768px - 1199px)**:
- Collapsible sidebar (toggle button)
- Timeline full width when sidebar collapsed
- Modals at 90% width

**Mobile (< 768px)**:
- Sidebar becomes full-screen overlay
- Timeline stacked vertically
- Modals full screen
- Comm cards simplified (hide metadata by default)

### 4.2 Accessibility

**Keyboard Navigation**:
- Tab through all interactive elements
- Enter/Space to activate buttons
- Escape to close modals
- Arrow keys to navigate lists

**Screen Reader Support**:
- ARIA labels on all icons
- ARIA live regions for real-time updates
- Semantic HTML (nav, main, aside)
- Alt text for status indicators

**Visual Accessibility**:
- High contrast mode support
- Color is not sole indicator (icons + text)
- Minimum font size 14px
- Focus indicators visible

---

## 5. Error States & Empty States

### 5.1 Error States

**Minion Creation Failed**:
```
┌────────────────────────────────────────────────┐
│ ⚠ Error Creating Minion                       │
├────────────────────────────────────────────────┤
│                                                │
│ Failed to create minion "AuthExpert":          │
│                                                │
│ • Legion at maximum capacity (20/20)           │
│                                                │
│ Please terminate unused minions or increase    │
│ the legion's max concurrent limit.             │
│                                                │
│                                 [OK]           │
└────────────────────────────────────────────────┘
```

**Comm Delivery Failed**:
```
Timeline shows:
┌────────────────────────────────────────────────┐
│ 👤 YOU → 🔧 Auth Expert              [TASK]   │
│ "Analyze SSO requirements"                     │
│                                                │
│ ⚠ Failed to deliver (minion terminated)        │
│ [Retry] [View Error]                           │
└────────────────────────────────────────────────┘
```

**SDK Session Crash**:
```
Sidebar shows:
🔧 Auth Expert [✗]

Minion detail shows:
┌────────────────────────────────────────────────┐
│ Status: 🔴 Error                               │
│                                                │
│ SDK session crashed at 10:45 AM                │
│                                                │
│ Error: Connection timeout                      │
│                                                │
│ [View Logs] [Restart Session] [Terminate]     │
└────────────────────────────────────────────────┘
```

### 5.2 Empty States

**No Minions in Legion**:
```
Timeline view shows:
┌────────────────────────────────────────────────┐
│ No minions in this legion yet.                 │
│                                                │
│ Create your first minion to get started.       │
│                                                │
│ [Create Minion]                                │
└────────────────────────────────────────────────┘
```

**No Comms in Timeline**:
```
┌────────────────────────────────────────────────┐
│ No communication yet.                          │
│                                                │
│ Send a Comm to a minion to start collaborating.│
└────────────────────────────────────────────────┘
```

**No Channels in Legion**:
```
Channels section in sidebar:
💬 Channels (0)
   No channels created yet.
   [Create Channel]
```

---

## 6. Animations & Transitions

### 6.1 Microinteractions

**New Comm Arrival**:
- Fade in from top (300ms ease-out)
- Slight yellow highlight for 1 second, then fade to normal

**Minion State Change**:
- Status indicator color transition (200ms)
- Pulse animation when transitioning to active

**Modal Open/Close**:
- Fade in background overlay (200ms)
- Slide down modal content (300ms ease-out)
- Reverse on close

**Sidebar Expand/Collapse**:
- Smooth width transition (250ms ease-in-out)
- Rotate caret icon (200ms)

### 6.2 Loading States

**Creating Minion**:
```
[Create Minion] → [Creating... ⟳]
Modal buttons disabled, spinner in button
```

**Sending Comm**:
```
[Send] → [Sending... ⟳]
Comm appears in timeline with faded opacity
On success: Full opacity, checkmark briefly appears
```

**Loading More Comms**:
```
[Load More (573 total)] → [Loading... ⟳]
Skeleton cards appear below
Cards populate when loaded
```

---

## 7. Visual Design System

### 7.1 Color Palette

**Primary Colors**:
- Primary Blue: #3B82F6 (actions, links)
- Success Green: #10B981 (active state, success)
- Warning Yellow: #F59E0B (paused, warnings)
- Error Red: #EF4444 (errors, critical actions)
- Gray Scale: #F9FAFB, #E5E7EB, #6B7280, #1F2937

**Comm Type Colors** (border-left accent):
- TASK: Blue (#3B82F6)
- QUESTION: Purple (#8B5CF6)
- REPORT: Green (#10B981)
- GUIDE: Yellow (#F59E0B)
- HALT/PIVOT: Red (#EF4444)
- SYSTEM: Gray (#6B7280)

### 7.2 Typography

**Font Family**:
- UI: Inter, system-ui, sans-serif
- Code/Monospace: 'Fira Code', monospace

**Font Sizes**:
- H1: 24px (legion name)
- H2: 20px (modal titles)
- H3: 16px (section headers)
- Body: 14px (default)
- Small: 12px (metadata, timestamps)

**Font Weights**:
- Regular: 400
- Medium: 500 (section headers)
- Semibold: 600 (minion names)
- Bold: 700 (legion name)

### 7.3 Spacing

**Padding**:
- xs: 4px
- sm: 8px
- md: 16px
- lg: 24px
- xl: 32px

**Margins**:
- Component gaps: 16px
- Section gaps: 24px
- Card padding: 16px

### 7.4 Icons

**Emoji-Based Icons** (for clarity):
- 🏛 Legion
- 👥 Hordes
- 🎯 Horde
- 👑 Overseer
- 🔧 Minion
- 💬 Channel
- 👤 User
- ● Active (green)
- ⏸ Paused (yellow)
- ✗ Error (red)

**Icon Library** (for actions):
- Feather Icons or Heroicons
- 20px default size
- Consistent stroke width

---

## 8. Performance Considerations

### 8.1 Optimization Strategies

**Timeline Rendering**:
- Virtualized scrolling (React Virtualized or similar)
- Lazy load Comm details (only render when expanded)
- Debounce search/filter input (300ms)

**WebSocket Updates**:
- Batch updates if multiple Comms arrive simultaneously
- Throttle UI updates to 60fps max
- Queue updates during browser tab inactive, apply on focus

**Sidebar Tree**:
- Collapse hordes by default if >10 minions
- Lazy render children when expanded
- Memoize minion components

### 8.2 Data Loading

**Initial Page Load**:
- Load legion metadata first
- Load recent 100 Comms
- Lazy load horde trees
- Progressive enhancement

**Infinite Scroll**:
- Load 100 Comms per page
- Preload next page when 80% scrolled
- Show loading skeleton

---

## Document Control

- **Version**: 1.0
- **Date**: 2025-10-19
- **Status**: Draft for Review
- **Dependencies**: legion_system_requirements.md, legion_technical_design.md
- **Next Steps**: Implementation plan, component specifications
- **Owner**: Design & Development Team
