import { describe, it, expect, beforeEach, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { renderWithStores } from '@/test-utils/render'
import RelaySessionInfoRow from '@/components/relay/RelaySessionInfoRow.vue'
import RelaySessionChip from '@/components/relay/RelaySessionChip.vue'
import { makeSession } from '@/test-utils/factories'

const apiMock = vi.hoisted(() => ({
  get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(), patch: vi.fn()
}))
vi.mock('@/utils/api', () => ({ api: apiMock, getAuthToken: vi.fn() }))

const SUMMARY = { relay_id: 'r1', adapter_type: 'slack', handle: 'ea', policy_count: 3 }
const ROUTES = [{ path: '/', component: { template: '<div/>' } }, { path: '/:rest(.*)', component: { template: '<div/>' } }]

beforeEach(() => {
  Object.values(apiMock).forEach(fn => fn.mockReset())
  apiMock.get.mockResolvedValue({ relays: [{ relay_id: 'r1', name: 'Slack WS' }] })
})

async function mountRow(session) {
  const rendered = renderWithStores(RelaySessionInfoRow, { props: { sessionId: 's1' }, routes: ROUTES })
  const { useSessionStore } = await import('@/stores/session')
  useSessionStore(rendered.pinia).sessions.set('s1', makeSession({ session_id: 's1', ...session }))
  await flushPromises()
  return rendered
}

describe('RelaySessionInfoRow', () => {
  it('is hidden when no relay is attached', async () => {
    const { wrapper } = await mountRow({ relay_summary: null })
    expect(wrapper.find('[data-testid="relay-info-row"]').exists()).toBe(false)
  })

  it('shows relay name, plain-text handle and count, with Policies and Audit links', async () => {
    const { wrapper, router } = await mountRow({ relay_summary: SUMMARY })
    expect(wrapper.find('[data-testid="relay-info-name"]').text()).toBe('Slack WS')
    expect(wrapper.find('[data-testid="relay-info-handle"]').text()).toBe('!ea')
    expect(wrapper.find('[data-testid="relay-info-count"]').text()).toBe('3 channel policies')
    await wrapper.find('[data-testid="relay-info-policies"]').trigger('click')
    expect(wrapper.emitted('navigate')).toHaveLength(1)
    await flushPromises()
    expect(router.currentRoute.value.fullPath).toBe('/settings/session/s1/relay')
    await wrapper.find('[data-testid="relay-info-audit"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.fullPath).toBe('/settings/relays?session=s1')
  })

  it('shows the 2a restart note with a Restart action only for the not-attached reason', async () => {
    const quiet = await mountRow({ relay_summary: SUMMARY, restart_required: ['Model changed'] })
    expect(quiet.wrapper.find('[data-testid="relay-info-restart"]').exists()).toBe(false)
    const { wrapper } = await mountRow({
      relay_summary: SUMMARY, restart_required: ['Relay attached after launch: restart to add the relay tools'],
    })
    expect(wrapper.find('[data-testid="relay-info-restart"]').text()).toContain('Relay attached')
    expect(wrapper.find('[data-testid="relay-info-restart-action"]').exists()).toBe(true)
  })
})

describe('RelaySessionChip', () => {
  it('renders the handle as text only when attached', () => {
    expect(mount(RelaySessionChip, { props: { summary: null } }).find('[data-testid="relay-session-chip"]').exists()).toBe(false)
    const w = mount(RelaySessionChip, { props: { summary: { ...SUMMARY, handle: '<b>x</b>' } } })
    expect(w.find('.relay-chip-handle').text()).toBe('!<b>x</b>')
    expect(w.find('b').exists()).toBe(false)
    expect(w.attributes('title')).toContain('3 channel policies')
  })
})
