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
    expect(router.currentRoute.value.fullPath).toBe('/settings/relays?tab=audit&session=s1')
  })

  it('shows no monitor count without monitors and no dot on an older Backend summary', async () => {
    const { wrapper } = await mountRow({ relay_summary: SUMMARY })
    expect(wrapper.find('[data-testid="relay-info-monitors"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="relay-info-dot"]').exists()).toBe(false)
  })

  it('shows the monitor count with paused / suspended tags, the dot only with attention, and the #monitors link', async () => {
    const quiet = await mountRow({
      relay_summary: { ...SUMMARY, monitors: { active: 1, paused: 1, suspended: 1 }, attention: false },
    })
    expect(quiet.wrapper.find('[data-testid="relay-info-monitors-link"]').text()).toBe('3 monitors')
    expect(quiet.wrapper.find('[data-testid="relay-info-monitors-paused"]').text()).toBe('1 paused')
    expect(quiet.wrapper.find('[data-testid="relay-info-monitors-suspended"]').text()).toBe('1 suspended')
    expect(quiet.wrapper.find('[data-testid="relay-info-dot"]').exists()).toBe(false)
    const { wrapper, router } = await mountRow({
      relay_summary: { ...SUMMARY, monitors: { active: 1, paused: 0, suspended: 0 }, attention: true },
    })
    expect(wrapper.find('[data-testid="relay-info-monitors-link"]').text()).toBe('1 monitor')
    expect(wrapper.find('[data-testid="relay-info-monitors-paused"]').exists()).toBe(false)
    const dot = wrapper.find('[data-testid="relay-info-dot"]')
    expect(dot.attributes('title')).toBe('Needs attention: see Monitors')
    await wrapper.find('[data-testid="relay-info-monitors-link"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.fullPath).toBe('/settings/session/s1/relay#monitors')
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

  it('adds the dot and the title text only with attention, and the monitor count when there are monitors', () => {
    const quiet = mount(RelaySessionChip, { props: { summary: { ...SUMMARY, monitors: { active: 3, paused: 0, suspended: 0 }, attention: false } } })
    expect(quiet.find('[data-testid="relay-chip-dot"]').exists()).toBe(false)
    expect(quiet.attributes('title')).toContain('3 monitors')
    expect(quiet.attributes('title')).not.toContain('needs attention')
    const lit = mount(RelaySessionChip, { props: { summary: { ...SUMMARY, monitors: { active: 3 }, attention: true } } })
    expect(lit.find('[data-testid="relay-chip-dot"]').exists()).toBe(true)
    expect(lit.attributes('title')).toContain('needs attention (monitors)')
    expect(mount(RelaySessionChip, { props: { summary: SUMMARY } }).find('[data-testid="relay-chip-dot"]').exists()).toBe(false)
  })
})
