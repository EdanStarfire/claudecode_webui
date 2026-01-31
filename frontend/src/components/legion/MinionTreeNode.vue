<template>
  <div v-if="minionData" class="minion-tree-node" :style="{ marginLeft: `${indent}px` }">
    <!-- Sidebar Layout (vertical, single column) -->
    <div v-if="layout === 'sidebar'" class="minion-card minion-card-clickable border-start border-2 mb-2 d-flex align-items-center p-2" :class="{ active: isSelected }" @click="handleClick">
      <!-- Status Indicator Dot -->
      <div class="status-dot me-2" :class="statusDotClass" :style="statusDotStyle"></div>

      <!-- Minion Info (Name + Latest Activity) -->
      <div class="minion-info flex-grow-1">
        <div class="minion-name">
          <!-- Overseer Icon -->
          <span v-if="isOverseerWithChildren" class="me-1">👑</span>

          <!-- Minion Name -->
          <strong>{{ minionWithLiveData?.name || minionData.name }}</strong>

          <!-- Children Count Badge -->
          <span v-if="isOverseerWithChildren" class="badge bg-secondary ms-2">
            {{ minionData.children.length }} {{ minionData.children.length === 1 ? 'child' : 'children' }}
          </span>
        </div>

        <!-- Latest Activity below name - Issue #291, #340: use live data for real-time updates -->
        <div v-if="minionWithLiveData?.latest_message" class="latest-activity text-muted">
          <span v-if="messagePrefix" class="activity-prefix">{{ messagePrefix }} </span>
          <span class="activity-content" :title="minionWithLiveData.latest_message">{{ truncatedMessage }}</span>
          <span v-if="relativeTime" class="activity-time ms-1">({{ relativeTime }})</span>
        </div>
        <!-- Fallback to last_comm if no latest_message -->
        <div v-else-if="minionData?.last_comm" class="latest-activity text-muted">
          <span class="activity-prefix">→ <strong>{{ getCommRecipient(minionData.last_comm) }}</strong>: </span>
          <span class="activity-content" :title="minionData.last_comm.content || ''">{{ getCommSummary(minionData.last_comm) }}</span>
        </div>
        <!-- Empty state -->
        <div v-else class="latest-activity text-muted fst-italic small">
          No activity yet
        </div>
      </div>

      <!-- Action Buttons (Issue #296) -->
      <button
        class="btn btn-sm btn-outline-secondary me-1"
        title="Edit minion"
        @click.stop="showEditModal"
      >
        ✏️
      </button>
      <button
        class="btn btn-sm btn-outline-secondary"
        title="Manage minion"
        @click.stop="showManageModal"
      >
        ⚙️
      </button>
    </div>

    <!-- Two-Column Layout (for HierarchyView) -->
    <div v-else class="minion-card minion-card-clickable border-start border-2 mb-2" :class="{ active: isSelected }" @click="handleClick">
      <div class="node-row">
        <!-- Left Column: Status + Name (30%) -->
        <div class="node-left">
          <!-- Status Dot -->
          <div class="status-dot me-2" :class="statusDotClass" :style="statusDotStyle"></div>

          <!-- Overseer Icon -->
          <span v-if="isOverseerWithChildren" class="me-2">👑</span>

          <!-- Minion Name -->
          <strong>{{ minionWithLiveData?.name || minionData.name }}</strong>

          <!-- Children Count Badge -->
          <span v-if="isOverseerWithChildren" class="badge bg-secondary ms-2">
            {{ minionData.children.length }} {{ minionData.children.length === 1 ? 'child' : 'children' }}
          </span>
        </div>

        <!-- Right Column: Latest Message or Last Comm (70%) -->
        <div class="node-right">
          <!-- Show latest_message if available (priority over comm) - #340: use live data -->
          <div v-if="minionWithLiveData?.latest_message" class="latest-message-preview">
            <span v-if="messagePrefix" class="message-prefix">{{ messagePrefix }}</span>
            <span class="message-content" :title="minionWithLiveData.latest_message">{{ truncatedMessage }}</span>
            <span v-if="relativeTime" class="message-time ms-1">({{ relativeTime }})</span>
          </div>
          <!-- Fallback to last_comm if no latest_message -->
          <div v-else-if="minionData?.last_comm" class="last-comm-preview">
            <span class="comm-direction">→ <strong>{{ getCommRecipient(minionData.last_comm) }}</strong>:</span>
            <span class="comm-content" :title="minionData.last_comm.content || ''">{{ getCommSummary(minionData.last_comm) }}</span>
          </div>
          <!-- Empty state -->
          <div v-else class="text-muted fst-italic small">
            No activity yet
          </div>
        </div>

        <!-- Action Buttons -->
        <div class="node-actions">
          <button
            class="btn btn-sm btn-outline-secondary me-1"
            title="Edit minion"
            @click.stop="showEditModal"
          >
            ✏️
          </button>
          <button
            class="btn btn-sm btn-outline-secondary"
            title="Manage minion"
            @click.stop="showManageModal"
          >
            ⚙️
          </button>
        </div>
      </div>
    </div>

    <!-- Recursively Render Children -->
    <div v-if="hasChildren" class="minion-children">
      <MinionTreeNode
        v-for="child in minionData.children"
        :key="child.id"
        :minion-data="child"
        :level="level + 1"
        :layout="layout"
        @minion-click="$emit('minion-click', $event)"
      />
    </div>
  </div>

  <!-- Debug: Show if minion data is missing -->
  <div v-else class="text-danger small ms-4">
    ⚠ Minion data is missing
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useSessionStore } from '@/stores/session'
import { useMessageStore } from '@/stores/message'
import { useUIStore } from '@/stores/ui'

const props = defineProps({
  minionData: {
    type: Object,
    required: true
  },
  level: {
    type: Number,
    default: 0
  },
  layout: {
    type: String,
    default: 'two-column', // 'two-column' for HierarchyView, 'sidebar' for ProjectItem sidebar
    validator: (value) => ['two-column', 'sidebar'].includes(value)
  }
})

const emit = defineEmits(['minion-click'])

const sessionStore = useSessionStore()
const messageStore = useMessageStore()
const uiStore = useUIStore()

// Get live session data for this minion
const liveSession = computed(() => {
  if (!props.minionData || !props.minionData.id) return null
  return sessionStore.getSession(props.minionData.id)
})

// Get latest message from message store (only for active session)
// Filters are synchronized with MessageList.vue shouldDisplayMessage() for consistency
const latestMessageFromStore = computed(() => {
  if (!props.minionData || !props.minionData.id) return null

  const messages = messageStore.messagesBySession.get(props.minionData.id)
  if (!messages || messages.length === 0) return null

  // Find the last meaningful message (apply same filtering as MessageList.vue)
  for (let i = messages.length - 1; i >= 0; i--) {
    const msg = messages[i]
    const subtype = msg.subtype || msg.metadata?.subtype

    // Skip result messages (tool results)
    if (msg.type === 'result') continue

    // Skip system init messages (synced with MessageList.vue)
    if (msg.type === 'system' && subtype === 'init') continue

    // Skip system task_notification messages (synced with MessageList.vue)
    if (msg.type === 'system' && subtype === 'task_notification') continue

    // Skip permission messages (synced with MessageList.vue)
    if (msg.type === 'permission_request' || msg.type === 'permission_response') continue

    // Skip user messages with only tool results (synced with MessageList.vue)
    if (msg.type === 'user' && msg.metadata?.has_tool_results) {
      const content = msg.content || ''
      if (content.match(/^Tool results?: \d+ results?$/i) || content.trim() === '') continue
    }

    // Skip skill-related user messages (synced with MessageList.vue)
    if (msg.type === 'user') {
      const content = msg.content || ''
      if (content.includes('<command-message>') && content.includes('skill is running')) continue
      if (content.startsWith('Base directory for this skill:')) continue
    }

    // Skip slash command user messages (synced with MessageList.vue)
    if (msg.type === 'user') {
      const content = msg.content || ''
      if (content.includes('<command-message>') &&
          content.includes('<command-name>') &&
          content.includes('<command-args>')) continue
      if (content.includes('ARGUMENTS:') &&
          (content.includes('<command-name>') || content.match(/\nARGUMENTS:/))) continue
    }

    // Skip system messages with generic placeholder content
    if (msg.type === 'system' && msg.content === 'System message') continue

    // Skip assistant messages with only tool uses (no actual text)
    if (msg.type === 'assistant' && msg.content === 'Assistant response') continue

    // Accept user, assistant (with content), or system (with content) messages
    if (msg.type === 'user' || msg.type === 'assistant' || msg.type === 'system') {
      return {
        content: msg.content,
        type: msg.type,
        timestamp: msg.timestamp
      }
    }
  }

  return null
})

// Merge live session data with hierarchy data
const minionWithLiveData = computed(() => {
  if (!props.minionData) return null

  const merged = { ...props.minionData }

  // Merge live session state if available
  if (liveSession.value) {
    merged.state = liveSession.value.state
    merged.is_processing = liveSession.value.is_processing
    // Merge name from live session (updated via PATCH)
    if (liveSession.value.name) {
      merged.name = liveSession.value.name
    }
    // Merge latest message from session store (updated via UI WebSocket broadcasts)
    if (liveSession.value.latest_message) {
      merged.latest_message = liveSession.value.latest_message
      merged.latest_message_type = liveSession.value.latest_message_type
      merged.latest_message_time = liveSession.value.latest_message_time
    }
  }

  // Merge latest message if available from message store (for active session)
  if (latestMessageFromStore.value) {
    merged.latest_message = latestMessageFromStore.value.content
    merged.latest_message_type = latestMessageFromStore.value.type
    merged.latest_message_time = latestMessageFromStore.value.timestamp
  }
  // Otherwise keep hierarchy data for latest_message (contains cached data from backend)
  // Note: For non-active sessions, we rely on hierarchy API data which should be
  // periodically refreshed or updated via WebSocket broadcasts

  return merged
})

// Indentation (24px per level)
const indent = computed(() => props.level * 24)

// Check if this minion is currently selected
const isSelected = computed(() => {
  if (!props.minionData || !props.minionData.id) return false
  return sessionStore.currentSessionId === props.minionData.id
})

// Check if minion has children
const hasChildren = computed(() => {
  return (
    props.minionData &&
    props.minionData.children &&
    props.minionData.children.length > 0
  )
})

// Check if minion is an overseer with children
const isOverseerWithChildren = computed(() => {
  return (
    props.minionData &&
    props.minionData.is_overseer &&
    hasChildren.value
  )
})

// Display state (matches SessionItem logic) - now uses live data
const displayState = computed(() => {
  const data = minionWithLiveData.value
  if (!data) return 'created'

  // Special case: PAUSED + processing = waiting for permission response (yellow blinking)
  if (data.state === 'paused' && data.is_processing) {
    return 'pending-prompt'
  }
  // Normal case: processing overrides state (purple blinking)
  return data.is_processing ? 'processing' : (data.state || 'created')
})

// Status dot CSS classes (matches SessionItem)
const statusDotClass = computed(() => {
  const state = displayState.value
  const classMap = {
    'created': 'status-dot-grey',
    'starting': 'status-dot-green status-blinking',
    'active': 'status-dot-green',
    'running': 'status-dot-green',
    'processing': 'status-dot-purple status-blinking',
    'paused': 'status-dot-grey',
    'pending-prompt': 'status-dot-yellow status-blinking',
    'terminated': 'status-dot-grey',
    'error': 'status-dot-red',
    'failed': 'status-dot-red'
  }
  return classMap[state] || 'status-dot-grey'
})

// Status dot inline styles (matches SessionItem)
const statusDotStyle = computed(() => {
  const state = displayState.value
  const bgColorMap = {
    'created': '#d3d3d3',
    'starting': '#90ee90',
    'active': '#90ee90',
    'running': '#90ee90',
    'processing': '#dda0dd',
    'paused': '#d3d3d3',
    'pending-prompt': '#ffc107',
    'terminated': '#d3d3d3',
    'error': '#ffb3b3',
    'failed': '#ffb3b3'
  }
  return {
    backgroundColor: bgColorMap[state] || '#d3d3d3'
  }
})

// Latest message display (issue #291) - now uses live data
const messagePrefix = computed(() => {
  const data = minionWithLiveData.value
  if (!data || !data.latest_message_type) return ''
  const type = data.latest_message_type
  if (type === 'user') return 'User -->:'
  if (type === 'system') return 'System:'
  return ''  // Assistant messages have no prefix
})

const truncatedMessage = computed(() => {
  const data = minionWithLiveData.value
  if (!data || !data.latest_message) return ''
  const msg = data.latest_message
  const maxLen = 150  // Same as comm summary length
  return msg.length > maxLen ? msg.slice(0, maxLen) + '...' : msg
})

const relativeTime = computed(() => {
  const data = minionWithLiveData.value
  if (!data || !data.latest_message_time) return ''

  try {
    const now = Date.now()
    let msgTime

    // Handle both ISO string and numeric timestamp formats
    if (typeof data.latest_message_time === 'number') {
      // If timestamp is in seconds (< year 3000 in milliseconds), convert to milliseconds
      msgTime = data.latest_message_time < 32503680000
        ? data.latest_message_time * 1000
        : data.latest_message_time
    } else {
      // Backend sends ISO string, parse it
      msgTime = new Date(data.latest_message_time).getTime()
    }

    // Validate the parsed time is reasonable (not from 1970 and not in future)
    if (isNaN(msgTime) || msgTime < 946684800000 || msgTime > now + 60000) { // Jan 1, 2000 to 1 min in future
      console.warn('Invalid timestamp for relative time calculation:', data.latest_message_time, 'parsed to:', msgTime)
      return ''
    }

    const diffMs = now - msgTime

    // If negative (future time), treat as "just now"
    if (diffMs < 0) return 'just now'

    const minutes = Math.floor(diffMs / 60000)
    const hours = Math.floor(minutes / 60)
    const days = Math.floor(hours / 24)

    if (days > 0) return `${days}d ago`
    if (hours > 0) return `${hours}h ago`
    if (minutes > 0) return `${minutes}m ago`
    return 'just now'
  } catch (e) {
    console.error('Error calculating relative time:', e, data.latest_message_time)
    return ''
  }
})

// Helper: Get comm recipient name
function getCommRecipient(comm) {
  if (comm.to_user) {
    return 'User'
  } else if (comm.to_minion_name) {
    return comm.to_minion_name
  }
  return 'unknown'
}

// Helper: Get comm summary (prioritize summary, fallback to content, truncate at 150 chars)
function getCommSummary(comm) {
  const text = comm.summary || comm.content || ''
  if (text.length > 150) {
    return text.substring(0, 150) + '...'
  }
  return text
}

// Handle minion card click - emit event to parent
function handleClick() {
  emit('minion-click', props.minionData.id)
}

// Action button handlers (Issue #296)
function showEditModal() {
  const session = sessionStore.sessions.get(props.minionData.id)
  if (session) {
    uiStore.showModal('edit-session', { session })
  }
}

function showManageModal() {
  const session = sessionStore.sessions.get(props.minionData.id)
  if (session) {
    uiStore.showModal('manage-session', { session })
  }
}
</script>

<style scoped>
.minion-card {
  background-color: white;
  border-color: #dee2e6;
}

.minion-tree-node {
  transition: margin-left 0.2s ease;
}

/* Sidebar Layout Styles */
.minion-info {
  min-width: 0; /* Allow text truncation */
  overflow: hidden;
}

.minion-name {
  font-size: 0.95rem;
  font-weight: 500;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.latest-activity {
  font-size: 0.85rem;
  line-height: 1.3;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  margin-top: 0.25rem;
}

.activity-prefix {
  color: #6c757d;
  font-size: 0.8rem;
}

.activity-content {
  font-style: italic;
}

.activity-time {
  color: #6c757d;
  font-size: 0.75rem;
  opacity: 0.8;
}

/* Two-Column Layout Styles (for HierarchyView) */
.node-row {
  display: flex;
  gap: 1rem;
  align-items: flex-start;
  padding: 0.5rem 0.75rem;
}

.node-left {
  flex: 0 0 30%;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 0.25rem;
}

.node-right {
  flex: 1;
  min-width: 0; /* Allow text truncation */
  padding-left: 1rem;
  border-left: 1px solid #dee2e6;
}

.node-actions {
  flex: 0 0 auto;
  display: flex;
  gap: 0.25rem;
  align-items: flex-start;
  margin-left: 0.5rem;
}

/* Latest message preview (two-column layout) */
.latest-message-preview {
  font-size: 0.9rem;
  line-height: 1.4;
}

.message-prefix {
  color: #6c757d;
  font-size: 0.85rem;
  font-weight: 500;
}

.message-content {
  color: #212529;
  word-wrap: break-word;
  font-style: italic;
  cursor: help;
}

.message-time {
  color: #6c757d;
  font-size: 0.8rem;
  opacity: 0.8;
}

/* Comm preview (two-column layout) */
.last-comm-preview {
  font-size: 0.9rem;
  line-height: 1.4;
}

.comm-direction {
  color: #6c757d;
  font-size: 0.85rem;
}

.comm-content {
  color: #212529;
  word-wrap: break-word;
  cursor: help;
}

/* Status dot styles - matches SessionItem.vue */
.status-dot {
  width: 12px;
  height: 12px;
  border-radius: 50%;
  border: 2px solid;
  flex-shrink: 0;
}

.status-dot-grey {
  background-color: #d3d3d3;
  border-color: #6c757d;
}

.status-dot-green {
  background-color: #90ee90;
  border-color: #28a745;
}

.status-dot-purple {
  background-color: #dda0dd;
  border-color: #6f42c1;
}

.status-dot-red {
  background-color: #ffb3b3;
  border-color: #dc3545;
}

.status-dot-yellow {
  background-color: #ffc107;
  border-color: #e0a800;
}

.status-blinking {
  animation: blink 1.5s infinite;
}

@keyframes blink {
  0%, 100% {
    opacity: 1;
  }
  50% {
    opacity: 0.3;
  }
}

/* Clickable minion card styles */
.minion-card-clickable {
  cursor: pointer;
  transition: background-color 0.2s ease;
}

.minion-card-clickable:hover {
  background-color: rgba(13, 110, 253, 0.1);
}

.minion-card-clickable.active {
  background-color: #0d6efd;
  color: white;
}

.minion-card-clickable.active .activity-prefix,
.minion-card-clickable.active .activity-time,
.minion-card-clickable.active .message-prefix,
.minion-card-clickable.active .message-time {
  color: rgba(255, 255, 255, 0.8);
}

.minion-card-clickable.active .activity-content,
.minion-card-clickable.active .message-content {
  color: white;
}
</style>
