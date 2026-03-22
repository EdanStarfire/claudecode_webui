<template>
  <div
    class="agent-chip"
    :class="{
      active: isActive,
      child: variant === 'child',
      'parent-of-active': isParentOfActive,
      ghost: isGhost
    }"
    role="button"
    :aria-label="`Select agent ${displayName}`"
    @click="handleClick"
    @contextmenu.prevent="handleManage"
    @touchstart="onTouchStart"
    @touchmove="onTouchMove"
    @touchend="onTouchEnd"
    @touchcancel="onTouchCancel"
    :title="chipTooltip"
  >
    <!-- Letter icon (ghost uses faded style) -->
    <div v-if="!isGhost" class="ac-letter-icon" :class="statusClass">
      {{ letterIcon }}
    </div>
    <div v-else class="ac-letter-icon ghost">
      {{ letterIcon }}
    </div>

    <!-- Info stack -->
    <div class="ac-info">
      <div class="ac-name">{{ displayName }}</div>
      <div v-if="sdkTitle" class="ac-sdk-title">{{ sdkTitle }}</div>
      <div v-if="isGhost" class="ac-status">Deleted</div>
      <div v-else-if="roleDescription" class="ac-description">{{ roleDescription }}</div>
    </div>

    <!-- Ghost dismiss button -->
    <button
      v-if="isGhost"
      class="ac-dismiss"
      @click.stop="$emit('dismiss', session.session_id || session.agent_id)"
      title="Remove from strip"
      aria-label="Dismiss ghost agent"
    >&times;</button>

    <!-- Alert badge -->
    <div v-if="alertType && !isGhost" class="ac-alert" :class="alertType" :aria-label="alertType === 'error' ? 'Error alert' : 'Permission required'">
      {{ alertType === 'error' ? '!' : '?' }}
    </div>

    <!-- Schedule badge -->
    <div v-if="hasSchedules" class="ac-schedule-badge" title="Has active schedules" aria-label="Has active schedules">
      <svg width="10" height="10" viewBox="0 0 16 16" fill="currentColor">
        <path d="M8 0a8 8 0 1 0 0 16A8 8 0 0 0 8 0zm0 14.5A6.5 6.5 0 1 1 8 1.5a6.5 6.5 0 0 1 0 13zM8.5 4H7v5l4.25 2.55.75-1.23L8.5 8.25V4z"/>
      </svg>
    </div>

    <!-- Docker badge -->
    <div v-if="session.docker_enabled" class="ac-docker-badge" title="Running with Docker isolation" aria-label="Docker isolated">
      <svg width="10" height="10" viewBox="0 0 16 16" fill="currentColor">
        <rect x="1" y="4" width="5" height="4" rx="0.5" stroke="currentColor" stroke-width="0.8" fill="none"/>
        <rect x="1" y="8" width="14" height="5" rx="1" stroke="currentColor" stroke-width="0.8" fill="none"/>
        <line x1="5" y1="8" x2="5" y2="13" stroke="currentColor" stroke-width="0.6"/>
        <line x1="9" y1="8" x2="9" y2="13" stroke="currentColor" stroke-width="0.6"/>
      </svg>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useSessionStore } from '@/stores/session'
import { useScheduleStore } from '@/stores/schedule'
import { useUIStore } from '@/stores/ui'
import { useLongPress } from '@/composables/useLongPress'

const props = defineProps({
  session: { type: Object, required: true },
  isActive: { type: Boolean, default: false },
  isParentOfActive: { type: Boolean, default: false },
  variant: { type: String, default: 'default' }, // 'default' | 'child'
  isGhost: { type: Boolean, default: false }
})

const emit = defineEmits(['select', 'manage', 'dismiss'])

const sessionStore = useSessionStore()
const scheduleStore = useScheduleStore()
const uiStore = useUIStore()

function handleManage() {
  uiStore.showModal('manage-session', { session: props.session })
  emit('manage', props.session.session_id)
}

const { onTouchStart, onTouchMove, onTouchEnd, onTouchCancel, fired } = useLongPress(handleManage)

const hasSchedules = computed(() =>
  scheduleStore.getScheduleCount(props.session.session_id) > 0
)

const displayName = computed(() =>
  props.session.name || props.session.role || 'Agent'
)

const letterIcon = computed(() =>
  (props.session.name || props.session.role || 'A')[0].toUpperCase()
)

const sdkTitle = computed(() => props.session.sdk_generated_name || null)

const roleDescription = computed(() => {
  const role = props.session.role
  if (!role) return null
  // Avoid duplicating if role is the same as the display name
  if (role === displayName.value) return null
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
  if (props.session.state === 'error') return 'error'
  if (props.session.state === 'paused') return 'permission'
  return null
})

const chipTooltip = computed(() => {
  const parts = [displayName.value]
  if (props.session.role && props.session.role !== displayName.value) parts.push(`Role: ${props.session.role}`)
  if (props.session.sdk_generated_name) parts.push(`Title: ${props.session.sdk_generated_name}`)
  parts.push(`Status: ${statusText.value}`)
  if (props.session.model) parts.push(`Model: ${props.session.model}`)
  if (props.session.docker_enabled) parts.push('Docker isolated')
  return parts.join('\n')
})

function handleClick() {
  if (fired.value) {
    fired.value = false
    return
  }
  emit('select', props.session.session_id || props.session.agent_id)
}
</script>

<style scoped>
.agent-chip {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 8px 12px;
  border-radius: 10px;
  border: 1px solid #e2e8f0;
  background: #fff;
  cursor: pointer;
  transition: all 0.15s;
  position: relative;
  flex-shrink: 0;
}

.agent-chip:hover {
  border-color: #93c5fd;
  background: #f0f7ff;
}

.agent-chip.active {
  border-color: #3b82f6;
  background: #eff6ff;
  box-shadow: 0 0 0 1px #3b82f6;
}

.agent-chip.child {
  border-color: #e2e8f0;
  background: #f8fafc;
}

.agent-chip.child.active {
  border-color: #3b82f6;
  background: #eff6ff;
  box-shadow: 0 0 0 1px #3b82f6;
}

.agent-chip.parent-of-active {
  border-style: dashed;
  border-color: #93c5fd;
  box-shadow: none;
}

/* Letter icon */
.ac-letter-icon {
  width: 32px;
  height: 32px;
  border-radius: 6px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 14px;
  font-weight: 700;
  color: #fff;
  border: 2px solid transparent;
}

.ac-letter-icon.created      { background: #94a3b8; border-color: #94a3b8; }
.ac-letter-icon.starting     { background: #3b82f6; border-color: #3b82f6; animation: pulse-starting 1.2s infinite; }
.ac-letter-icon.active       { background: #22c55e; border-color: #22c55e; }
.ac-letter-icon.active-processing { background: #8b5cf6; border-color: #8b5cf6; }
.ac-letter-icon.paused       { background: #f59e0b; border-color: #f59e0b; }
.ac-letter-icon.terminating  { background: #f97316; border-color: #f97316; }
.ac-letter-icon.terminated   { background: #cbd5e1; border-color: #cbd5e1; color: #94a3b8; }
.ac-letter-icon.error        { background: #ef4444; border-color: #ef4444; animation: pulse-error 1.5s infinite; }
.ac-letter-icon.ghost        { background: #f1f5f9; border-color: #cbd5e1; color: #94a3b8; }

.ac-info {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.ac-name {
  font-size: 12px;
  font-weight: 600;
  color: #1e293b;
  line-height: 1.2;
  max-width: 150px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ac-sdk-title {
  font-size: 10px;
  color: #475569;
  font-style: italic;
  line-height: 1.3;
  max-width: 150px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ac-description {
  font-size: 10px;
  color: #94a3b8;
  line-height: 1.3;
  max-width: 150px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ac-status {
  font-size: 10px;
  color: #64748b;
  line-height: 1.2;
  max-width: 140px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ac-alert {
  position: absolute;
  top: -3px;
  right: -3px;
  width: 15px;
  height: 15px;
  border-radius: 50%;
  font-size: 8px;
  font-weight: 700;
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
}

.ac-alert.permission { background: #f59e0b; }
.ac-alert.error { background: #ef4444; }

.ac-schedule-badge {
  position: absolute;
  right: -3px;
  top: 50%;
  transform: translateY(-50%);
  width: 15px;
  height: 15px;
  border-radius: 50%;
  background: #6366f1;
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
}

.ac-docker-badge {
  position: absolute;
  left: -3px;
  bottom: -3px;
  width: 15px;
  height: 15px;
  border-radius: 50%;
  background: #0db7ed;
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
}

/* Ghost chip variant */
.agent-chip.ghost {
  opacity: 0.6;
  border-style: dashed;
  border-color: #cbd5e1;
  background: #f8fafc;
}

.agent-chip.ghost:hover {
  opacity: 0.8;
  border-color: #94a3b8;
}

.ac-dismiss {
  background: none;
  border: none;
  color: #94a3b8;
  font-size: 16px;
  line-height: 1;
  padding: 0 2px;
  cursor: pointer;
  flex-shrink: 0;
  align-self: center;
}

.ac-dismiss:hover {
  color: #ef4444;
}

@keyframes pulse-starting {
  0%, 100% { opacity: 1; box-shadow: 0 0 0 0 rgba(59, 130, 246, 0.4); }
  50%       { opacity: 0.8; box-shadow: 0 0 0 4px rgba(59, 130, 246, 0); }
}

@keyframes pulse-error {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.4; }
}
</style>
