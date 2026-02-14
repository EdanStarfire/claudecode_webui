<template>
  <div
    class="timeline-node"
    :class="nodeClasses"
    :title="tooltip"
    @click.stop="$emit('click')"
    @mouseenter="showTooltip = true"
    @mouseleave="showTooltip = false"
  >
    <div class="node-dot" :class="dotClasses"></div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { generateShortToolSummary } from '@/utils/toolSummary'
import { useMessageStore } from '@/stores/message'
import { useSessionStore } from '@/stores/session'

const props = defineProps({
  tool: { type: Object, required: true },
  isExpanded: { type: Boolean, default: false }
})

defineEmits(['click'])

const messageStore = useMessageStore()
const sessionStore = useSessionStore()
const showTooltip = ref(false)

const effectiveStatus = computed(() => {
  const sessionId = sessionStore.currentSessionId
  if (!sessionId) return props.tool.status

  // Check backend status first
  if (props.tool.backendStatus) {
    const map = {
      'pending': 'pending',
      'awaiting_permission': 'permission_required',
      'running': 'executing',
      'completed': 'completed',
      'failed': 'error',
      'denied': 'completed',
      'interrupted': 'orphaned'
    }
    return map[props.tool.backendStatus] || props.tool.backendStatus
  }

  // Check orphaned
  if (messageStore.isToolUseOrphaned(sessionId, props.tool.id)) {
    return 'orphaned'
  }

  return props.tool.status
})

const hasError = computed(() => {
  return props.tool.result?.error || props.tool.status === 'error' || props.tool.permissionDecision === 'deny'
})

const statusColor = computed(() => {
  const status = effectiveStatus.value
  switch (status) {
    case 'completed':
      return hasError.value ? '#ef4444' : '#22c55e'
    case 'error':
      return '#ef4444'
    case 'executing':
      return '#8b5cf6'
    case 'permission_required':
      return '#ffc107'
    case 'orphaned':
      return '#94a3b8'
    case 'pending':
    default:
      return '#e2e8f0'
  }
})

const nodeClasses = computed(() => ({
  'node-expanded': props.isExpanded,
  'node-running': effectiveStatus.value === 'executing',
  'node-permission': effectiveStatus.value === 'permission_required'
}))

const dotClasses = computed(() => ({
  'dot-running': effectiveStatus.value === 'executing',
  'dot-permission': effectiveStatus.value === 'permission_required',
  'dot-error': effectiveStatus.value === 'error' || (effectiveStatus.value === 'completed' && hasError.value)
}))

const tooltip = computed(() => {
  const summary = generateShortToolSummary(props.tool)
  const statusLabel = {
    'completed': hasError.value ? 'Failed' : 'Done',
    'error': 'Failed',
    'executing': 'Running...',
    'permission_required': 'Needs permission',
    'orphaned': 'Cancelled',
    'pending': 'Pending'
  }[effectiveStatus.value] || effectiveStatus.value
  return `${summary} [${statusLabel}]`
})

// Expose for parent
defineExpose({ statusColor, effectiveStatus })
</script>

<style scoped>
.timeline-node {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  cursor: pointer;
  z-index: 1;
}

.node-dot {
  width: 12px;
  height: 12px;
  border-radius: 50%;
  border: 2px solid white;
  background-color: v-bind(statusColor);
  transition: transform 0.15s, box-shadow 0.15s;
}

.timeline-node:hover .node-dot {
  transform: scale(1.5);
}

/* Expanded dot: blue ring */
.node-expanded .node-dot {
  box-shadow: 0 0 0 2px #3b82f6;
}

/* Running dot: amber glow pulse */
.dot-running {
  animation: running-pulse 1.5s ease-in-out infinite;
}

/* Permission dot: orange glow pulse */
.dot-permission {
  animation: permission-pulse 2s ease-in-out infinite;
}

/* Error dot: red pulse */
.dot-error {
  animation: error-pulse 1.5s ease-in-out infinite;
}

/* Expanded + running: combined blue ring + amber pulse */
.node-expanded .dot-running {
  animation: running-pulse 1.5s ease-in-out infinite;
  box-shadow: 0 0 0 2px #3b82f6;
}

@keyframes running-pulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(139, 92, 246, 0.4); }
  50% { box-shadow: 0 0 6px 2px rgba(139, 92, 246, 0.6); }
}

@keyframes permission-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.5; }
}

@keyframes error-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.4; }
}
</style>
