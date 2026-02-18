<template>
  <div
    class="timeline-node"
    :class="[nodeClasses, { 'node-compact': compact }]"
    :title="tooltip"
    @click.stop="$emit('click')"
    @mouseenter="showTooltip = true"
    @mouseleave="showTooltip = false"
  >
    <div class="node-dot" :class="dotClasses"></div>
    <span class="node-label">{{ toolLabel }}</span>
  </div>
</template>

<script setup>
import { computed, ref, toRef } from 'vue'
import { generateShortToolSummary } from '@/utils/toolSummary'
import { useToolStatus } from '@/composables/useToolStatus'

const props = defineProps({
  tool: { type: Object, required: true },
  isExpanded: { type: Boolean, default: false },
  compact: { type: Boolean, default: false }
})

defineEmits(['click'])

const showTooltip = ref(false)

const { effectiveStatus, statusColor, hasError } = useToolStatus(toRef(props, 'tool'))

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

const toolLabel = computed(() => {
  const name = props.tool.name || ''
  // For MCP tools (mcp__server__toolName), extract just the tool name after the last __
  if (name.startsWith('mcp__')) {
    const lastSep = name.lastIndexOf('__')
    if (lastSep > 4) return name.slice(lastSep + 2)
  }
  return name.replace(/Tool$/, '')
})

// Expose for parent
defineExpose({ statusColor, effectiveStatus })
</script>

<style scoped>
.timeline-node {
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-width: 20px;
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

.node-label {
  font-size: 9px;
  color: #94a3b8;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 60px;
  line-height: 1;
  margin-top: 1px;
}

/* Mobile compact size */
.node-compact {
  min-width: 16px;
}

.node-compact .node-dot {
  width: 9px;
  height: 9px;
}

.node-compact .node-label {
  font-size: 7px;
  max-width: 40px;
}
</style>
