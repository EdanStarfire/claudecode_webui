import { computed } from 'vue'

/**
 * Shared composable for tool status computation.
 * Consolidates effectiveStatus mapping duplicated in
 * TimelineDetail, ActivityTimeline, and TimelineNode.
 *
 * Issue #2110 (stage 4b-B): `tool.status` is now the single, backend-normalized frontend-
 * display field (pending/permission_required/executing/completed/error — denied and
 * interrupted both read as 'completed', matching every direct `.status` consumer). Orphaned-
 * ness is carried separately via the `_isOrphaned`/`_orphanedInfo` stamp applyRecord's
 * tool_call branch sets directly on the object — no backendStatus field, no session-scoped
 * orphan Map/store lookup needed here anymore.
 *
 * @param {import('vue').Ref<Object>} toolRef - reactive ref or toRef to the tool/toolCall prop
 * @returns {{ effectiveStatus: import('vue').ComputedRef<string>, isOrphaned: import('vue').ComputedRef<boolean>, orphanedInfo: import('vue').ComputedRef<Object|null>, statusColor: import('vue').ComputedRef<string>, hasError: import('vue').ComputedRef<boolean> }}
 */
export function useToolStatus(toolRef) {
  const hasError = computed(() => {
    const tool = toolRef.value
    return tool?.result?.error || tool?.status === 'error' || tool?.permissionDecision === 'deny'
  })

  // Near-passthrough of tool.status — the one exception is the orphaned stamp, which
  // overrides the 'completed' value denied/interrupted both normalize to, since several
  // display sites (TimelineNode's tooltip, SubagentTimeline) distinguish "orphaned" from a
  // normal completion.
  const effectiveStatus = computed(() => getEffectiveStatusForTool(toolRef.value))

  const isOrphaned = computed(() => !!toolRef.value?._isOrphaned)

  const orphanedInfo = computed(() => toolRef.value?._orphanedInfo || null)

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
 * Get effective status for a plain tool object (non-reactive).
 * For use in watchers and plain function calls. Issue #2110 (stage 4b-B): near-passthrough of
 * `tool.status` (the single backend-normalized field) — the one exception is the `_isOrphaned`
 * stamp, which overrides to 'orphaned' (see useToolStatus()'s own comment for why this diverges
 * from plain 'completed' at a handful of display sites).
 */
export function getEffectiveStatusForTool(tool) {
  if (!tool) return 'pending'
  if (tool._isOrphaned) return 'orphaned'
  return tool.status ?? 'pending'
}
