import { describe, it, expect, vi } from 'vitest'
import { renderWithStores } from '@/test-utils/render'
import QueueSection from '@/components/tasks/QueueSection.vue'
import { useSessionStore } from '@/stores/session'

const apiMock = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() }))
vi.mock('@/utils/api', () => ({ api: apiMock, getAuthToken: vi.fn() }))

const flush = () => new Promise(r => setTimeout(r, 0))

function item(id, content, metadata = {}) {
  return { queue_id: id, session_id: 's1', content, metadata, status: 'pending', position: 0, created_at: 1700000000 }
}

describe('QueueSection: Slack instruction items (issue #2103)', () => {
  it('shows a quiet "Slack:" prefix and the instruction text, never the nonce block', async () => {
    apiMock.get.mockResolvedValue({ items: [
      item('a', 'run the nightly checks', { relay_inbound: { thread_ts: '1.1' } }),
      item('b', 'plain manual message'),
      item('c', 'x'.repeat(200), { relay_inbound: {} }),
    ] })
    const { wrapper, pinia } = renderWithStores(QueueSection, { props: { expanded: true } })
    useSessionStore(pinia).currentSessionId = 's1'
    await flush()
    await flush()
    const labels = wrapper.findAll('.item-content').map(w => w.text())
    expect(labels[0]).toBe('Slack: run the nightly checks')
    expect(labels[1]).toBe('plain manual message')
    expect(labels[2].startsWith('Slack: xxx')).toBe(true)
    expect(labels[2].length).toBeLessThanOrEqual('Slack: '.length + 52 + 3)
    expect(wrapper.text()).not.toContain('OWNER INSTRUCTION')
  })
})
