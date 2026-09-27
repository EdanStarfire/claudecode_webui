import { computed } from 'vue'
import { useSessionStore } from '@/stores/session'
import { HYDRATION_LOADING_STAGES } from '@/utils/hydrationStage'

/**
 * Issue #2035: session data-hydration stage visibility, shared by InputArea.vue
 * (placeholder/error banner) and MessageList.vue (empty-state loading/error/ready split).
 */
export function useHydrationStage(sessionIdRef) {
  const sessionStore = useSessionStore()

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

  return {
    hydrationStageEntry,
    hydrationStage,
    hydrationError,
    isHydrationLoading,
    hydrationErrorMessage,
  }
}
