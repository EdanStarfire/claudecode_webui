import { computed } from 'vue'
import { useSessionStore } from '@/stores/session'
import { useUIStore } from '@/stores/ui'

/**
 * Issue #2333: runtime "restart required" state of a session.
 *
 * The backend computes `session.restart_required` (a list of human-readable reasons) when a saved
 * model-related change cannot take effect on the running session; it clears on (re)start.
 * `openRestart()` reuses the existing session manage modal, whose "Restart Agent" action does the
 * restart and reconnect.
 *
 * @param {import('vue').Ref<string>|import('vue').ComputedRef<string>} sessionIdRef
 */
export function useRestartRequired(sessionIdRef) {
  const sessionStore = useSessionStore()
  const uiStore = useUIStore()

  const session = computed(() => (sessionIdRef.value ? sessionStore.getSession(sessionIdRef.value) : null))
  const reasons = computed(() => session.value?.restart_required ?? [])
  const hasRestartRequired = computed(() => reasons.value.length > 0)

  function openRestart() {
    if (session.value) uiStore.showModal('manage-session', { session: session.value })
  }

  return { reasons, hasRestartRequired, openRestart }
}
