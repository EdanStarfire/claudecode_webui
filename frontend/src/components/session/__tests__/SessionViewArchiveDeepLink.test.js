import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'
import { nextTick, KeepAlive, h } from 'vue'

vi.mock('@/utils/api', () => ({ api: { get: vi.fn().mockResolvedValue({}), post: vi.fn(), put: vi.fn(), delete: vi.fn(), patch: vi.fn() }, getAuthToken: vi.fn(), getMutationsInFlight: () => 0 }))
vi.mock('@/composables/useNotifications', () => ({ notify: vi.fn() }))

import SessionView from '@/components/session/SessionView.vue'
import { useSessionStore } from '@/stores/session'
import { useMessageStore } from '@/stores/message'

// Issue #2030 stage 3 PR 3F: a cold load of an archive route (a deep link) reaches the view before the
// session list arrived; the archive's messages load as soon as the project id is known.
describe('SessionView archive cold load', () => {
  let pinia
  let router
  beforeEach(async () => {
    pinia = createPinia()
    setActivePinia(pinia)
    router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: { render: () => h('div') } }, { path: '/session/:sessionId/archive/:archiveId', component: { render: () => h('div') } }] })
    await router.push('/session/s1/archive/a1?msg=m1')
    await router.isReady()
  })

  it('waits for the session list instead of silently showing nothing, then loads the archive window', async () => {
    const messageStore = useMessageStore(pinia)
    const load = vi.spyOn(messageStore, 'loadMessages').mockResolvedValue()
    const sessionStore = useSessionStore(pinia)
    const errors = []
    vi.spyOn(console, 'error').mockImplementation((...a) => errors.push(a.map(String).join(' ')))
    const stubs = Object.fromEntries(['SessionStateStatusLine', 'SessionStatusBar', 'MessageList', 'AgentNotificationStrip', 'PermissionQueue', 'ArchiveTabBar', 'ArchiveProxyLogsTab', 'ArchiveQueueTab', 'ArchiveAttachmentsTab', 'ArchiveHistoryTab'].map(n => [n, true]))
    const wrapper = mount({ render: () => h(KeepAlive, null, [h(SessionView, { sessionId: 's1', archiveId: 'a1' })]) }, { global: { plugins: [pinia, router], stubs: { ...stubs, InputArea: { template: '<div />', methods: { focusInput() {} } }, KeepAlive: false } } })
    await new Promise(r => setTimeout(r, 30))
    expect(load).not.toHaveBeenCalled()
    sessionStore.sessions = new Map([['s1', { session_id: 's1', project_id: 'p1' }]])
    await nextTick()
    await new Promise(r => setTimeout(r, 30))
    expect(load).toHaveBeenCalledTimes(1)
    expect(load.mock.calls[0][3].baseUrl).toBe('/api/projects/p1/archives/s1/a1/messages')
    wrapper.unmount()
  })
})
