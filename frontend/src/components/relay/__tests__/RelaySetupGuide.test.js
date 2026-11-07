import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { mount, flushPromises } from '@vue/test-utils'
import RelaySetupGuide from '@/components/relay/RelaySetupGuide.vue'

const apiMock = vi.hoisted(() => ({
  get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(), patch: vi.fn()
}))
vi.mock('@/utils/api', () => ({ api: apiMock, getAuthToken: vi.fn() }))

const MANIFEST = {
  manifest: { display_information: { name: 'ccWebUI Relay' } },
  manifest_json: '{\n  "display_information": {"name": "ccWebUI Relay"}\n}',
  scopes: {
    required: [{ name: 'chat:write', kind: 'scope', reason: 'Post as you.' }],
    optional: [{ name: 'search:read.public', kind: 'scope', reason: 'Granular.' }],
    inbound: [{ name: 'message.im', kind: 'event', reason: 'DMs.' }],
  },
  steps: [{ title: 'Create the app', detail: 'Use the manifest.' }],
}

describe('RelaySetupGuide', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    Object.values(apiMock).forEach(fn => fn.mockReset())
    apiMock.get.mockResolvedValue(MANIFEST)
  })

  it('renders steps, manifest and the three scope groups', async () => {
    const w = mount(RelaySetupGuide, { props: { relayId: 'r1', open: true } })
    await flushPromises()
    expect(w.text()).toContain('Create the app')
    expect(w.find('[data-testid="relay-manifest-json"]').text()).toContain('ccWebUI Relay')
    expect(w.text()).toContain('Required (user token)')
    expect(w.text()).toContain('Inbound (next stage)')
    expect(w.text()).toContain('chat:write')
  })

  it('toggling inbound off reloads without inbound and hides the group', async () => {
    const w = mount(RelaySetupGuide, { props: { relayId: 'r1', open: true } })
    await flushPromises()
    await w.find('[data-testid="relay-include-inbound"]').setValue(false)
    await flushPromises()
    expect(apiMock.get).toHaveBeenLastCalledWith('/api/relays/r1/manifest?include_inbound=false')
    expect(w.text()).not.toContain('Inbound (next stage)')
  })

  it('copies the manifest JSON to the clipboard', async () => {
    const writeText = vi.fn().mockResolvedValue()
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    const w = mount(RelaySetupGuide, { props: { relayId: 'r1', open: true } })
    await flushPromises()
    await w.find('[data-testid="relay-manifest-copy"]').trigger('click')
    await flushPromises()
    expect(writeText).toHaveBeenCalledWith(MANIFEST.manifest_json)
  })

  it('downloads slack-app-manifest.json', async () => {
    URL.createObjectURL = vi.fn(() => 'blob:x')
    URL.revokeObjectURL = vi.fn()
    let name = null
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function () { name = this.download })
    const w = mount(RelaySetupGuide, { props: { relayId: 'r1', open: true } })
    await flushPromises()
    await w.find('[data-testid="relay-manifest-download"]').trigger('click')
    expect(name).toBe('slack-app-manifest.json')
    click.mockRestore()
  })
})
