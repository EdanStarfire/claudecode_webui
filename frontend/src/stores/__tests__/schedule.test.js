import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

const apiMock = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  delete: vi.fn()
}))
vi.mock('@/utils/api', () => ({ api: apiMock }))

beforeEach(() => {
  setActivePinia(createPinia())
  Object.values(apiMock).forEach(fn => fn.mockReset())
})

describe('schedule store', () => {
  describe('getAutoResumeSchedule (issue #2190)', () => {
    it('finds the pending auto_resume schedule for a session across legions', async () => {
      const { useScheduleStore } = await import('@/stores/schedule')
      const store = useScheduleStore()

      store.schedulesByLegion.set('legion-1', [
        { schedule_id: 's1', schedule_type: 'prompt', minion_id: 'sess-1' },
        { schedule_id: 's2', schedule_type: 'auto_resume', minion_id: 'sess-1', next_run: 1234567890 }
      ])

      const found = store.getAutoResumeSchedule('sess-1')

      expect(found).toEqual(
        expect.objectContaining({ schedule_id: 's2', schedule_type: 'auto_resume' })
      )
    })

    it('returns null when no auto_resume schedule exists for the session', async () => {
      const { useScheduleStore } = await import('@/stores/schedule')
      const store = useScheduleStore()

      store.schedulesByLegion.set('legion-1', [
        { schedule_id: 's1', schedule_type: 'prompt', minion_id: 'sess-1' }
      ])

      expect(store.getAutoResumeSchedule('sess-1')).toBeNull()
    })

    it('does not match an auto_resume schedule belonging to a different session', async () => {
      const { useScheduleStore } = await import('@/stores/schedule')
      const store = useScheduleStore()

      store.schedulesByLegion.set('legion-1', [
        { schedule_id: 's2', schedule_type: 'auto_resume', minion_id: 'sess-other' }
      ])

      expect(store.getAutoResumeSchedule('sess-1')).toBeNull()
    })
  })
})
