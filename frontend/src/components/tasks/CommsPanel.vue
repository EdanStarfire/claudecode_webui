<template>
  <div class="comms-panel">
    <!-- Empty state -->
    <div v-if="comms.length === 0" class="empty-placeholder">
      <span>Communication between this session's minions will appear here</span>
    </div>

    <!-- Comms list -->
    <div v-else class="comms-list" ref="commsList">
      <div
        v-for="comm in comms"
        :key="comm.comm_id"
        class="comm-item"
        :style="commStyle(comm)"
      >
        <!-- Header: icon + type + direction + time -->
        <div class="comm-meta">
          <span class="comm-icon">{{ commIcon(comm.comm_type) }}</span>
          <span class="comm-type">{{ capitalize(comm.comm_type) }}</span>
          <span class="comm-direction">{{ commDirection(comm) }}</span>
          <span class="comm-time">{{ formatTimestamp(comm.timestamp) }}</span>
        </div>

        <!-- Summary -->
        <div class="comm-summary">{{ comm.summary || truncate(comm.content, 120) }}</div>

        <!-- Expandable content -->
        <div
          v-if="comm.content && comm.content !== comm.summary"
          class="comm-expand"
          @click="toggleExpand(comm.comm_id)"
        >
          <span class="expand-chevron" :class="{ expanded: expandedComms.has(comm.comm_id) }">&#x25B6;</span>
          <span class="expand-label">{{ expandedComms.has(comm.comm_id) ? 'Hide details' : 'Show details' }}</span>
        </div>
        <div v-if="expandedComms.has(comm.comm_id)" class="comm-content" v-html="renderMarkdown(comm.content)"></div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
import { useSessionStore } from '@/stores/session'
import { useLegionStore } from '@/stores/legion'
import { useWebSocketStore } from '@/stores/websocket'
import { formatTimestamp } from '@/utils/time'
import DOMPurify from 'dompurify'
import { marked } from 'marked'

const sessionStore = useSessionStore()
const legionStore = useLegionStore()
const wsStore = useWebSocketStore()

const commsList = ref(null)
const expandedComms = ref(new Set())
let connectedLegionId = null

// Reserved minion IDs
const SYSTEM_MINION_ID = 'ffffffff-ffff-ffff-ffff-ffffffffffff'
const USER_MINION_ID = '00000000-0000-0000-0000-000000000000'

// Configure marked
marked.setOptions({ breaks: true, gfm: true })

// Current project (legion) ID from the active session
const projectId = computed(() => sessionStore.currentSession?.project_id || null)

// Current session ID (used to identify outbound comms from this session/minion)
const currentSessionId = computed(() => sessionStore.currentSessionId)

// Comms involving the current session (as sender or recipient)
const comms = computed(() => {
  if (!projectId.value) return []
  const allComms = legionStore.commsByLegion.get(projectId.value) || []
  const sessionId = currentSessionId.value
  if (!sessionId) return []
  return allComms.filter(c =>
    c.from_minion_id === sessionId || c.to_minion_id === sessionId
  )
})

// Color palette for other minions (pastel tints)
const MINION_COLORS = [
  { bg: '#fef3c7', border: '#fde68a' }, // amber
  { bg: '#fce7f3', border: '#fbcfe8' }, // pink
  { bg: '#e0e7ff', border: '#c7d2fe' }, // indigo (lighter than user)
  { bg: '#d1fae5', border: '#a7f3d0' }, // emerald
  { bg: '#ede9fe', border: '#ddd6fe' }, // violet
  { bg: '#fee2e2', border: '#fecaca' }, // red
  { bg: '#cffafe', border: '#a5f3fc' }, // cyan
  { bg: '#fef9c3', border: '#fef08a' }, // yellow
]

// Stable color assignment per sender name
const senderColorMap = new Map()

function getColorForSender(senderId) {
  if (!senderId) return null
  if (senderColorMap.has(senderId)) return senderColorMap.get(senderId)

  // Simple hash to pick a consistent color index
  let hash = 0
  for (let i = 0; i < senderId.length; i++) {
    hash = ((hash << 5) - hash + senderId.charCodeAt(i)) | 0
  }
  const index = Math.abs(hash) % MINION_COLORS.length
  const color = MINION_COLORS[index]
  senderColorMap.set(senderId, color)
  return color
}

// Determine comm card style based on sender identity
function commStyle(comm) {
  const senderId = comm.from_minion_id

  // System comms: muted gray
  if (senderId === SYSTEM_MINION_ID) {
    return { background: '#f1f5f9', borderColor: '#e2e8f0', opacity: 0.8 }
  }

  // User comms: indigo tint (matches user chat bubble)
  if (comm.from_user || senderId === USER_MINION_ID) {
    return { background: '#eef2ff', borderColor: '#e0e7ff' }
  }

  // Active session outbound: assistant colors (matches assistant chat bubble)
  if (senderId === currentSessionId.value) {
    return { background: '#f8fafc', borderColor: '#e2e8f0' }
  }

  // Other minions: unique color per sender
  const color = getColorForSender(senderId)
  if (color) {
    return { background: color.bg, borderColor: color.border }
  }

  // Fallback
  return { background: '#f8fafc', borderColor: '#e2e8f0' }
}

// Icon mapping
function commIcon(type) {
  const icons = {
    task: '\u{1F4CB}',
    question: '\u{2753}',
    report: '\u{1F4CA}',
    info: '\u{1F4A1}',
    system: '\u{2699}\u{FE0F}',
    halt: '\u{1F6D1}',
    pivot: '\u{1F6D1}',
    spawn: '\u{1F31F}',
    dispose: '\u{1F5D1}\u{FE0F}',
    thought: '\u{1F4AD}'
  }
  return icons[type] || '\u{1F4AC}'
}

function capitalize(str) {
  if (!str) return ''
  return str.charAt(0).toUpperCase() + str.slice(1)
}

function commDirection(comm) {
  const from = comm.from_minion_name || (comm.from_user ? 'User' : 'System')
  const to = comm.to_minion_name || (comm.to_user ? 'User' : 'Unknown')
  return `${from} \u{2192} ${to}`
}

function truncate(text, max) {
  if (!text) return ''
  if (text.length <= max) return text
  return text.substring(0, max) + '...'
}

function toggleExpand(commId) {
  if (expandedComms.value.has(commId)) {
    expandedComms.value.delete(commId)
  } else {
    expandedComms.value.add(commId)
  }
  expandedComms.value = new Set(expandedComms.value)
}

function renderMarkdown(content) {
  if (!content) return ''
  let html = marked.parse(content)
  html = html.replace(/\n</g, '<')
  html = html.replace(/\n+$/, '')
  return DOMPurify.sanitize(html)
}

// Auto-scroll when new comms arrive
watch(() => comms.value.length, async () => {
  await nextTick()
  if (commsList.value) {
    commsList.value.scrollTop = commsList.value.scrollHeight
  }
})

// Connect to legion when project changes
function connectToLegion(legionId) {
  if (!legionId) return
  if (connectedLegionId === legionId) return

  // Disconnect previous
  if (connectedLegionId) {
    wsStore.disconnectLegion()
  }

  connectedLegionId = legionId
  legionStore.setCurrentLegion(legionId)
  legionStore.loadTimeline(legionId)
  wsStore.connectLegion(legionId)
}

watch(projectId, (newId) => {
  if (newId) {
    connectToLegion(newId)
  }
}, { immediate: true })

onMounted(() => {
  if (projectId.value) {
    connectToLegion(projectId.value)
  }
})

onUnmounted(() => {
  if (connectedLegionId) {
    wsStore.disconnectLegion()
    connectedLegionId = null
  }
})
</script>

<style scoped>
.comms-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.empty-placeholder {
  text-align: center;
  padding: 32px 16px;
  color: #94a3b8;
  font-size: 12px;
  font-style: italic;
}

.comms-list {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  padding: 8px;
}

.comm-item {
  padding: 8px 10px;
  margin-bottom: 6px;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  font-size: 12px;
}

.comm-meta {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-wrap: wrap;
  margin-bottom: 4px;
}

.comm-icon {
  font-size: 11px;
  flex-shrink: 0;
}

.comm-type {
  font-weight: 600;
  color: #334155;
  font-size: 11px;
}

.comm-direction {
  color: #64748b;
  font-size: 11px;
}

.comm-time {
  color: #94a3b8;
  font-size: 10px;
  margin-left: auto;
  white-space: nowrap;
}

.comm-summary {
  color: #1e293b;
  font-size: 12px;
  line-height: 1.4;
  word-wrap: break-word;
}

.comm-expand {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-top: 4px;
  cursor: pointer;
  color: #64748b;
  font-size: 10px;
}

.comm-expand:hover {
  color: #334155;
}

.expand-chevron {
  font-size: 8px;
  transition: transform 0.2s;
}

.expand-chevron.expanded {
  transform: rotate(90deg);
}

.expand-label {
  font-size: 10px;
}

.comm-content {
  margin-top: 6px;
  padding-top: 6px;
  border-top: 1px solid rgba(0, 0, 0, 0.08);
  color: #334155;
  font-size: 12px;
  line-height: 1.5;
  word-wrap: break-word;
}

.comm-content :deep(pre) {
  background: rgba(0, 0, 0, 0.04);
  padding: 8px;
  border-radius: 4px;
  overflow-x: auto;
  margin: 4px 0;
  font-size: 11px;
}

.comm-content :deep(code) {
  background: rgba(0, 0, 0, 0.06);
  padding: 1px 4px;
  border-radius: 3px;
  font-family: 'Courier New', monospace;
  font-size: 11px;
}

.comm-content :deep(pre code) {
  background: transparent;
  padding: 0;
}

.comm-content :deep(p) {
  margin: 0 0 4px 0;
}

.comm-content :deep(p:last-child) {
  margin-bottom: 0;
}

.comm-content :deep(ul),
.comm-content :deep(ol) {
  padding-left: 1.2rem;
  margin: 4px 0;
}
</style>
