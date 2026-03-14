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
        :class="{ expandable: hasExpandableContent(comm) }"
        :style="commStyle(comm)"
        @click="hasExpandableContent(comm) && toggleExpand(comm.comm_id)"
      >
        <!-- Header: icon + type + direction + time + chevron -->
        <div class="comm-meta">
          <span class="comm-icon">{{ commIcon(comm.comm_type) }}</span>
          <span class="comm-type">{{ capitalize(comm.comm_type) }}</span>
          <span class="comm-direction">{{ commDirection(comm) }}</span>
          <span class="comm-time">{{ formatTimestamp(comm.timestamp) }}</span>
          <svg
            v-if="hasExpandableContent(comm)"
            class="expand-chevron"
            :class="{ expanded: expandedComms.has(comm.comm_id) }"
            width="12" height="12" viewBox="0 0 12 12" fill="none"
          >
            <path d="M4.5 2.5L8 6L4.5 9.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
        </div>

        <!-- Summary -->
        <div class="comm-summary">{{ comm.summary || truncate(comm.content, 120) }}</div>

        <!-- Expanded content -->
        <div v-if="expandedComms.has(comm.comm_id)" class="comm-content" v-html="renderMarkdown(comm.content)"></div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, nextTick } from 'vue'
import { useSessionStore } from '@/stores/session'
import { useLegionStore } from '@/stores/legion'
import { useWebSocketStore } from '@/stores/websocket'
import { formatTimestamp } from '@/utils/time'
import { getAgentColor, slugifyAgentName } from '@/composables/useAgentColor'
import { renderMarkdown } from '@/composables/useMarkdown'
import { useMermaid } from '@/composables/useMermaid'
import { useResourceImages } from '@/composables/useResourceImages'

const sessionStore = useSessionStore()
const legionStore = useLegionStore()
const wsStore = useWebSocketStore()

const commsList = ref(null)
const expandedComms = ref(new Set())

// Reserved minion IDs
const SYSTEM_MINION_ID = 'ffffffff-ffff-ffff-ffff-ffffffffffff'
const USER_MINION_ID = '00000000-0000-0000-0000-000000000000'

// Mermaid diagram rendering for expanded comm content
useMermaid(commsList)

// Current project (legion) ID from the active session
const projectId = computed(() => sessionStore.currentSession?.project_id || null)

// Current session ID (used to identify outbound comms from this session/minion)
const currentSessionId = computed(() => sessionStore.currentSessionId)

// Inline resource image click-to-open
useResourceImages(commsList, currentSessionId)

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

// Determine comm card style based on sender identity
function commStyle(comm) {
  const senderId = comm.from_minion_id

  // System comms: muted gray
  if (senderId === SYSTEM_MINION_ID) {
    const c = getAgentColor('system')
    return { background: c.bg, borderColor: c.border, opacity: 0.8 }
  }

  // User comms: indigo tint (matches user chat bubble)
  if (comm.from_user || senderId === USER_MINION_ID) {
    const c = getAgentColor('user')
    return { background: c.bg, borderColor: c.border }
  }

  // Active session outbound: assistant colors (matches assistant chat bubble)
  if (senderId === currentSessionId.value) {
    return { background: '#f8fafc', borderColor: '#e2e8f0' }
  }

  // Other minions: unique color per sender name
  const session = sessionStore.sessions.get(senderId)
  const name = session?.name || comm.from_minion_name || 'unknown'
  const slug = slugifyAgentName(name)
  const color = getAgentColor(slug)
  return { background: color.bg, borderColor: color.border }
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

function hasExpandableContent(comm) {
  return comm.content && comm.content !== comm.summary
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

  // Always ensure current legion is set in store
  legionStore.setCurrentLegion(legionId)

  // Load timeline data if not already loaded for this legion
  const existingComms = legionStore.commsByLegion.get(legionId)
  if (!existingComms) {
    legionStore.loadTimeline(legionId)
  }

  // Connect WebSocket if not already connected to this legion
  if (!wsStore.legionConnected || wsStore.currentLegionId !== legionId) {
    wsStore.connectLegion(legionId)
  }
}

// Watch project changes
watch(projectId, (newId) => {
  if (newId) {
    connectToLegion(newId)
  }
}, { immediate: true })

// Reload timeline when legion WebSocket reconnects
watch(() => wsStore.legionConnected, (connected) => {
  if (connected && projectId.value) {
    legionStore.loadTimeline(projectId.value)
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
  white-space: nowrap;
}

.comm-summary {
  color: #1e293b;
  font-size: 12px;
  line-height: 1.4;
  word-wrap: break-word;
}

.comm-item.expandable {
  cursor: pointer;
}

.comm-item.expandable:hover {
  filter: brightness(0.97);
}

.expand-chevron {
  flex-shrink: 0;
  margin-left: auto;
  color: #94a3b8;
  transition: transform 0.2s;
}

.expand-chevron.expanded {
  transform: rotate(90deg);
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
