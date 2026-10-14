import { computed } from 'vue'
import { useSessionStore } from '@/stores/session'
import { usePollingStore } from '@/stores/polling'
import { HYDRATION_LOADING_STAGES } from '@/utils/hydrationStage'

/**
 * Issue #2035: session data-hydration stage visibility, shared by InputArea.vue
 * (placeholder/error banner) and MessageList.vue (empty-state loading/error/ready split).
 */
export function useHydrationStage(sessionIdRef) {
  const sessionStore = useSessionStore()
  const pollingStore = usePollingStore()

  const hydrationStageEntry = computed(() => sessionStore.hydrationStageBySession.get(sessionIdRef.value))
  const hydrationStage = computed(() => hydrationStageEntry.value?.stage ?? 'ready')
  const hydrationError = computed(() => hydrationStage.value === 'error')
  const isHydrationLoading = computed(() => HYDRATION_LOADING_STAGES.includes(hydrationStage.value))
  const hydrationErrorMessage = computed(() => {
    const err = hydrationStageEntry.value?.error
    if (!err) return ''
    const verb = err.kind === 'timeout' ? 'Timed out loading' : 'Failed to load'
    return `${verb} session data (${err.stage}) — ${err.message}`
  })
  // Issue #2140: per-page progress during loading_history — { page, recordsReceived,
  // totalCount } | undefined (no progress reported yet, or a non-history stage).
  const hydrationProgress = computed(() => hydrationStageEntry.value?.progress)

  // Issue #2140 (AC5): a resync (evicted cursor / stream_reset) stall — independent of
  // hydrationStage, which is already 'ready' by the time a resync can even happen.
  const resyncStall = computed(() => pollingStore.resyncStallBySession.get(sessionIdRef.value) || { active: false })

  return {
    hydrationStageEntry,
    hydrationStage,
    hydrationError,
    isHydrationLoading,
    hydrationErrorMessage,
    hydrationProgress,
    resyncStall,
  }
}
