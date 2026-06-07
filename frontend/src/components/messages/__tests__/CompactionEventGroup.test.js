import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { ref } from 'vue'
import { renderWithStores } from '@/test-utils/render'
import CompactionEventGroup from '@/components/messages/CompactionEventGroup.vue'
import { useResourceStore } from '@/stores/resource'
import { useSessionStore } from '@/stores/session'

// Mock API calls used by stores
const apiMock = vi.hoisted(() => ({
  get: vi.fn().mockResolvedValue({ sessions: [], projects: [] }),
  post: vi.fn(),
  put: vi.fn(),
  delete: vi.fn(),
  patch: vi.fn(),
}))
vi.mock('@/utils/api', () => ({ api: apiMock, apiGet: vi.fn(), apiDelete: vi.fn(), getAuthToken: vi.fn().mockReturnValue(null) }))
vi.mock('@/composables/useNotifications', () => ({ notify: vi.fn() }))

// Boundary timestamp in the past (>30s ago) — allows static 'failed' state
const OLD_TS = 1700000000.0
// Boundary timestamp very recent — will be 'pending' on mount
const RECENT_TS = Date.now() / 1000

function makeCompactionMessages(boundaryTs = OLD_TS, compactMetadata = { pre_tokens: 147213 }) {
  return [
    // messages[0]: first status message (timestamp shown in pill)
    {
      type: 'system',
      metadata: { subtype: 'status', status: 'compacting' },
      timestamp: boundaryTs - 5,
    },
    // messages[1]: second status message
    {
      type: 'system',
      metadata: { subtype: 'status', status: null },
      timestamp: boundaryTs - 4,
    },
    // messages[2]: compact_boundary — this is boundaryMessage
    {
      type: 'system',
      metadata: {
        subtype: 'compact_boundary',
        init_data: { compact_metadata: compactMetadata },
      },
      timestamp: boundaryTs,
    },
    // messages[3]: continuation user message
    {
      type: 'user',
      content: 'This session is being continued from a previous conversation...',
      timestamp: boundaryTs + 1,
    },
  ]
}

const SESSION_ID = 'sess-compaction-test'

beforeEach(() => {
  setActivePinia(createPinia())
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('CompactionEventGroup — summary state machine', () => {
  it('status=ready when a matching resource exists for the boundary timestamp', async () => {
    const messages = makeCompactionMessages(OLD_TS)
    const { wrapper } = renderWithStores(CompactionEventGroup, {
      props: { messages },
      provide: { viewSessionId: ref(SESSION_ID) },
    })

    // Inject a matching resource into the resource store
    const resourceStore = useResourceStore()
    resourceStore.resourcesBySession.set(SESSION_ID, [
      {
        resource_id: 'res-1',
        title: 'Compaction Summary — 14:32',
        description: `compaction:${Math.floor(OLD_TS)}`,
        session_id: SESSION_ID,
      },
    ])
    resourceStore.resourcesBySession = new Map(resourceStore.resourcesBySession)

    await wrapper.vm.$nextTick()

    // Green pill must be visible
    const pill = wrapper.find('.pill-summary-status.ready')
    expect(pill.exists()).toBe(true)
    expect(pill.text()).toContain('View summary')
  })

  it('status=failed when no resource and boundary timestamp is >30s old', async () => {
    const messages = makeCompactionMessages(OLD_TS)
    const { wrapper } = renderWithStores(CompactionEventGroup, {
      props: { messages },
      provide: { viewSessionId: ref(SESSION_ID) },
    })

    await wrapper.vm.$nextTick()

    const pill = wrapper.find('.pill-summary-status.failed')
    expect(pill.exists()).toBe(true)
    expect(pill.text()).toContain('unavailable')
  })

  it('status=pending when no resource and boundary timestamp is <30s old', async () => {
    // Use a very recent timestamp (now) — elapsed < 30s
    const recentTs = Date.now() / 1000
    const messages = makeCompactionMessages(recentTs)
    const { wrapper } = renderWithStores(CompactionEventGroup, {
      props: { messages },
      provide: { viewSessionId: ref(SESSION_ID) },
    })

    await wrapper.vm.$nextTick()

    const pill = wrapper.find('.pill-summary-status.pending')
    expect(pill.exists()).toBe(true)
    expect(pill.text()).toContain('Distilling')
  })

  it('pending transitions to failed after 30s elapses', async () => {
    const recentTs = Date.now() / 1000
    const messages = makeCompactionMessages(recentTs)
    const { wrapper } = renderWithStores(CompactionEventGroup, {
      props: { messages },
      provide: { viewSessionId: ref(SESSION_ID) },
    })

    await wrapper.vm.$nextTick()
    expect(wrapper.find('.pill-summary-status.pending').exists()).toBe(true)

    // Advance fake timers by 31 seconds
    vi.advanceTimersByTime(31_000)
    await wrapper.vm.$nextTick()

    expect(wrapper.find('.pill-summary-status.failed').exists()).toBe(true)
    expect(wrapper.find('.pill-summary-status.pending').exists()).toBe(false)
  })

  it('status=disabled when history_distillation_enabled=false in effective config', async () => {
    const messages = makeCompactionMessages(OLD_TS)
    const { wrapper } = renderWithStores(CompactionEventGroup, {
      props: { messages },
      provide: { viewSessionId: ref(SESSION_ID) },
    })

    const sessionStore = useSessionStore()
    sessionStore.effectiveConfigBySession.set(SESSION_ID, { history_distillation_enabled: false })
    sessionStore.effectiveConfigBySession = new Map(sessionStore.effectiveConfigBySession)

    await wrapper.vm.$nextTick()

    // No summary sub-pill should exist
    expect(wrapper.find('.pill-summary-status').exists()).toBe(false)
    // No summary section in expanded panel either
    await wrapper.find('.msg-pill').trigger('click')
    await wrapper.vm.$nextTick()
    expect(wrapper.find('.summary-link-section').exists()).toBe(false)
  })

  it('gated-off render is structurally identical to baseline (no pill, no panel section)', async () => {
    const messages = makeCompactionMessages(OLD_TS)
    const { wrapper } = renderWithStores(CompactionEventGroup, {
      props: { messages },
      provide: { viewSessionId: ref(SESSION_ID) },
    })

    const sessionStore = useSessionStore()
    sessionStore.effectiveConfigBySession.set(SESSION_ID, { history_distillation_enabled: false })
    sessionStore.effectiveConfigBySession = new Map(sessionStore.effectiveConfigBySession)
    await wrapper.vm.$nextTick()

    // Click to expand
    await wrapper.find('.msg-pill').trigger('click')
    await wrapper.vm.$nextTick()

    // Only continuation section should appear, no summary-link-section
    expect(wrapper.find('.continuation-section').exists()).toBe(true)
    expect(wrapper.find('.summary-link-section').exists()).toBe(false)
  })

  it('clicking the ready pill calls resourceStore.openFullViewById', async () => {
    const messages = makeCompactionMessages(OLD_TS)
    const { wrapper } = renderWithStores(CompactionEventGroup, {
      props: { messages },
      provide: { viewSessionId: ref(SESSION_ID) },
    })

    const resourceStore = useResourceStore()
    resourceStore.resourcesBySession.set(SESSION_ID, [
      {
        resource_id: 'res-2',
        title: 'Compaction Summary — 14:32',
        description: `compaction:${Math.floor(OLD_TS)}`,
        session_id: SESSION_ID,
      },
    ])
    resourceStore.resourcesBySession = new Map(resourceStore.resourcesBySession)
    const openSpy = vi.spyOn(resourceStore, 'openFullViewById').mockImplementation(() => {})

    await wrapper.vm.$nextTick()

    const pill = wrapper.find('.pill-summary-status.ready')
    await pill.trigger('click')

    expect(openSpy).toHaveBeenCalledWith('res-2', SESSION_ID)
  })

  it('ready→failed: resource present means no failed state regardless of elapsed time', async () => {
    // Even with an old boundary timestamp, if a resource exists the status is 'ready'.
    const messages = makeCompactionMessages(OLD_TS)
    const { wrapper } = renderWithStores(CompactionEventGroup, {
      props: { messages },
      provide: { viewSessionId: ref(SESSION_ID) },
    })

    const resourceStore = useResourceStore()
    resourceStore.resourcesBySession.set(SESSION_ID, [
      {
        resource_id: 'res-3',
        title: 'Compaction Summary — 14:32',
        description: `compaction:${Math.floor(OLD_TS)}`,
        session_id: SESSION_ID,
      },
    ])
    resourceStore.resourcesBySession = new Map(resourceStore.resourcesBySession)
    await wrapper.vm.$nextTick()

    expect(wrapper.find('.pill-summary-status.ready').exists()).toBe(true)
    expect(wrapper.find('.pill-summary-status.failed').exists()).toBe(false)
  })
})
