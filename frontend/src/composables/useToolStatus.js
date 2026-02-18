import { computed } from 'vue'
import { useMessageStore } from '@/stores/message'
import { useSessionStore } from '@/stores/session'

/**
 * Shared composable for tool status computation.
 * Consolidates effectiveStatus mapping duplicated in
 * TimelineDetail, ActivityTimeline, and TimelineNode.
 *
 * @param {import('vue').Ref<Object>} toolRef - reactive ref or toRef to the tool/toolCall prop
 * @returns {{ effectiveStatus: import('vue').ComputedRef<string>, isOrphaned: import('vue').ComputedRef<boolean>, orphanedInfo: import('vue').ComputedRef<Object|null>, statusColor: import('vue').ComputedRef<string>, hasError: import('vue').ComputedRef<boolean> }}
 */
export function useToolStatus(toolRef) {
  const messageStore = useMessageStore()
  const sessionStore = useSessionStore()

  const hasError = computed(() => {
    const tool = toolRef.value
    return tool?.result?.error || tool?.status === 'error' || tool?.permissionDecision === 'deny'
  })

  const effectiveStatus = computed(() => {
    const tool = toolRef.value
    if (!tool) return 'pending'

    const sessionId = sessionStore.currentSessionId
    if (!sessionId) return tool.status

    // Check backend status first (from ToolCallUpdate messages)
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

    // Check backend state from message store
    const backendState = messageStore.getBackendToolState(sessionId, tool.id)
    if (backendState) {
      const map = {
        'pending': 'pending',
        'permission_required': 'permission_required',
        'executing': 'executing',
        'completed': 'completed',
        'failed': 'error',
        'orphaned': 'orphaned'
      }
      return map[backendState.state] || backendState.state
    }

    // Check orphaned via message store
    if (messageStore.isToolUseOrphaned(sessionId, tool.id)) {
      return 'orphaned'
    }

    return tool.status
  })

  const isOrphaned = computed(() => {
    const tool = toolRef.value
    if (!tool) return false
    if (tool.backendStatus === 'interrupted') return true
    const sessionId = sessionStore.currentSessionId
    if (!sessionId) return false
    return messageStore.isToolUseOrphaned(sessionId, tool.id)
  })

  const orphanedInfo = computed(() => {
    const tool = toolRef.value
    if (!tool) return null
    const sessionId = sessionStore.currentSessionId
    if (!sessionId) return null
    return messageStore.getOrphanedInfo(sessionId, tool.id)
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

  return { effectiveStatus, isOrphaned, orphanedInfo, statusColor, hasError }
}

/**
 * Backend status mapping (non-reactive utility).
 * Used by ActivityTimeline watcher where a plain function is needed.
 */
const backendStatusMap = {
  'pending': 'pending',
  'awaiting_permission': 'permission_required',
  'running': 'executing',
  'completed': 'completed',
  'failed': 'error',
  'denied': 'completed',
  'interrupted': 'orphaned'
}

/**
 * Get effective status for a plain tool object (non-reactive).
 * For use in watchers and plain function calls.
 */
export function getEffectiveStatusForTool(tool) {
  if (!tool) return 'pending'

  if (tool.backendStatus) {
    return backendStatusMap[tool.backendStatus] || tool.backendStatus
  }

  return tool.status
}

/**
 * Get color for a given status string (non-reactive).
 * For use in segment gradient computation.
 */
export function getColorForStatus(status, tool) {
  const hasError = tool?.result?.error || tool?.status === 'error' || tool?.permissionDecision === 'deny'
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
