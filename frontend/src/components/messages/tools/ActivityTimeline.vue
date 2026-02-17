<template>
  <div class="activity-timeline" :class="{ 'timeline-mobile': uiStore.isMobile }" v-if="sortedTools.length > 0">
    <!-- Timeline Row (nodes + segments) -->
    <div class="timeline-row">
      <!-- Timeline items (nodes + segments) -->
      <template v-for="(tool, index) in sortedTools" :key="tool.id">
        <!-- Segment between nodes (not before first) -->
        <TimelineSegment
          v-if="index > 0"
          :leftColor="getNodeColor(sortedTools[index - 1])"
          :rightColor="getNodeColor(tool)"
          :compact="uiStore.isMobile"
        />

        <!-- Node -->
        <TimelineNode
          :ref="el => setNodeRef(tool.id, el)"
          :tool="tool"
          :isExpanded="expandedNodeId === tool.id"
          :compact="uiStore.isMobile"
          @click="toggleDetail(tool.id)"
        />
      </template>
    </div>

    <!-- Detail Panel (one at a time) -->
    <TimelineDetail
      v-if="expandedNodeId && expandedTool"
      :toolCall="expandedTool"
    />
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { useMessageStore } from '@/stores/message'
import { useSessionStore } from '@/stores/session'
import { useUIStore } from '@/stores/ui'
import TimelineNode from './TimelineNode.vue'
import TimelineSegment from './TimelineSegment.vue'
import TimelineDetail from './TimelineDetail.vue'

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
const uiStore = useUIStore()

// Local state for this timeline instance
const expandedNodeId = ref(null)
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

// Expanded tool
const expandedTool = computed(() => {
  if (!expandedNodeId.value) return null
  return sortedTools.value.find(t => t.id === expandedNodeId.value)
})

// Auto-expand when a tool needs permission, auto-collapse when resolved
watch(sortedTools, (tools) => {
  const permTool = tools.find(t => getEffectiveStatus(t) === 'permission_required')
  if (permTool) {
    expandedNodeId.value = permTool.id
  } else if (expandedNodeId.value) {
    // Permission was resolved — collapse the detail panel
    const expanded = tools.find(t => t.id === expandedNodeId.value)
    if (expanded && getEffectiveStatus(expanded) !== 'permission_required') {
      expandedNodeId.value = null
    }
  }
}, { deep: true, immediate: true })

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
      return '#ffc107'
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

</script>

<style scoped>
.activity-timeline {
  margin-top: 4px;
}

.timeline-row {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  min-height: 20px;
  gap: 2px 0;
  padding: 2px 0;
}
</style>
