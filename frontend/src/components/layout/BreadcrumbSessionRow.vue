<template>
  <div>
    <div
      class="bc-session-card"
      :class="{ current: isCurrent, child: depth > 0 }"
      role="button"
      :aria-label="`Select agent ${displayName}${isUnreviewed ? ' (new since last viewed)' : ''}`"
      :title="chipTooltip"
      @click="handleClick"
    >
      <span
        v-if="hasChildren"
        class="chip-expand"
        role="button"
        :aria-label="isExpanded ? 'Collapse children' : 'Expand children'"
        @click.stop="toggleExpand"
      >
        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" :style="{ transform: isExpanded ? 'rotate(90deg)' : 'none' }"><polyline points="9 6 15 12 9 18"></polyline></svg>
      </span>
      <span v-else class="chip-expand"></span>

      <span class="status-sq" :class="[statusClass, { unread: isUnreviewed }]"></span>

      <span class="chip-text">
        <span class="chip-name">{{ displayName }}</span>
        <span v-if="sdkTitle" class="chip-sub">{{ sdkTitle }}</span>
        <span v-else-if="roleDescription" class="chip-sub">{{ roleDescription }}</span>
      </span>

      <span v-if="alertType" class="chip-alert" :class="alertType">{{ alertType === 'error' ? '!' : '?' }}</span>
      <span v-if="hasSchedules" class="chip-schedule" title="Has active schedules">
        <svg width="9" height="9" viewBox="0 0 16 16" fill="currentColor"><path d="M8 0a8 8 0 1 0 0 16A8 8 0 0 0 8 0zm0 14.5A6.5 6.5 0 1 1 8 1.5a6.5 6.5 0 0 1 0 13zM8.5 4H7v5l4.25 2.55.75-1.23L8.5 8.25V4z"/></svg>
      </span>
      <span v-if="effectiveDockerEnabled" class="chip-docker" title="Running with Docker isolation">
        <svg width="9" height="9" viewBox="0 0 16 16" fill="currentColor">
          <rect x="1" y="4" width="5" height="4" rx="0.5" stroke="currentColor" stroke-width="0.8" fill="none"/>
          <rect x="1" y="8" width="14" height="5" rx="1" stroke="currentColor" stroke-width="0.8" fill="none"/>
        </svg>
      </span>
    </div>

    <div v-if="hasChildren && isExpanded" class="bc-session-children">
      <BreadcrumbSessionRow
        v-for="childId in sortedChildIds"
        :key="childId"
        :session="sessionStore.getSession(childId)"
        :depth="depth + 1"
        @select="$emit('select', $event)"
      />
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useSessionStore } from '@/stores/session'
import { useScheduleStore } from '@/stores/schedule'
import { useUIStore } from '@/stores/ui'
import { useSessionState } from '@/composables/useSessionState'
import { compareAgents } from '@/utils/agentSort'

defineOptions({ name: 'BreadcrumbSessionRow' })

const props = defineProps({
  session: { type: Object, required: true },
  depth: { type: Number, default: 0 }
})

const emit = defineEmits(['select'])

const sessionStore = useSessionStore()
const scheduleStore = useScheduleStore()
const uiStore = useUIStore()
const { isError, isPaused } = useSessionState(computed(() => props.session))

const isCurrent = computed(() => props.session.session_id === sessionStore.currentSessionId)

const displayName = computed(() => props.session.name || props.session.role || 'Agent')
const sdkTitle = computed(() => props.session.sdk_generated_name || null)
const roleDescription = computed(() => {
  const role = props.session.role
  if (!role || role === displayName.value) return null
  return role
})

const statusClass = computed(() => {
  const state = props.session.state
  if (state === 'error') return 'error'
  if (state === 'starting') return 'starting'
  if (state === 'active' && props.session.is_processing) return 'active-processing'
  if (state === 'active') return 'active'
  if (state === 'paused') return 'paused'
  if (state === 'terminating') return 'terminating'
  if (state === 'terminated') return 'terminated'
  return 'created'
})

const statusText = computed(() => {
  const state = props.session.state
  if (state === 'active' && props.session.is_processing) return 'Processing...'
  if (state === 'active') return 'Idle'
  if (state === 'paused') return 'Awaiting input'
  if (state === 'error') return 'Error'
  if (state === 'terminated') return 'Stopped'
  if (state === 'starting') return 'Starting...'
  if (state === 'created') return 'Ready'
  return state || 'Unknown'
})

const alertType = computed(() => {
  if (isError.value) return 'error'
  if (isPaused.value) return 'permission'
  return null
})

const isUnreviewed = computed(() => sessionStore.isUnreviewed(props.session.session_id))

const hasSchedules = computed(() => scheduleStore.getScheduleCount(props.session.session_id) > 0)

const effectiveDockerEnabled = computed(() => {
  if (props.session.config?.docker_enabled === true) return true
  const ec = sessionStore.effectiveConfigBySession.get(props.session.session_id)
  return ec?.docker_enabled === true
})

const childIds = computed(() =>
  (props.session.child_minion_ids || []).filter(id => sessionStore.getSession(id))
)

const hasChildren = computed(() => childIds.value.length > 0)

const sortedChildIds = computed(() => {
  const mode = uiStore.agentSort
  return [...childIds.value].sort((a, b) => {
    const sa = sessionStore.getSession(a)
    const sb = sessionStore.getSession(b)
    return compareAgents(mode, sa, sb, {
      nameOf: s => s.name,
      orderOf: s => s.order,
      idOf: s => s.session_id
    })
  })
})

// Auto-expand when the active session is a descendant, even if not manually expanded —
// mirrors StackedChip's hasActiveDescendant fallback so the current session is never hidden.
const hasActiveDescendant = computed(() => {
  const activeId = sessionStore.currentSessionId
  if (!activeId) return false
  function checkDescendants(ids) {
    for (const id of ids) {
      if (id === activeId) return true
      const child = sessionStore.getSession(id)
      if (child?.child_minion_ids && checkDescendants(child.child_minion_ids)) return true
    }
    return false
  }
  return checkDescendants(childIds.value)
})

const isExpanded = computed(() =>
  uiStore.expandedStacks.has(props.session.session_id) || hasActiveDescendant.value
)

function toggleExpand() {
  uiStore.toggleStack(props.session.session_id)
}

const chipTooltip = computed(() => {
  const parts = [displayName.value]
  if (props.session.role && props.session.role !== displayName.value) parts.push(`Role: ${props.session.role}`)
  if (props.session.sdk_generated_name) parts.push(`Title: ${props.session.sdk_generated_name}`)
  parts.push(`Status: ${statusText.value}`)
  return parts.join('\n')
})

function handleClick() {
  emit('select', props.session.session_id)
}
</script>

<style scoped>
.bc-session-card {
  position: relative;
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 7px 10px;
  margin-bottom: 4px;
  border-radius: 3px;
  border: 1px solid var(--bs-border-color);
  background: var(--bs-tertiary-bg);
  cursor: pointer;
  transition: border-color 0.15s, background 0.15s;
}

.bc-session-card:hover {
  border-color: #93c5fd;
  background: var(--bs-secondary-bg);
}

.bc-session-card.current {
  border-color: #3b82f6;
  box-shadow: 0 0 0 1px #3b82f6;
}

.bc-session-card.child {
  background: var(--bs-secondary-bg);
}

.chip-expand {
  width: 14px;
  height: 14px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--bs-secondary-color);
  flex-shrink: 0;
}

.status-sq {
  width: 10px;
  height: 10px;
  border-radius: 2px;
  flex-shrink: 0;
}

.status-sq.created      { background: #94a3b8; }
.status-sq.starting     { background: #3b82f6; }
.status-sq.active       { background: #22c55e; }
.status-sq.active-processing { background: #8b5cf6; }
.status-sq.paused       { background: #f59e0b; }
.status-sq.terminating  { background: #f97316; }
.status-sq.terminated   { background: #cbd5e1; }
.status-sq.error        { background: #ef4444; }
.status-sq.unread       { background: var(--color-unread); }

.chip-text {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  min-width: 0;
}

.chip-name {
  font-size: 12.5px;
  font-weight: 500;
  color: var(--bs-emphasis-color);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chip-sub {
  font-size: 10.5px;
  color: var(--bs-secondary-color);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chip-alert {
  flex-shrink: 0;
  width: 15px;
  height: 15px;
  border-radius: 50%;
  font-size: 9px;
  font-weight: 700;
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
}

.chip-alert.error { background: #ef4444; }
.chip-alert.permission { background: #f59e0b; }

.chip-schedule, .chip-docker {
  flex-shrink: 0;
  width: 14px;
  height: 14px;
  border-radius: 50%;
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
}

.chip-schedule { background: #6366f1; }
.chip-docker { background: #0db7ed; }

.bc-session-children {
  margin-left: 18px;
  padding-left: 10px;
  border-left: 1px dashed var(--bs-border-color);
}
</style>
