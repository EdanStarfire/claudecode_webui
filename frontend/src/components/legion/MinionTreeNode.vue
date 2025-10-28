<template>
  <div v-if="minionData" class="minion-tree-node" :style="{ marginLeft: `${indent}px` }">
    <div class="minion-card border-start border-2 mb-2">
      <div class="node-row">
        <!-- Left Column: Status + Name (30%) -->
        <div class="node-left">
          <!-- Status Dot (reuses SessionItem styles) -->
          <div class="status-dot me-2" :class="statusDotClass" :style="statusDotStyle"></div>

          <!-- Overseer Icon -->
          <span v-if="isOverseerWithChildren" class="me-2">👑</span>

          <!-- Minion Name -->
          <strong>{{ minionData.name }}</strong>

          <!-- Children Count Badge -->
          <span v-if="isOverseerWithChildren" class="badge bg-secondary ms-2">
            {{ minionData.children.length }} {{ minionData.children.length === 1 ? 'child' : 'children' }}
          </span>
        </div>

        <!-- Right Column: Last Comm (70%) -->
        <div class="node-right">
          <div v-if="minionData.last_comm" class="last-comm-preview">
            <span class="comm-direction">
              → <strong>{{ getCommRecipient(minionData.last_comm) }}</strong>:
            </span>
            <span
              class="comm-content"
              :title="minionData.last_comm.content || ''"
            >
              {{ getCommSummary(minionData.last_comm) }}
            </span>
          </div>
          <div v-else class="text-muted fst-italic small">
            No communications yet
          </div>
        </div>
      </div>
    </div>

    <!-- Recursively Render Children -->
    <div v-if="hasChildren" class="minion-children">
      <MinionTreeNode
        v-for="child in minionData.children"
        :key="child.id"
        :minion-data="child"
        :level="level + 1"
      />
    </div>
  </div>

  <!-- Debug: Show if minion data is missing -->
  <div v-else class="text-danger small ms-4">
    ⚠ Minion data is missing
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  minionData: {
    type: Object,
    required: true
  },
  level: {
    type: Number,
    default: 0
  }
})

// Indentation (24px per level)
const indent = computed(() => props.level * 24)

// Check if minion has children
const hasChildren = computed(() => {
  return (
    props.minionData &&
    props.minionData.children &&
    props.minionData.children.length > 0
  )
})

// Check if minion is an overseer with children
const isOverseerWithChildren = computed(() => {
  return (
    props.minionData &&
    props.minionData.is_overseer &&
    hasChildren.value
  )
})

// Display state (matches SessionItem logic)
const displayState = computed(() => {
  if (!props.minionData) return 'created'

  // Special case: PAUSED + processing = waiting for permission response (yellow blinking)
  if (props.minionData.state === 'paused' && props.minionData.is_processing) {
    return 'pending-prompt'
  }
  // Normal case: processing overrides state (purple blinking)
  return props.minionData.is_processing ? 'processing' : (props.minionData.state || 'created')
})

// Status dot CSS classes (matches SessionItem)
const statusDotClass = computed(() => {
  const state = displayState.value
  const classMap = {
    'created': 'status-dot-grey',
    'starting': 'status-dot-green status-blinking',
    'active': 'status-dot-green',
    'running': 'status-dot-green',
    'processing': 'status-dot-purple status-blinking',
    'paused': 'status-dot-grey',
    'pending-prompt': 'status-dot-yellow status-blinking',
    'terminated': 'status-dot-grey',
    'error': 'status-dot-red',
    'failed': 'status-dot-red'
  }
  return classMap[state] || 'status-dot-grey'
})

// Status dot inline styles (matches SessionItem)
const statusDotStyle = computed(() => {
  const state = displayState.value
  const bgColorMap = {
    'created': '#d3d3d3',
    'starting': '#90ee90',
    'active': '#90ee90',
    'running': '#90ee90',
    'processing': '#dda0dd',
    'paused': '#d3d3d3',
    'pending-prompt': '#ffc107',
    'terminated': '#d3d3d3',
    'error': '#ffb3b3',
    'failed': '#ffb3b3'
  }
  return {
    backgroundColor: bgColorMap[state] || '#d3d3d3'
  }
})

// Helper: Get comm recipient name
function getCommRecipient(comm) {
  if (comm.to_user) {
    return 'User'
  } else if (comm.to_minion_name) {
    return comm.to_minion_name
  } else if (comm.to_channel_name) {
    return `#${comm.to_channel_name}`
  }
  return 'unknown'
}

// Helper: Get comm summary (prioritize summary, fallback to content, truncate at 150 chars)
function getCommSummary(comm) {
  const text = comm.summary || comm.content || ''
  if (text.length > 150) {
    return text.substring(0, 150) + '...'
  }
  return text
}
</script>

<style scoped>
.minion-card {
  background-color: white;
  border-color: #dee2e6;
  padding: 0.5rem 0.75rem;
}

.minion-tree-node {
  transition: margin-left 0.2s ease;
}

/* Two-column layout: 30% left, 70% right */
.node-row {
  display: flex;
  gap: 1rem;
  align-items: flex-start;
}

.node-left {
  flex: 0 0 30%;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 0.25rem;
}

.node-right {
  flex: 1;
  min-width: 0; /* Allow text truncation */
  padding-left: 1rem;
  border-left: 1px solid #dee2e6;
}

.last-comm-preview {
  font-size: 0.9rem;
  line-height: 1.4;
}

.comm-direction {
  color: #6c757d;
  font-size: 0.85rem;
}

.comm-content {
  color: #212529;
  word-wrap: break-word;
  cursor: help; /* Show help cursor on hover to indicate tooltip */
}

/* Status dot styles - matches SessionItem.vue */
.status-dot {
  width: 12px;
  height: 12px;
  border-radius: 50%;
  border: 2px solid;
  flex-shrink: 0;
}

.status-dot-grey {
  background-color: #d3d3d3;
  border-color: #6c757d;
}

.status-dot-green {
  background-color: #90ee90;
  border-color: #28a745;
}

.status-dot-purple {
  background-color: #dda0dd;
  border-color: #6f42c1;
}

.status-dot-red {
  background-color: #ffb3b3;
  border-color: #dc3545;
}

.status-dot-yellow {
  background-color: #ffc107;
  border-color: #e0a800;
}

.status-blinking {
  animation: blink 1.5s infinite;
}

@keyframes blink {
  0%, 100% {
    opacity: 1;
  }
  50% {
    opacity: 0.3;
  }
}
</style>
