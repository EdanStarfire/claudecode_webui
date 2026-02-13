<template>
  <div class="activity-timeline" v-if="sortedTools.length > 0">
    <!-- Timeline Row (nodes + segments) -->
    <div class="timeline-row">
      <!-- Overflow chip (when collapsed, shows count of hidden tools) -->
      <TimelineOverflow
        v-if="hasOverflow && !isOverflowExpanded"
        :count="overflowCount"
        @toggle="toggleOverflow"
      />

      <!-- Timeline items (nodes + segments) -->
      <template v-for="(tool, index) in visibleTools" :key="tool.id">
        <!-- Segment between nodes (not before first) -->
        <TimelineSegment
          v-if="index > 0"
          :leftColor="getNodeColor(visibleTools[index - 1])"
          :rightColor="getNodeColor(tool)"
        />

        <!-- Node -->
        <TimelineNode
          :ref="el => setNodeRef(tool.id, el)"
          :tool="tool"
          :isExpanded="expandedNodeId === tool.id"
          @click="toggleDetail(tool.id)"
        />
      </template>
    </div>

    <!-- Summary (tool count + status) -->
    <div class="timeline-summary">
      <span class="summary-count">{{ sortedTools.length }} tool{{ sortedTools.length !== 1 ? 's' : '' }}</span>
      <span v-if="runningCount > 0" class="summary-running">{{ runningCount }} running</span>
      <span v-if="permissionCount > 0" class="summary-permission">{{ permissionCount }} needs permission</span>
    </div>

    <!-- Detail Panel (one at a time) -->
    <TimelineDetail
      v-if="expandedNodeId && expandedTool"
      :toolCall="expandedTool"
    />
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useMessageStore } from '@/stores/message'
import { useSessionStore } from '@/stores/session'
import TimelineNode from './TimelineNode.vue'
import TimelineSegment from './TimelineSegment.vue'
import TimelineDetail from './TimelineDetail.vue'
import TimelineOverflow from './TimelineOverflow.vue'

const OVERFLOW_THRESHOLD = 10

const props = defineProps({
  tools: {
    type: Array,
    required: true,
    default: () => []
  },
  messageId: {
    type: String,
    default: null
  }
})

const messageStore = useMessageStore()
const sessionStore = useSessionStore()

// Local state for this timeline instance
const expandedNodeId = ref(null)
const isOverflowExpanded = ref(false)
const nodeRefs = ref({})

function setNodeRef(id, el) {
  if (el) nodeRefs.value[id] = el
}

// Sort tools chronologically
const sortedTools = computed(() => {
  return [...props.tools]
    .map((tool, index) => ({ tool, originalIndex: index }))
    .sort((a, b) => {
      if (a.tool.timestamp && b.tool.timestamp) {
        const timeA = new Date(a.tool.timestamp).getTime()
        const timeB = new Date(b.tool.timestamp).getTime()
        if (timeA !== timeB) return timeA - timeB
      }
      return a.originalIndex - b.originalIndex
    })
    .map(({ tool }) => tool)
})

// Overflow logic
const hasOverflow = computed(() => sortedTools.value.length > OVERFLOW_THRESHOLD)

const overflowCount = computed(() => {
  if (!hasOverflow.value) return 0
  return sortedTools.value.length - OVERFLOW_THRESHOLD
})

const visibleTools = computed(() => {
  if (!hasOverflow.value || isOverflowExpanded.value) {
    return sortedTools.value
  }
  // Show only the latest OVERFLOW_THRESHOLD tools
  return sortedTools.value.slice(-OVERFLOW_THRESHOLD)
})

// Expanded tool
const expandedTool = computed(() => {
  if (!expandedNodeId.value) return null
  return sortedTools.value.find(t => t.id === expandedNodeId.value)
})

// Status counts
const runningCount = computed(() => {
  return sortedTools.value.filter(t => getEffectiveStatus(t) === 'executing').length
})

const permissionCount = computed(() => {
  return sortedTools.value.filter(t => getEffectiveStatus(t) === 'permission_required').length
})

// Get effective status for a tool
function getEffectiveStatus(tool) {
  const sessionId = sessionStore.currentSessionId
  if (!sessionId) return tool.status

  if (tool.backendStatus) {
    const map = {
      'pending': 'pending',
      'awaiting_permission': 'permission_required',
      'running': 'executing',
      'completed': 'completed',
      'failed': 'error',
      'denied': 'completed',
      'interrupted': 'orphaned'
    }
    return map[tool.backendStatus] || tool.backendStatus
  }

  if (messageStore.isToolUseOrphaned(sessionId, tool.id)) {
    return 'orphaned'
  }

  return tool.status
}

// Get node color for segment gradient computation
function getNodeColor(tool) {
  const status = getEffectiveStatus(tool)
  const hasError = tool.result?.error || tool.status === 'error' || tool.permissionDecision === 'deny'

  switch (status) {
    case 'completed':
      return hasError ? '#ef4444' : '#22c55e'
    case 'error':
      return '#ef4444'
    case 'executing':
      return '#8b5cf6'
    case 'permission_required':
      return '#f97316'
    case 'orphaned':
      return '#94a3b8'
    case 'pending':
    default:
      return '#e2e8f0'
  }
}

// Toggle detail panel
function toggleDetail(toolId) {
  if (expandedNodeId.value === toolId) {
    expandedNodeId.value = null
  } else {
    expandedNodeId.value = toolId
  }
}

function toggleOverflow() {
  isOverflowExpanded.value = !isOverflowExpanded.value
}
</script>

<style scoped>
.activity-timeline {
  margin-top: 4px;
}

.timeline-row {
  display: flex;
  align-items: center;
  min-height: 20px;
  gap: 0;
  padding: 2px 0;
}

.timeline-summary {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 2px;
  font-size: 10px;
  color: #94a3b8;
}

.summary-count {
  font-weight: 500;
}

.summary-running {
  color: #8b5cf6;
  font-weight: 600;
}

.summary-permission {
  color: #f97316;
  font-weight: 600;
}
</style>
