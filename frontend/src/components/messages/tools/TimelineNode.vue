<template>
  <div
    class="tool-row"
    :class="[rowClasses, { 'row-compact': compact }]"
    :style="{ borderLeftColor: statusColor }"
    :title="tooltip"
    role="button"
    :aria-label="tooltip"
    data-testid="timeline-node"
    @click.stop="$emit('click')"
    @mouseenter="showTooltip = true"
    @mouseleave="showTooltip = false"
  >
    <span class="row-dot" :class="dotClasses"></span>
    <span class="row-name">{{ toolLabel }}</span>
    <code v-if="targetText" class="row-target">{{ targetText }}</code>
    <span
      v-if="approvalIndicator"
      class="approval-icon"
      :style="{ color: approvalIndicator.color }"
      :aria-label="approvalIndicator.ariaLabel"
      role="img"
    >{{ approvalIndicator.icon }}</span>
    <span
      v-if="hookSummary.count > 0"
      class="hook-count-badge"
      :class="'hook-badge-' + hookSummary.aggregateStatus"
      :aria-label="`${hookSummary.count} hook${hookSummary.count > 1 ? 's' : ''} (${hookSummary.aggregateStatus})`"
    >⚙ {{ hookSummary.count }}</span>
  </div>
</template>

<script setup>
import { computed, ref, toRef } from 'vue'
import { generateShortToolSummary } from '@/utils/toolSummary'
import { useToolStatus } from '@/composables/useToolStatus'
import { aggregateHookStatus } from '@/utils/hookCorrelation'

const props = defineProps({
  tool: { type: Object, required: true },
  isExpanded: { type: Boolean, default: false },
  compact: { type: Boolean, default: false },
  hooks: { type: Array, default: () => [] },
})

defineEmits(['click'])

const showTooltip = ref(false)

const { effectiveStatus, statusColor, hasError, isOrphaned } = useToolStatus(toRef(props, 'tool'))

const approvalIndicator = computed(() => {
  if (isOrphaned.value) {
    return { icon: '⚠', color: '#f97316', ariaLabel: 'Orphaned - tool execution was interrupted' }
  }
  if (props.tool.autoApprovedReason) {
    return { icon: '⚡', color: '#a78bfa', ariaLabel: `Auto-approved: ${props.tool.autoApprovedReason}` }
  }
  if (props.tool.permissionDecision === 'allow') {
    return { icon: '✓', color: '#22c55e', ariaLabel: 'Approved by user' }
  }
  if (props.tool.permissionDecision === 'deny') {
    return { icon: '✗', color: '#ef4444', ariaLabel: 'Denied by user' }
  }
  return null
})

const rowClasses = computed(() => ({
  'row-expanded': props.isExpanded,
  'row-running': effectiveStatus.value === 'executing',
  'row-permission': effectiveStatus.value === 'permission_required'
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

// Target chip: the part of the short summary after "ToolName: " (file path, pattern,
// command, etc). Falls back to null when the summary has no such suffix (e.g. TodoWrite).
const targetText = computed(() => {
  const summary = generateShortToolSummary(props.tool)
  const displayPrefix = `${toolLabel.value}: `
  if (summary.startsWith(displayPrefix)) return summary.slice(displayPrefix.length)
  const rawPrefix = `${props.tool.name || ''}: `
  if (summary.startsWith(rawPrefix)) return summary.slice(rawPrefix.length)
  return null
})

const hookSummary = computed(() => ({
  count: props.hooks.length,
  aggregateStatus: aggregateHookStatus(props.hooks) || 'success',
}))

// Expose for parent
defineExpose({ statusColor, effectiveStatus })
</script>

<style scoped>
.tool-row {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 4px 6px;
  border-radius: 5px;
  max-width: 100%;
  width: fit-content;
  background: var(--tool-bg);
  border: 1px solid var(--tool-border);
  border-left: 3px solid transparent;
  cursor: pointer;
  transition: background 0.15s, box-shadow 0.15s;
}

.tool-row:hover {
  background: var(--tool-bg-header);
}

.row-expanded {
  box-shadow: 0 0 0 2px #3b82f6;
}

.row-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
  background-color: v-bind(statusColor);
}

/* Running dot: amber/violet glow pulse */
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

.row-name {
  font-size: 12px;
  font-weight: 600;
  color: var(--tool-text);
  white-space: nowrap;
  flex-shrink: 0;
}

.row-target {
  color: var(--tool-text-muted);
  background: var(--tool-bg-header);
  padding: 1px 5px;
  border-radius: 3px;
  font-family: var(--tool-font-mono);
  font-size: var(--tool-code-font-size);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}

.approval-icon {
  font-size: 10px;
  font-weight: 700;
  flex-shrink: 0;
}

/* Mobile compact size */
.row-compact {
  padding: 3px 5px;
  gap: 5px;
}

.row-compact .row-dot {
  width: 6px;
  height: 6px;
}

.row-compact .row-name {
  font-size: 11px;
}

.row-compact .row-target {
  font-size: 10px;
}

/* Hook count badge */
.hook-count-badge {
  padding: 0 4px;
  border-radius: 3px;
  font-size: 9px;
  font-weight: 700;
  line-height: 15px;
  white-space: nowrap;
  color: var(--hook-badge-text);
  flex-shrink: 0;
}

.hook-badge-success { background: var(--hook-badge-success-bg); }
.hook-badge-failure { background: var(--hook-badge-failure-bg); }
.hook-badge-pending { background: var(--hook-badge-pending-bg); }
.hook-badge-mixed   { background: var(--hook-badge-mixed-bg); }
</style>
