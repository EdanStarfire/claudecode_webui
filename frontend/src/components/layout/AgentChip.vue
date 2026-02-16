<template>
  <div
    class="agent-chip"
    :class="{
      active: isActive,
      child: variant === 'child'
    }"
    @click="handleClick"
    :title="chipTooltip"
  >
    <div class="ac-dot" :class="statusClass"></div>
    <div class="ac-info">
      <div class="ac-name">{{ displayName }}</div>
      <div class="ac-status">{{ statusText }}</div>
    </div>
    <div v-if="alertType" class="ac-alert" :class="alertType">
      {{ alertType === 'error' ? '!' : '?' }}
    </div>
    <div v-if="hasSchedules" class="ac-schedule-badge" title="Has active schedules">
      <svg width="10" height="10" viewBox="0 0 16 16" fill="currentColor">
        <path d="M8 0a8 8 0 1 0 0 16A8 8 0 0 0 8 0zm0 14.5A6.5 6.5 0 1 1 8 1.5a6.5 6.5 0 0 1 0 13zM8.5 4H7v5l4.25 2.55.75-1.23L8.5 8.25V4z"/>
      </svg>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useSessionStore } from '@/stores/session'
import { useScheduleStore } from '@/stores/schedule'

const props = defineProps({
  session: { type: Object, required: true },
  isActive: { type: Boolean, default: false },
  variant: { type: String, default: 'default' } // 'default' | 'child'
})

const emit = defineEmits(['select'])

const sessionStore = useSessionStore()
const scheduleStore = useScheduleStore()

const hasSchedules = computed(() =>
  scheduleStore.getScheduleCount(props.session.session_id) > 0
)

const displayName = computed(() =>
  props.session.name || props.session.role || 'Agent'
)

const statusClass = computed(() => {
  const state = props.session.state
  if (state === 'error') return 'error'
  if (state === 'paused') return 'waiting'
  if (state === 'active' && props.session.is_processing) return 'active'
  if (state === 'active') return 'idle'
  return 'none'
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
  if (props.session.role) parts.push(`Role: ${props.session.role}`)
  parts.push(`Status: ${statusText.value}`)
  if (props.session.model) parts.push(`Model: ${props.session.model}`)
  return parts.join('\n')
})

function handleClick() {
  emit('select', props.session.session_id)
}
</script>

<style scoped>
.agent-chip {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 12px;
  border-radius: 10px;
  border: 1px solid #e2e8f0;
  background: #fff;
  cursor: pointer;
  transition: all 0.15s;
  position: relative;
  white-space: nowrap;
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
  border-color: #bfdbfe;
  background: #f0f7ff;
}

.ac-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
  background: #e2e8f0;
}

.ac-dot.active { background: #8b5cf6; }
.ac-dot.idle { background: #22c55e; }
.ac-dot.waiting { background: #ffc107; }
.ac-dot.error {
  background: #ef4444;
  animation: pulse-error 1.5s infinite;
}
.ac-dot.none { background: #e2e8f0; }

.ac-info {
  display: flex;
  flex-direction: column;
}

.ac-name {
  font-size: 12px;
  font-weight: 600;
  color: #1e293b;
  line-height: 1.2;
}

.ac-status {
  font-size: 10px;
  color: #64748b;
  line-height: 1.2;
  max-width: 140px;
  overflow: hidden;
  text-overflow: ellipsis;
}

.ac-alert {
  position: absolute;
  top: -4px;
  right: -4px;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  font-size: 9px;
  font-weight: 700;
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 2px solid #fff;
}

.ac-alert.permission { background: #f59e0b; }
.ac-alert.error { background: #ef4444; }

.ac-schedule-badge {
  position: absolute;
  right: -4px;
  top: 50%;
  transform: translateY(-50%);
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: #6366f1;
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 2px solid #fff;
}

@keyframes pulse-error {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.4; }
}
</style>
