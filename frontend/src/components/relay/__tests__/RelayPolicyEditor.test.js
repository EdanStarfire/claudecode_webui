import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { mount, flushPromises } from '@vue/test-utils'
import RelayPolicyEditor from '@/components/relay/RelayPolicyEditor.vue'

const apiMock = vi.hoisted(() => ({
  get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(), patch: vi.fn()
}))
vi.mock('@/utils/api', () => ({ api: apiMock, getAuthToken: vi.fn() }))

const MODEL = {
  values: ['v1', 'v2', 'v3'], target_pattern: '^[CD][A-Z0-9]{6,}$', target_label: 'Test target ID',
  value_help: { v1: 'first help', v2: 'second help' },
}
const A = 'C0AAAAAAA1'
const B = 'C0BBBBBBB2'
const payload = (policies) => ({ relay_id: 'r1', session_id: 's1', policy_model: MODEL, policies })

function conversationsGet(extra = {}) {
  return (url, opts) => {
    if (url.endsWith('/conversations') && opts?.params?.ids) {
      return Promise.resolve({ labels: Object.fromEntries(opts.params.ids.split(',').map(i => [i, `#name-${i}`])) })
    }
    return extra[url] ? Promise.resolve(extra[url]) : Promise.resolve({ entries: [] })
  }
}

beforeEach(() => {
  setActivePinia(createPinia())
  Object.values(apiMock).forEach(fn => fn.mockReset())
})

async function mountLive(policies) {
  apiMock.get.mockImplementation(conversationsGet({ '/api/sessions/s1/relay': payload(policies) }))
  const w = mount(RelayPolicyEditor, { props: { mode: 'live', sessionId: 's1' } })
  await flushPromises()
  return w
}

describe('RelayPolicyEditor (live)', () => {
  it('renders values, target label and help from the policy model only', async () => {
    const w = await mountLive({ [A]: { policy: 'v2', source: 'session' } })
    const options = w.findAll('[data-testid="relay-policy-row-C0AAAAAAA1"] .strip-btn').map(o => o.attributes('aria-label'))
    expect(options).toEqual(['v1', 'v2', 'v3'])
    expect(w.find('[data-testid="relay-policy-row-C0AAAAAAA1"] [aria-checked="true"]').attributes('aria-label')).toBe('v2')
    expect(w.find('[data-testid="relay-policy-opt-v1"]').attributes('title')).toContain('first help')
    expect(w.find('[data-testid="relay-picker-input"]').attributes('placeholder')).toBe('#channel, @person or paste an ID')
    expect(w.find('[data-testid="relay-policy-explain"]').text()).toContain('second help')
    expect(w.find('[data-testid="relay-policy-live-note"]').text()).toBe('Applies immediately.')
  })

  it('keeps the explanation collapsed by default and shows the unlisted line', async () => {
    const w = await mountLive({})
    const details = w.find('[data-testid="relay-policy-explain"]')
    expect(details.attributes('open')).toBeUndefined()
    expect(details.text()).toContain('Unlisted channels ask on first use')
    expect(w.find('[data-testid="relay-policy-empty"]').exists()).toBe(true)
  })

  it('resolves labels for policy targets and keeps the bare ID in the hover text', async () => {
    const w = await mountLive({ [A]: { policy: 'v1', source: 'session' } })
    const row = w.find(`[data-testid="relay-policy-row-${A}"]`)
    expect(row.find('[data-testid="relay-policy-label"]').text()).toBe(`#name-${A}`)
    expect(row.attributes('title')).toContain(A) // the ID lives in the hover text
  })

  it('tags every row with the app\'s SourceMarker (P/T/S) and offers reset only on own rows', async () => {
    const w = await mountLive({
      [A]: { policy: 'v1', source: 'template' }, [B]: { policy: 'v2', source: 'session' },
    })
    const inherited = w.find(`[data-testid="relay-policy-row-${A}"]`)
    const own = w.find(`[data-testid="relay-policy-row-${B}"]`)
    expect(inherited.find('[data-testid="relay-policy-tier"]').text()).toBe('T')
    expect(inherited.find('[data-testid="relay-policy-tier"]').attributes('title')).toBe('Inherited from template')
    expect(inherited.find('[data-testid="relay-policy-remove"]').exists()).toBe(false)
    expect(own.find('[data-testid="relay-policy-tier"]').text()).toBe('S')
    expect(own.find('[data-testid="relay-policy-remove"]').attributes('title')).toBe('Reset to inherited value')
  })

  it('change / override calls the exact relay-qualified PUT and adopts the returned payload', async () => {
    const w = await mountLive({ [A]: { policy: 'v1', source: 'template' } })
    apiMock.put.mockResolvedValue(payload({ [A]: { policy: 'v3', source: 'session' } }))
    await w.find('[data-testid="relay-policy-opt-v3"]').trigger('click')
    await flushPromises()
    expect(apiMock.put).toHaveBeenCalledWith(`/api/sessions/s1/relays/r1/policies/${A}`, { policy: 'v3' })
    expect(w.find('[data-testid="relay-policy-tier"]').text()).toBe('S')
    expect(w.find('[data-testid="relay-policy-row-C0AAAAAAA1"] [aria-checked="true"]').attributes('aria-label')).toBe('v3')
  })

  it('remove calls DELETE and shows the revealed lower-tier value from the payload', async () => {
    const w = await mountLive({ [A]: { policy: 'v3', source: 'session' } })
    apiMock.delete.mockResolvedValue(payload({ [A]: { policy: 'v1', source: 'profile' } }))
    await w.find('[data-testid="relay-policy-remove"]').trigger('click')
    await flushPromises()
    expect(apiMock.delete).toHaveBeenCalledWith(`/api/sessions/s1/relays/r1/policies/${A}`)
    expect(w.find('[data-testid="relay-policy-tier"]').text()).toBe('P')
  })

  it.each([409, 422])('rolls back and shows the error on a %s', async (status) => {
    const w = await mountLive({ [A]: { policy: 'v1', source: 'session' } })
    apiMock.put.mockRejectedValue(Object.assign(new Error('nope'), { status, data: { detail: `rejected ${status}` } }))
    await w.find('[data-testid="relay-policy-opt-v3"]').trigger('click')
    await flushPromises()
    expect(w.find('[data-testid="relay-policy-error"]').text()).toContain('nope')
    expect(w.find('[aria-checked="true"]').attributes('aria-label')).toBe('v1')
  })

  it('serialises concurrent edits and a failed edit rolls back only its own target', async () => {
    const w = await mountLive({ [A]: { policy: 'v1', source: 'session' }, [B]: { policy: 'v1', source: 'session' } })
    let releaseA
    apiMock.put.mockImplementationOnce(() => new Promise((resolve) => { releaseA = () => resolve(payload({ [A]: { policy: 'v2', source: 'session' }, [B]: { policy: 'v1', source: 'session' } })) }))
    apiMock.put.mockRejectedValueOnce(new Error('rejected'))
    const rowOf = (t) => w.find(`[data-testid="relay-policy-row-${t}"]`)
    await rowOf(A).find('[data-testid="relay-policy-opt-v2"]').trigger('click')
    await rowOf(B).find('[data-testid="relay-policy-opt-v3"]').trigger('click')
    expect(apiMock.put).toHaveBeenCalledTimes(1) // B waits for A
    releaseA()
    await flushPromises()
    expect(apiMock.put).toHaveBeenCalledTimes(2)
    const rows = [A, B].map(t => rowOf(t).find('[aria-checked="true"]').attributes('aria-label'))
    expect(rows).toEqual(['v2', 'v1']) // A kept its new value; B rolled back alone
    expect(w.find('[data-testid="relay-policy-error"]').text()).toContain('rejected')
  })

  it('ignores a stale session-relay response after the session changed', async () => {
    let releaseOld
    apiMock.get.mockImplementation((url, opts) => {
      if (url === '/api/sessions/old/relay') return new Promise((resolve) => { releaseOld = () => resolve(payload({ [A]: { policy: 'v1', source: 'session' } })) })
      if (url === '/api/sessions/new/relay') return Promise.resolve(payload({ [B]: { policy: 'v2', source: 'session' } }))
      return Promise.resolve({ labels: {} })
    })
    const w = mount(RelayPolicyEditor, { props: { mode: 'live', sessionId: 'old' } })
    await w.setProps({ sessionId: 'new' })
    await flushPromises()
    releaseOld()
    await flushPromises()
    expect(w.find(`[data-testid="relay-policy-row-${B}"]`).exists()).toBe(true)
    expect(w.find(`[data-testid="relay-policy-row-${A}"]`).exists()).toBe(false)
  })

  it('adding from the picker PUTs the chosen value for the picked target', async () => {
    const w = await mountLive({})
    apiMock.put.mockResolvedValue(payload({ [B]: { policy: 'v2', source: 'session' } }))
    await w.find('[data-testid="relay-policy-new-value"] [data-testid="relay-policy-opt-v2"]').trigger('click')
    w.findComponent({ name: 'RelayChannelPicker' }).vm.$emit('select', { target: B, label: '#picked' })
    await flushPromises()
    expect(apiMock.put).toHaveBeenCalledWith(`/api/sessions/s1/relays/r1/policies/${B}`, { policy: 'v2' })
  })

  it('refuses a target that does not match the model pattern', async () => {
    const w = await mountLive({})
    w.findComponent({ name: 'RelayChannelPicker' }).vm.$emit('select', { target: 'bad id', label: 'x' })
    await flushPromises()
    expect(apiMock.put).not.toHaveBeenCalled()
    expect(w.find('[data-testid="relay-policy-error"]').text()).toContain('Test target ID')
  })

  it('renders Slack-controlled labels as text, not markup', async () => {
    apiMock.get.mockImplementation((url, opts) => url === '/api/sessions/s1/relay'
      ? Promise.resolve(payload({ [A]: { policy: 'v1', source: 'session' } }))
      : Promise.resolve({ labels: { [A]: '#<img src=x onerror=alert(1)>' } }))
    const w = mount(RelayPolicyEditor, { props: { mode: 'live', sessionId: 's1' } })
    await flushPromises()
    expect(w.find('img').exists()).toBe(false)
    expect(w.find('[data-testid="relay-policy-label"]').text()).toBe('#<img src=x onerror=alert(1)>')
  })
})

describe('RelayPolicyEditor (local)', () => {
  const mountLocal = (props) => {
    apiMock.get.mockImplementation(conversationsGet())
    return mount(RelayPolicyEditor, { props: { mode: 'local', relayId: 'r1', policyModel: MODEL, ...props } })
  }

  it('emits only own-tier nested entries and never the inherited keys', async () => {
    const w = mountLocal({
      modelValue: { r1: { [A]: 'v1' } },
      inherited: { [B]: { policy: 'v3', source: 'profile' } },
    })
    await flushPromises()
    await w.find(`[data-testid="relay-policy-row-${A}"] [data-testid="relay-policy-opt-v2"]`).trigger('click')
    expect(w.emitted('update:modelValue').at(-1)[0]).toEqual({ r1: { [A]: 'v2' } })
    expect(apiMock.put).not.toHaveBeenCalled()
    expect(w.find('[data-testid="relay-policy-live-note"]').exists()).toBe(false)
  })

  it('overriding an inherited row emits one own entry, not the inherited set', async () => {
    const w = mountLocal({ modelValue: null, inherited: { [B]: { policy: 'v3', source: 'template' }, [A]: { policy: 'v1', source: 'profile' } } })
    await flushPromises()
    await w.find(`[data-testid="relay-policy-row-${B}"] [data-testid="relay-policy-opt-v1"]`).trigger('click')
    expect(w.emitted('update:modelValue').at(-1)[0]).toEqual({ r1: { [B]: 'v1' } })
  })

  it('removing the last own entry drops the relay key; other relays are preserved', async () => {
    const w = mountLocal({ modelValue: { r1: { [A]: 'v1' }, other: { [B]: 'v2' } } })
    await flushPromises()
    await w.find('[data-testid="relay-policy-remove"]').trigger('click')
    expect(w.emitted('update:modelValue').at(-1)[0]).toEqual({ other: { [B]: 'v2' } })
  })

  it('adding from the picker emits the nested shape, never the flat one', async () => {
    const w = mountLocal({ modelValue: null })
    await flushPromises()
    w.findComponent({ name: 'RelayChannelPicker' }).vm.$emit('select', { target: A, label: '#x' })
    await flushPromises()
    const emitted = w.emitted('update:modelValue').at(-1)[0]
    expect(emitted).toEqual({ r1: { [A]: 'v1' } })
  })
})

describe('RelayPolicyEditor layout contract', () => {
  it('a row is one line: tag, truncating label with full text in the title, strip, reset', async () => {
    const w = await mountLive({ [A]: { policy: 'v1', source: 'session' } })
    const row = w.find(`[data-testid="relay-policy-row-${A}"]`)
    expect(row.attributes('title')).toBe(`#name-${A} · ${A}`)
    expect(row.find('code').exists()).toBe(false) // no second line with the monospace ID
    const kids = [...row.element.children].map(c => c.className.split(' ')[0])
    expect(kids).toEqual(['source-marker', 'row-label', 'policy-strip', 'field-reset-btn'])
    expect(row.find('select').exists()).toBe(false)
  })
})
