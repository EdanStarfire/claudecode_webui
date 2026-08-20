<template>
  <div class="activity-timeline" :class="{ 'timeline-mobile': uiStore.isMobile }" v-if="sortedTools.length > 0" data-testid="activity-timeline">
    <!-- Timeline Row (tool chips, stacked vertically) -->
    <div class="timeline-row">
      <template v-for="tool in sortedTools" :key="tool.id">
        <TimelineNode
          :tool="tool"
          :isExpanded="expandedNodeId === tool.id"
          :compact="uiStore.isMobile"
          :hooks="hooksForTool(tool.id)"
          @click="toggleDetail(tool.id)"
        />

        <!-- Detail Panel (one at a time, inline beneath its own pill) -->
        <TimelineDetail
          v-if="expandedNodeId === tool.id"
          :toolCall="tool"
        />

        <!-- Permission Prompt (inline beneath its own pill) -->
        <PermissionPrompt
          v-if="expandedNodeId === tool.id && needsPermission"
          ref="permissionRef"
          :toolCall="tool"
        />
      </template>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch, nextTick, inject } from 'vue'
import { useUIStore } from '@/stores/ui'
import { useMessageStore } from '@/stores/message'
import { getEffectiveStatusForTool } from '@/composables/useToolStatus'
import TimelineNode from './TimelineNode.vue'
import TimelineDetail from './TimelineDetail.vue'
import PermissionPrompt from './PermissionPrompt.vue'

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

const uiStore = useUIStore()
const messageStore = useMessageStore()

// Issue #1350: inject session id for hook correlation
const viewSessionId = inject('viewSessionId', null)

function hooksForTool(toolId) {
  const sid = viewSessionId?.value
  if (!sid || !toolId) return []
  return messageStore.hooksForToolCall(sid, toolId)
}

// Issue #1748 (stage: windowing): which tool's detail panel is expanded is store-backed
// (messageStore.getExpandedTimelineTool/setExpandedTimelineTool), not a local ref — at a real
// overscan value this row can genuinely unmount when scrolled out of view and remount with a
// fresh setup() call on scroll-back, which would otherwise silently collapse a tool card the
// user deliberately expanded. Mirrors SubagentTimeline.vue's isLegExpanded/toggleLegExpanded,
// already store-backed for the same cross-remount-survival reason (see its own comment).
// scopeKey identifies this ActivityTimeline's own tool group: messageId when the caller has one
// (AssistantMessage passes the segment's message id), falling back to the first tool's id so two
// different instances never collide if messageId is ever absent. Callers that render more than
// one ActivityTimeline sharing the same messageId (SubagentLegTranscript.vue, one instance per
// interleaved tool-run within a leg) must suffix it themselves to stay unique per instance.
const scopeKey = computed(() => props.messageId || props.tools[0]?.id || null)
const expandedNodeId = computed(() => messageStore.getExpandedTimelineTool(scopeKey.value))
// Issue #1748 (stage: windowing) review fix: also store-backed, not a local ref. This tracks
// whether the CURRENT expansion was auto-triggered by a permission need (vs. a manual click), so
// the watch below only auto-collapses what it auto-expanded. A local ref here would reset to
// false on remount, forgetting that the row was auto-expanded — if the permission then resolves
// off-screen (e.g. via PermissionQueue's always-mounted floating panel) while unmounted, a
// fresh mount would never auto-collapse the now-stale expanded panel on scroll-back.
const expandedForPermission = computed(() => messageStore.isExpandedTimelineToolAutoPermission(scopeKey.value))
const permissionRef = ref(null)

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

// Whether the expanded tool currently needs a permission prompt
const needsPermission = computed(() => {
  if (!expandedTool.value) return false
  return getEffectiveStatusForTool(expandedTool.value) === 'permission_required'
})

// Scroll permission prompt into view when it appears (respects auto-scroll toggle)
watch(needsPermission, (needs) => {
  if (needs && uiStore.autoScrollEnabled) {
    nextTick(() => {
      permissionRef.value?.[0]?.$el?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
    })
  }
})

// Auto-expand when a tool needs permission; auto-collapse when permission resolves
watch(sortedTools, (tools) => {
  const permTool = tools.find(t => getEffectiveStatusForTool(t) === 'permission_required')
  if (permTool) {
    messageStore.setExpandedTimelineTool(scopeKey.value, permTool.id, { autoPermission: true })
  } else if (expandedForPermission.value) {
    messageStore.setExpandedTimelineTool(scopeKey.value, null)
  } else if (expandedNodeId.value) {
    const stillExists = tools.some(t => t.id === expandedNodeId.value)
    if (!stillExists) messageStore.setExpandedTimelineTool(scopeKey.value, null)
  }
}, { deep: true, immediate: true })

// Toggle detail panel
function toggleDetail(toolId) {
  if (expandedNodeId.value === toolId) {
    messageStore.setExpandedTimelineTool(scopeKey.value, null)
  } else {
    messageStore.setExpandedTimelineTool(scopeKey.value, toolId)
  }
}

</script>

<style scoped>
.activity-timeline {
  margin-top: 4px;
}

.timeline-row {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 4px;
  padding: 2px 0;
}
</style>
