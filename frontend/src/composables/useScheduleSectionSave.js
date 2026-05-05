import { useScheduleStore } from '@/stores/schedule'
import { useSessionStore } from '@/stores/session'

/**
 * Returns an async save function that performs the dual-PATCH for ephemeral schedule sections:
 * 1. PATCH schedule session_config
 * 2. PATCH bound ephemeral agent session (if ephemeral_agent_id exists)
 */
export function useScheduleSectionSave({ scheduleId, legionId, ephemeralAgentId }) {
  const scheduleStore = useScheduleStore()
  const sessionStore = useSessionStore()

  return async function saveScheduleSessionConfig(newSessionConfig) {
    await scheduleStore.updateSchedule(legionId.value, scheduleId.value, { session_config: newSessionConfig })
    if (ephemeralAgentId.value) {
      await sessionStore.patchSession(ephemeralAgentId.value, newSessionConfig)
    }
  }
}
