<template>
  <div class="agent-overview" v-if="session">
    <!-- Agent Identity -->
    <div class="overview-identity">
      <div class="overview-avatar" :style="{ background: avatarColor }">
        {{ avatarLetter }}
      </div>
      <div class="overview-info">
        <div class="overview-name">{{ session.name || 'Agent' }}</div>
        <div class="overview-role" v-if="session.role">{{ session.role }}</div>
        <span class="overview-status-badge" :class="statusClass">{{ statusLabel }}</span>
      </div>
    </div>

    <!-- Stats Grid -->
    <div class="overview-stats">
      <div class="stat-item">
        <span class="stat-value">{{ messageCount }}</span>
        <span class="stat-label">Messages</span>
      </div>
      <div class="stat-item">
        <span class="stat-value">{{ toolCallCount }}</span>
        <span class="stat-label">Tool Calls</span>
      </div>
      <div class="stat-item">
        <span class="stat-value">{{ childCount }}</span>
        <span class="stat-label">Children</span>
      </div>
      <div class="stat-item">
        <span class="stat-value">{{ uptime }}</span>
        <span class="stat-label">Uptime</span>
      </div>
    </div>

    <!-- Action Buttons -->
    <div class="overview-actions">
      <button class="btn-overview btn-overview-primary" @click="editSession" title="Edit session settings">
        Edit
      </button>
      <button class="btn-overview" @click="manageSession" title="Manage session lifecycle">
        Manage
      </button>
      <button class="btn-overview" @click="showInfo" title="View session details">
        Info
      </button>
    </div>
  </div>
  <div class="agent-overview agent-overview-empty" v-else>
    <span class="empty-text">No session selected</span>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useSessionStore } from '@/stores/session'
import { useMessageStore } from '@/stores/message'
import { useUIStore } from '@/stores/ui'

const sessionStore = useSessionStore()
const messageStore = useMessageStore()
const uiStore = useUIStore()

const now = ref(Date.now())
let interval = null

onMounted(() => {
  interval = setInterval(() => { now.value = Date.now() }, 10000)
})

onUnmounted(() => {
  if (interval) clearInterval(interval)
})

const session = computed(() => {
  const id = sessionStore.currentSessionId
  if (!id) return null
  return sessionStore.getSession(id)
})

const avatarLetter = computed(() => {
  const name = session.value?.name || 'A'
  return name.charAt(0).toUpperCase()
})

const avatarColor = computed(() => {
  const name = session.value?.name || ''
  let hash = 0
  for (let i = 0; i < name.length; i++) {
    hash = name.charCodeAt(i) + ((hash << 5) - hash)
  }
  const hue = Math.abs(hash % 360)
  return `hsl(${hue}, 60%, 55%)`
})

const statusClass = computed(() => {
  if (!session.value) return 'status-none'
  const state = session.value.state
  if (state === 'active' && session.value.is_processing) return 'status-active'
  if (state === 'active') return 'status-idle'
  if (state === 'paused') return 'status-waiting'
  if (state === 'error') return 'status-error'
  if (state === 'terminated') return 'status-terminated'
  return 'status-none'
})

const statusLabel = computed(() => {
  if (!session.value) return 'None'
  const state = session.value.state
  if (state === 'active' && session.value.is_processing) return 'Processing'
  if (state === 'active') return 'Idle'
  if (state === 'paused') return 'Paused'
  if (state === 'error') return 'Error'
  if (state === 'terminated') return 'Terminated'
  if (state === 'created') return 'Created'
  if (state === 'starting') return 'Starting'
  return state
})

const messageCount = computed(() => {
  const id = sessionStore.currentSessionId
  if (!id) return 0
  const msgs = messageStore.messagesBySession.get(id)
  if (!msgs) return 0
  return msgs.filter(m => m.type === 'user' || m.type === 'assistant').length
})

const toolCallCount = computed(() => {
  const id = sessionStore.currentSessionId
  if (!id) return 0
  const tools = messageStore.toolCallsBySession.get(id)
  return tools ? tools.length : 0
})

const childCount = computed(() => {
  if (!session.value) return 0
  return session.value.child_minion_ids?.length || 0
})

const uptime = computed(() => {
  if (!session.value?.started_at) return '--'
  const _ = now.value // trigger reactivity
  const start = new Date(session.value.started_at).getTime()
  const diff = Date.now() - start
  if (diff < 0) return '--'
  const mins = Math.floor(diff / 60000)
  if (mins < 60) return `${mins}m`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours}h ${mins % 60}m`
  return `${Math.floor(hours / 24)}d ${hours % 24}h`
})

function editSession() {
  if (session.value) {
    uiStore.showModal('edit-session', { session: session.value })
  }
}

function manageSession() {
  if (session.value) {
    uiStore.showModal('manage-session', { session: session.value })
  }
}

function showInfo() {
  if (session.value) {
    uiStore.showModal('session-info', { sessionId: session.value.session_id })
  }
}
</script>

<style scoped>
.agent-overview {
  padding: 12px;
  border-bottom: 1px solid #e2e8f0;
  flex-shrink: 0;
}

.agent-overview-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 16px;
}

.empty-text {
  font-size: 12px;
  color: #94a3b8;
  font-style: italic;
}

/* Identity Row */
.overview-identity {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
}

.overview-avatar {
  width: 40px;
  height: 40px;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 18px;
  font-weight: 700;
  color: white;
  flex-shrink: 0;
}

.overview-info {
  min-width: 0;
}

.overview-name {
  font-size: 16px;
  font-weight: 700;
  color: #1e293b;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.overview-role {
  font-size: 11px;
  color: #64748b;
  margin-bottom: 2px;
}

.overview-status-badge {
  display: inline-block;
  font-size: 10px;
  font-weight: 600;
  padding: 1px 8px;
  border-radius: 10px;
  text-transform: uppercase;
  letter-spacing: 0.3px;
}

.status-active { background: #dcfce7; color: #166534; }
.status-idle { background: #fef9c3; color: #854d0e; }
.status-waiting { background: #ffedd5; color: #9a3412; }
.status-error { background: #fee2e2; color: #991b1b; }
.status-terminated { background: #f1f5f9; color: #475569; }
.status-none { background: #f1f5f9; color: #94a3b8; }

/* Stats Grid */
.overview-stats {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 4px;
  margin-bottom: 10px;
}

.stat-item {
  text-align: center;
  padding: 4px 2px;
  background: #f8fafc;
  border-radius: 4px;
}

.stat-value {
  display: block;
  font-size: 14px;
  font-weight: 700;
  color: #1e293b;
}

.stat-label {
  display: block;
  font-size: 9px;
  color: #94a3b8;
  text-transform: uppercase;
  letter-spacing: 0.3px;
}

/* Action Buttons */
.overview-actions {
  display: flex;
  gap: 6px;
}

.btn-overview {
  flex: 1;
  padding: 4px 8px;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  background: white;
  color: #475569;
  font-size: 11px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s;
  text-align: center;
}

.btn-overview:hover {
  background: #f1f5f9;
  border-color: #cbd5e1;
}

.btn-overview-primary {
  background: #3b82f6;
  border-color: #3b82f6;
  color: white;
}

.btn-overview-primary:hover {
  background: #2563eb;
}
</style>
