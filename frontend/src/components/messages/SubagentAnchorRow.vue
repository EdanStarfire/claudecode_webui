<template>
  <div class="anchor-row-group">
    <div
      class="anchor-row"
      :class="[`anchor-${anchorType}`, statusText ? `anchor-status-${statusText}` : null, clickable ? 'anchor-row-clickable' : null]"
      :style="rowStyle"
      :role="clickable ? 'button' : null"
      :tabindex="clickable ? 0 : null"
      @click="clickable && emit('click', $event)"
      @keydown.enter="clickable && emit('click', $event)"
      @keydown.space.prevent="clickable && emit('click', $event)"
    >
      <span class="anchor-icon">{{ icon }}</span>
      <span v-if="subagentType" class="anchor-type-badge">{{ subagentType }}</span>
      <span class="anchor-description">{{ truncatedDescription }}</span>
      <!-- Issue #1746 (stage: subagents) review fix: badge color must follow the actual outcome
           (statusText: completed/failed/stopped), not the anchor's structural type (which is
           always 'completed' for any terminal leg) — otherwise a failed leg renders with the
           same green styling as a success. -->
      <span v-if="statusText" class="anchor-status-badge" :class="`status-${statusText}`">{{ statusText }}</span>
      <span class="anchor-timestamp">{{ formattedTimestamp }}</span>
    </div>
    <!-- Issue #1746 follow-up (user feedback): content that actually guides the main session
         (a message pushed to main, or a subagent's own completion report) must render with the
         same full markdown support as any other user/assistant message — not clamped to one
         truncated line with no way to see what was cut off. Always visible, no collapse. -->
    <div v-if="markdownBody" class="anchor-body">
      <MarkdownView :content="markdownBody" />
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { formatTimestamp } from '@/utils/time'
import MarkdownView from '@/components/common/MarkdownView.vue'

const props = defineProps({
  // Issue #1746 (stage: subagents): one of the spec's causal anchor types. 'pushed' (a
  // mid-flight push to main during subagent work) has no wired data source in this stage —
  // the type is supported here for forward compatibility but nothing currently triggers it.
  anchorType: {
    type: String,
    required: true,
    validator: v => ['launch', 'resumed', 'completed', 'permission-needed', 'pushed'].includes(v)
  },
  agentColor: {
    type: Object,
    required: true
  },
  description: {
    type: String,
    default: ''
  },
  subagentType: {
    type: String,
    default: null
  },
  timestamp: {
    type: [Number, String],
    default: null
  },
  statusText: {
    type: String,
    default: ''
  },
  // Issue #1746 follow-up: only the PRIMARY (launch/resumed) row is clickable — toggles this
  // leg's transcript, mirroring what the (now-removed) local gutter chip used to do.
  clickable: {
    type: Boolean,
    default: false
  },
  // Issue #1746 follow-up (user feedback): full markdown content shown below the compact
  // header row, always visible — used for 'pushed' (the actual message sent to main) and
  // 'completed' (the subagent's own result/summary, when one exists) anchor types.
  markdownBody: {
    type: String,
    default: null
  }
})

const emit = defineEmits(['click'])

const ICONS = {
  launch: '\u{1F916}',        // robot
  resumed: '↻',          // clockwise arrow
  completed: '✔',        // check
  failed: '✕',           // cross
  stopped: '⏹',      // stop square
  'permission-needed': '⚠', // warning
  pushed: '↑',           // up arrow
}

// Issue #1746 (stage: subagents) review fix: a terminal leg's icon must follow the actual
// outcome (statusText: completed/failed/stopped), not just the structural anchorType (which
// is always 'completed' for any terminal leg) — otherwise a failed subagent shows a checkmark.
const icon = computed(() => ICONS[props.statusText] || ICONS[props.anchorType] || '\u{1F916}')

const truncatedDescription = computed(() => {
  const desc = props.description || ''
  return desc.length > 120 ? desc.slice(0, 120) + '...' : desc
})

const formattedTimestamp = computed(() => {
  return props.timestamp ? formatTimestamp(props.timestamp) : ''
})

// Issue #1746 (stage: subagents) §8: soft/translucent row wash derived from the existing
// per-agent accent token via color-mix(), rather than 12x4 new literal wash colors — the
// existing --agent-color-N-bg values are opaque badge-style backgrounds, not the subtle
// full-bleed row tint this row wants (matching stage-1's --row-assistant-wash treatment).
// --subagent-wash-pct is tuned higher per dark theme (styles.css) per spec's opacity warning.
const rowStyle = computed(() => ({
  borderLeftColor: props.agentColor.border,
  '--row-accent': props.agentColor.accent,
}))
</script>

<style scoped>
.anchor-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 5px 10px;
  border-left: 3px solid transparent;
  border-radius: 4px;
  font-size: 12px;
  min-height: 26px;
  background: color-mix(in srgb, var(--row-accent) var(--subagent-wash-pct, 8%), transparent);
}

.anchor-row-group + .anchor-row-group {
  margin-top: 2px;
}

.anchor-body {
  padding: 4px 10px 8px calc(10px + 3px);
  font-size: 13px;
  color: var(--bs-body-color);
}

.anchor-row-clickable {
  cursor: pointer;
}

.anchor-row-clickable:hover {
  background: color-mix(in srgb, var(--row-accent) calc(var(--subagent-wash-pct, 8%) * 1.6), transparent);
}

.anchor-row-clickable:focus-visible {
  outline: 2px solid var(--row-accent);
  outline-offset: -2px;
}

.anchor-icon {
  font-size: 13px;
  flex-shrink: 0;
}

.anchor-type-badge {
  background: var(--agent-badge-bg, var(--bs-tertiary-bg));
  color: var(--agent-badge-text, var(--bs-body-color));
  padding: 1px 6px;
  border-radius: 4px;
  font-size: 10px;
  font-weight: 600;
  text-transform: lowercase;
  font-family: 'Courier New', monospace;
  flex-shrink: 0;
}

.anchor-description {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--bs-body-color);
}

.anchor-status-badge {
  padding: 1px 7px;
  border-radius: 4px;
  font-size: 10px;
  font-weight: 600;
  flex-shrink: 0;
}

.status-completed {
  background: var(--bs-success-bg-subtle, #d1fae5);
  color: var(--bs-success-text-emphasis, #065f46);
}

.status-failed {
  background: var(--tool-error-bg, #fee2e2);
  color: var(--tool-error-text, #991b1b);
}

.status-stopped {
  background: var(--bs-warning-bg-subtle, #fef3c7);
  color: var(--bs-warning-text-emphasis, #92400e);
}

.status-permission-needed {
  background: var(--bs-warning-bg-subtle, #fff3cd);
  color: var(--bs-warning-text-emphasis, #664d03);
}

.anchor-permission-needed {
  border-left-color: var(--bs-warning-border-subtle, #ffc107) !important;
}

.anchor-status-failed {
  border-left-color: var(--tool-error-border, #f87171) !important;
}

.anchor-timestamp {
  color: var(--bs-secondary-color);
  font-size: 10px;
  flex-shrink: 0;
  opacity: 0.75;
}

/* Mobile: tighter row padding (matches AssistantMessage's 16px -> 12px breakpoint) */
@media (max-width: 768px) {
  .anchor-row {
    padding: 5px 8px;
  }
}
</style>
