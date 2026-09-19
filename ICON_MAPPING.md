# Icon Standardization Mapping

This document maps all current icon usage across the Vue 3 frontend to proposed standardized UTF emojis.

**Format**: Current Icon(s) → Proposed Emoji → Use Case/State → Where Used

---

## Actions

### Edit/Settings
**Current**: ✏️ (emoji)
**Proposed**: ✏️
**Use Case**: Edit actions, modify settings
**Where Used**:
- `SessionItem.vue:28` - Manage session button
- `ProjectItem.vue:42` - Edit/delete project button
- `SessionStatusBar.vue:64` - acceptEdits permission mode icon

**Bootstrap Icon**: `bi-gear`
**Current**: Used in CreateMinionModal.vue:22
**Proposed**: ⚙️
**Use Case**: Settings/configuration management (template management)
**Where Used**:
- `CreateMinionModal.vue:22` - Manage Templates button

### Add/Create
**Current**: ➕ (emoji)
**Proposed**: ➕
**Use Case**: Create new items (sessions, minions, projects)
**Where Used**:
- `ProjectItem.vue:52` - Add session/minion button

### Delete/Remove
**Bootstrap Icon**: `bi-trash`, `bi-x-circle`
**Current**: Multiple instances with Bootstrap icons
**Proposed**: 🗑️ (for delete) / ❌ (for close/cancel)
**Use Case**: Delete/remove operations
**Where Used**:
- `ProjectEditModal.vue:65` - Delete Project button (`bi-trash`)
- `ToolCallCard.vue:26,142` - Orphaned tool/Permission denied (`bi-x-circle`)
- `ShellToolHandler.vue:51` - Error state (`bi-x-circle`)
- `CommandToolHandler.vue:32` - Error state (`bi-x-circle`)
- `NotebookEditToolHandler.vue:46` - Error state (`bi-x-circle`)
- `ExitPlanModeToolHandler.vue:31` - Error state (`bi-x-circle`)
- `SlashCommandToolHandler.vue:21` - Error state (`bi-x-circle`)

### Expand/Collapse
**Bootstrap Icon**: `bi-chevron-up`, `bi-chevron-down`, `bi-chevron-right`
**Current**: Bootstrap icons for expandable sections (inconsistent patterns: up/down vs down/right)
**Proposed**: ▾ (expanded) / ▸ (collapsed)
**Rationale**:
- Standardizes to down/right pattern (points to where content is)
- ▾/▸ are smaller, more compact triangles designed for UI indicators
- Fixes current inconsistency between components
**Use Case**: Expand/collapse UI sections
**Where Used**:
- `ToolCallCard.vue:17` - Tool card expansion (currently `bi-chevron-up` / `bi-chevron-down`)
- `ThinkingBlock.vue:7` - Thinking block expansion (currently `bi-chevron-down` / `bi-chevron-right`)
- `SlashCommandToolHandler.vue:7` - Content expansion (currently `bi-chevron-down` / `bi-chevron-right`)

### Refresh/Reload
**Bootstrap Icon**: `bi-arrow-clockwise`
**Current**: Bootstrap icon
**Proposed**: 🔄
**Use Case**: Refresh directory listing
**Where Used**:
- `FolderBrowserModal.vue:29` - Refresh directory button

### Navigate Up/Parent
**Bootstrap Icon**: `bi-arrow-up-circle`
**Current**: Bootstrap icon
**Proposed**: ⬆️
**Use Case**: Navigate to parent directory
**Where Used**:
- `FolderBrowserModal.vue:55` - Parent directory navigation

### Folder/Directory
**Bootstrap Icon**: `bi-folder-fill`
**Current**: Bootstrap icon
**Proposed**: 📁
**Use Case**: Directory/folder representation
**Where Used**:
- `FolderBrowserModal.vue:70` - Folder items in browser

### Success/Completion
**Bootstrap Icon**: `bi-check-circle`
**Current**: Bootstrap icons and ✅ emoji
**Proposed**: ✅
**Use Case**: Success states, approval, completion
**Where Used**:
- `ToolCallCard.vue:72,83` - Permission approve buttons
- `ShellToolHandler.vue:47` - Success state
- `CommandToolHandler.vue:28` - Success state
- `NotebookEditToolHandler.vue:42` - Success state
- `ExitPlanModeToolHandler.vue:27` - Success state

### Information/Help
**Bootstrap Icon**: `bi-lightbulb`, `bi-chat-left-text`
**Current**: ℹ️ emoji and Bootstrap icons
**Proposed**: ℹ️ (info) / 💡 (suggestions/ideas)
**Use Case**: Information display, suggestions, thinking
**Where Used**:
- `SessionStatusBar.vue:18` - Info button (already ℹ️)
- `ToolCallCard.vue:48` - Permission suggestions (`bi-lightbulb`)
- `ToolCallCard.vue:101` - Guidance section (`bi-chat-left-text`)
- `ThinkingBlock.vue:9` - Thinking indicator (`bi-lightbulb`)

### Repeat/Retry
**Bootstrap Icon**: `bi-arrow-repeat`
**Current**: Bootstrap icon
**Proposed**: 🔁
**Use Case**: Retry with guidance
**Where Used**:
- `ToolCallCard.vue:120` - Provide Guidance & Continue button

### Security/Permission
**Bootstrap Icon**: `bi-shield-x`
**Current**: Bootstrap icon
**Proposed**: 🔐
**Use Case**: Permission requests, security warnings
**Where Used**:
- `ToolCallCard.vue:130` - Permission request cancelled

### Tools
**Bootstrap Icon**: `bi-tools`
**Current**: Bootstrap icon
**Proposed**: 🔧
**Use Case**: Tool results section
**Where Used**:
- `UserMessage.vue:13` - Tool Results header

---

## States

### Session States

**Current**: Colored dots (status-dot with CSS background colors)
**Proposed**: Keep colored dots (not emojis) for better visual consistency
**Use Case**: Session state indication
**Where Used**:
- `SessionItem.vue:14,73-121` - Session status dot
- `SessionStateStatusLine.vue:2-20` - Progress bar state colors

**State Color Mapping** (no changes needed, already well-designed):
- Created: Grey dot
- Starting: Light green (blinking)
- Active: Light green
- Running: Light green
- Processing: Light purple (blinking)
- Paused: Grey
- Pending-prompt: Yellow (blinking)
- Terminated: Grey
- Error/Failed: Light red

### Permission Modes

**Current**: Emojis
**Proposed**: Keep current emojis
**Use Case**: Permission mode indication
**Where Used**:
- `SessionStatusBar.vue:62-67`
  - default: 🔒
  - acceptEdits: ✏️
  - plan: 📋
  - bypassPermissions: ☢️

### Session Management Actions

**Current**: Emojis
**Proposed**: Keep current emojis
**Use Case**: Session management buttons
**Where Used**:
- `SessionManageModal.vue:61,70,88,97`
  - Restart: 🔄
  - End Session: 🚪
  - Reset: 🗑️
  - Delete: ❌

**Warning**: ⚠️ (already emoji) - used in `SessionManageModal.vue:30,38`

### Tool Call Status Icons

**Current**: Emojis
**Proposed**: Keep current emojis
**Use Case**: Tool execution status
**Where Used**:
- `ToolCallCard.vue:262-278`
  - pending: 🔄
  - permission_required: ❓
  - executing: ⚡
  - completed (success): ✅
  - completed (denied): ❌
  - error: 💥
  - default: 🔧

---

### Warning/Alert

**Bootstrap Icon**: `bi-exclamation-triangle`, `bi-exclamation-triangle-fill` (SVG)
**Current**: SVG Bootstrap icon
**Proposed**: ⚠️
**Use Case**: Warning messages, disconnection alerts
**Where Used**:
- `InputArea.vue:9-11` - Disconnected warning banner (SVG)
- `ShellToolHandler.vue:36` - Background process warning

---

## Legion/Multi-Agent

### Legion Project Indicator

**Current**: 🏛 (emoji)
**Proposed**: 🏛
**Use Case**: Multi-agent legion project marker
**Where Used**:
- `ProjectItem.vue:29` - Legion project icon

### Timeline

**Current**: 📊 (emoji)
**Proposed**: 📊
**Use Case**: Legion timeline view
**Where Used**:
- `ProjectItem.vue:92` - Timeline navigation item

---

## Status Bar Actions

### Autoscroll Toggle

**Current**: ⬇️ (emoji)
**Proposed**: ⬇️
**Use Case**: Autoscroll on/off indicator
**Where Used**:
- `SessionStatusBar.vue:36` - Autoscroll toggle button

### Settings/Manage

**Current**: ⚙️ (emoji)
**Proposed**: ⚙️
**Use Case**: Session management access
**Where Used**:
- `SessionStatusBar.vue:25` - Manage button

---

## Summary by Category

### Actions Needing Replacement (Bootstrap Icons → Emojis)

1. **Settings/Configuration**: `bi-gear` → ⚙️
2. **Delete**: `bi-trash` → 🗑️
3. **Error/Cancel**: `bi-x-circle` → ❌
4. **Expand/Collapse**: `bi-chevron-up/down/right` → ▾/▸
5. **Refresh**: `bi-arrow-clockwise` → 🔄
6. **Navigate Up**: `bi-arrow-up-circle` → ⬆️
7. **Folder**: `bi-folder-fill` → 📁
8. **Success**: `bi-check-circle` → ✅
9. **Information**: `bi-lightbulb` → 💡 (suggestions), keep ℹ️ for info
10. **Chat/Guidance**: `bi-chat-left-text` → 💬
11. **Repeat**: `bi-arrow-repeat` → 🔁
12. **Shield/Security**: `bi-shield-x` → 🔒
13. **Tools**: `bi-tools` → 🔧
14. **Warning**: `bi-exclamation-triangle` (SVG) → ⚠️

### Emojis Already in Use (Keep As-Is)

**Actions**:
- ✏️ Edit
- ➕ Add/Create
- ℹ️ Info
- 🔄 Restart
- 🚪 End Session
- 🗑️ Reset
- ❌ Delete/Deny
- ⚠️ Warning
- ✅ Approve/Success
- ⬇️ Autoscroll
- ⚙️ Manage/Settings

**Tool States**:
- 🔄 Pending
- ❓ Permission Required
- ⚡ Executing
- ✅ Completed (success)
- ❌ Completed (denied)
- 💥 Error
- 🔧 Default/Tool

**Legion**:
- 🏛 Legion Project
- 📊 Timeline

**Permission Modes**:
- 🔒 Default
- ✏️ Accept Edits
- 📋 Plan
- ☢️ Bypass Permissions

### Session States (Colored Dots - No Change)

- Created, Starting, Active, Running, Processing, Paused, Pending-prompt, Terminated, Error/Failed
- These use CSS background colors and border colors for visual distinction
- Recommendation: Keep as colored dots (not emojis) for better UX consistency

---

## Files Containing Bootstrap Icons (13 files)

1. `InputArea.vue` - Warning SVG
2. `CreateMinionModal.vue` - Settings icon
3. `ToolCallCard.vue` - Multiple icons (chevrons, x-circle, lightbulb, check-circle, etc.)
4. `ShellToolHandler.vue` - Warning, check-circle, x-circle
5. `SlashCommandToolHandler.vue` - Chevrons, x-circle
6. `SkillToolHandler.vue` - (Contains "bi-" search results, needs verification)
7. `NotebookEditToolHandler.vue` - Check-circle, x-circle
8. `ExitPlanModeToolHandler.vue` - Check-circle, x-circle
9. `CommandToolHandler.vue` - Check-circle, x-circle
10. `UserMessage.vue` - Tools icon
11. `ProjectEditModal.vue` - Trash icon
12. `ThinkingBlock.vue` - Chevrons, lightbulb
13. `FolderBrowserModal.vue` - Arrow-clockwise, arrow-up-circle, folder-fill

---

## Recommended Implementation Order

Based on frequency and impact, suggested commit order:

1. **Expand/Collapse Icons** (`bi-chevron-*` → ▾/▸) - High visibility, 3 components, fixes inconsistency
2. **Success/Error Icons** (`bi-check-circle`, `bi-x-circle` → ✅/❌) - High frequency, 8+ components
3. **Settings/Configuration** (`bi-gear`) - 1 component
4. **Delete** (`bi-trash`) - 1 component
5. **Folder Browser Icons** (`bi-arrow-clockwise`, `bi-arrow-up-circle`, `bi-folder-fill`) - 1 component, 3 icons
6. **Permission/Guidance Icons** (`bi-lightbulb`, `bi-chat-left-text`, `bi-shield-x`, `bi-arrow-repeat`) - 2 components
7. **Tools Icon** (`bi-tools`) - 1 component
8. **Warning Icon** (`bi-exclamation-triangle` SVG) - 2 components

---

## Notes

- **Session state dots**: Already well-designed using CSS colors. No emoji replacement recommended.
- **Accessibility**: All emojis should have proper `aria-label` attributes for screen readers.
- **Bootstrap Icons Removal**: After all replacements, Bootstrap Icons should be fully removed from usage (no separate package to remove, it's part of Bootstrap CSS).
- **Mobile Responsive**: Icon-only buttons on mobile (<768px) make standardization critical for UX.
- **Platform Rendering**: UTF emojis render differently across platforms, but this is acceptable for this use case.
