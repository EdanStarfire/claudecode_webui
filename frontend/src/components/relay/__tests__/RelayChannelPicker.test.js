import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { mount, flushPromises } from '@vue/test-utils'
import RelayChannelPicker from '@/components/relay/RelayChannelPicker.vue'

const apiMock = vi.hoisted(() => ({
  get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(), patch: vi.fn()
}))
vi.mock('@/utils/api', () => ({ api: apiMock, getAuthToken: vi.fn() }))

const MODEL = { values: ['a'], target_pattern: '^[CD][A-Z0-9]{6,}$', target_label: 'Test ID' }
const rows = [
  { id: 'C0PUBLIC01', kind: 'public', name: 'general', label: '#general' },
  { id: 'C0BLOCKED1', kind: 'private', name: 'secret', label: '#secret' },
  { id: 'D0DM000001', kind: 'dm', name: 'Alice', label: 'DM with Alice', user: 'U1' },
]

beforeEach(() => {
  vi.useFakeTimers()
  setActivePinia(createPinia())
  Object.values(apiMock).forEach(fn => fn.mockReset())
  apiMock.get.mockResolvedValue({ entries: rows, next_cursor: null, truncated: false })
})
afterEach(() => vi.useRealTimers())

const mk = (props = {}) => mount(RelayChannelPicker, {
  props: { relayId: 'r1', policyModel: MODEL, statusFor: t => ({ C0BLOCKED1: 'blocked' }[t] || null), ...props },
  attachTo: document.body,
})

describe('RelayChannelPicker', () => {
  it('lists blocked and unlisted entries with their status, grouped by kind', async () => {
    const w = mk()
    await w.find('input').trigger('focus')
    await vi.runAllTimersAsync()
    await flushPromises()
    expect(w.find('[data-testid="relay-picker-row-C0PUBLIC01"]').text()).toContain('unlisted')
    expect(w.find('[data-testid="relay-picker-row-C0BLOCKED1"]').text()).toContain('blocked')
    const groups = w.findAll('.picker-group').map(g => g.text())
    expect(groups).toEqual(['Channels', 'Private channels', 'Direct messages'])
    w.unmount()
  })

  it('debounces typing into one request and aborts the stale one', async () => {
    const w = mk()
    await w.find('input').trigger('focus')
    await vi.runAllTimersAsync()
    apiMock.get.mockClear()
    const signals = []
    apiMock.get.mockImplementation((url, opts) => { signals.push(opts.signal); return new Promise(() => {}) })
    const input = w.find('input')
    await input.setValue('g')
    await input.setValue('ge')
    await input.setValue('gen')
    expect(apiMock.get).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(250)
    expect(apiMock.get).toHaveBeenCalledTimes(1)
    expect(apiMock.get.mock.calls[0][1].params.q).toBe('gen')
    await input.setValue('gene')
    await vi.advanceTimersByTimeAsync(250)
    expect(signals[0].aborted).toBe(true)
    w.unmount()
  })

  it('people are behind a toggle and need two characters', async () => {
    const w = mk()
    await w.find('input').trigger('focus')
    await vi.runAllTimersAsync()
    apiMock.get.mockClear()
    await w.find('[data-testid="relay-picker-people"]').setValue(true)
    await vi.runAllTimersAsync()
    expect(apiMock.get.mock.calls.at(-1)[1].params.kinds).toBeNull()
    await w.find('[data-testid="relay-picker-input"]').setValue('al')
    await vi.advanceTimersByTimeAsync(250)
    expect(apiMock.get.mock.calls.at(-1)[1].params.kinds).toContain('person')
    w.unmount()
  })

  it('a person without a known DM is disabled and says so; with a DM it targets the DM', async () => {
    apiMock.get.mockResolvedValue({ entries: [
      { id: 'U0NODM0001', kind: 'person', name: 'Bob', label: 'Bob', dm_id: null },
      { id: 'U0HASDM001', kind: 'person', name: 'Cy', label: 'Cy', dm_id: 'D0CYDM0001' },
    ] })
    const w = mk()
    await w.find('input').trigger('focus')
    await vi.runAllTimersAsync()
    await flushPromises()
    const nodm = w.find('[data-testid="relay-picker-row-U0NODM0001"]')
    expect(nodm.attributes('disabled')).toBeDefined()
    expect(nodm.text()).toContain('no DM yet')
    await w.find('[data-testid="relay-picker-row-U0HASDM001"]').trigger('click')
    expect(w.emitted('select')[0][0]).toEqual({ target: 'D0CYDM0001', label: 'Cy' })
    w.unmount()
  })

  it('a pasted ID matching the policy model pattern can be used directly', async () => {
    const w = mk()
    await w.find('[data-testid="relay-picker-input"]').setValue('C0PASTED01')
    await vi.runAllTimersAsync()
    await w.find('[data-testid="relay-picker-paste"]').trigger('click')
    expect(w.emitted('select')[0][0].target).toBe('C0PASTED01')
    await w.find('[data-testid="relay-picker-input"]').setValue('not an id')
    expect(w.find('[data-testid="relay-picker-paste"]').exists()).toBe(false)
    w.unmount()
  })

  it('pages with the cursor and shows the load error', async () => {
    apiMock.get.mockResolvedValueOnce({ entries: [rows[0]], next_cursor: 'o:1' })
    const w = mk()
    await w.find('input').trigger('focus')
    await vi.runAllTimersAsync()
    await flushPromises()
    apiMock.get.mockResolvedValueOnce({ entries: [rows[1]], next_cursor: null })
    await w.find('[data-testid="relay-picker-more"]').trigger('click')
    await flushPromises()
    expect(apiMock.get.mock.calls.at(-1)[1].params.cursor).toBe('o:1')
    expect(w.findAll('.picker-row').length).toBe(2)
    apiMock.get.mockRejectedValueOnce(Object.assign(new Error('x'), { data: { detail: { code: 'query_required', message: 'Need more characters' } } }))
    await w.find('[data-testid="relay-picker-input"]').setValue('zz')
    await vi.advanceTimersByTimeAsync(250)
    await flushPromises()
    expect(w.find('[data-testid="relay-picker-error"]').text()).toBe('Need more characters')
    w.unmount()
  })

  it('Load more continues the search that produced the cursor, not the box text', async () => {
    apiMock.get.mockResolvedValueOnce({ entries: [rows[0]], next_cursor: 'o:1' })
    const w = mk()
    await w.find('[data-testid="relay-picker-input"]').setValue('eng')
    await vi.advanceTimersByTimeAsync(250)
    await flushPromises()
    apiMock.get.mockResolvedValueOnce({ entries: [rows[1]], next_cursor: null })
    await w.find('[data-testid="relay-picker-input"]').setValue('ops') // not searched yet
    await w.find('[data-testid="relay-picker-more"]').trigger('click')
    await flushPromises()
    const params = apiMock.get.mock.calls.at(-1)[1].params
    expect(params.q).toBe('eng')
    expect(params.cursor).toBe('o:1')
    w.unmount()
  })

  it('renders names as text and selects with the keyboard', async () => {
    apiMock.get.mockResolvedValue({ entries: [{ id: 'C0EVIL0001', kind: 'public', name: 'x', label: '#<b>bold</b>' }] })
    const w = mk()
    await w.find('input').trigger('focus')
    await vi.runAllTimersAsync()
    await flushPromises()
    expect(w.find('.picker-panel b').exists()).toBe(false)
    await w.find('[data-testid="relay-picker-input"]').trigger('keydown.enter')
    expect(w.emitted('select')[0][0].target).toBe('C0EVIL0001')
    w.unmount()
  })
})
